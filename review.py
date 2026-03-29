#!/usr/bin/env python3
"""
投稿レビューCLI（iTerm用）
昨日〜直近の投稿を1本ずつ確認し、評価・フィードバックを記録する。

使い方:
  python3 review.py            # 未評価の投稿を順番にレビュー
  python3 review.py --days 3   # 直近3日分をレビュー
  python3 review.py --summary  # フィードバック集計を表示
"""

import json
import sys
import os
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
FEEDBACK_FILE = BASE / "feedback.jsonl"

ACCOUNTS = {
    "truth": {
        "name": "@truth_body_salon",
        "log": BASE / "log_truth.jsonl",
        "posted": BASE / "log_truth_posted.jsonl",
        "insights": BASE / "insights_truth.json",
    },
    "masa": {
        "name": "@masahide_takahashi_",
        "log": BASE / "log_masa.jsonl",
        "posted": BASE / "log_masa_posted.jsonl",
        "insights": BASE / "insights_masa.json",
    },
}

RATINGS = {"g": "good", "b": "bad", "s": "skip", "q": "quit"}
RATING_LABEL = {"good": "✅ good", "bad": "❌ bad", "skip": "⏭  skip"}


# ── ロード ────────────────────────────────────────

def load_posts_for_date(acct: str, target_date: str) -> list[str]:
    log = ACCOUNTS[acct]["log"]
    if not log.exists():
        return []
    for line in reversed(log.read_text().splitlines()):
        try:
            e = json.loads(line)
            if e.get("date") == target_date:
                return e.get("posts", [])
        except Exception:
            pass
    return []


def load_posted_for_date(acct: str, target_date: str) -> list[dict]:
    f = ACCOUNTS[acct]["posted"]
    if not f.exists():
        return []
    return [
        json.loads(l) for l in f.read_text().splitlines()
        if l.strip() and json.loads(l).get("date") == target_date
    ]


def load_feedback() -> set[tuple]:
    """(account, date, index) の評価済みセット"""
    if not FEEDBACK_FILE.exists():
        return set()
    reviewed = set()
    for l in FEEDBACK_FILE.read_text().splitlines():
        try:
            e = json.loads(l)
            reviewed.add((e["account"], e["date"], e["index"]))
        except Exception:
            pass
    return reviewed


def load_insights(acct: str) -> dict:
    f = ACCOUNTS[acct]["insights"]
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


def save_feedback(entry: dict):
    with open(FEEDBACK_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── パターン検出 ───────────────────────────────────

def detect_pattern(text: str, acct: str) -> str:
    if acct == "truth":
        if text.startswith("「"):
            return "quote_empathy"
        lines = [l for l in text.split("\n") if l.strip()]
        if len(lines) <= 2:
            return "insight"
        if any(c in text for c in ["▶", "◎", "①", "②"]):
            return "education"
        if any(w in text for w in ["ワーママ", "育児", "家事も"]):
            return "workmom"
        if any(w in text for w in ["長岡", "先日", "お客様", "整体師"]):
            return "story"
        return "insight"
    else:  # masa
        if any(c in text for c in ["①", "②", "③"]):
            return "education"
        if any(w in text for w in ["先日", "お客様", "事例"]):
            return "story"
        if any(w in text for w in ["LINE登録", "プロフィールのリンク", "相談"]):
            return "cta"
        return "insight"


# ── 表示 ─────────────────────────────────────────

def print_post(acct: str, date_str: str, index: int, text: str, post_id: str, total: int):
    handle = ACCOUNTS[acct]["name"].replace("@", "")
    url = f"https://www.threads.com/@{handle}/post/{post_id}" if post_id else ""
    pattern = detect_pattern(text, acct)

    print()
    print(f"  {'─'*56}")
    print(f"  {ACCOUNTS[acct]['name']}  [{date_str}]  #{index+1}/{total}  ({pattern})")
    if url:
        print(f"  {url}")
    print(f"  {'─'*56}")
    # テキストをインデントして表示
    for line in text.split("\n"):
        print(f"  {line}")
    print(f"  {'─'*56}")


def prompt_rating() -> tuple[str, str]:
    """(rating_key, comment) を返す"""
    print("  評価: [g]ood / [b]ad / [s]kip / [q]uit  (Enterでskip)")
    print("  コメント: 評価の後にスペースで入力可 (例: g 文体が自然)  ", end="")
    raw = input().strip()
    if not raw:
        return "skip", ""
    parts = raw.split(" ", 1)
    key = parts[0].lower()
    comment = parts[1].strip() if len(parts) > 1 else ""
    if key not in RATINGS:
        return "skip", ""
    return RATINGS[key], comment


# ── メインレビューループ ───────────────────────────

def run_review(days: int = 2):
    reviewed = load_feedback()
    targets = []

    today = date.today()
    for d in range(1, days + 1):
        target_date = (today - timedelta(days=d)).strftime("%Y-%m-%d")
        for acct in ("truth", "masa"):
            posts = load_posts_for_date(acct, target_date)
            posted = load_posted_for_date(acct, target_date)
            for e in posted:
                idx = e.get("index", 0)
                pid = e.get("post_id", "")
                if (acct, target_date, idx) not in reviewed and idx < len(posts):
                    targets.append({
                        "acct": acct,
                        "date": target_date,
                        "index": idx,
                        "text": posts[idx],
                        "post_id": pid,
                    })

    if not targets:
        print("\n  未レビューの投稿はありません。")
        return

    print(f"\n  未レビュー: {len(targets)}件をレビューします")
    print("  Ctrl+C でいつでも中断できます\n")

    counts = {"good": 0, "bad": 0, "skip": 0}

    try:
        for i, item in enumerate(targets):
            print_post(
                item["acct"], item["date"], item["index"],
                item["text"], item["post_id"], len(targets)
            )
            print(f"  進捗: {i+1}/{len(targets)}", end="  ")

            rating, comment = prompt_rating()

            if rating == "quit":
                print("  レビューを中断しました。")
                break

            if rating != "skip":
                entry = {
                    "account": item["acct"],
                    "date": item["date"],
                    "index": item["index"],
                    "post_id": item["post_id"],
                    "rating": rating,
                    "comment": comment,
                    "pattern": detect_pattern(item["text"], item["acct"]),
                    "text": item["text"][:80],
                    "reviewed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
                save_feedback(entry)

            counts[rating if rating != "quit" else "skip"] += 1
            print(f"  → {RATING_LABEL.get(rating, '')}")

    except KeyboardInterrupt:
        print("\n  中断しました。")

    print(f"\n  {'─'*40}")
    print(f"  レビュー結果: ✅ {counts['good']}件  ❌ {counts['bad']}件  ⏭  {counts['skip']}件")
    print(f"  {'─'*40}")

    if counts["good"] + counts["bad"] > 0:
        print("\n  重みを自動調整しますか？ [y/N]: ", end="")
        if input().strip().lower() == "y":
            import subprocess
            result = subprocess.run(
                [sys.executable, str(BASE / "analyze_and_tune.py"), "--quiet"],
                capture_output=True, text=True
            )
            print(result.stdout or "  調整完了")


# ── サマリー表示 ──────────────────────────────────

def show_summary():
    if not FEEDBACK_FILE.exists():
        print("  フィードバックデータがありません。")
        return

    from collections import defaultdict
    stats = defaultdict(lambda: {"good": 0, "bad": 0, "total": 0})
    recent_bad = []

    for l in FEEDBACK_FILE.read_text().splitlines():
        try:
            e = json.loads(l)
            pattern = e.get("pattern", "unknown")
            acct = e.get("account", "")
            key = f"{acct}/{pattern}"
            stats[key]["total"] += 1
            if e["rating"] == "good":
                stats[key]["good"] += 1
            elif e["rating"] == "bad":
                stats[key]["bad"] += 1
                recent_bad.append(e)
        except Exception:
            pass

    print(f"\n  {'─'*52}")
    print(f"  フィードバック集計（累計）")
    print(f"  {'─'*52}")
    for key, s in sorted(stats.items()):
        total = s["total"]
        good = s["good"]
        rate = round(good / total * 100) if total else 0
        bar = "█" * (rate // 10) + "░" * (10 - rate // 10)
        print(f"  {key:<35} [{bar}] {rate:3}%  ({good}/{total})")

    if recent_bad:
        print(f"\n  ❌ 直近のbad評価（コメントあり）")
        for e in recent_bad[-5:]:
            if e.get("comment"):
                print(f"  [{e['date']}] {e['account']}/{e.get('pattern','')}  → {e['comment']}")

    print(f"  {'─'*52}\n")


# ── エントリポイント ──────────────────────────────

def main():
    args = sys.argv[1:]

    if "--summary" in args or "-s" in args:
        show_summary()
        return

    days = 2
    for i, a in enumerate(args):
        if a == "--days" and i + 1 < len(args):
            days = int(args[i + 1])

    run_review(days=days)


if __name__ == "__main__":
    main()
