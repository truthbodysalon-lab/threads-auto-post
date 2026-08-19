#!/usr/bin/env python3
"""デスクトップの画像フォルダからThreadsへ画像投稿する（truth/nagaoka・2026-08-19）。

流れ: /Users/mt112/Desktop/Threads投稿画像/<acct>/ の画像を検出
  → リサイズ/変換(sips) → リポジトリ images/<acct>/ へコミット＆push（公開raw URL化）
  → ファイル名キーワードに応じた本文を生成 → IMAGEコンテナ作成→公開
  → 元画像を 使用済み/ へ移動・投稿台帳に記録（同一コミットでpush）

制約・ルール:
- 1アカウント1日2枚まで（画像は挑戦枠。playbook testingとして実測評価）
- 投稿時間帯6-23時のみ。R1: 全HTTP通信にtimeout必須
- 本文はプレイブック準拠の短文。URL無し（HPB一本化ルールに抵触しない）
- HEICはjpgへ自動変換。幅1440px超は縮小（Threads上限8MB対策）

使い方: python3 image_post.py [--dry-run]
launchd: com.threads.imagepost が4時間毎に実行
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).parent
SRC = Path("/Users/mt112/Desktop/Threads投稿画像")
REPO_IMG = BASE / "images"
STATE = BASE / "image_post_state.json"     # 日次カウント（.gitignore）
RAW = "https://raw.githubusercontent.com/truthbodysalon-lab/threads-auto-post/main/images"
DAILY_IMG_CAP = 2
TIMEOUT = 30

sys.path.insert(0, str(BASE))
from auto_post import ACCOUNTS, load_env, POST_HOUR_START, POST_HOUR_END  # noqa: E402
import os  # noqa: E402

load_env()

# 本文テンプレ（カテゴリ別・URL無し・短文・店舗性あり。L9準拠=実績クロージング無し）
CAPTIONS = {
    "外観": [
        "長岡駅から車で5分。この建物が目印です。\n専用駐車場もあるので、お車のままどうぞ。",
        "はじめての方が迷わないように、外観を載せておきます。\n院の前に駐車場があります。",
    ],
    "内装": [
        "施術を受ける空間はこんな感じです。\n完全予約制なので、他の方と重なりません。",
        "「整体が初めてで緊張する」という方のために、院内を載せておきます。\n静かな空間でゆっくり受けられます。",
    ],
    "施術": [
        "施術中の一枚。\n揉むだけではなく、原因になっている姿勢や関節から整えていきます。",
        "実際の施術風景です。\nどこが原因かを確かめながら、ひとつずつ整えます。",
    ],
    "駐車場": [
        "院の前の専用駐車場です。\n雪の日も雨の日も、お車のまま来られます。",
        "「駐車場ありますか？」とよく聞かれるので、写真でお答えします。\nあります。無料です。",
    ],
    "カウンセリング": [
        "初回は、まずじっくりお話を聞くところから始まります。\nいきなり施術はしません。原因の見当をつけてから整えます。",
        "カウンセリング中の一枚。\n「どこが痛いか」より「いつから・どんな時に痛むか」を大事に聞いています。",
    ],
    "図解": [
        "保存して、思い出した時にやってみてください。",
        "文章より図の方が伝わると思ったので、まとめました。\n保存しておくと便利です。",
        "今日の1枚。当てはまる方は試してみてください。",
        "スクショ保存推奨です。",
    ],
    "汎用": [
        "長岡市で頭痛・肩こりを根本から整えている整体院です。\n今日も一枚。",
        "日々の院の様子を、たまに写真でも載せていきます。",
    ],
}
NAGAOKA_EXTRA = "\n兄妹で営んでいる小さな院です。"   # nagaokaの汎用系に時々付与


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(BASE / "image_post.log", "a") as f:
        f.write(line + "\n")


def _state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _count_today(st, acct):
    d = st.get(acct, {})
    return d.get("count", 0) if d.get("date") == date.today().isoformat() else 0


def _mark(st, acct):
    st[acct] = {"date": date.today().isoformat(),
                "count": _count_today(st, acct) + 1}
    STATE.write_text(json.dumps(st, ensure_ascii=False))


def category_of(name: str) -> str:
    for k in ("外観", "内装", "施術", "駐車場", "カウンセリング", "図解"):
        if name.startswith(k):
            return k
    return "汎用"


def used_captions(acct: str) -> set:
    pfile = BASE / f"log_{acct}_posted.jsonl"
    out = set()
    if pfile.exists():
        for line in pfile.read_text(encoding="utf-8").splitlines()[-300:]:
            try:
                out.add(json.loads(line).get("text", "").split("\n")[0])
            except Exception:
                pass
    return out


def pick_caption(acct: str, cat: str) -> str:
    used = used_captions(acct)
    pool = [c for c in CAPTIONS[cat] if c.split("\n")[0] not in used] or CAPTIONS[cat]
    cap = random.choice(pool)
    if acct == "nagaoka" and cat in ("汎用", "内装") and random.random() < 0.5:
        cap += NAGAOKA_EXTRA
    return cap


def prepare_image(src: Path, acct: str) -> Path | None:
    """変換・リサイズしてリポジトリへ配置。戻り値=リポジトリ内パス。"""
    REPO_IMG.joinpath(acct).mkdir(parents=True, exist_ok=True)
    h = hashlib.md5(src.read_bytes()).hexdigest()[:10]
    dst = REPO_IMG / acct / f"{date.today().strftime('%Y%m%d')}_{h}.jpg"
    r = subprocess.run(["sips", "-s", "format", "jpeg", "--resampleWidth", "1440",
                        str(src), "--out", str(dst)], capture_output=True, timeout=120)
    if r.returncode != 0 or not dst.exists():
        # リサイズ失敗時は形式変換のみ試す
        r = subprocess.run(["sips", "-s", "format", "jpeg", str(src), "--out", str(dst)],
                           capture_output=True, timeout=120)
        if r.returncode != 0 or not dst.exists():
            log(f"変換失敗: {src.name} {r.stderr[:100]}")
            return None
    if dst.stat().st_size > 7_500_000:
        log(f"サイズ超過: {src.name}")
        dst.unlink(missing_ok=True)
        return None
    return dst


def git_push(paths: list, msg: str) -> bool:
    try:
        subprocess.run(["git", "-C", str(BASE), "add"] + [str(p) for p in paths],
                       capture_output=True, timeout=60)
        subprocess.run(["git", "-C", str(BASE), "commit", "-m", msg],
                       capture_output=True, timeout=60)
        for _ in range(3):
            r = subprocess.run(["git", "-C", str(BASE), "push"], capture_output=True, timeout=120)
            if r.returncode == 0:
                return True
            subprocess.run(["git", "-C", str(BASE), "pull", "--rebase"], capture_output=True, timeout=120)
        return False
    except Exception as e:
        log(f"git失敗: {e}")
        return False


def post_image(acct: str, image_url: str, text: str) -> str | None:
    token = os.environ[ACCOUNTS[acct]["token_key"]]
    uid = os.environ[ACCOUNTS[acct]["uid_key"]]
    try:
        data = urllib.parse.urlencode({"media_type": "IMAGE", "image_url": image_url,
                                       "text": text, "access_token": token}).encode()
        with urllib.request.urlopen(
                urllib.request.Request(f"https://graph.threads.net/v1.0/{uid}/threads", data=data),
                timeout=TIMEOUT) as r:
            cid = json.loads(r.read())["id"]
        time.sleep(8)   # 画像処理待ち（テキストより長め）
        data2 = urllib.parse.urlencode({"creation_id": cid, "access_token": token}).encode()
        with urllib.request.urlopen(
                urllib.request.Request(f"https://graph.threads.net/v1.0/{uid}/threads_publish", data=data2),
                timeout=TIMEOUT) as r:
            return json.loads(r.read())["id"]
    except Exception as e:
        log(f"{acct} 投稿失敗: {e}")
        return None


def record_posted(acct: str, post_id: str, text: str):
    pfile = BASE / f"log_{acct}_posted.jsonl"
    with open(pfile, "a", encoding="utf-8") as f:
        f.write(json.dumps({"date": date.today().isoformat(), "index": -1,
                            "post_id": post_id, "text": text, "kind": "image"},
                           ensure_ascii=False) + "\n")
    return pfile


def main():
    dry = "--dry-run" in sys.argv
    hour = datetime.now().hour
    if not (POST_HOUR_START <= hour < POST_HOUR_END):
        print("投稿時間外")
        return
    st = _state()
    for acct in ("truth", "nagaoka"):
        folder = SRC / acct
        if not folder.exists():
            continue
        if _count_today(st, acct) >= DAILY_IMG_CAP:
            continue
        imgs = sorted([p for p in folder.iterdir()
                       if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".heic") and not p.name.startswith(".")])
        if not imgs:
            continue
        src = imgs[0]
        cat = category_of(src.name)
        cap = pick_caption(acct, cat)
        log(f"{acct}: {src.name} (カテゴリ={cat}) を処理")
        if dry:
            log(f"[dry-run] caption:\n{cap}")
            continue
        repo_img = prepare_image(src, acct)
        if repo_img is None:
            # 壊れた画像は使用済みへ退避してブロックを防ぐ
            shutil.move(str(src), str(SRC / "使用済み" / acct / ("SKIP_" + src.name)))
            continue
        if not git_push([repo_img], f"chore: image for {acct} [skip ci]"):
            log(f"{acct}: push失敗→今回は見送り")
            repo_img.unlink(missing_ok=True)
            continue
        url = f"{RAW}/{acct}/{repo_img.name}"
        time.sleep(5)   # raw URL反映待ち
        pid = post_image(acct, url, cap)
        if pid:
            _mark(st, acct)
            pfile = record_posted(acct, pid, cap)
            git_push([pfile], f"chore: image post log {acct} [skip ci]")
            shutil.move(str(src), str(SRC / "使用済み" / acct / src.name))
            log(f"{acct}: ✓ 画像投稿 {pid}")
        else:
            log(f"{acct}: 投稿失敗（画像は残置・次回再試行）")


if __name__ == "__main__":
    main()
