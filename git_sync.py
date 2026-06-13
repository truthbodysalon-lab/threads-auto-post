#!/usr/bin/env python3
"""
ローカル→GitHub 同期フラッシュ（push詰まり恒久対策）

ローカルの自動ジョブ（daily_sync / analyze_and_tune / auto_post）が作った
コミットが、git競合で push されず溜まるのを防ぐ。
- 未コミットのデータ変更があればまとめてコミット
- 未pushのコミットがあれば pull --rebase -X ours → push（リトライ）
launchd から30分毎に実行する想定。冪等（変更が無ければ何もしない）。

使い方:
  python3 git_sync.py
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
LOG = BASE / "git_sync.log"

# ローカルジョブが更新しうるデータファイル（コードは手動コミットなので含めない）
DATA_FILES = [
    "log_truth.jsonl", "log_nagaoka.jsonl", "log_masa.jsonl",
    "log_truth_posted.jsonl", "log_nagaoka_posted.jsonl", "log_masa_posted.jsonl",
    "weights_truth.json", "weights_nagaoka.json", "weights_masa.json",
    "insights_truth.json", "insights_nagaoka.json", "insights_masa.json",
    "past_posts.json", "past_posts_nagaoka.json", "past_posts_masa.json",
    "followers_history.json", "line_history.json",
    "shared_posted_guard.jsonl", "feedback.json",
    "line_listin_state.json",  # LINEリストイン 1日1回 の状態（cross-system共有）
]


def _git(*args, check=False):
    return subprocess.run(["git", *args], cwd=str(BASE),
                          capture_output=True, text=True, check=check)


def _log(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {msg}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def unpushed_count() -> int:
    r = _git("rev-list", "--count", "origin/main..HEAD")
    try:
        return int(r.stdout.strip() or "0")
    except ValueError:
        return 0


def main():
    _git("fetch", "origin", "main")

    # 1) 未コミットのデータ変更をまとめてコミット
    existing = [f for f in DATA_FILES if (BASE / f).exists()]
    _git("add", *existing)
    staged = _git("diff", "--cached", "--quiet")
    if staged.returncode != 0:  # 差分あり
        _git("commit", "-m", f"chore: local data sync {datetime.now().strftime('%Y-%m-%d %H:%M')} [skip ci]")
        _log("ローカルデータ変更をコミット")

    # 2) 未pushがあるか
    n = unpushed_count()
    if n == 0:
        _log("未push 0件（同期済み）")
        return

    _log(f"未push {n}件 → 同期開始")

    # 3) rebase -X ours で取り込みつつ push（リトライ）
    # ※ 未コミット/未追跡ファイルを失わないよう、stash は必ず pop で復元する
    for attempt in range(1, 5):
        push = _git("push", "origin", "main")
        if push.returncode == 0:
            _log(f"✓ push成功（{n}件反映）")
            return
        # 失敗 → リモートを取り込んで再挑戦
        stash = _git("stash", "-u")
        stashed = "No local changes" not in (stash.stdout + stash.stderr)
        rb = _git("pull", "--rebase", "-X", "ours", "origin", "main")
        # rebase中断の保険
        if "rebase" in (rb.stderr + rb.stdout).lower() and rb.returncode != 0:
            _git("rebase", "--abort")
            _git("merge", "-X", "ours", "origin/main", "-m", "merge: reconcile data [skip ci]")
        if stashed:
            pop = _git("stash", "pop")   # drop ではなく pop（退避した変更を必ず戻す）
            if pop.returncode != 0:
                # pop衝突時もデータを失わないようstashは保持（手動回収可能）
                _log("  [WARN] stash pop 衝突。stashに退避を保持しています（git stash list で確認可）")
        _log(f"  リトライ {attempt}: リモート取り込み後に再push")

    _log("⚠ push最終失敗（次回フラッシュで再試行）")


if __name__ == "__main__":
    main()
