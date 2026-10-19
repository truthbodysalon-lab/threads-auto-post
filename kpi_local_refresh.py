#!/usr/bin/env python3
"""
ローカル専用のKPI履歴更新＋分析レポート保存（launchd com.threads.review・毎朝9:00）

2026-10-20 効率化監査F37: 旧 analyze_and_tune.py のローカル実行は、クラウド(generate.yml 毎日6:47)と
重み(weights_*.json)の更新が二重になり、Mac側の push が30分毎の pull_sync.sh(reset --hard)と競合していた。
重み更新はクラウドに一本化し、Macにしか出来ない次の3つだけを本スクリプトが担う。
  1. followers_history.json の更新（follower_tracker）
  2. line_history.json の更新（LINE harness のDBはローカルにしか無い＝line_tracker）
  3. Obsidianの「分析レポート」(SNS・Threads/分析レポート/分析_日付.md)の保存
     ※ 重み・dead_openings は保存しない（analyze_and_tune の save_weights 等を無効化して再計算だけ行う）
更新した2つの履歴ファイルのみを commit → git_sync.py 経由で push する。
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import analyze_and_tune as at

BASE = Path(__file__).parent
HISTORY_FILES = ["followers_history.json", "line_history.json"]


def push_histories() -> None:
    files = [f for f in HISTORY_FILES if (BASE / f).exists()]
    subprocess.run(["git", "add", *files], cwd=str(BASE), check=False)
    if subprocess.run(["git", "diff", "--cached", "--quiet", "--", *files], cwd=str(BASE)).returncode == 0:
        return
    subprocess.run(["git", "commit", "-m", f"chore: KPI履歴更新 {at.TODAY} [skip ci]", "--", *files],
                   cwd=str(BASE), check=False)
    subprocess.run([sys.executable, "git_sync.py"], cwd=str(BASE), check=False)


def main() -> int:
    at.refresh_follower_history()
    at.refresh_line_history()

    # 重み・dead_openings はクラウドが正本。ここでは保存せず、レポート用の再計算だけ行う
    at.save_weights = lambda *a, **k: None
    at.update_dead_openings_from_losers = lambda acct: None

    lines = [
        f"# Threads 投稿分析レポート｜{at.TODAY}",
        f"> 更新: {datetime.now().strftime('%H:%M')}（ローカル再計算・重みはクラウドが更新）",
        "",
        "---",
        "",
    ]
    for acct in ("truth", "nagaoka", "masa"):
        try:
            lines += at.run(acct)
        except Exception as e:  # 1アカウントの失敗で履歴更新まで止めない
            print(f"[WARN] {acct} のレポート再計算に失敗: {e}")
    try:
        at.save_obsidian_report(lines)
    except Exception as e:
        print(f"[WARN] 分析レポート保存に失敗: {e}")
    push_histories()
    return 0


if __name__ == "__main__":
    sys.exit(main())
