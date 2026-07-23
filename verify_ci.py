#!/usr/bin/env python3
"""
CI版verify_systemラッパー（GitHub Actionsから毎日実行。Macが停止していても検品が回る最終防衛線）。

verify_system.py --json をサブプロセス実行し、結果を人間可読でActionsログに出す。
FAILが1件以上ある場合、またはverify_system.py自体がクラッシュ/JSON出力不正だった場合に
notifications.jsonlへ通知記録する（2026-07-21方針でLINEへは送信しない）。
WARNのみ・全PASSは通知しない（通知疲れ防止・watchdog_ci.pyと同方針）。

使い方:
  python3 verify_ci.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).parent
JST = timezone(timedelta(hours=9))
NOTIFICATIONS = BASE / "notifications.jsonl"


def line_push(msg: str):
    """通知記録（旧: LINE Push。watchdog_ci.pyのline_push()と同一実装をここに再掲）。
    2026-07-21ユーザー方針によりLINEへは一切送信しない（メッセージ件数消費のため）。
    代わりにリポジトリ内 notifications.jsonl へ追記し、CIがcommit→ローカルpull_sync.shが
    Obsidian(_通知)+~/.claude/notifications.log へ転送する。"""
    try:
        entry = {"ts": datetime.now(JST).isoformat(timespec="seconds"), "msg": msg}
        with NOTIFICATIONS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"通知記録: {msg[:60]}")
    except Exception as e:
        print(f"通知記録失敗: {e}")


def _run_url() -> str:
    """今回のActions実行へのURL（環境変数が揃わなければ空文字）。"""
    server = os.environ.get("GITHUB_SERVER_URL", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def main():
    proc = subprocess.run(
        [sys.executable, str(BASE / "verify_system.py"), "--json"],
        cwd=str(BASE), capture_output=True, text=True)

    data = None
    try:
        data = json.loads(proc.stdout)
    except Exception:
        data = None

    # 異常系: JSON解析失敗、または想定外の終了コード（0=PASS/WARN, 1=FAIL 以外）
    # → verify_system.py自体がクラッシュ（例外の外側で落ちた）とみなす
    abnormal = data is None or "summary" not in data or "results" not in data \
        or proc.returncode not in (0, 1)

    if abnormal:
        print("verify_system.py の実行結果が不正です（クラッシュ or JSON解析失敗の疑い）")
        print(f"exit code: {proc.returncode}")
        print(f"--- stdout (末尾2000字) ---\n{proc.stdout[-2000:]}")
        print(f"--- stderr (末尾2000字) ---\n{proc.stderr[-2000:]}")

        msg_lines = ["🚨Threads Daily Verify: verify_system.py クラッシュ/出力異常",
                     f"exit={proc.returncode}"]
        if proc.stderr.strip():
            msg_lines.append(f"stderr末尾: {proc.stderr.strip()[-500:]}")
        run_url = _run_url()
        if run_url:
            msg_lines.append(run_url)
        line_push("\n".join(msg_lines))
        sys.exit(1)

    summary = data["summary"]
    results = data["results"]
    fails = [r for r in results if r.get("status") == "FAIL"]
    warns = [r for r in results if r.get("status") == "WARN"]

    print(f"=== システム検証(CI) {summary.get('checked_at', '?')} ===")
    print(f"総合: {summary.get('overall', '?')} | "
          f"PASS {summary.get('pass', '?')} / WARN {summary.get('warn', '?')} / FAIL {summary.get('fail', '?')}\n")
    for r in warns:
        print(f"  ⚠ [{r.get('category')}] {r.get('id')}: {r.get('detail')}")
    for r in fails:
        print(f"  ✗ [{r.get('category')}] {r.get('id')}: {r.get('detail')}")
    if not fails and not warns:
        print("  全項目PASS")

    if fails:
        msg_lines = [f"🚨Threads Daily Verify: FAIL {len(fails)}件検出"]
        for r in fails:
            msg_lines.append(f"・[{r.get('id')}] {r.get('detail')}")
        run_url = _run_url()
        if run_url:
            msg_lines.append(run_url)
        line_push("\n".join(msg_lines))
        sys.exit(1)

    # WARNのみ・全PASSはログのみで通知しない（通知疲れ防止）
    sys.exit(0)


if __name__ == "__main__":
    main()
