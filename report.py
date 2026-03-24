#!/usr/bin/env python3
"""
自動投稿レポートを Obsidian myfiles に書き出すスクリプト
launchd から auto_post.py 実行後に呼ばれる / 単体でも実行可能
"""

import json
import subprocess
from datetime import date, datetime
from pathlib import Path

BASE     = Path(__file__).parent
TODAY    = date.today().strftime("%Y-%m-%d")
NOW      = datetime.now().strftime("%H:%M")
MYFILES  = Path("/Users/mt112/Desktop/my files/myfiles")
REPORT_DIR = MYFILES / "SNS・Threads" / "自動投稿ログ"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

ACCOUNTS = {
    "truth": {
        "name": "@truth_body_salon",
        "log":    BASE / "log_truth.jsonl",
        "posted": BASE / "log_truth_posted.jsonl",
        "insights": BASE / "insights_truth.json",
    },
    "masa": {
        "name": "@masahide_takahashi_",
        "log":    BASE / "log_masa.jsonl",
        "posted": BASE / "log_masa_posted.jsonl",
        "insights": BASE / "insights_masa.json",
    },
}


def load_today_posts(acct: str) -> list[str]:
    f = ACCOUNTS[acct]["log"]
    if not f.exists():
        return []
    for line in reversed(f.read_text().splitlines()):
        try:
            e = json.loads(line)
            if e.get("date") == TODAY:
                return e.get("posts", [])
        except Exception:
            continue
    return []


def load_posted(acct: str) -> list[dict]:
    f = ACCOUNTS[acct]["posted"]
    if not f.exists():
        return []
    return [
        json.loads(l) for l in f.read_text().splitlines()
        if l.strip() and json.loads(l).get("date") == TODAY
    ]


def load_all_posted(acct: str) -> list[dict]:
    """全日付の投稿履歴"""
    f = ACCOUNTS[acct]["posted"]
    if not f.exists():
        return []
    entries = []
    for l in f.read_text().splitlines():
        try:
            entries.append(json.loads(l))
        except Exception:
            pass
    return entries


def load_insights(acct: str) -> dict:
    f = ACCOUNTS[acct]["insights"]
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


def threads_url(post_id: str, acct: str) -> str:
    handle = ACCOUNTS[acct]["name"].replace("@", "")
    return f"https://www.threads.com/@{handle}/post/{post_id}"


def build_report() -> str:
    lines = [
        f"# Threads 自動投稿レポート｜{TODAY}",
        f"> 最終更新: {NOW}",
        "",
        "---",
        "",
    ]

    for acct, info in ACCOUNTS.items():
        name = info["name"]
        posts     = load_today_posts(acct)
        posted    = load_posted(acct)
        all_posted = load_all_posted(acct)
        insights  = load_insights(acct)

        total_posted = len(all_posted)
        today_count  = len(posted)
        remaining    = len(posts) - today_count if posts else "不明"

        lines += [
            f"## {name}",
            "",
            "### 📊 今日の状況",
            f"| 項目 | 数値 |",
            f"|------|------|",
            f"| 生成済み | {len(posts)}本 |",
            f"| 投稿済み | {today_count}本 |",
            f"| 残り | {remaining}本 |",
            f"| 累計投稿数 | {total_posted}本 |",
            "",
        ]

        # インサイト（あれば）
        if insights:
            lines += [
                "### 📈 パフォーマンス（最新）",
                f"| 指標 | 数値 |",
                f"|------|------|",
                f"| 平均いいね | {insights.get('avg_likes', '-')} |",
                f"| 平均閲覧 | {insights.get('avg_views', '-')} |",
                f"| 直近7日 投稿数 | {insights.get('recent_7days_count', '-')}本 |",
                f"| 直近7日 平均いいね | {insights.get('recent_7days_avg_likes', '-')} |",
                "",
            ]

        # 本日投稿済み一覧
        if posted:
            lines += ["### ✅ 本日投稿済み", ""]
            for e in posted:
                idx  = e.get("index", 0)
                pid  = e.get("post_id", "")
                err  = e.get("error", "")
                text = ""
                if posts and idx < len(posts):
                    text = posts[idx].replace("\n", " ")[:60] + "..."

                url_part = f" → [Threads]({threads_url(pid, acct)})" if pid else ""
                err_part = f" ⚠️ {err}" if err else ""
                lines.append(f"- **#{idx+1}** {text}{url_part}{err_part}")
            lines.append("")
        else:
            lines += ["### ✅ 本日投稿済み", "なし（まだ時間外か未実行）", ""]

        # 未投稿の予定内容
        posted_indices = {e.get("index") for e in posted}
        pending = [(i, t) for i, t in enumerate(posts) if i not in posted_indices]
        if pending:
            lines += [
                f"### 🕐 投稿予定（残り{len(pending)}本）",
                "",
                "<details><summary>クリックして展開</summary>",
                "",
            ]
            for i, text in pending[:10]:
                lines.append(f"**#{i+1}**")
                lines.append(text)
                lines.append("")
            if len(pending) > 10:
                lines.append(f"…他{len(pending)-10}本")
            lines += ["</details>", ""]

        lines += ["---", ""]

    # エラーログ（あれば）
    error_log = BASE / "error.log"
    if error_log.exists():
        errors = error_log.read_text().strip().splitlines()[-20:]
        if errors:
            lines += [
                "## ⚠️ エラーログ（直近20件）",
                "",
                "```",
            ] + errors + ["```", ""]

    lines += [
        "---",
        f"*自動生成 by threads-auto-post｜{TODAY} {NOW}*",
    ]

    return "\n".join(lines)


def write_report():
    report = build_report()
    out = REPORT_DIR / f"{TODAY}.md"
    out.write_text(report, encoding="utf-8")
    print(f"✓ レポート保存: {out}")
    return out


def print_dashboard():
    """ターミナルでのダッシュボード表示"""
    print(f"\n{'='*52}")
    print(f"  Threads 自動投稿ダッシュボード｜{TODAY} {NOW}")
    print(f"{'='*52}")

    for acct, info in ACCOUNTS.items():
        name     = info["name"]
        posts    = load_today_posts(acct)
        posted   = load_posted(acct)
        insights = load_insights(acct)

        total    = len(posts)
        done     = len(posted)
        remain   = total - done

        print(f"\n  {name}")
        print(f"  {'─'*46}")
        print(f"  生成: {total}本  投稿済: {done}本  残り: {remain}本")

        if insights:
            print(f"  平均いいね: {insights.get('avg_likes','-')}  "
                  f"平均閲覧: {insights.get('avg_views','-')}")

        if posted:
            last = posted[-1]
            idx  = last.get("index", 0)
            pid  = last.get("post_id", "")
            text = ""
            if posts and idx < len(posts):
                text = posts[idx].replace("\n", " ")[:45] + "..."
            print(f"  最終投稿: #{idx+1} {text}")
            if pid:
                print(f"           {threads_url(pid, acct)}")

    print(f"\n{'='*52}")
    print(f"  レポートはObsidianで確認できます")
    print(f"  myfiles/SNS・Threads/自動投稿ログ/{TODAY}.md")
    print(f"{'='*52}\n")


if __name__ == "__main__":
    import sys
    if "--dashboard" in sys.argv or "-d" in sys.argv:
        print_dashboard()
    else:
        write_report()
        print_dashboard()
