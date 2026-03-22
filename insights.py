#!/usr/bin/env python3
"""
投稿インサイト取得・分析スクリプト
両アカウントの投稿パフォーマンス（いいね・閲覧・返信）を取得して分析する。

使い方:
  python3 insights.py           # 両アカウント分析
  python3 insights.py truth     # truth_body_salon のみ
  python3 insights.py masa      # masahide_takahashi_ のみ
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import defaultdict

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
        "insights_file": BASE / "insights_truth.json",
        "past_posts_file": BASE / "past_posts.json",
    },
    "masa": {
        "name": "@masahide_takahashi_",
        "token": os.environ.get("THREADS_ACCESS_TOKEN_MASA", ""),
        "user_id": os.environ.get("THREADS_USER_ID_MASA", ""),
        "insights_file": BASE / "insights_masa.json",
        "past_posts_file": BASE / "past_posts_masa.json",
    },
}

BASE_URL = "https://graph.threads.net/v1.0"


# ── API取得 ────────────────────────────────────────

def fetch_posts_with_metrics(acct: str, limit: int = 100) -> list:
    a = ACCOUNTS[acct]
    url = (
        f"{BASE_URL}/{a['user_id']}/threads"
        f"?fields=id,text,timestamp,like_count,views,replies_count"
        f"&limit={limit}&access_token={a['token']}"
    )
    all_posts = []
    while url and len(all_posts) < limit:
        with urllib.request.urlopen(url) as r:
            data = json.loads(r.read())
        all_posts.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
    return all_posts


# ── 分析 ──────────────────────────────────────────

def analyze(posts: list, acct: str) -> dict:
    # テキストなし・テスト投稿を除外
    valid = [
        p for p in posts
        if p.get("text") and "テスト投稿" not in p.get("text", "")
    ]

    if not valid:
        return {}

    total = len(valid)
    total_likes = sum(p.get("like_count", 0) for p in valid)
    total_views = sum(p.get("views", 0) for p in valid)
    total_replies = sum(p.get("replies_count", 0) for p in valid)

    avg_likes = total_likes / total
    avg_views = total_views / total

    # TOP10（いいね順）
    sorted_by_likes = sorted(valid, key=lambda p: p.get("like_count", 0), reverse=True)
    top10 = [
        {
            "text": p["text"][:80],
            "likes": p.get("like_count", 0),
            "views": p.get("views", 0),
            "replies": p.get("replies_count", 0),
            "date": p.get("timestamp", "")[:10],
        }
        for p in sorted_by_likes[:10]
    ]

    # 文字数帯別パフォーマンス
    def char_bucket(text):
        n = len(text)
        if n < 50: return "〜50字"
        if n < 100: return "50〜100字"
        if n < 150: return "100〜150字"
        return "150字以上"

    bucket_stats = defaultdict(lambda: {"count": 0, "likes": 0, "views": 0})
    for p in valid:
        b = char_bucket(p.get("text", ""))
        bucket_stats[b]["count"] += 1
        bucket_stats[b]["likes"] += p.get("like_count", 0)
        bucket_stats[b]["views"] += p.get("views", 0)

    bucket_avg = {
        b: {
            "count": s["count"],
            "avg_likes": round(s["likes"] / s["count"], 2),
            "avg_views": round(s["views"] / s["count"], 2),
        }
        for b, s in bucket_stats.items()
    }

    # 最近7日間 vs それ以前
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    recent = [p for p in valid if p.get("timestamp", "")[:10] >= seven_days_ago]
    recent_avg_likes = sum(p.get("like_count", 0) for p in recent) / len(recent) if recent else 0

    return {
        "account": ACCOUNTS[acct]["name"],
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_posts": total,
        "total_likes": total_likes,
        "total_views": total_views,
        "total_replies": total_replies,
        "avg_likes": round(avg_likes, 2),
        "avg_views": round(avg_views, 2),
        "recent_7days_count": len(recent),
        "recent_7days_avg_likes": round(recent_avg_likes, 2),
        "top10_by_likes": top10,
        "performance_by_length": bucket_avg,
    }


def print_report(result: dict):
    print(f"\n{'='*50}")
    print(f"📊 {result['account']} インサイトレポート")
    print(f"   更新: {result['updated_at']}")
    print(f"{'='*50}")
    print(f"  総投稿数:     {result['total_posts']}件")
    print(f"  平均いいね:   {result['avg_likes']}")
    print(f"  平均閲覧:     {result['avg_views']}")
    print(f"  直近7日間:    {result['recent_7days_count']}本 / 平均いいね {result['recent_7days_avg_likes']}")

    print(f"\n📌 文字数別パフォーマンス")
    for bucket, stat in sorted(result["performance_by_length"].items()):
        print(f"  {bucket}: {stat['count']}件 / 平均いいね {stat['avg_likes']} / 平均閲覧 {stat['avg_views']}")

    print(f"\n🏆 TOP10（いいね順）")
    for i, p in enumerate(result["top10_by_likes"], 1):
        print(f"  {i}. いいね{p['likes']} 閲覧{p['views']} [{p['date']}]")
        print(f"     {p['text'][:60].replace(chr(10), ' ')}")


def run(acct: str):
    name = ACCOUNTS[acct]["name"]
    print(f"{name} の投稿を取得中...")
    try:
        posts = fetch_posts_with_metrics(acct, limit=200)
        print(f"  {len(posts)}件取得")

        # past_posts更新
        past_file = ACCOUNTS[acct]["past_posts_file"]
        with open(past_file, "w") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)

        # 分析
        result = analyze(posts, acct)

        # 保存
        insights_file = ACCOUNTS[acct]["insights_file"]
        with open(insights_file, "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print_report(result)
        print(f"\n  → {insights_file.name} に保存しました")

    except urllib.error.HTTPError as e:
        print(f"  ERROR: {e.read().decode()}")


def main():
    target = sys.argv[1].lower() if len(sys.argv) > 1 else "all"
    if target == "truth":
        run("truth")
    elif target == "masa":
        run("masa")
    else:
        run("truth")
        print()
        run("masa")


if __name__ == "__main__":
    main()
