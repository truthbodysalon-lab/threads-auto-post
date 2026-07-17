#!/usr/bin/env python3
"""
自動投稿スクリプト（確認なし・自動エラー修正）
launchd から30分おきに呼ばれ、両アカウントに1本ずつ投稿する。
投稿時間帯: 7:00〜23:00

自動エラー対処:
  - 投稿データなし  → 自動生成して続行
  - トークン期限切れ → 自動リフレッシュして再試行
  - ネットワーク系  → 最大3回リトライ
  - レート制限      → 60秒待機後リトライ
"""

from __future__ import annotations

import fcntl
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
try:
    from duplicate_guard import (
        normalize_text as dg_normalize,
        is_duplicate as dg_is_duplicate,
        mark_pending as dg_mark_pending,
        mark_posted as dg_mark_posted,
        marked_today as dg_marked_today,
    )
except ImportError:
    def dg_normalize(t): return t
    def dg_is_duplicate(t, a): return False
    def dg_mark_pending(t, a): pass
    def dg_mark_posted(t, a, pid): pass
    def dg_marked_today(t, a): return False

# get_next_post / get_posted_texts で使う正規化（duplicate_guard と同じロジック）
_normalize_post_key = dg_normalize

BASE = Path(__file__).parent
ERROR_LOG = BASE / "error.log"
LOCK_FILE = BASE / ".autopost.lock"
POST_HOUR_START = 6
POST_HOUR_END = 23
MAX_RETRY = 3

ACCOUNTS = {
    "truth": {
        "name": "@truth_body_salon",
        "log": BASE / "log_truth.jsonl",
        "posted": BASE / "log_truth_posted.jsonl",
        "token_key": "THREADS_ACCESS_TOKEN_TRUTH",
        "uid_key": "THREADS_USER_ID_TRUTH",
        "autopost_log": BASE / "autopost_truth.log",
        "bridge_text": "ご予約・詳細はこちらから 👇",
    },
    "nagaoka": {
        "name": "@truth_nagaoka",
        "log": BASE / "log_nagaoka.jsonl",
        "posted": BASE / "log_nagaoka_posted.jsonl",
        "token_key": "THREADS_ACCESS_TOKEN_NAGAOKA",
        "uid_key": "THREADS_USER_ID_NAGAOKA",
        "autopost_log": BASE / "autopost_nagaoka.log",
        "bridge_text": "ご予約・詳細はこちらから 👇",
    },
    "masa": {
        "name": "@masahide_takahashi_",
        "log": BASE / "log_masa.jsonl",
        "posted": BASE / "log_masa_posted.jsonl",
        "token_key": "THREADS_ACCESS_TOKEN_MASA",
        "uid_key": "THREADS_USER_ID_MASA",
        "autopost_log": BASE / "autopost_masa.log",
        "bridge_text": "詳しくはこちら 👇",
    },
}
BASE_URL = "https://graph.threads.net/v1.0"
ENV_FILE = BASE / ".env"


# ── .env 読み書き ─────────────────────────────────────

def load_env():
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

def save_env_key(key: str, value: str):
    lines = ENV_FILE.read_text().splitlines()
    new_lines, updated = [], False
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n")
    os.environ[key] = value

load_env()


# ── ログ ──────────────────────────────────────────────

def log_info(acct: str, msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    print(line, end="")
    with open(ACCOUNTS[acct]["autopost_log"], "a") as f:
        f.write(line)

def log_error(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] ERROR: {msg}\n"
    print(line, end="", file=sys.stderr)
    with open(ERROR_LOG, "a") as f:
        f.write(line)


# ── Claude APIで2本目コメント生成 ───────────────────────────

ACCOUNT_PERSONAS = {
    "truth": "整体院（truth body salon）のアカウント。首・肩・腰・頭痛など体の不調を根本から改善する整体サロン。",
    "masa": "整体サロンのオーナー・masahide_takahashiの個人アカウント。集客・SNS運用・動画マーケティングについて発信。",
}

_BRIDGE_DISABLED = False  # 2026-07-03: Anthropic API(従量課金)→Gemini API(既存キー・無料枠)に切替


def _load_gemini_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    # ~/.claude/secrets/gemini.env が正本（2026-07-03ローテーション後）。~/.env は旧キーの可能性あり
    for path in ("~/.claude/secrets/gemini.env", "~/.zshenv", "~/.env"):
        try:
            with open(os.path.expanduser(path)) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("export "):
                        line = line[len("export "):]
                    if line.startswith("GEMINI_API_KEY"):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def generate_bridge_comment(clean_text: str, acct: str) -> str:
    """メイン投稿の内容をもとにGemini APIで補足説明コメントを生成する。失敗時はデフォルトテキストを返す。"""
    global _BRIDGE_DISABLED
    api_key = _load_gemini_key()
    if not api_key or _BRIDGE_DISABLED:
        return ACCOUNTS[acct].get("bridge_text", "詳しくはこちら 👇")

    persona = ACCOUNT_PERSONAS.get(acct, "")
    prompt = (
        f"あなたは{persona}\n\n"
        "以下のThreads投稿（1/3）に続く、2/3のコメントを書いてください。\n"
        "【条件】\n"
        "- 投稿内容の原因・メカニズム・具体的な補足情報を解説する\n"
        "- 100〜180文字程度\n"
        "- 自然な話し言葉。絵文字は1〜2個まで\n"
        "- URLや「ご予約はこちら」などの案内は絶対に含めない\n"
        "- コメント本文のみを出力（前置き・説明不要）\n\n"
        f"【投稿内容】\n{clean_text}"
    )

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        # thinkingBudget:0 で思考トークンを止めないと、maxOutputTokens を思考が食い潰して本文が途切れる
        "generationConfig": {"maxOutputTokens": 512, "thinkingConfig": {"thinkingBudget": 0}},
    }, ensure_ascii=False).encode()

    # 503(高負荷)・429(レート制限)は一時的なので、モデルを変えつつ最大3回試す
    attempts = ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-flash-latest"]
    for i, model in enumerate(attempts):
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.loads(r.read())
            parts = result["candidates"][0]["content"].get("parts", [])
            text = "".join(p.get("text", "") for p in parts).strip()
            # 途切れ・極端な長短は不採用（デフォルト文の方がマシ）
            if text and 40 <= len(text) <= 250 and result["candidates"][0].get("finishReason") == "STOP":
                return text
        except Exception as e:
            body = ""
            if isinstance(e, urllib.error.HTTPError):
                try:
                    body = e.read().decode()
                except Exception:
                    body = ""
            # 認証エラー等の恒久的失敗は以降スキップ（無駄打ち・ログ連発を止める）
            if isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 403):
                if not _BRIDGE_DISABLED:
                    print(f"[generate_bridge_comment] Gemini APIが恒久エラーのため、以降はデフォルト文に固定: {body[:120]}", file=sys.stderr)
                _BRIDGE_DISABLED = True
                break
            if i < len(attempts) - 1:
                time.sleep(3)
    return ACCOUNTS[acct].get("bridge_text", "詳しくはこちら 👇")


# ── トークン自動リフレッシュ ──────────────────────────────

def refresh_token(acct: str) -> bool:
    token = os.environ.get(ACCOUNTS[acct]["token_key"], "")
    url = f"{BASE_URL}/refresh_access_token?grant_type=th_refresh_token&access_token={token}"
    try:
        with urllib.request.urlopen(url) as r:
            data = json.loads(r.read())
        new_token = data.get("access_token", "")
        if new_token:
            save_env_key(ACCOUNTS[acct]["token_key"], new_token)
            log_info(acct, f"トークン自動リフレッシュ完了")
            return True
    except Exception as e:
        log_error(f"{ACCOUNTS[acct]['name']} トークンリフレッシュ失敗: {e}")
    return False


# ── 投稿データ管理 ────────────────────────────────────

def get_posted_indices(acct: str, today: str) -> set:
    pfile = ACCOUNTS[acct]["posted"]
    if not pfile.exists():
        return set()
    return {
        json.loads(l)["index"]
        for l in pfile.read_text().splitlines()
        if l.strip() and json.loads(l).get("date") == today
    }

def mark_posted(acct: str, today: str, index: int, post_id: str, text: str = ""):
    with open(ACCOUNTS[acct]["posted"], "a") as f:
        f.write(json.dumps({"date": today, "index": index, "post_id": post_id, "text": text}, ensure_ascii=False) + "\n")

def get_posted_texts(acct: str) -> set:
    """全期間の投稿済みテキスト一覧（重複防止用）"""
    pfile = ACCOUNTS[acct]["posted"]
    if not pfile.exists():
        return set()
    texts = set()
    for l in pfile.read_text().splitlines():
        if l.strip():
            try:
                entry = json.loads(l)
                if entry.get("text"):
                    texts.add(entry["text"])
            except json.JSONDecodeError:
                pass  # 書き込み競合で生じた不完全行はスキップ
    return texts

# ── LINEリストイン投稿：定期織り込み（1日1回・重複ガード免除）─────
# 「織り交ぜる」意図どおり毎日確実に出すため、通常の7日重複ガードを免除し
# 1日1回に制限する（同URL系を重複扱いしてブロックされ続ける問題への対処）。
# truth/nagaoka: lin.ee/qbRbPAm (頭痛改善情報配信LINE)
# masa: lin.ee/8PsIHHC (SNS集客相談LINE)
_LINE_LISTIN_URLS = ["lin.ee/qbRbPAm", "lin.ee/8PsIHHC"]
_LINE_STATE_FILE = BASE / "line_listin_state.json"
# 1日あたりのLINEリストイン投稿回数の上限（LINE登録を伸ばすため truth/nagaoka を2回に）。
# masaは月間URL上限2本の別ルールがあるため1のまま（実質は月2本程度）。
_LINE_DAILY = {"truth": 2, "nagaoka": 2, "masa": 1}


def _is_line_listin(text: str) -> bool:
    return any(url in (text or "") for url in _LINE_LISTIN_URLS)


def _recent_listin_firstlines(acct: str, days: int = 7) -> set:
    """直近 days 日に投稿済みのLINEリストイン投稿の1文目集合。
    リストインは重複ガード(skip_dup=True)を免除しているため、同一テンプレが
    7日以内に再投稿されるのを防ぐ目的で個別ログから直接1文目を収集する。"""
    pfile = ACCOUNTS[acct]["posted"]
    if not pfile.exists():
        return set()
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    fls = set()
    for line in pfile.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if (e.get("date") or "")[:10] < cutoff:
            continue
        t = e.get("text", "")
        if _is_line_listin(t):
            fls.add(t.split("\n")[0].strip())
    return fls


_SHINDAN_URL = "https://truthbodysalon-lab.github.io/zutsu-shindan/"


def _is_shindan(text: str) -> bool:
    """頭痛タイプ診断アンカー投稿の判定（2026-07-05導入）"""
    return _SHINDAN_URL in (text or "")


def _shindan_anchor_ok(acct: str, today: str, text: str) -> bool:
    """頭痛タイプ診断アンカー投稿の7日重複ガード緩和判定（2026-07-11追加）。
    各アカウントのテンプレは4種のみで毎日2本必要なため、7日ガードだと
    2日目以降は全滅して一度も投稿されない実障害があった
    （_access_anchor_ok と同じ問題パターン）。当日未使用の1文目なら通す。"""
    if not _is_shindan(text):
        return False
    pfile = ACCOUNTS[acct]["posted"]
    if not pfile.exists():
        return True
    first = (text or "").split("\n")[0].strip()
    for line in pfile.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if (e.get("date") or "")[:10] != today:
            continue
        t = e.get("text", "")
        if _is_shindan(t) and t.split("\n")[0].strip() == first:
            return False  # 本日すでに同一テンプレ使用済み
    return True


def _is_store_access(text: str) -> bool:
    """店舗アクセスアンカー投稿の判定（全テンプレ共通の確定フレーズで判定）"""
    t = text or ""
    return "長岡駅" in t and "車で5分" in t


def _access_anchor_ok(acct: str, today: str, text: str) -> bool:
    """店舗アクセス投稿の7日重複ガード緩和判定（2026-07-03追加）。
    テンプレ約9種が7日ガードで全滅し nagaoka は06-26以降0本になっていたため、
    『1日1本まで・3日以内の同一1文目は回避』に緩和して毎日のアンカー投稿を確保する。"""
    if not _is_store_access(text):
        return False
    pfile = ACCOUNTS[acct]["posted"]
    if not pfile.exists():
        return True
    cutoff = (date.today() - timedelta(days=3)).strftime("%Y-%m-%d")
    first = (text or "").split("\n")[0].strip()
    for line in pfile.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        d = (e.get("date") or "")[:10]
        t = e.get("text", "")
        if not _is_store_access(t):
            continue
        if d == today:
            return False  # 本日すでにアクセス投稿済み（1日1本まで）
        if d >= cutoff and t.split("\n")[0].strip() == first:
            return False  # 3日以内に同一テンプレを使用済み
    return True


def _is_hpb_cta(text: str) -> bool:
    """ホットペッパー予約導線CTA投稿の判定（肩こり/頭痛CTA共通）"""
    return "beauty.hotpepper.jp" in (text or "")


def _hpb_anchor_ok(acct: str, today: str, text: str) -> bool:
    """ホットペッパー予約導線CTAの7日重複ガード緩和判定（2026-07-15追加）。
    CTA_KATAKORI_TEMPLATES/CTA_ZUTSUU_TEMPLATESは各12種のみで固定フレーズの
    1文目が実質12パターンしかなく、7日ガード＋truth/nagaoka共有の
    shared_posted_guardにより全滅し、実測で1度も実投稿されていなかった
    （_access_anchor_ok/_shindan_anchor_ok と同じ障害パターン）。
    実投稿ログ(clean_text=URL抽出後の本文)は1文目が候補と一致するため、
    本日まだ同一1文目を使っていなければ通す。"""
    if not _is_hpb_cta(text):
        return False
    pfile = ACCOUNTS[acct]["posted"]
    if not pfile.exists():
        return True
    first = (text or "").split("\n")[0].strip()
    for line in pfile.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if (e.get("date") or "")[:10] != today:
            continue
        if (e.get("text") or "").split("\n")[0].strip() == first:
            return False  # 本日すでに同一テンプレ使用済み
    return True


def _posted_count_today(acct: str) -> int:
    """log_{acct}_posted.jsonl の本日の投稿件数（バッチ投稿の打ち切り判定用）"""
    pfile = ACCOUNTS[acct]["posted"]
    if not pfile.exists():
        return 0
    today = date.today().strftime("%Y-%m-%d")
    n = 0
    for line in pfile.read_text(encoding="utf-8").splitlines():
        try:
            if json.loads(line).get("date") == today:
                n += 1
        except Exception:
            pass
    return n


def _line_count_today(acct: str, today: str) -> int:
    """本日すでに投稿したLINEリストインの回数。旧形式（値が日付文字列）も後方互換で解釈。"""
    try:
        v = json.loads(_LINE_STATE_FILE.read_text()).get(acct)
    except Exception:
        return 0
    if isinstance(v, dict):
        return v.get("count", 0) if v.get("date") == today else 0
    # 旧形式: {acct: "YYYY-MM-DD"} は「その日1回済み」を意味する
    return 1 if v == today else 0


def _monthly_line_url_count(acct: str) -> int:
    """当月に実投稿済みのLINE URL(lin.ee)付き投稿数。
    feedbackルール『masa: LINE URLありは月間2本以下』の実投稿側ガード用。"""
    pfile = ACCOUNTS[acct]["posted"]
    if not pfile.exists():
        return 0
    month = date.today().strftime("%Y-%m")
    n = 0
    for line in pfile.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if (e.get("date") or "")[:7] == month and "lin.ee" in (e.get("text") or ""):
            n += 1
    return n


def _line_done_today(acct: str, today: str) -> bool:
    # masaはfeedbackルール「LINE URL付きは月間2本以下」が最優先。
    # 「1日1回確実に織り込む」ロジックがmasaにも毎日適用され月30本ペースに
    # なっていた設計矛盾を修正（2026-07-03）。上限到達後はLINE投稿を完了扱いにする。
    if acct == "masa" and _monthly_line_url_count("masa") >= 2:
        return True
    return _line_count_today(acct, today) >= _LINE_DAILY.get(acct, 1)


def _mark_line_done(acct: str, today: str):
    try:
        d = json.loads(_LINE_STATE_FILE.read_text()) if _LINE_STATE_FILE.exists() else {}
    except Exception:
        d = {}
    cur = _line_count_today(acct, today)
    d[acct] = {"date": today, "count": cur + 1}
    try:
        _LINE_STATE_FILE.write_text(json.dumps(d, ensure_ascii=False))
    except Exception:
        pass


def get_next_post(acct: str, today: str):
    log_file = ACCOUNTS[acct]["log"]
    if not log_file.exists():
        return None, -1
    entries = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
    today_entries = [e for e in entries if e.get("date") == today]
    if not today_entries:
        return None, -1

    # 全バッチから候補を収集（正規化キーで重複排除）
    seen_keys = set()
    all_posts = []
    for entry in today_entries:
        for post in entry.get("posts", []):
            key = _normalize_post_key(post)[:80]
            if key not in seen_keys:
                seen_keys.add(key)
                all_posts.append(post)

    # 投稿済みテキスト（新フォーマット: text フィールドあり）
    posted_texts = get_posted_texts(acct)

    # 旧フォーマット対応: インデックス→テキスト変換（最終バッチ基準・正規化済み）
    last_batch = today_entries[-1].get("posts", [])
    old_posted_keys = set()
    for idx in get_posted_indices(acct, today):
        if idx < len(last_batch):
            old_posted_keys.add(_normalize_post_key(last_batch[idx])[:80])

    line_done = _line_done_today(acct, today)

    # API実投稿の直近1文目（CIが先に投稿した分をローカルが再選択しないよう除外）
    api_recent = _recent_api_firstlines(acct)

    # LINEリストインは1日1回、確実に織り込む（重複ガード免除）。
    # フィードバック: truth/nagaoka は5〜7%の頻度で投稿されるべき。
    # 序盤でも投稿される確率を高めて目標達成を目指す。
    if not line_done:
        posted_today = len(get_posted_indices(acct, today))
        # 確率的採用から確定採用へ変更（feedback.json: truth 5-7%, nagaoka <10%）
        # 序盤(0-2本)では確実に出す、3本以上で判定再開するが優先採用維持
        should_post_line = (posted_today <= 1) or (posted_today >= 1 and random.random() < 0.9)
        if should_post_line:
            # リストインは重複ガード免除のため、ここで7日以内の同一1文目を避ける
            recent_listin = _recent_listin_firstlines(acct)
            line_candidates = [(i, t) for i, t in enumerate(all_posts) if _is_line_listin(t)]
            for i, text in line_candidates:
                if text.split("\n")[0].strip() not in recent_listin:
                    return text, i
            # 全候補が7日以内に使用済みなら、取りこぼし防止で先頭を採用
            if line_candidates:
                return line_candidates[0][1], line_candidates[0][0]

    for i, text in enumerate(all_posts):
        if _is_line_listin(text):
            continue  # LINEは上で処理（未実施なら採用済み／実施済みならスキップ）
        # mark_posted が保存するテキストと同じ正規化で比較する
        norm = _normalize_post_key(text)
        if norm not in posted_texts and norm[:80] not in old_posted_keys:
            # 7日以内の重複は投稿せずスキップ（ループで次の候補へ）
            # ただし店舗アクセスアンカー（2026-07-03）・頭痛タイプ診断アンカー（2026-07-11）・
            # ホットペッパー予約導線CTA（2026-07-15）は緩和ルールで採用可
            if dg_is_duplicate(norm, acct):
                anchor_ok = _access_anchor_ok(acct, today, text) \
                    or _shindan_anchor_ok(acct, today, text) \
                    or _hpb_anchor_ok(acct, today, text)
                if not anchor_ok:
                    continue
                # アンカー緩和が効いても、本日すでにshared guardに記録済み
                # （投稿成功済み or 直前の試行がAPI側で重複拒否されPENDING記録済み）なら
                # post_to_threads側の通常dup判定で必ず再度弾かれるため、再選択しない。
                # これを入れないと同一アンカー候補を延々選び続けて丸1日投稿が止まる
                # （2026-07-15 truth/nagaoka障害・2026-07-16修正）。
                if dg_marked_today(norm, acct):
                    continue
            # API実投稿の直近に同じ1文目があれば飛ばす（系統間ラグ対策）
            if dg_normalize(text).split("\n")[0].strip()[:40] in api_recent:
                continue
            return text, i

    # 通常投稿が尽きていて、LINEが未実施なら最後に出す（取りこぼし防止）
    if not line_done:
        recent_listin = _recent_listin_firstlines(acct)
        line_candidates = [(i, t) for i, t in enumerate(all_posts) if _is_line_listin(t)]
        for i, text in line_candidates:
            if text.split("\n")[0].strip() not in recent_listin:
                return text, i
        if line_candidates:
            return line_candidates[0][1], line_candidates[0][0]
    return None, -1

def ensure_generated(acct: str, today: str):
    log_file = ACCOUNTS[acct]["log"]
    if log_file.exists():
        entries = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
        # posts が空配列のエントリーは「生成済み」とみなさない
        if any(e.get("date") == today and e.get("posts") for e in entries):
            return
    log_info(acct, "投稿データなし → 自動生成中...")
    result = subprocess.run(
        [sys.executable, str(BASE / "generate_remix.py"), acct],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        log_info(acct, result.stdout.splitlines()[0] if result.stdout else "生成完了")
    else:
        log_error(f"{ACCOUNTS[acct]['name']} 生成失敗: {result.stderr[:200]}")


# ── API呼び出し（リトライ付き） ────────────────────────────

def api_request(url: str, data: bytes = None, retry: int = 0) -> dict:
    try:
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        err_data = json.loads(body) if body.startswith("{") else {}
        code = err_data.get("error", {}).get("code", 0)
        # レート制限 → 60秒待機リトライ
        if code in (4, 17, 32) and retry < MAX_RETRY:
            log_error(f"レート制限(code={code}) → 60秒後リトライ ({retry+1}/{MAX_RETRY})")
            time.sleep(60)
            return api_request(url, data, retry + 1)
        raise e
    except urllib.error.URLError as e:
        # ネットワーク系 → 10秒待機リトライ
        if retry < MAX_RETRY:
            log_error(f"ネットワークエラー → 10秒後リトライ ({retry+1}/{MAX_RETRY}): {e}")
            time.sleep(10)
            return api_request(url, data, retry + 1)
        raise e


_URL_RE = re.compile(r'https?://\S+')

def extract_url_and_cta(text: str):
    """
    本文からURLとその直前のCTAラベル行を切り出す。
    戻り値: (URL除去済み本文, CTAブロック文字列 or None)
    例: CTAブロック = "▶ 予約はこちら\nhttps://beauty.hotpepper.jp/..."
    """
    match = _URL_RE.search(text)
    if not match:
        return text, None

    lines = text.split('\n')
    url_idx = next(i for i, l in enumerate(lines) if _URL_RE.search(l))

    # URLの直前に空でない行があればCTAラベルとして含める
    cta_start = url_idx
    if url_idx > 0 and lines[url_idx - 1].strip():
        cta_start = url_idx - 1

    # 本文末尾の空行を除去
    body_end = cta_start
    while body_end > 0 and not lines[body_end - 1].strip():
        body_end -= 1

    body = '\n'.join(lines[:body_end])
    cta_block = '\n'.join(lines[cta_start:url_idx + 1])
    return body, cta_block


def post_reply_to_threads(acct: str, reply_to_id: str, text: str) -> str:
    """指定投稿へのコメント（返信）を投稿してpost IDを返す"""
    token = os.environ[ACCOUNTS[acct]["token_key"]]
    user_id = os.environ[ACCOUNTS[acct]["uid_key"]]

    data = urllib.parse.urlencode({
        "media_type": "TEXT",
        "text": text,
        "reply_to_id": reply_to_id,
        "access_token": token,
    }).encode()
    container_id = api_request(f"{BASE_URL}/{user_id}/threads", data)["id"]
    time.sleep(2)
    data2 = urllib.parse.urlencode({"creation_id": container_id, "access_token": token}).encode()
    return api_request(f"{BASE_URL}/{user_id}/threads_publish", data2)["id"]


def _recently_posted_on_threads(acct: str, text: str, hours: int = 12) -> bool:
    """Threads APIの実投稿を正とする最終重複チェック（投稿系統をまたぐ二重投稿を防ぐ）。
    直近 hours 時間内に同じ1文目の投稿があれば True。API失敗時は False（通常フローに委ねる）。"""
    try:
        target_fl = dg_normalize(text).split("\n")[0].strip()[:40]
        if not target_fl:
            return False
        token = os.environ[ACCOUNTS[acct]["token_key"]]
        uid = os.environ[ACCOUNTS[acct]["uid_key"]]
        url = f"{BASE_URL}/{uid}/threads?fields=id,text,timestamp&limit=25&access_token={token}"
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        for p in data.get("data", []):
            ptext = p.get("text", "")
            if not ptext:
                continue
            try:
                pts = datetime.fromisoformat(p.get("timestamp", "").replace("Z", "+00:00"))
            except Exception:
                pts = datetime.now(timezone.utc)
            if pts < cutoff:
                continue
            if dg_normalize(ptext).split("\n")[0].strip()[:40] == target_fl:
                return True
        return False
    except Exception:
        return False


def _parse_ts(ts: str):
    try:
        return datetime.strptime(ts.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
    except Exception:
        return None


def _recent_api_firstlines(acct: str, hours: int = 12) -> set:
    """直近 hours 時間に Threads へ実投稿された1文目の集合（選択段階での除外用）。
    get_next_post がこれを使い、CIが先に投稿した記事をローカルが再選択して
    投稿が止まる事故を防ぐ。API失敗時は空集合（通常フローに委ねる）。"""
    try:
        token = os.environ[ACCOUNTS[acct]["token_key"]]
        uid = os.environ[ACCOUNTS[acct]["uid_key"]]
        url = f"{BASE_URL}/{uid}/threads?fields=id,text,timestamp&limit=25&access_token={token}"
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        out = set()
        for p in data.get("data", []):
            ptext = p.get("text", "")
            if not ptext:
                continue
            pts = _parse_ts(p.get("timestamp", ""))
            if pts and pts < cutoff:
                continue
            fl = dg_normalize(ptext).split("\n")[0].strip()[:40]
            if fl:
                out.add(fl)
        return out
    except Exception:
        return set()


def post_to_threads(acct: str, text: str, skip_dup: bool = False) -> str:
    """Threads に投稿して post ID を返す。
    重複チェックをここで行う — run_account の実装に関わらず必ず通る。
    重複の場合は DuplicatePost 例外を投げる（呼び出し側でログしてスキップ）。
    skip_dup=True（LINEリストイン等の意図的な定期投稿）は重複ガードを免除する。
    """
    norm = dg_normalize(text)
    if not skip_dup:
        if dg_is_duplicate(norm, acct):
            raise _DuplicatePost(f"{text[:50]}")
    # API実投稿を正とする最終重複チェック（系統をまたぐ二重投稿を防ぐ。LINEも対象）
    if _recently_posted_on_threads(acct, text):
        # 消費済みとして記録してから弾く。これをしないと get_next_post が同じ投稿を
        # 選び続けて最大12時間パイプラインが詰まり、1日50本を割る原因になる。
        if not skip_dup:
            dg_mark_pending(norm, acct)
        raise _DuplicatePost(f"[API直近重複] {text[:50]}")
    if not skip_dup:
        dg_mark_pending(norm, acct)   # API呼び出し前に記録（タイムアウト対策）

    token = os.environ[ACCOUNTS[acct]["token_key"]]
    user_id = os.environ[ACCOUNTS[acct]["uid_key"]]
    data = urllib.parse.urlencode({"media_type": "TEXT", "text": text, "access_token": token}).encode()
    container_id = api_request(f"{BASE_URL}/{user_id}/threads", data)["id"]
    time.sleep(2)
    data2 = urllib.parse.urlencode({"creation_id": container_id, "access_token": token}).encode()
    post_id = api_request(f"{BASE_URL}/{user_id}/threads_publish", data2)["id"]
    if not skip_dup:
        dg_mark_posted(norm, acct, post_id)   # 成功後に確定記録
    return post_id


class _DuplicatePost(Exception):
    """post_to_threads が重複を検知したときに投げる内部例外"""
    pass


# ── アカウント実行 ────────────────────────────────────

def run_account(acct: str):
    today = date.today().strftime("%Y-%m-%d")
    name = ACCOUNTS[acct]["name"]

    ensure_generated(acct, today)

    text, index = get_next_post(acct, today)
    if text is None and _posted_count_today(acct) < DAILY_TARGET:
        # 適格な投稿が尽きたが目標50本に未達 → 追加バッチを生成して補充する
        # （キューはあっても重複除外で選べる投稿が枯渇するケースがある）
        log_info(acct, f"{name} 適格投稿が枯渇（{_posted_count_today(acct)}本/{DAILY_TARGET}）→ 追加生成")
        subprocess.run([sys.executable, str(BASE / "generate_remix.py"), acct],
                       capture_output=True, text=True, timeout=300)
        text, index = get_next_post(acct, today)
    is_line = _is_line_listin(text or "")  # 元テキスト(URL付き)でLINEリストイン判定
    is_shindan = _is_shindan(text or "")   # 頭痛タイプ診断アンカー判定（2026-07-11）
    # ホットペッパー予約CTA・店舗アクセスアンカーも get_next_post 側で7日重複ガードを
    # 緩和して選ばれている（_hpb_anchor_ok/_access_anchor_ok）。post_to_threads側の
    # 通常dup判定（緩和なし）に skip_dup=False のまま渡すと、選ばれた直後に必ず
    # [重複スキップ]で弾かれ実質0投稿になる実障害があったため、同様にskip_dup対象に含める
    # （2026-07-18検証で発覚: 07-10以降HPB CTA本文が一度も実投稿されていなかった）。
    is_hpb = _is_hpb_cta(text or "")
    is_access = _is_store_access(text or "")
    if text is None:
        log_info(acct, f"{name} 今日の投稿完了")
        return

    comment_parts: list[str] = []

    if is_line or is_shindan:
        # LINEリストイン・頭痛タイプ診断アンカーは「URLを本文末尾に残す」ルール厳守
        # （診断は「👇」の直後にURLが来る設計のため、コメントに追い出すと意味を成さない）
        # → 分割もURL除去もしない
        clean_text = (text or "").strip()
        cta_block = None
    else:
        # URLをメイン本文から除去し、コメントに回す
        clean_text, cta_block = extract_url_and_cta(text)

        # テキストをコメント部分に分割する
        # 優先順位: [COMMENT] タグ → 【続き】マーカー → \n\n 段落区切り
        if "\n[COMMENT]\n" in clean_text:
            # [COMMENT] で複数コメントに分割
            segments = clean_text.split("\n[COMMENT]\n")
            clean_text = segments[0].strip()
            comment_parts = [s.strip() for s in segments[1:] if s.strip()]
        elif "\n\n【続き】\n" in clean_text:
            parts = clean_text.split("\n\n【続き】\n", 1)
            clean_text = parts[0].strip()
            if parts[1].strip():
                comment_parts = [parts[1].strip()]
        elif "\n\n" in clean_text:
            # マーカーなし長文 → 最初の段落のみ本文、残りをコメントへ
            first_para, rest = clean_text.split("\n\n", 1)
            rest = rest.strip()
            if rest:
                clean_text = first_para.rstrip()
                comment_parts = [rest]

    log_info(acct, f"{name} [{index+1}本目]: {clean_text[:40].replace(chr(10), ' ')}...")

    # URLがある場合のみ補足説明コメントをAIで事前生成（投稿前に準備）
    bridge = generate_bridge_comment(clean_text, acct) if cta_block else None

    def _post_comments(post_id: str, suffix: str = ""):
        """コメント部分→CTAを順番に投稿するヘルパー"""
        last_id = post_id
        for part in comment_parts:
            try:
                time.sleep(3)
                last_id = post_reply_to_threads(acct, last_id, part)
                log_info(acct, f"{name} ✓ コメント投稿完了{suffix}")
            except Exception as ce:
                log_error(f"{name} コメント投稿失敗{suffix}: {ce}")
        if cta_block:
            try:
                b = bridge or ACCOUNTS[acct].get("bridge_text", "詳しくはこちら 👇")
                time.sleep(3)
                r1 = post_reply_to_threads(acct, last_id, b)
                time.sleep(2)
                post_reply_to_threads(acct, r1, cta_block)
                log_info(acct, f"{name} ✓ コメントにURL投稿完了{suffix}")
            except Exception as ce:
                log_error(f"{name} URL投稿失敗{suffix}: {ce}")

    try:
        # post_to_threads 内部で重複チェック・pending・posted を一括処理
        post_id = post_to_threads(acct, clean_text, skip_dup=(is_line or is_shindan or is_hpb or is_access))
        mark_posted(acct, today, index, post_id, clean_text)
        if is_line:
            _mark_line_done(acct, today)
        log_info(acct, f"{name} ✓ 完了 (ID: {post_id})")
        _post_comments(post_id)

    except _DuplicatePost as dp:
        log_info(acct, f"{name} [重複スキップ] {str(dp)[:60]}")
        if is_line:
            # LINEリストインがAPI重複で弾かれた場合、本日消化済みにしないと
            # get_next_post が同じLINE投稿を選び続けて全投稿が止まる
            # （2026-07-10 truthが16本で夜まで停止した実障害）。
            _mark_line_done(acct, today)
        else:
            # 設計原則: 弾いた投稿は必ず消費済みにする。本文分割後(clean_text)が重複でも
            # 元のキュー全文は未マークのため、ここで全文normを消費済みにしないと
            # 同じ投稿を無限再選択して停止する（2026-07-16 truthが5本で停止した実障害）。
            try:
                dg_mark_pending(dg_normalize(text), acct)
            except Exception:
                pass

    except urllib.error.HTTPError as e:
        body = e.read().decode()
        err_data = json.loads(body) if body.startswith("{") else {}
        code = err_data.get("error", {}).get("code", 0)

        # トークン期限切れ → 自動リフレッシュして再投稿
        if code == 190:
            log_info(acct, f"{name} トークン期限切れ → 自動リフレッシュ...")
            if refresh_token(acct):
                try:
                    post_id = post_to_threads(acct, clean_text, skip_dup=(is_line or is_shindan or is_hpb or is_access))
                    mark_posted(acct, today, index, post_id, clean_text)
                    if is_line:
                        _mark_line_done(acct, today)
                    log_info(acct, f"{name} ✓ 完了（リフレッシュ後） (ID: {post_id})")
                    _post_comments(post_id, "（リフレッシュ後）")
                except _DuplicatePost as dp:
                    log_info(acct, f"{name} [重複スキップ] {str(dp)[:60]}")
                except Exception as e2:
                    log_error(f"{name} リフレッシュ後も失敗 → 手動で auth.py を実行してください: {e2}")
            else:
                log_error(f"{name} トークン再取得が必要です（手動: python3 auth.py）")
        else:
            log_error(f"{name} 投稿失敗 (code={code}): {body[:200]}")

    except Exception as e:
        log_error(f"{name} 予期しないエラー: {e}")


# ── メイン ────────────────────────────────────────────

def _ensure_caffeinate():
    """投稿時間帯にAC電源なら、スリープ防止(caffeinate)が動いているか確認し、無ければ起動する。
    Mac再起動・日中の復帰でも自動でスリープ防止を効かせる（sudo不要・AC時のみ）。"""
    try:
        now = datetime.now()
        if not (POST_HOUR_START <= now.hour < POST_HOUR_END):
            return
        batt = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
        if "AC Power" not in batt:        # バッテリー時は何もしない（浪費防止）
            return
        running = subprocess.run(["pgrep", "-f", "caffeinate -s"], capture_output=True, text=True).stdout.strip()
        if running:                        # 既に効いている
            return
        end = now.replace(hour=POST_HOUR_END, minute=0, second=0, microsecond=0)
        sec = max(60, int((end - now).total_seconds()))
        subprocess.Popen(["caffeinate", "-s", "-t", str(sec)])
        log_info("system", f"AC電源を検知 → スリープ防止を自動起動（{sec}秒）")
    except Exception:
        pass


def main():
    now = datetime.now()
    if not (POST_HOUR_START <= now.hour < POST_HOUR_END):
        print(f"[{now.strftime('%H:%M')}] 投稿時間外 → スキップ")
        return

    _ensure_caffeinate()   # AC電源を検知してスリープ防止を自動起動

    # 並行実行防止（launchd が重なった場合にスキップ）
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"[{now.strftime('%H:%M')}] 別プロセスが実行中 → スキップ")
        lock_fd.close()
        return

    try:
        _run_main()
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


# 1回の実行で各アカウント最大何本投稿するか（単一系統で頻度を上げる）
# 1日の投稿目標と上限（過剰投稿=スパムを防ぎつつ、50本を均等ペースで担保）
DAILY_TARGET = int(os.environ.get("DAILY_TARGET", "50"))   # 各アカウント1日の目標本数
DAILY_CAP = int(os.environ.get("DAILY_CAP", "55"))         # 上限（これ以上は出さない）
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN", "8"))      # 通常の1サイクル上限
MAX_BURST = int(os.environ.get("MAX_BURST", "15"))         # 大きく遅れた時の一気回復上限（スリープ復帰時）
POSTS_PER_RUN = int(os.environ.get("POSTS_PER_RUN", "3"))  # 後方互換


def _target_cumulative_by_now(hour: int) -> int:
    """投稿時間帯(6-23時)で DAILY_TARGET を均等配分した、現時点であるべき累計本数。"""
    start, end = POST_HOUR_START, POST_HOUR_END
    if hour < start:
        return 0
    frac = min(1.0, (hour - start + 1) / max(1, end - start))
    return int(DAILY_TARGET * frac)


def _run_account_batch(acct: str):
    """ペースに追従して投稿する。
    - 目標(50)に対し『今あるべき累計』との不足分を、最大 MAX_PER_RUN 本まで埋める
    - DAILY_CAP に達したら出さない（過剰投稿=スパム防止）
    - 遅れていればまとめて回復、進んでいれば控える（Mac休止後のキャッチアップ対応）
    """
    posted = _posted_count_today(acct)
    # ログ同期ラグ（pull_syncのreset等）でローカルログが実投稿数を過小カウントし
    # 上限超過する事故（2026-07-16 nagaoka71本）を防ぐため、外形(API実測)と比べて大きい方を使う
    try:
        from watchdog_ci import api_count_today
        api_n = api_count_today(acct)
        if api_n > posted:
            log_info(acct, f"{ACCOUNTS[acct]['name']} ログ{posted}本<API実測{api_n}本 → 実測を採用")
            posted = api_n
    except Exception:
        pass
    if posted >= DAILY_CAP:
        log_info(acct, f"{ACCOUNTS[acct]['name']} 本日上限({DAILY_CAP})到達 → スキップ")
        return
    hour = datetime.now().hour
    want = _target_cumulative_by_now(hour)             # 今あるべき累計
    need = max(0, want - posted)
    # 遅れ幅を自動検知してバースト量を調整（スリープ復帰時に一気に回復）
    # 通常は MAX_PER_RUN まで。大きく遅れている時は最大15本まで一気に出す。
    burst_cap = MAX_BURST if need > MAX_PER_RUN else MAX_PER_RUN
    n = min(need, burst_cap)
    if n == 0 and posted < DAILY_TARGET and hour >= 21:
        n = 1   # 終盤のみの取りこぼし防止。終日この床を効かせると毎5分の実行が
                # 常に1本投稿し、50本を昼までに使い切って午後が無音になる
                # （2026-07-10 masaが12:56に50本完了→半日沈黙した実障害）。
    if n > MAX_PER_RUN:
        log_info(acct, f"{ACCOUNTS[acct]['name']} 遅れ検知（不足{need}本）→ {n}本まとめて回復")
    fails = 0
    for i in range(n):
        if _posted_count_today(acct) >= DAILY_CAP:
            break
        before = _posted_count_today(acct)
        run_account(acct)
        after = _posted_count_today(acct)
        if after <= before:
            # 1本の失敗（重複スキップ等）で全体を打ち切らない。連続3回失敗で今回は諦める
            # （次サイクル5分後に再試行される）。即断打ち切りは1日50本割れの主因だった。
            fails += 1
            if fails >= 3:
                break
        else:
            fails = 0
        if i < n - 1:
            time.sleep(4)


def _run_main():
    target = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    if target in ("truth", "nagaoka", "masa"):
        _run_account_batch(target)
    else:
        _run_account_batch("truth")
        time.sleep(5)
        _run_account_batch("nagaoka")
        time.sleep(5)
        _run_account_batch("masa")

    # 投稿後にObsidianレポートを自動更新
    try:
        subprocess.run([sys.executable, str(BASE / "report.py")],
                       capture_output=True, timeout=30)
    except Exception:
        pass


if __name__ == "__main__":
    main()
