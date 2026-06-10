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
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
try:
    from duplicate_guard import (
        normalize_text as dg_normalize,
        is_duplicate as dg_is_duplicate,
        mark_pending as dg_mark_pending,
        mark_posted as dg_mark_posted,
    )
except ImportError:
    def dg_normalize(t): return t
    def dg_is_duplicate(t, a): return False
    def dg_mark_pending(t, a): pass
    def dg_mark_posted(t, a, pid): pass

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

_BRIDGE_DISABLED = False  # 残高不足/認証エラー検知後はAPIを叩かずデフォルトに切替（無駄打ち・ログ連発防止）


def generate_bridge_comment(clean_text: str, acct: str) -> str:
    """メイン投稿の内容をもとにClaude APIで補足説明コメントを生成する。失敗時はデフォルトテキストを返す。"""
    global _BRIDGE_DISABLED
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
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
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}]
    }, ensure_ascii=False).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            result = json.loads(r.read())
        return result["content"][0]["text"].strip()
    except Exception as e:
        # 残高不足・認証エラー等の恒久的失敗は以降スキップ（無駄打ち・ログ連発を止める）
        body = ""
        if isinstance(e, urllib.error.HTTPError):
            try:
                body = e.read().decode()
            except Exception:
                body = ""
        if "credit balance" in body or "authentication" in body or (isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 403)):
            if not _BRIDGE_DISABLED:
                print(f"[generate_bridge_comment] APIが恒久エラー(残高不足等)のため、以降はデフォルト文に固定: {body[:120]}", file=sys.stderr)
            _BRIDGE_DISABLED = True
        # それ以外（一時的な失敗）は静かにフォールバック
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
            entry = json.loads(l)
            if entry.get("text"):
                texts.add(entry["text"])
    return texts

# ── LINEリストイン投稿：定期織り込み（1日1回・重複ガード免除）─────
# 「織り交ぜる」意図どおり毎日確実に出すため、通常の7日重複ガードを免除し
# 1日1回に制限する（同URL系を重複扱いしてブロックされ続ける問題への対処）。
_LINE_LISTIN_URL = "lin.ee/qbRbPAm"
_LINE_STATE_FILE = BASE / "line_listin_state.json"


def _is_line_listin(text: str) -> bool:
    return _LINE_LISTIN_URL in (text or "")


def _line_done_today(acct: str, today: str) -> bool:
    try:
        return json.loads(_LINE_STATE_FILE.read_text()).get(acct) == today
    except Exception:
        return False


def _mark_line_done(acct: str, today: str):
    try:
        d = json.loads(_LINE_STATE_FILE.read_text()) if _LINE_STATE_FILE.exists() else {}
    except Exception:
        d = {}
    d[acct] = today
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

    # LINEリストインは1日1回、確実に織り込む（重複ガード免除）。
    # キュー位置に依存せず必ず出すため、未実施なら最初の候補を優先採用する。
    if not line_done:
        # 既に数本投稿済みなら自然に混ぜ、序盤は通常投稿を優先（毎日同じ先頭固定を避ける）
        posted_today = len(get_posted_indices(acct, today))
        if posted_today >= 2:
            for i, text in enumerate(all_posts):
                if _is_line_listin(text):
                    return text, i

    for i, text in enumerate(all_posts):
        if _is_line_listin(text):
            continue  # LINEは上で処理（未実施なら採用済み／実施済みならスキップ）
        # mark_posted が保存するテキストと同じ正規化で比較する
        norm = _normalize_post_key(text)
        if norm not in posted_texts and norm[:80] not in old_posted_keys:
            # 7日以内の重複は投稿せずスキップ（ループで次の候補へ）
            if dg_is_duplicate(norm, acct):
                continue
            return text, i

    # 通常投稿が尽きていて、LINEが未実施なら最後に出す（取りこぼし防止）
    if not line_done:
        for i, text in enumerate(all_posts):
            if _is_line_listin(text):
                return text, i
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
    is_line = _is_line_listin(text or "")  # 元テキスト(URL付き)でLINEリストイン判定
    if text is None:
        log_info(acct, f"{name} 今日の投稿完了")
        return

    comment_parts: list[str] = []

    if is_line:
        # LINEリストインは「URLを本文末尾に残す」ルール厳守 → 分割もURL除去もしない
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
        post_id = post_to_threads(acct, clean_text, skip_dup=is_line)
        mark_posted(acct, today, index, post_id, clean_text)
        if is_line:
            _mark_line_done(acct, today)
        log_info(acct, f"{name} ✓ 完了 (ID: {post_id})")
        _post_comments(post_id)

    except _DuplicatePost as dp:
        log_info(acct, f"{name} [重複スキップ] {str(dp)[:60]}")

    except urllib.error.HTTPError as e:
        body = e.read().decode()
        err_data = json.loads(body) if body.startswith("{") else {}
        code = err_data.get("error", {}).get("code", 0)

        # トークン期限切れ → 自動リフレッシュして再投稿
        if code == 190:
            log_info(acct, f"{name} トークン期限切れ → 自動リフレッシュ...")
            if refresh_token(acct):
                try:
                    post_id = post_to_threads(acct, clean_text, skip_dup=is_line)
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

def main():
    now = datetime.now()
    if not (POST_HOUR_START <= now.hour < POST_HOUR_END):
        print(f"[{now.strftime('%H:%M')}] 投稿時間外 → スキップ")
        return

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


def _run_main():
    target = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    if target == "truth":
        run_account("truth")
    elif target == "nagaoka":
        run_account("nagaoka")
    elif target == "masa":
        run_account("masa")
    else:
        run_account("truth")
        time.sleep(5)
        run_account("nagaoka")
        time.sleep(5)
        run_account("masa")

    # 投稿後にObsidianレポートを自動更新
    try:
        subprocess.run([sys.executable, str(BASE / "report.py")],
                       capture_output=True, timeout=30)
    except Exception:
        pass


if __name__ == "__main__":
    main()
