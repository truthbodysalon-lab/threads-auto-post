#!/usr/bin/env python3
"""
自動投稿スクリプト
launchd から30分おきに呼ばれ、両アカウントの未投稿を1本ずつ投稿する。
投稿時間帯: 7:00〜22:00（30分おき・最大30本/日）

使い方:
  python3 auto_post.py          # 両アカウント投稿
  python3 auto_post.py truth    # truth_body_salon のみ
  python3 auto_post.py masa     # masahide_takahashi_ のみ
"""

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).parent
ERROR_LOG = BASE / "error.log"

POST_HOUR_START = 7
POST_HOUR_END = 22

ACCOUNTS = {
    "truth": {
        "name": "@truth_body_salon",
        "log": BASE / "log_truth.jsonl",
        "posted": BASE / "log_truth_posted.jsonl",
        "token_key": "THREADS_ACCESS_TOKEN_TRUTH",
        "uid_key": "THREADS_USER_ID_TRUTH",
        "autopost_log": BASE / "autopost_truth.log",
    },
    "masa": {
        "name": "@masahide_takahashi_",
        "log": BASE / "log_masa.jsonl",
        "posted": BASE / "log_masa_posted.jsonl",
        "token_key": "THREADS_ACCESS_TOKEN_MASA",
        "uid_key": "THREADS_USER_ID_MASA",
        "autopost_log": BASE / "autopost_masa.log",
    },
}

BASE_URL = "https://graph.threads.net/v1.0"

# .env 読み込み
for line in (BASE / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()


def log_info(acct: str, msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    print(line, end="")
    with open(ACCOUNTS[acct]["autopost_log"], "a") as f:
        f.write(line)


def log_error(acct: str, msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] ERROR: {msg}\n"
    print(line, end="", file=sys.stderr)
    with open(ERROR_LOG, "a") as f:
        f.write(line)


def get_posted_indices(acct: str, today: str) -> set:
    pfile = ACCOUNTS[acct]["posted"]
    if not pfile.exists():
        return set()
    return {
        json.loads(l)["index"]
        for l in pfile.read_text().splitlines()
        if l.strip() and json.loads(l).get("date") == today
    }


def mark_posted(acct: str, today: str, index: int, post_id: str):
    with open(ACCOUNTS[acct]["posted"], "a") as f:
        f.write(json.dumps({"date": today, "index": index, "post_id": post_id}, ensure_ascii=False) + "\n")


def get_next_post(acct: str, today: str):
    log_file = ACCOUNTS[acct]["log"]
    if not log_file.exists():
        return None, -1
    entries = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
    today_entries = [e for e in entries if e.get("date") == today]
    if not today_entries:
        return None, -1
    posts = today_entries[-1].get("posts", [])
    posted = get_posted_indices(acct, today)
    for i, text in enumerate(posts):
        if i not in posted:
            return text, i
    return None, -1


def ensure_generated(acct: str, today: str):
    log_file = ACCOUNTS[acct]["log"]
    if log_file.exists():
        entries = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
        if any(e.get("date") == today for e in entries):
            return
    log_info(acct, f"今日の投稿を生成中...")
    result = subprocess.run(
        [sys.executable, str(BASE / "generate_remix.py"), acct],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        log_error(acct, f"生成失敗: {result.stderr}")
    else:
        log_info(acct, result.stdout.splitlines()[0] if result.stdout else "生成完了")


def post_to_threads(acct: str, text: str) -> str:
    token = os.environ[ACCOUNTS[acct]["token_key"]]
    user_id = os.environ[ACCOUNTS[acct]["uid_key"]]

    data = urllib.parse.urlencode({"media_type": "TEXT", "text": text, "access_token": token}).encode()
    with urllib.request.urlopen(
        urllib.request.Request(f"{BASE_URL}/{user_id}/threads", data=data, method="POST")
    ) as r:
        container_id = json.loads(r.read())["id"]

    time.sleep(2)

    data2 = urllib.parse.urlencode({"creation_id": container_id, "access_token": token}).encode()
    with urllib.request.urlopen(
        urllib.request.Request(f"{BASE_URL}/{user_id}/threads_publish", data=data2, method="POST")
    ) as r:
        return json.loads(r.read())["id"]


def run_account(acct: str):
    today = date.today().strftime("%Y-%m-%d")
    name = ACCOUNTS[acct]["name"]

    ensure_generated(acct, today)

    text, index = get_next_post(acct, today)
    if text is None:
        log_info(acct, f"{name} 今日の投稿完了")
        return

    log_info(acct, f"{name} 投稿中 [{index+1}本目]: {text[:40].replace(chr(10), ' ')}...")
    try:
        post_id = post_to_threads(acct, text)
        mark_posted(acct, today, index, post_id)
        log_info(acct, f"{name} ✓ 投稿完了 (ID: {post_id})")
    except urllib.error.HTTPError as e:
        log_error(acct, f"{name} 投稿失敗: {e.read().decode()}")
    except Exception as e:
        log_error(acct, f"{name} 投稿失敗: {e}")


def main():
    now = datetime.now()
    if not (POST_HOUR_START <= now.hour < POST_HOUR_END):
        print(f"[{now.strftime('%H:%M')}] 投稿時間外 → スキップ")
        return

    target = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    if target == "truth":
        run_account("truth")
    elif target == "masa":
        run_account("masa")
    else:
        run_account("truth")
        time.sleep(5)  # アカウント間隔
        run_account("masa")


if __name__ == "__main__":
    main()
