#!/usr/bin/env python3
"""
日中の投稿見張り番（30分毎にlaunchdから実行）。監視＋LINE通知専用（投稿は行わない）。

2026-07-10の実障害（①masa: ペースフロアバグで50本を昼に使い切り午後無音
②truth: LINE重複の無限再選択で16本のまま夜まで全停止）を受けて新設。
当初は「ログ上は正常でも外形上は止まっている」を日中に検知し、自己修復
（auto_post.py直接実行）まで行っていた。

2026-07-21改修: クラウド一本化（07-10）後もこの自己修復が生きていたため、
ローカルの投稿記録（mark_postedによるlog_*_posted.jsonl追記）がcommitされず、
30分毎のpull_sync.shの`git reset --hard origin/main`で消去され続ける事故が
発生（07-20は3アカウントとも外形実測で約50本投稿済みなのに正本台帳には
11/3/11本しか残らず、台帳・PDCA分析データが欠損。二重運用による重複スキップ
も誘発した）。これを受け、投稿の修復はクラウド側watchdog_ci.py
（health_check.yml、30分毎、外形API基準、遅れ8本超で自動修復）に完全一本化。
本スクリプトはauto_post.pyの実行を一切行わず、外形監視とLINE通知のみを担う。

チェック（アカウント毎）:
  A. ペース回廊: 投稿数（外形API実測を第一とする）が「今あるべき累計」から
     大きく外れていないか
     - 遅れ(want-15超の不足) → クラウド側watchdog_ciの自己修復が追いついて
       いない疑いとしてLINE通知のみ（修復は行わない）
     - 進みすぎ(want+10超) → 固め打ちバグの再発を検知（通知のみ）
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
import auto_post as ap  # noqa: E402  (ACCOUNTS / ペース関数を再利用)
from watchdog_ci import api_count_today  # noqa: E402  (外形API実測)

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


def check_account(acct: str) -> None:
    hour = datetime.now().hour

    # 投稿数は外形API実測を第一とする（ローカル台帳はpull_syncの同期ラグで
    # 過少カウントし得るため、台帳単独での遅れ判定は誤報になる）。
    # API照会に失敗した場合は誤報を避けるため監視をスキップする
    # （クラウド側watchdog_ciが引き続き防衛線）。
    try:
        api_n = api_count_today(acct)
        if api_n < 0:
            raise RuntimeError("api_count_today failed")
    except Exception as e:
        log(f"{acct}: API実測不可のため監視スキップ ({e})")
        return

    posted = max(ap._posted_count_today(acct), api_n)
    want = ap._target_cumulative_by_now(hour)

    # A-2: 固め打ち検知（ペースより大幅先行 = フロア/暴走バグの再発）
    if posted > want + 10:
        log(f"{acct}: 固め打ち疑い posted={posted} want={want}")
        _push_once_per_day(acct, f"⚠️Threads {acct}: 投稿が先行しすぎ({posted}本/目標{want})。固め打ちバグの疑い。")
        return

    # A-1: 遅れ検知（クラウド側watchdog_ciが遅れ8本超で自動修復するため、
    # ローカルは「クラウド修復が機能していない」深刻な停滞のみ通知する）
    behind = want - posted
    if behind <= 15:
        return  # 正常回廊内（クラウド修復の猶予込み）

    log(f"{acct}: 遅れ検知 posted={posted} want={want} (不足{behind})")
    _push_once_per_day(acct, f"🚨Threads {acct}: 投稿ペース停滞（{posted}本/目標{want}・クラウド自己修復が追いついていない疑い）。health_check.ymlの実行状況を確認してください")


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
