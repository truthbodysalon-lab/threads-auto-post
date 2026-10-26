#!/usr/bin/env python3
"""デスクトップの画像フォルダからThreadsへ画像投稿する（truth/nagaoka・2026-08-19）。

流れ: /Users/mt112/Threads投稿画像/<acct>/ の画像を検出（Desktop直下はTCCでlaunchdから読めないためホーム直下が実体・Desktopはsymlink）
  → リサイズ/変換(sips) → リポジトリ images/<acct>/ へコミット＆push（公開raw URL化）
  → ファイル名キーワードに応じた本文を生成 → IMAGEコンテナ作成→公開
  → 元画像を 使用済み/ へ移動・投稿台帳に記録（同一コミットでpush）

制約・ルール:
- 1日の上限: truth=4枚(2026-10-26 masa指示で再開)／nagaoka=4枚（画像は挑戦枠。playbook testingとして実測評価）。
  2026-10-24 本人指示で nagaoka を2→4枚へ（分析: 画像つき投稿 x1.6、兄妹・地域の写真つきは x8。自動の画像つきは3本しかなかった）
- 投稿時間帯6-23時のみ。R1: 全HTTP通信にtimeout必須
- 本文はプレイブック準拠の短文。URL無し（HPB一本化ルールに抵触しない）
- HEICはjpgへ自動変換。幅1440px超は縮小（Threads上限8MB対策）

使い方: python3 image_post.py [--dry-run]
launchd: com.threads.imagepost が4時間毎に実行（1回につき各アカ1枚＝投稿時間帯6-23時で約4回→nagaoka 4枚/日）
"""
from __future__ import annotations

import hashlib
import json
import random
import shlex
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).parent
# Desktop直下はTCCでlaunchdから読めないためホーム直下が実体・Desktopはsymlink（2026-08-21）
SRC = Path("/Users/mt112/Threads投稿画像")
REPO_IMG = BASE / "images"
STATE = BASE / "image_post_state.json"     # 日次カウント・使用ログ・通知フラグ（.gitignore）
RAW = "https://raw.githubusercontent.com/truthbodysalon-lab/threads-auto-post/main/images"
DAILY_IMG_CAP = {"truth": 4, "nagaoka": 4}   # アカウント別の1日上限（2026-10-24 nagaokaを4枚へ。2026-10-26 masa指示でtruthも再開・nagaokaと同じ4枚）
TIMEOUT = 30
RECYCLE_DAYS = 7            # 使用済み画像の再利用しきい値（日）。適格0なら最終手段でLRU再利用
                            # するため実質の間隔は 画像枚数÷日次上限（例: 8枚÷2本=4日）に収束する
                            # （2026-08-27 masa指示「定期的に何度も使える様に」＝常設ローテーション化）
NOTIFY_SCRIPT = Path("/Users/mt112/.claude/scripts/notify.sh")
IMG_EXTS = (".jpg", ".jpeg", ".png", ".heic")

sys.path.insert(0, str(BASE))
from auto_post import ACCOUNTS, load_env, POST_HOUR_START, POST_HOUR_END  # noqa: E402
import os  # noqa: E402

load_env()

# 本文テンプレ（カテゴリ別・URL無し・短文・店舗性あり。L9準拠=実績クロージング無し）
CAPTIONS = {
    "外観": [
        "はじめて来る方が迷わないように、目印になる外観です。\n長岡駅から車で5分、専用駐車場もあります。",
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
        "今日の院の様子を一枚。\n長岡市で頭痛・肩こりを根本から整えています。",
        "日々の院の様子を、たまに写真でも載せていきます。",
    ],
}
NAGAOKA_EXTRA = "\n兄妹で営んでいる小さな院です。"   # nagaokaの汎用系に時々付与

# nagaoka専用キャプション（2026-10-24 本人指示: 兄妹・地域・天気の話に寄せる）。
# 地域は「相談をいただくことがある」止まりで断定しない（見附・三条は variety_templates / 本人の既存投稿で確認済みの範囲）。
# 1行目は 長岡/当院/来院/施術/改善 で始めない・含めない（_is_valid_first_line と同じ方針）。URL・予約誘導なし。
CAPTIONS_NAGAOKA = {
    "外観": [
        "兄妹でやっている、小さな院の外観です。\n見附や三条など、長岡の外からも相談をいただくことがあります。",
        "はじめての方が迷わないように、外観を載せておきます。\n院の前に駐車場があります。兄妹でお待ちしています。",
        "兄妹で営んでいる院の、入口の写真です。\n遠くからお越しになる方も、車で停めやすいようにしています。",
    ],
    "内装": [
        "兄妹でやっているので、院の中は小さくて静かです。\n「整体が初めてで緊張する」という方も、ゆっくり話せます。",
        "院の中の様子です。\n静かな空間で、まずはお話から聞いていきます。",
        "兄妹で整えた、小さな院の中です。\n見附や三条からお越しの方にも、くつろいでもらえるようにしています。",
    ],
    "汎用": [
        "兄妹で営んでいる小さな院の、今日の一枚です。\n肩の重さや頭の重さの相談を、ひとつずつ聞いています。",
        "長岡の小さな院の様子を、ときどき写真でも載せていきます。\n兄妹でやっているので、気軽に話しかけてください。",
    ],
    "天気": [   # ファイル名の語（天気_雨_01 など）で選別する。WEATHER_CAPTION_IDX_BY_TAG 参照
        "雨の日は、頭が重くなるという声をよく聞きます。\n気圧が下がる日は、首の後ろを温めてから動いてみてください。",
        "天気が崩れる前日、肩が重くなる人はいませんか？\n前の晩に首元を温めておくと、楽な人もいます。",
        "寒くなってきました。\n首元を一枚足すだけで、夕方の肩の重さが違う人がいます。",
        "曇りの日は、なんとなく体が重い。\nそんな日は、首の後ろを温めて、深呼吸を3回。",
        "雪の朝は、肩に力が入りやすいです。\n布団から出る前に、首をゆっくり倒してみてください。",
        "雨の音がする朝は、頭が重くて起きにくい人もいます。\n起き上がる前に、首の後ろを温めてみてください。",
    ],
    "図解": [
        "保存して、思い出した時にやってみてください。",
        "文章より図の方が伝わると思ったので、まとめました。\n保存しておくと便利です。",
        "今日の1枚。当てはまる方は試してみてください。",
        "スクショ保存推奨です。",
    ],
}
# truth側に「天気」画像が置かれた場合の汎用キャプション
CAPTIONS["天気"] = CAPTIONS_NAGAOKA["天気"]
# 天気画像は絵に合う文だけを使う（雨の絵に雪の文を付けない）。ファイル名: 天気_<雨|曇り|雪|寒い>_NN.png
WEATHER_CAPTION_IDX_BY_TAG = {"雨": [0, 1, 5], "曇り": [3], "雪": [4], "寒い": [2]}


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


def _save_state(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False))


def _record_used(st, acct: str, filename: str, category: str):
    """使用済み移動・再利用のたびに使用日時とカテゴリを記録（30日リサイクル判定用）。"""
    st.setdefault("used_log", {}).setdefault(acct, {})[filename] = {
        "used_at": datetime.now().isoformat(), "category": category}
    _save_state(st)


def _last_used(st, acct: str, filename: str) -> dict | None:
    return st.get("used_log", {}).get(acct, {}).get(filename)


def recycle_candidates(st, acct: str, min_days: float = RECYCLE_DAYS) -> list[tuple[datetime, Path, str | None]]:
    """使用済み/ 内で最終使用から min_days 日以上経過した画像を、古い順に返す。
    使用日時は状態ファイルの記録を優先し、記録が無ければファイルのmtimeで代用する。"""
    used_dir = SRC / "使用済み" / acct
    if not used_dir.exists():
        return []
    now = datetime.now()
    out = []
    for p in sorted(used_dir.iterdir()):
        if p.name.startswith(".") or p.name.startswith("SKIP_"):
            continue
        if p.suffix.lower() not in IMG_EXTS:
            continue
        entry = _last_used(st, acct, p.name)
        if entry and entry.get("used_at"):
            try:
                used_at = datetime.fromisoformat(entry["used_at"])
            except Exception:
                used_at = datetime.fromtimestamp(p.stat().st_mtime)
        else:
            used_at = datetime.fromtimestamp(p.stat().st_mtime)
        if (now - used_at).total_seconds() / 86400 >= min_days:
            out.append((used_at, p, entry.get("category") if entry else None))
    out.sort(key=lambda t: t[0])   # 最も古い使用から
    return out


def pick_recycle_category(default_cat: str, last_cat: str | None) -> str:
    """再利用時は前回と別のキャプションを選ぶ。写真内容と無関係なカテゴリ
    （内装写真に図解用など）を当てると不自然なため、振替先は「汎用」に限定する。"""
    # 図解・天気は絵の内容と文が対応するため汎用に振り替えない（4〜6本のキャプションを順に使う）
    if default_cat in ("図解", "天気"):
        return default_cat
    if last_cat and default_cat == last_cat and default_cat != "汎用":
        return "汎用"
    return default_cat


def notify_stock_empty(st) -> bool:
    """在庫0かつリサイクル適格も0の日に1日1回だけDiscord通知。送信したらTrue。"""
    today = date.today().isoformat()
    if st.get("notify_empty_date") == today:
        return False
    msg = ("Threads画像在庫が空です。"
           "~/Threads投稿画像/truth・nagaokaに写真を追加してください。")
    try:
        subprocess.run(
            ["bash", "-c", f"echo {shlex.quote(msg)} | {shlex.quote(str(NOTIFY_SCRIPT))}"],
            capture_output=True, timeout=30)
        st["notify_empty_date"] = today
        _save_state(st)
        log("枯渇通知を送信")
        return True
    except Exception as e:
        log(f"枯渇通知失敗: {e}")
        return False


def category_of(name: str) -> str:
    for k in ("外観", "内装", "施術", "駐車場", "カウンセリング", "図解", "天気"):
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


def pick_caption(acct: str, cat: str, name: str = "") -> str:
    used = used_captions(acct)
    src = CAPTIONS_NAGAOKA.get(cat) if acct == "nagaoka" and CAPTIONS_NAGAOKA.get(cat) else CAPTIONS.get(cat) or CAPTIONS["汎用"]
    if cat == "天気":
        for tag, idxs in WEATHER_CAPTION_IDX_BY_TAG.items():
            if tag in name:
                src = [src[i] for i in idxs if i < len(src)] or src
                break
    pool = [c for c in src if c.split("\n")[0] not in used] or src
    cap = random.choice(pool)
    # NAGAOKA_EXTRA(兄妹)は兄妹がまだ入っていない汎用／内装だけに付ける（兄妹は1本1回まで）
    if acct == "nagaoka" and cat in ("汎用", "内装") and "兄妹" not in cap and random.random() < 0.5 \
            and not CAPTIONS_NAGAOKA.get(cat):
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
    exhausted = []
    for acct in ("truth", "nagaoka"):
        folder = SRC / acct
        if not folder.exists():
            # 2026-10-24: フォルダが無いと黙ってスキップし、9/2以降の約7週間1枚も投稿されていなかった。
            # 作って先へ進む（在庫0なら使用済みのリサイクルで回る）。
            folder.mkdir(parents=True, exist_ok=True)
            log(f"{acct}: 画像フォルダが無かったので作成 {folder}")
        if _count_today(st, acct) >= DAILY_IMG_CAP.get(acct, 2):
            continue
        imgs = sorted([p for p in folder.iterdir()
                       if p.suffix.lower() in IMG_EXTS and not p.name.startswith(".")])

        recycled = False
        last_cat = None
        if imgs:
            src = imgs[0]
        else:
            # 在庫0 → 使用済み/ のリサイクル候補（最も古い使用から）を常設ローテーション利用
            candidates = recycle_candidates(st, acct)
            if not candidates:
                # クールダウン適格が無くても止めない: 前日以前に使った中で最も古いものを再利用
                candidates = recycle_candidates(st, acct, min_days=1)
            if not candidates:
                log(f"{acct}: 使える画像が1枚も無い（新規在庫0・使用済みも空/本日使用のみ）")
                exhausted.append(acct)
                continue
            _used_at, src, last_cat = candidates[0]
            recycled = True

        cat = category_of(src.name)
        cap_cat = pick_recycle_category(cat, last_cat) if recycled else cat
        cap = pick_caption(acct, cap_cat, src.name)
        tag = "リサイクル" if recycled else "新規"
        log(f"{acct}: {src.name} (カテゴリ={cap_cat}・{tag}) を処理")
        if dry:
            log(f"[dry-run] caption:\n{cap}")
            continue
        repo_img = prepare_image(src, acct)
        if repo_img is None:
            # 壊れた画像は使用済みへ退避（SKIP_）してブロックを防ぐ。リサイクル元は既に使用済みなのでリネームのみ
            if recycled:
                src.rename(src.with_name("SKIP_" + src.name))
            else:
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
            if recycled:
                _record_used(st, acct, src.name, cap_cat)   # 使用済み内で使用日時のみ更新
            else:
                shutil.move(str(src), str(SRC / "使用済み" / acct / src.name))
                _record_used(st, acct, src.name, cap_cat)
            log(f"{acct}: ✓ 画像投稿 {pid} ({tag})")
        else:
            log(f"{acct}: 投稿失敗（画像は残置・次回再試行）")

    if exhausted and not dry:
        notify_stock_empty(st)


if __name__ == "__main__":
    main()
