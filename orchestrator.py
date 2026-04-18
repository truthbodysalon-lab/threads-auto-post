#!/usr/bin/env python3
"""
Orchestrator — 全エージェントを調整して1回の投稿サイクルを実行

フロー:
  1. AnalysisAgent  → パフォーマンス分析・重み自動更新
  2. GeneratorAgent → Claude API（またはテンプレート）で投稿生成
  3. QualityAgent   → 品質フィルタリング
  4. PostingAgent   → Threads API に投稿
  5. report.py      → Obsidianレポート更新
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

BASE  = Path(__file__).parent
TODAY = date.today().strftime("%Y-%m-%d")

# エージェントをインポート
sys.path.insert(0, str(BASE))
from agents.generator import generate as gen_posts
from agents.quality   import check    as quality_check
from agents.analysis  import run      as run_analysis

POST_HOUR_START = 6
POST_HOUR_END   = 23
BASE_URL        = "https://graph.threads.net/v1.0"
ENV_FILE        = BASE / ".env"

ACCOUNTS = {
    "truth": {
        "name":      "@truth_body_salon",
        "log":       BASE / "log_truth.jsonl",
        "posted":    BASE / "log_truth_posted.jsonl",
        "token_key": "THREADS_ACCESS_TOKEN_TRUTH",
        "uid_key":   "THREADS_USER_ID_TRUTH",
    },
    "nagaoka": {
        "name":      "@truth_nagaoka",
        "log":       BASE / "log_nagaoka.jsonl",
        "posted":    BASE / "log_nagaoka_posted.jsonl",
        "token_key": "THREADS_ACCESS_TOKEN_NAGAOKA",
        "uid_key":   "THREADS_USER_ID_NAGAOKA",
    },
    "masa": {
        "name":      "@masahide_takahashi_",
        "log":       BASE / "log_masa.jsonl",
        "posted":    BASE / "log_masa_posted.jsonl",
        "token_key": "THREADS_ACCESS_TOKEN_MASA",
        "uid_key":   "THREADS_USER_ID_MASA",
    },
}


# ── 環境変数 ──────────────────────────────────────────

def load_env():
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

load_env()


# ── ログ ──────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


# ── 投稿データ管理 ────────────────────────────────────

def save_posts(acct: str, posts: list[str]):
    log_file = ACCOUNTS[acct]["log"]
    entry = {"date": TODAY, "posts": posts, "generated_by": "orchestrator"}
    with open(log_file, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_today_posts(acct: str) -> list[str]:
    log_file = ACCOUNTS[acct]["log"]
    if not log_file.exists():
        return []
    for line in reversed(log_file.read_text().splitlines()):
        try:
            e = json.loads(line)
            if e.get("date") == TODAY:
                return e.get("posts", [])
        except Exception:
            pass
    return []


def get_posted_indices(acct: str) -> set:
    pfile = ACCOUNTS[acct]["posted"]
    if not pfile.exists():
        return set()
    return {
        json.loads(l)["index"]
        for l in pfile.read_text().splitlines()
        if l.strip() and json.loads(l).get("date") == TODAY
    }


def get_posted_texts(acct: str) -> set:
    pfile = ACCOUNTS[acct]["posted"]
    if not pfile.exists():
        return set()
    return {
        json.loads(l).get("text", "")
        for l in pfile.read_text().splitlines()
        if l.strip()
    }


def mark_posted(acct: str, index: int, post_id: str, text: str = ""):
    with open(ACCOUNTS[acct]["posted"], "a") as f:
        f.write(json.dumps({
            "date": TODAY, "index": index,
            "post_id": post_id, "text": text
        }, ensure_ascii=False) + "\n")


# ── Threads API ───────────────────────────────────────

def api_post(acct: str, text: str) -> str:
    token   = os.environ[ACCOUNTS[acct]["token_key"]]
    user_id = os.environ[ACCOUNTS[acct]["uid_key"]]

    data = urllib.parse.urlencode({"media_type": "TEXT", "text": text, "access_token": token}).encode()
    with urllib.request.urlopen(
        urllib.request.Request(f"{BASE_URL}/{user_id}/threads", data=data), timeout=30
    ) as r:
        cid = json.loads(r.read())["id"]

    time.sleep(2)
    data2 = urllib.parse.urlencode({"creation_id": cid, "access_token": token}).encode()
    with urllib.request.urlopen(
        urllib.request.Request(f"{BASE_URL}/{user_id}/threads_publish", data2), timeout=30
    ) as r:
        return json.loads(r.read())["id"]


# ── フェーズ別実行 ────────────────────────────────────

def phase_generate(acct: str) -> list[str]:
    """Phase 1: GeneratorAgent → QualityAgent"""
    log(f"[{acct}] Phase1: 生成開始 (GeneratorAgent)")
    # nagaoka は軽症者ターゲット40本生成、他は30本
    count = 40 if acct == "nagaoka" else 30
    raw = gen_posts(acct, count=count)
    log(f"[{acct}] 生成: {len(raw)}本")

    log(f"[{acct}] Phase2: 品質チェック (QualityAgent)")
    posts = quality_check(raw, acct)
    log(f"[{acct}] 品質通過: {len(posts)}本")

    save_posts(acct, posts)
    return posts


def _parse_sections(text: str) -> list[str]:
    """[COMMENT] マーカーで本文とコメントセクションに分割する"""
    import re as _re
    sections = [s.strip() for s in _re.split(r"\[COMMENT\]", text) if s.strip()]
    return sections


def _post_reply(acct: str, reply_text: str, parent_id: str) -> str:
    """指定投稿へのコメント返信を投稿してIDを返す"""
    token   = os.environ[ACCOUNTS[acct]["token_key"]]
    user_id = os.environ[ACCOUNTS[acct]["uid_key"]]
    d = urllib.parse.urlencode({
        "media_type": "TEXT", "text": reply_text,
        "reply_to_id": parent_id, "access_token": token
    }).encode()
    with urllib.request.urlopen(
        urllib.request.Request(f"{BASE_URL}/{user_id}/threads", d), timeout=30
    ) as r:
        cid = json.loads(r.read())["id"]
    time.sleep(2)
    dp = urllib.parse.urlencode({"creation_id": cid, "access_token": token}).encode()
    with urllib.request.urlopen(
        urllib.request.Request(f"{BASE_URL}/{user_id}/threads_publish", dp), timeout=30
    ) as r:
        return json.loads(r.read())["id"]


def phase_post(acct: str, posts: list[str]):
    """Phase 3: PostingAgent — 次の1本を投稿（[COMMENT]分割→返信スレッド対応）"""
    posted_idx   = get_posted_indices(acct)
    posted_texts = get_posted_texts(acct)

    # 未投稿かつテキスト未使用の次の1本
    target = None
    for i, text in enumerate(posts):
        clean = text[:80]
        if i not in posted_idx and clean not in posted_texts:
            target = (i, text)
            break

    if target is None:
        log(f"[{acct}] 今日の投稿完了")
        return

    idx, full_text = target

    # [COMMENT] マーカーで分割（あれば本文+コメント、なければ旧ロジック）
    import re
    sections = _parse_sections(full_text)

    if len(sections) > 1:
        # 新フォーマット: 本文 → コメント1 → コメント2 → ... の順でスレッド投稿
        main_body = sections[0]
        # URL抽出（本文またはコメントに含まれる場合）
        all_urls = re.findall(r"https?://\S+", full_text)
        # 本文からURLを除去
        main_body_clean = re.sub(r"\n*https?://\S+", "", main_body).strip()

        log(f"[{acct}] Phase3: 投稿 #{idx+1}（{len(sections)}セクション）— {main_body_clean[:40].replace(chr(10),' ')}...")

        try:
            post_id = api_post(acct, main_body_clean)
            mark_posted(acct, idx, post_id, main_body_clean)
            log(f"[{acct}] ✓ 本文投稿完了 (ID: {post_id})")

            # コメントを返信スレッドとして順番に投稿
            parent_id = post_id
            for ci, comment_text in enumerate(sections[1:], 1):
                comment_clean = re.sub(r"\n*https?://\S+", "", comment_text).strip()
                # 最後のコメントにURLがある場合は追記
                is_last = (ci == len(sections) - 1)
                if is_last and all_urls:
                    comment_clean = comment_clean + "\n" + all_urls[-1]
                try:
                    time.sleep(3)
                    parent_id = _post_reply(acct, comment_clean, parent_id)
                    log(f"[{acct}] ✓ コメント{ci}投稿完了 (ID: {parent_id})")
                except Exception as ce:
                    log(f"[{acct}] コメント{ci}失敗（前投稿は成功）: {ce}")
                    break

        except Exception as e:
            log(f"[{acct}] ✗ 投稿失敗: {e}")

    else:
        # 旧フォーマット: URLをコメントに分離する従来ロジック
        text = full_text
        url_match = re.search(r"https?://\S+", text)
        if url_match:
            url = url_match.group()
            clean_text = re.sub(r"\n*https?://\S+", "", text).strip()
            lines = clean_text.splitlines()
            if lines and re.search(r"(予約|LINE|こちら)", lines[-1]):
                clean_text = "\n".join(lines[:-1]).strip()
        else:
            clean_text = text
            url = None

        log(f"[{acct}] Phase3: 投稿 #{idx+1} — {clean_text[:40].replace(chr(10),' ')}...")

        try:
            post_id = api_post(acct, clean_text)
            mark_posted(acct, idx, post_id, clean_text)
            log(f"[{acct}] ✓ 投稿完了 (ID: {post_id})")

            if url:
                try:
                    bridge = "ご予約・詳細はこちら 👇" if acct == "truth" else "詳しくはこちら 👇"
                    time.sleep(3)
                    r1pid = _post_reply(acct, bridge, post_id)
                    time.sleep(2)
                    _post_reply(acct, url, r1pid)
                    log(f"[{acct}] ✓ URLコメント投稿完了")
                except Exception as ce:
                    log(f"[{acct}] URLコメント失敗（本投稿は成功）: {ce}")

        except Exception as e:
            log(f"[{acct}] ✗ 投稿失敗: {e}")


def phase_analyze(acct: str):
    """Phase 4: AnalysisAgent — 重み自動更新"""
    try:
        weights = run_analysis(acct, verbose=False)
        if weights:
            log(f"[{acct}] Phase4: パターン重み更新 ({len(weights)}パターン)")
    except Exception as e:
        log(f"[{acct}] AnalysisAgent エラー（スキップ）: {e}")


# ── メインループ ──────────────────────────────────────

def run_account(acct: str):
    # Phase 4: 分析（毎回少しずつ更新）
    phase_analyze(acct)

    # 今日の投稿データを確認
    posts = get_today_posts(acct)
    if not posts:
        # Phase 1+2: 生成+品質チェック
        posts = phase_generate(acct)
        if not posts:
            log(f"[{acct}] 生成失敗 → スキップ")
            return

    # Phase 3: 投稿
    phase_post(acct, posts)


def main():
    now = datetime.now()
    if not (POST_HOUR_START <= now.hour < POST_HOUR_END):
        log(f"投稿時間外（{now.hour}時）→ スキップ")
        return

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

    # レポート更新
    try:
        import subprocess
        subprocess.run([sys.executable, str(BASE / "report.py")],
                       capture_output=True, timeout=30)
    except Exception:
        pass


if __name__ == "__main__":
    main()
