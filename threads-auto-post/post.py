#!/usr/bin/env python3
"""
Threads投稿スクリプト
generate_posts.py で生成したテキストをThreads APIで実際に投稿します。

使い方:
  python post.py truth   # @truth_body_salon に1本投稿
  python post.py masa    # @masahide_takahashi_ に1本投稿
  python post.py all     # 両アカウントに1本ずつ投稿

事前準備:
  python auth.py         # @truth_body_salon の認証
  python auth.py masa    # @masahide_takahashi_ の認証
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import date

# .env 読み込み
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

BASE_URL = "https://graph.threads.net/v1.0"
LOG_TRUTH = Path(__file__).parent / "log_truth.jsonl"
LOG_MASA = Path(__file__).parent / "log_masa.jsonl"


# ── Threads API ────────────────────────────────────


def api_post(url: str, data: dict) -> dict:
    encoded = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=encoded, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def api_get(url: str) -> dict:
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


def create_text_container(user_id: str, token: str, text: str) -> str:
    """投稿コンテナを作成してIDを返す"""
    url = f"{BASE_URL}/{user_id}/threads"
    data = {
        "media_type": "TEXT",
        "text": text,
        "access_token": token,
    }
    result = api_post(url, data)
    return result["id"]


def publish_container(user_id: str, token: str, container_id: str) -> str:
    """コンテナを公開してpost IDを返す"""
    url = f"{BASE_URL}/{user_id}/threads_publish"
    data = {
        "creation_id": container_id,
        "access_token": token,
    }
    result = api_post(url, data)
    return result["id"]


def post_to_threads(user_id: str, token: str, text: str) -> str:
    """テキストをThreadsに投稿してpost IDを返す"""
    container_id = create_text_container(user_id, token, text)
    time.sleep(1)  # API推奨: 作成後1秒待機
    post_id = publish_container(user_id, token, container_id)
    return post_id


# ── ログから次の未投稿テキストを取得 ─────────────────


def get_next_post(log_file: Path, account: str) -> tuple[str, int]:
    """
    ログファイルから今日の未投稿テキストを1本返す。
    返り値: (テキスト, ログ内インデックス)
    """
    today = date.today().strftime("%Y-%m-%d")
    posted_log = Path(str(log_file).replace(".jsonl", "_posted.jsonl"))

    # 投稿済みIDを収集
    posted_ids = set()
    if posted_log.exists():
        for line in posted_log.read_text().splitlines():
            if line.strip():
                entry = json.loads(line)
                if entry.get("date") == today:
                    posted_ids.add(entry.get("index"))

    # 今日のエントリを探す
    if not log_file.exists():
        return None, -1

    entries = [
        json.loads(line)
        for line in log_file.read_text().splitlines()
        if line.strip()
    ]

    today_entries = [e for e in entries if e.get("date") == today]
    if not today_entries:
        print(f"  今日の生成データがありません。先に generate_posts.py を実行してください。")
        return None, -1

    latest = today_entries[-1]
    posts = latest.get("posts", [])

    for i, text in enumerate(posts):
        if i not in posted_ids:
            return text, i

    print(f"  今日の投稿はすべて投稿済みです（{len(posts)}本完了）")
    return None, -1


def mark_as_posted(log_file: Path, index: int, post_id: str):
    today = date.today().strftime("%Y-%m-%d")
    posted_log = Path(str(log_file).replace(".jsonl", "_posted.jsonl"))
    entry = {"date": today, "index": index, "post_id": post_id}
    with open(posted_log, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── メイン ────────────────────────────────────────


def post_account(account: str):
    if account == "truth":
        token = os.environ.get("THREADS_ACCESS_TOKEN_TRUTH", "")
        user_id = os.environ.get("THREADS_USER_ID_TRUTH", "")
        log_file = LOG_TRUTH
        name = "@truth_body_salon"
    else:
        token = os.environ.get("THREADS_ACCESS_TOKEN_MASA", "")
        user_id = os.environ.get("THREADS_USER_ID_MASA", "")
        log_file = LOG_MASA
        name = "@masahide_takahashi_"

    if not token or not user_id:
        print(f"  [ERROR] {name} のトークンが未設定です。先に auth.py を実行してください。")
        return False

    text, index = get_next_post(log_file, account)
    if text is None:
        return False

    print(f"\n投稿先: {name}")
    print(f"テキスト:\n{'─'*40}\n{text}\n{'─'*40}")

    confirm = input("投稿しますか？ [y/N]: ").strip().lower()
    if confirm != "y":
        print("  スキップしました")
        return False

    try:
        post_id = post_to_threads(user_id, token, text)
        mark_as_posted(log_file, index, post_id)
        print(f"  ✓ 投稿完了 (ID: {post_id})")
        return True
    except Exception as e:
        print(f"  [ERROR] 投稿失敗: {e}")
        return False


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "truth"

    if target == "all":
        post_account("truth")
        post_account("masa")
    elif target in ("truth", "masa"):
        post_account(target)
    else:
        print(f"使い方: python post.py [truth|masa|all]")
        sys.exit(1)


if __name__ == "__main__":
    main()
