#!/usr/bin/env python3
"""
フォロワー数 日次トラッカー（予算ゼロ・Threads API無料枠のみ使用）

Threads User Insights API から followers_count を取得し、
followers_history.json に日次スナップショットと前日比デルタを追記する。

この「フォロワー増加デルタ」が analyze_and_tune.py の重み調整に使われ、
「いいね/閲覧が多い投稿」ではなく「実際にフォロワーを増やした投稿パターン」を
優先的に増やす閉ループになる。

使い方:
  python3 follower_tracker.py          # 全アカウント
  python3 follower_tracker.py truth    # truth のみ
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path

BASE = Path(__file__).parent

for line in (BASE / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

ACCOUNTS = {
    "truth": {
        "name": "@truth_body_salon",
        "token": os.environ.get("THREADS_ACCESS_TOKEN_TRUTH", ""),
        "user_id": os.environ.get("THREADS_USER_ID_TRUTH", ""),
    },
    "nagaoka": {
        "name": "@truth_nagaoka",
        "token": os.environ.get("THREADS_ACCESS_TOKEN_NAGAOKA", ""),
        "user_id": os.environ.get("THREADS_USER_ID_NAGAOKA", ""),
    },
    "masa": {
        "name": "@masahide_takahashi_",
        "token": os.environ.get("THREADS_ACCESS_TOKEN_MASA", ""),
        "user_id": os.environ.get("THREADS_USER_ID_MASA", ""),
    },
}

BASE_URL = "https://graph.threads.net/v1.0"
HISTORY_FILE = BASE / "followers_history.json"
TODAY = date.today().strftime("%Y-%m-%d")


def fetch_followers_count(acct: str) -> int | None:
    a = ACCOUNTS[acct]
    if not a["user_id"] or not a["token"]:
        return None
    url = (
        f"{BASE_URL}/{a['user_id']}/threads_insights"
        f"?metric=followers_count&access_token={a['token']}"
    )
    try:
        with urllib.request.urlopen(url) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"  [{acct}] ERROR: {e.read().decode()[:200]}")
        return None
    except Exception as e:
        print(f"  [{acct}] ERROR: {e}")
        return None

    for metric in data.get("data", []):
        if metric.get("name") == "followers_count":
            tv = metric.get("total_value", {})
            if "value" in tv:
                return int(tv["value"])
            # period系で values 配列のこともある
            vals = metric.get("values", [])
            if vals:
                return int(vals[-1].get("value", 0))
    return None


def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_history(history: dict):
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2))


def run(acct: str, history: dict):
    name = ACCOUNTS[acct]["name"]
    count = fetch_followers_count(acct)
    if count is None:
        print(f"  [{acct}] {name}: 取得失敗（スキップ）")
        return

    series = history.setdefault(acct, [])
    prev = series[-1]["followers"] if series else None
    delta = (count - prev) if prev is not None else 0

    # 同日2回目の実行は上書き（デルタは前回の確定値から再計算）
    if series and series[-1]["date"] == TODAY:
        base_prev = series[-2]["followers"] if len(series) >= 2 else count
        series[-1] = {"date": TODAY, "followers": count, "delta": count - base_prev}
        delta = series[-1]["delta"]
    else:
        series.append({"date": TODAY, "followers": count, "delta": delta})

    sign = f"+{delta}" if delta >= 0 else str(delta)
    print(f"  [{acct}] {name}: {count}人 (前日比 {sign})")


def main():
    target = sys.argv[1].lower() if len(sys.argv) > 1 else "all"
    history = load_history()

    print(f"フォロワー数記録 {TODAY}")
    if target in ACCOUNTS:
        run(target, history)
    else:
        for acct in ACCOUNTS:
            run(acct, history)

    save_history(history)
    print(f"  → {HISTORY_FILE.name} に保存")


if __name__ == "__main__":
    main()
