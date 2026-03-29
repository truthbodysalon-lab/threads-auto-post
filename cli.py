#!/usr/bin/env python3
"""
Threads自動投稿システム CLI

使い方:
  python3 cli.py                        # メニュー表示
  python3 cli.py generate [truth|masa]  # 今日の投稿を30本生成
  python3 cli.py post [truth|masa]      # 次の1本を投稿（確認あり）
  python3 cli.py post all [truth|masa]  # 今日の残り全本を投稿
  python3 cli.py status [truth|masa]    # 今日の投稿状況を確認
  python3 cli.py feedback               # フィードバックを記録
  python3 cli.py log [truth|masa]       # 直近の投稿履歴
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).parent
TODAY = date.today().strftime("%Y-%m-%d")
FEEDBACK_FILE = Path.home() / ".claude/projects/-Users-mt112-Desktop/memory/feedback_post_feedback.md"

ACCOUNTS = {
    "truth": {
        "name": "@truth_body_salon",
        "log": BASE / "log_truth.jsonl",
        "posted": BASE / "log_truth_posted.jsonl",
        "token_key": "THREADS_ACCESS_TOKEN_TRUTH",
        "uid_key": "THREADS_USER_ID_TRUTH",
        "generate_arg": "truth",
    },
    "masa": {
        "name": "@masahide_takahashi_",
        "log": BASE / "log_masa.jsonl",
        "posted": BASE / "log_masa_posted.jsonl",
        "token_key": "THREADS_ACCESS_TOKEN_MASA",
        "uid_key": "THREADS_USER_ID_MASA",
        "generate_arg": "masa",
    },
}

BASE_URL = "https://graph.threads.net/v1.0"

# .env 読み込み
for line in (BASE / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()


# ── 共通関数 ──────────────────────────────────────

def resolve_account(arg: str) -> str:
    if "masa" in arg.lower():
        return "masa"
    return "truth"


def get_today_posts(acct: str) -> list:
    log = ACCOUNTS[acct]["log"]
    if not log.exists():
        return []
    entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    today = [e for e in entries if e.get("date") == TODAY]
    return today[-1]["posts"] if today else []


def get_posted_indices(acct: str) -> set:
    pfile = ACCOUNTS[acct]["posted"]
    if not pfile.exists():
        return set()
    return {
        json.loads(l)["index"]
        for l in pfile.read_text().splitlines()
        if l.strip() and json.loads(l).get("date") == TODAY
    }


def mark_posted(acct: str, index: int, post_id: str):
    pfile = ACCOUNTS[acct]["posted"]
    with open(pfile, "a") as f:
        f.write(json.dumps({"date": TODAY, "index": index, "post_id": post_id}, ensure_ascii=False) + "\n")


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


# ── コマンド ──────────────────────────────────────

def cmd_generate(acct: str):
    import subprocess
    name = ACCOUNTS[acct]["name"]
    print(f"{name} の投稿を生成中...")
    result = subprocess.run(
        [sys.executable, str(BASE / "generate_remix.py"), acct],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)


def cmd_status(acct: str):
    """ダッシュボード表示 + Obsidianレポートを更新"""
    import subprocess
    # report.py のダッシュボード表示を使う
    result = subprocess.run(
        [sys.executable, str(BASE / "report.py")],
        capture_output=False
    )
    if result.returncode != 0:
        # フォールバック: シンプル表示
        name  = ACCOUNTS[acct]["name"]
        posts = get_today_posts(acct)
        posted = get_posted_indices(acct)
        print(f"\n📊 {name} の今日の状況 ({TODAY})")
        if not posts:
            print("  生成データなし → python3 cli.py generate で生成してください")
            return
        print(f"  生成数:   {len(posts)}本")
        print(f"  投稿済み: {len(posted)}本")
        print(f"  残り:     {len(posts) - len(posted)}本")


def cmd_post(acct: str, mode: str = "one"):
    name = ACCOUNTS[acct]["name"]
    posts = get_today_posts(acct)
    if not posts:
        print(f"{name} の今日の投稿データがありません。先に generate を実行してください。")
        return

    posted = get_posted_indices(acct)
    pending = [(i, t) for i, t in enumerate(posts) if i not in posted]

    if not pending:
        print(f"{name} の今日の投稿はすべて完了しています。")
        return

    targets = pending if mode == "all" else [pending[0]]

    for index, text in targets:
        print(f"\n[{name}] [{index+1}/{len(posts)}]\n{'─'*40}\n{text}\n{'─'*40}")
        try:
            post_id = post_to_threads(acct, text)
            mark_posted(acct, index, post_id)
            print(f"✓ 投稿完了 (ID: {post_id})")
            if mode == "all":
                time.sleep(3)
        except urllib.error.HTTPError as e:
            print(f"✗ エラー: {e.read().decode()}")


def cmd_feedback():
    """フィードバックマネージャーを起動（パターン重み・NGワード・テンプレ管理）"""
    import subprocess
    subprocess.run([sys.executable, str(BASE / "feedback_manager.py")])


def cmd_log(acct: str, n: int = 10):
    name = ACCOUNTS[acct]["name"]
    pfile = ACCOUNTS[acct]["posted"]
    if not pfile.exists():
        print(f"{name} の投稿履歴がありません")
        return
    entries = [json.loads(l) for l in pfile.read_text().splitlines() if l.strip()]
    posts = get_today_posts(acct)
    print(f"\n📋 {name} 直近{min(n, len(entries))}件")
    for e in entries[-n:]:
        i = e["index"]
        text = (posts[i][:50].replace("\n", " ") + "...") if i < len(posts) else "（不明）"
        print(f"  [{e['date']}] #{i+1}: {text}")


def cmd_review(days: int = 2):
    import subprocess
    subprocess.run(
        [sys.executable, str(BASE / "review.py"), "--days", str(days)],
        cwd=str(BASE)
    )


def cmd_tune(acct: str = "all"):
    import subprocess
    args = [sys.executable, str(BASE / "analyze_and_tune.py")]
    if acct != "all":
        args.append(acct)
    subprocess.run(args, cwd=str(BASE))


def cmd_analyze(acct: str):
    import subprocess
    name = ACCOUNTS[acct]["name"]
    print(f"{name} のインサイトを取得・分析中...")
    result = subprocess.run(
        [sys.executable, str(BASE / "insights.py"), acct],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)


def cmd_analyze_show(acct: str):
    """保存済みインサイトを表示（APIなし）"""
    insights_file = BASE / f"insights_{acct}.json"
    if not insights_file.exists():
        print(f"インサイトデータがありません。先に analyze を実行してください。")
        return
    data = json.loads(insights_file.read_text())
    name = ACCOUNTS[acct]["name"]
    print(f"\n{'='*50}")
    print(f"📊 {name}")
    print(f"   更新: {data.get('updated_at', '不明')}")
    print(f"{'='*50}")
    print(f"  総投稿数:     {data.get('total_posts', 0)}件")
    print(f"  平均いいね:   {data.get('avg_likes', 0)}")
    print(f"  平均閲覧:     {data.get('avg_views', 0)}")
    print(f"  直近7日間:    {data.get('recent_7days_count', 0)}本 / 平均いいね {data.get('recent_7days_avg_likes', 0)}")
    print(f"\n📌 文字数別パフォーマンス")
    for bucket, stat in sorted(data.get("performance_by_length", {}).items()):
        print(f"  {bucket}: {stat['count']}件 / 平均いいね {stat['avg_likes']} / 平均閲覧 {stat['avg_views']}")
    print(f"\n🏆 TOP10（いいね順）")
    for i, p in enumerate(data.get("top10_by_likes", []), 1):
        print(f"  {i}. いいね{p['likes']} 閲覧{p['views']} [{p['date']}]")
        print(f"     {p['text'][:60].replace(chr(10), ' ')}")


def cmd_menu():
    print("\n=== Threads自動投稿システム ===")
    print(f"日付: {TODAY}")
    print()
    print("  アカウント: truth (@truth_body_salon) / masa (@masahide_takahashi_)")
    print()
    print("  generate [truth|masa]  - 30本生成")
    print("  status   [truth|masa]  - 投稿状況確認")
    print("  post     [truth|masa]  - 次の1本を投稿")
    print("  post all [truth|masa]  - 残りを全部投稿")
    print("  review   [N日]         - 投稿をレビュー・評価 (例: review 3)")
    print("  tune     [truth|masa]  - 分析＆生成パターン重み調整")
    print("  analyze  [truth|masa]  - インサイト取得・分析（API）")
    print("  show     [truth|masa]  - 保存済みインサイト表示")
    print("  feedback               - メモ記録")
    print("  log      [truth|masa]  - 投稿履歴")
    print("  q                      - 終了")
    print()

    while True:
        raw = input("> ").strip().lower().split()
        if not raw:
            continue
        cmd = raw[0]
        arg = raw[1] if len(raw) > 1 else "truth"
        acct = resolve_account(arg)

        if cmd in ("q", "quit"):
            break
        elif cmd == "generate":
            cmd_generate(acct)
        elif cmd == "status":
            cmd_status(acct)
        elif cmd == "post":
            mode = "all" if len(raw) > 1 and raw[1] == "all" else "one"
            a = resolve_account(raw[2] if len(raw) > 2 else raw[1] if len(raw) > 1 else "truth")
            cmd_post(a, mode)
        elif cmd == "review":
            days = int(raw[1]) if len(raw) > 1 and raw[1].isdigit() else 2
            cmd_review(days)
        elif cmd == "tune":
            cmd_tune(arg if arg in ("truth", "masa") else "all")
        elif cmd == "analyze":
            cmd_analyze(acct)
        elif cmd == "show":
            cmd_analyze_show(acct)
        elif cmd == "feedback":
            cmd_feedback()
        elif cmd == "log":
            cmd_log(acct)
        else:
            print("コマンドが不明です")
        print()


# ── エントリポイント ──────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        cmd_menu()
        sys.exit(0)

    cmd = args[0]
    # アカウント引数を探す
    acct_arg = next((a for a in args[1:] if a in ("truth", "masa")), "truth")
    acct = resolve_account(acct_arg)

    if cmd == "generate":
        cmd_generate(acct)
    elif cmd in ("status", "report", "dashboard"):
        cmd_status(acct)
    elif cmd == "post":
        mode = "all" if "all" in args[1:] else "one"
        cmd_post(acct, mode)
    elif cmd == "review":
        days = int(args[1]) if len(args) > 1 and args[1].isdigit() else 2
        cmd_review(days)
    elif cmd == "tune":
        cmd_tune(acct_arg if acct_arg in ("truth", "masa") else "all")
    elif cmd == "analyze":
        cmd_analyze(acct)
    elif cmd == "show":
        cmd_analyze_show(acct)
    elif cmd == "feedback":
        cmd_feedback()
    elif cmd == "log":
        n = int(args[-1]) if args[-1].isdigit() else 10
        cmd_log(acct, n)
    else:
        print(__doc__)
