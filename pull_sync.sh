#!/usr/bin/env bash
# pull_sync.sh — クラウド(GitHub Actions)が正本になったthreads-auto-postのローカルミラー更新
# 2026-07-10 カットオーバー: 旧gitsync(push型・-X oursでクラウド上書き事故リスク)を置換。
# ローカルは読み取り専用ミラー。review/verify/views等の分析タスクが新鮮なデータを読むためだけに同期する。
set -euo pipefail
LOCK_DIR="/tmp/threads-pullsync.lock"
LOG="$HOME/Library/Logs/threads-pullsync.log"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then exit 0; fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
cd /Users/mt112/threads-auto-post
{
  echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] pull_sync start"
  git fetch origin main
  # 編集中は自動一時停止（2026-07-24追加）:
  #   ソース編集中に hard reset が走って未保存の作業を消す事故を防ぐ。
  #   判定は「reset --hard が実際に破壊しうる かつ 実行時ログの通常ドリフトでは発火しない」
  #   2条件のみ:
  #     (A) 未pushコミットがある（agentがcommit済みだがpush前 → resetで消える）
  #     (B) 編集ロック .pullsync-pause が新しい（Claudeが編集した瞬間にフックがtouch、15分でTTL自動解除）
  #   ※「追跡ファイルの未コミット変更」は判定に使わない: shared_posted_guard.jsonl 等の
  #     実行時ログをローカル投稿(com.uplink.threads)が常時追記しており、それだと永久スキップになるため。
  EDIT_LOCK="/Users/mt112/threads-auto-post/.pullsync-pause"
  skip_reason=""
  if [ "$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)" != "0" ]; then
    skip_reason="ローカル未pushコミットあり"
  elif [ -f "$EDIT_LOCK" ]; then
    # ロックのmtimeが15分以内なら有効（古い置き忘れロックは無視して自己回復）
    now=$(date +%s); mt=$(stat -f %m "$EDIT_LOCK" 2>/dev/null || echo 0)
    if [ "$((now - mt))" -lt 900 ]; then skip_reason="編集ロック有効(.pullsync-pause)"; fi
  fi
  if [ -n "$skip_reason" ]; then
    echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] pull_sync SKIP reset: $skip_reason — ローカル編集を保護"
  else
    # ローカルの追跡ファイル変更はクラウド正本で上書き（未追跡ファイル=ローカルレポート類は残す）
    git reset --hard origin/main
  fi

  # 通知ブリッジ: クラウドCIが記録した notifications.jsonl の新着行を
  # ローカル通知（line-push-masahide.sh = Obsidian _通知 + ~/.claude/notifications.log）へ転送
  # （2026-07-21方針: LINEへは送信しない。ヘルパーはファイル記録実装）
  NOTIFY_STATE="$HOME/.claude/threads_notify_delivered.count"
  NOTIFY_HELPER="$HOME/.claude/scripts/line-push-masahide.sh"
  if [ -f notifications.jsonl ] && [ -x "$NOTIFY_HELPER" ]; then
    total=$(wc -l < notifications.jsonl | tr -d ' ')
    done_n=$(cat "$NOTIFY_STATE" 2>/dev/null || echo 0)
    case "$done_n" in ''|*[!0-9]*) done_n=0;; esac
    if [ "$total" -lt "$done_n" ]; then done_n=0; fi   # ファイル再作成時は先頭から
    if [ "$total" -gt "$done_n" ]; then
      tail -n "+$((done_n + 1))" notifications.jsonl | while IFS= read -r line; do
        msg=$(printf '%s' "$line" | python3 -c '
import sys, json
d = json.loads(sys.stdin.read())
print("[" + d.get("ts", "") + "] " + d.get("msg", ""))
' 2>/dev/null) || msg="$line"
        printf '%s' "$msg" | "$NOTIFY_HELPER" || true
      done
      echo "$total" > "$NOTIFY_STATE"
      echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] notify bridge: $((total - done_n))件転送"
    fi
  fi

  echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] pull_sync done ($(git rev-parse --short HEAD))"
} >> "$LOG" 2>&1
