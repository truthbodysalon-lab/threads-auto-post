#!/usr/bin/env python3
"""
CI版verify_systemラッパー（GitHub Actionsから毎日実行。Macが停止していても検品が回る最終防衛線）。

verify_system.py --json をサブプロセス実行し、結果を人間可読でActionsログに出す。
FAILが1件以上ある場合、またはverify_system.py自体がクラッシュ/JSON出力不正だった場合に
LINE Push通知を送る。WARNのみ・全PASSは通知しない（通知疲れ防止・watchdog_ci.pyと同方針）。

使い方:
  python3 verify_ci.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).parent


def line_push(msg: str):
    """LINE Push通知（watchdog_ci.pyのline_push()と同一実装をここに再掲）。"""
    tok = os.environ.get("LINE_NOTIFY_TOKEN", "")
    uid = os.environ.get("LINE_NOTIFY_USER_ID", "")
    if not tok or not uid:
        print("LINE通知: secrets未設定でスキップ")
        return
    try:
        req = urllib.request.Request(
            "https://api.line.me/v2/bot/message/push",
            data=json.dumps({"to": uid, "messages": [{"type": "text", "text": msg[:4900]}]}).encode(),
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            method="POST")
        urllib.request.urlopen(req, timeout=20)
        print(f"LINE通知送信: {msg[:60]}")
    except urllib.error.HTTPError as e:
        # LINE APIのエラー本文（message/details）まで出して原因特定できるようにする
        # （本文にトークン等の秘匿値は含まれないため出力は安全）
        try:
            body = e.read().decode(errors="replace")
        except Exception:
            body = "(本文読取不可)"
        print(f"LINE通知失敗: HTTP {e.code} {body}")
    except Exception as e:
        print(f"LINE通知失敗: {e}")


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
