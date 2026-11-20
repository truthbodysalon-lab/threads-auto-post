#!/usr/bin/env python3
"""
LINE登録（友だち追加）日次トラッカー — masa専用・予算ゼロ

masaのThreads → プロフィールリンク → LINE公式（LINE harness）への
登録数を、harnessのSQLite DBから日次で取得し line_history.json に追記する。

これが analyze_and_tune.py に渡り、「フォロワーを増やした型」よりさらに上位の
最終KPI=「LINE登録に繋がった投稿パターン」を重みへ反映する。

⚠️ harness DBはローカルファイルのため、このスクリプトはローカル（launchd）専用。
   CI（GitHub Actions）では DB が存在せずスキップされる。

使い方:
  python3 line_tracker.py
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

BASE = Path(__file__).parent
HISTORY_FILE = BASE / "line_history.json"
TODAY = date.today().strftime("%Y-%m-%d")

# masaのLINE harness DB（ローカル）
HARNESS_DB = Path("/Users/mt112/Desktop/line-harness/data.db")

# どのThreadsアカウントに紐づくか
ACCT = "masa"


def total_friends() -> int | None:
    if not HARNESS_DB.exists():
        print(f"  harness DB が見つかりません: {HARNESS_DB}（CI環境ではスキップ）")
        return None
    try:
        con = sqlite3.connect(f"file:{HARNESS_DB}?mode=ro", uri=True)
        cur = con.execute("SELECT COUNT(*) FROM friends")
        n = cur.fetchone()[0]
        con.close()
        return int(n)
    except Exception as e:
        print(f"  DB読み取りエラー: {e}")
        return None


def added_today() -> int:
    """added_at が今日の友だち数（参考値）"""
    if not HARNESS_DB.exists():
        return 0
    try:
        con = sqlite3.connect(f"file:{HARNESS_DB}?mode=ro", uri=True)
        cur = con.execute(
            "SELECT COUNT(*) FROM friends WHERE substr(added_at,1,10)=?",
            (TODAY,),
        )
        n = cur.fetchone()[0]
        con.close()
        return int(n)
    except Exception:
        return 0


def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            return {}
    return {}


def main():
    count = total_friends()
    if count is None:
        return

    history = load_history()
    series = history.setdefault(ACCT, [])
    prev = series[-1]["friends"] if series else None
    delta = (count - prev) if prev is not None else added_today()

    if series and series[-1]["date"] == TODAY:
        base_prev = series[-2]["friends"] if len(series) >= 2 else count - added_today()
        series[-1] = {"date": TODAY, "friends": count, "delta": count - base_prev}
        delta = series[-1]["delta"]
    else:
        series.append({"date": TODAY, "friends": count, "delta": delta})

    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2))
    sign = f"+{delta}" if delta >= 0 else str(delta)
    print(f"LINE登録 {TODAY}: 総{count}人 (前日比 {sign}) → {HISTORY_FILE.name}")


if __name__ == "__main__":
    main()
