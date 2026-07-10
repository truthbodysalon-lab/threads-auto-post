#!/usr/bin/env python3
"""
日中の投稿見張り番（30分毎にlaunchdから実行）。

2026-07-10の実障害（①masa: ペースフロアバグで50本を昼に使い切り午後無音
②truth: LINE重複の無限再選択で16本のまま夜まで全停止）を受けて新設。
「ログ上は正常でも外形上は止まっている」を日中に検知し、安全な自己修復を行い、
直らなければLINEで通知する。沈黙障害を構造的に無くすための最終防衛線。

チェック（アカウント毎）:
  A. ペース回廊: 投稿数が「今あるべき累計」から大きく外れていないか
     - 遅れ(want-8超の不足) → 詰まりと判定して修復
     - 進みすぎ(want+10超) → 固め打ちバグの再発を検知（通知のみ）
  B. ループ検知: autopostログ直近が同一「重複スキップ」の繰り返し
     → LINEリストインなら本日消化済みにして解除
  C. 修復後に1バッチ実行 → まだ止まっていればLINE Push（1日1回/acctまで）
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
import auto_post as ap  # noqa: E402  (ACCOUNTS / ペース関数 / LINE状態を再利用)

STATE = BASE / "watchdog_state.json"
LOG = BASE / "watchdog.log"
PUSH = "/Users/mt112/.claude/scripts/line-push-masahide.sh"


def log(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def _state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(d: dict):
    try:
        STATE.write_text(json.dumps(d, ensure_ascii=False))
    except Exception:
        pass


def _push_once_per_day(acct: str, msg: str):
    st = _state()
    key = f"push_{acct}"
    today = date.today().isoformat()
    if st.get(key) == today:
        return
    try:
        subprocess.run([PUSH, msg], capture_output=True, timeout=30)
        st[key] = today
        _save_state(st)
        log(f"LINE通知送信: {msg[:60]}")
    except Exception as e:
        log(f"LINE通知失敗: {e}")


def _recent_log_lines(acct: str, n: int = 8) -> list[str]:
    f = ap.ACCOUNTS[acct]["autopost_log"]
    try:
        return Path(f).read_text(encoding="utf-8").splitlines()[-n:]
    except Exception:
        return []


def _detect_dup_loop(acct: str) -> str | None:
    """直近ログが同一投稿の重複スキップ連発なら、その先頭文字列を返す。"""
    lines = [l for l in _recent_log_lines(acct) if "重複スキップ" in l]
    if len(lines) < 3:
        return None
    sigs = {l.split("重複スキップ]", 1)[-1].strip()[:24] for l in lines[-3:]}
    return sigs.pop() if len(sigs) == 1 else None


def check_account(acct: str) -> None:
    today = date.today().strftime("%Y-%m-%d")
    hour = datetime.now().hour
    posted = ap._posted_count_today(acct)
    want = ap._target_cumulative_by_now(hour)

    # A-2: 固め打ち検知（ペースより大幅先行 = フロア/暴走バグの再発）
    if posted > want + 10:
        log(f"{acct}: 固め打ち疑い posted={posted} want={want}")
        _push_once_per_day(acct, f"⚠️Threads {acct}: 投稿が先行しすぎ({posted}本/目標{want})。固め打ちバグの疑い。")
        return

    # A-1: 遅れ検知
    behind = want - posted
    if behind <= 8:
        return  # 正常回廊内

    log(f"{acct}: 遅れ検知 posted={posted} want={want} (不足{behind})")

    # B: 重複ループなら解除（LINEリストインの本日消化）
    sig = _detect_dup_loop(acct)
    if sig:
        log(f"{acct}: 重複ループ検知 [{sig}] → LINE消化フラグで解除")
        ap._mark_line_done(acct, today)

    # C: 修復バッチを即実行
    try:
        subprocess.run([sys.executable, str(BASE / "auto_post.py"), acct],
                       capture_output=True, timeout=540)
    except Exception as e:
        log(f"{acct}: 修復バッチ失敗 {e}")

    after = ap._posted_count_today(acct)
    if after > posted:
        log(f"{acct}: 回復 {posted}→{after}本")
    else:
        log(f"{acct}: 回復せず（{after}本のまま）→ 通知")
        tail = " / ".join(_recent_log_lines(acct, 2))[-120:]
        _push_once_per_day(acct, f"🚨Threads {acct}: 投稿停止の疑い（{after}本/目標{want}・自動修復失敗）。直近ログ: {tail}")


def main():
    hour = datetime.now().hour
    if not (ap.POST_HOUR_START <= hour < ap.POST_HOUR_END):
        return
    for acct in ("truth", "nagaoka", "masa"):
        try:
            check_account(acct)
        except Exception as e:
            log(f"{acct}: watchdog例外 {e}")


if __name__ == "__main__":
    main()
