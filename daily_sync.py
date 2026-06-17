#!/usr/bin/env python3
"""
毎朝6:00に実行:
1. myfilesを参照して当日分投稿を生成
2. GitHubにプッシュ（GitHub Actionsが使えるように）
"""
import subprocess
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).parent
TODAY = date.today().strftime("%Y-%m-%d")
LOG = BASE / f"daily_sync_{TODAY}.log"

def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE), **kwargs)
    msg = f"$ {' '.join(cmd)}\n{result.stdout}{result.stderr}".strip()
    print(msg)
    LOG.write_text((LOG.read_text() if LOG.exists() else "") + msg + "\n\n", encoding="utf-8")
    return result

def generate_today():
    """当日分がなければ投稿を生成"""
    import json

    for account in ["truth", "masa"]:
        log_file = BASE / f"log_{account}.jsonl"
        has_today = False
        if log_file.exists():
            for line in log_file.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                    if entry.get("date") == TODAY:
                        has_today = True
                        break
                except Exception:
                    pass

        if not has_today:
            print(f"[{account}] 本日分を生成中...")
            run([sys.executable, "generate_remix.py", account])
        else:
            print(f"[{account}] 本日分は生成済み")

def git_push():
    """変更をコミットし、push は git_sync.py（stash安全・rebase競合対策済み）に委譲する。
    ここで直接 pull --rebase すると未追跡/未ステージのデータ変更があるとき
    『cannot pull with rebase: You have unstaged changes』で失敗しerror.logを汚すため、
    push の競合解決は冪等な git_sync.py に一本化する。"""
    run(["git", "add", "log_truth.jsonl", "log_masa.jsonl"])
    result = run(["git", "diff", "--cached", "--quiet"])
    if result.returncode == 0:
        print("変更なし、プッシュ不要")
        return

    run(["git", "commit", "-m", f"auto: generate posts {TODAY}"])
    # push（とリモート競合の解決）は git_sync.py に委譲（stash安全・冪等）
    run([sys.executable, "git_sync.py"])
    print("✓ GitHubへ同期（git_sync経由）")

if __name__ == "__main__":
    print(f"=== daily_sync 開始 {TODAY} ===")
    generate_today()
    git_push()
    print("=== daily_sync 完了 ===")
