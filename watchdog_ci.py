#!/usr/bin/env python3
"""
CI版watchdog（GitHub Actionsから30分毎に実行。Macが停止していても動く最終防衛線）。

ローカル版watchdog.pyと違い、投稿数を**Threads API（外形）**で数える。
リポジトリのログはローカルMacの未同期分を含まないため、外形カウントが唯一の真実。

チェック（アカウント毎）:
  A. ペース回廊: API実投稿数 vs 今あるべき累計（6〜23時に50本を均等配分）
     - 遅れ8本超 → 詰まりと判定: LINEリストイン消化フラグを立て、修復バッチ実行
     - 先行10本超 → 固め打ちバグ再発: LINE通知のみ
  B. 修復しても遅れが解消しなければ LINE Push 通知（1日1回/acctまで。state=repo内ファイル）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
import urllib.parse
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).parent
STATE = BASE / "watchdog_ci_state.json"
JST = timezone(timedelta(hours=9))
ACCTS = {"truth": "TRUTH", "nagaoka": "NAGAOKA", "masa": "MASA"}
POST_HOUR_START, POST_HOUR_END, DAILY_TARGET = 6, 23, 50
PACE_FULL_HOUR = int(os.environ.get("PACE_FULL_HOUR", "21"))  # auto_post.pyと同じ前倒し按分

for line in (BASE / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def api_count_today(acct: str) -> int:
    """Threads APIで本日(JST)の実投稿数を数える（外形＝唯一の真実）。失敗時-1。"""
    uid = os.environ.get(f"THREADS_USER_ID_{ACCTS[acct]}")
    tok = os.environ.get(f"THREADS_ACCESS_TOKEN_{ACCTS[acct]}")
    today = datetime.now(JST).date().isoformat()
    n, url = 0, f"https://graph.threads.net/v1.0/{uid}/threads?fields=id,timestamp,is_reply&limit=100&access_token={tok}"
    # スコープ耐性: threads_read_replies が無いトークン（nagaokaで実発生・2026-07-12）は
    # is_reply指定が code10 で失敗する。その場合はis_reply抜きで数え、リプライ込み概算として扱う
    # （監視が沈黙するより過大側の概算で継続する方が安全）。
    def _fetch(u):
        with urllib.request.urlopen(u, timeout=20) as r:
            return json.loads(r.read())
    try:
        try:
            _fetch(url.replace("&limit=100", "&limit=1"))
        except Exception:
            url = url.replace("id,timestamp,is_reply", "id,timestamp")
            print(f"{acct}: is_reply不可トークン→リプライ込み概算で継続")
        for _ in range(3):  # コメント除外後の本体50本を確実に拾う
            with urllib.request.urlopen(url, timeout=20) as r:
                d = json.loads(r.read())
            stop = False
            for p in d.get("data", []):
                if p.get("is_reply"):
                    continue   # コメント（返信）は本体投稿数に含めない
                ts = p.get("timestamp", "")
                try:
                    # Threadsは "+0000" 形式（fromisoformatはPy3.11未満で非対応）
                    dt = datetime.strptime(ts.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
                    jst_d = dt.astimezone(JST).date().isoformat()
                except Exception:
                    continue
                if jst_d == today:
                    n += 1
                else:
                    stop = True
            nxt = d.get("paging", {}).get("next")
            if stop or not nxt:
                break
            url = nxt
        return n
    except Exception as e:
        print(f"{acct}: APIカウント失敗 {e}")
        return -1


def want_now() -> int:
    h = datetime.now(JST).hour
    if h < POST_HOUR_START:
        return 0
    frac = min(1.0, (h - POST_HOUR_START + 1) / max(1, PACE_FULL_HOUR - POST_HOUR_START + 1))
    return int(DAILY_TARGET * frac)


def line_push(msg: str):
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
    except Exception as e:
        print(f"LINE通知失敗: {e}")


def _state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _notified_today(st: dict, key: str) -> bool:
    return st.get(key) == datetime.now(JST).date().isoformat()


def main():
    h = datetime.now(JST).hour
    if not (POST_HOUR_START <= h < POST_HOUR_END):
        print(f"投稿時間外({h}時JST) → スキップ")
        return
    st = _state()
    today = datetime.now(JST).date().isoformat()
    want = want_now()
    repaired = []
    for acct in ACCTS:
        n = api_count_today(acct)
        if n < 0:
            continue
        print(f"{acct}: 実投稿{n} / あるべき{want}")
        gap = want - n
        if n > want + 10 and not _notified_today(st, f"front_{acct}"):
            line_push(f"⚠️Threads {acct}: 投稿が先行しすぎ({n}本/目標{want})。固め打ちバグの疑い(CI watchdog)")
            st[f"front_{acct}"] = today
        # 通常は8本超の遅れで修復。21時以降は残り時間が無いため1本の不足でも埋めに行く
        elif gap > 8 or (h >= 21 and gap > 0):
            print(f"{acct}: 遅れ{gap}本 → LINE消化フラグ＋修復バッチ")
            # LINE無限ループの可能性を先に潰す（消化済み扱い）
            try:
                f = BASE / "line_listin_state.json"
                d = json.loads(f.read_text()) if f.exists() else {}
                d[acct] = {"date": today, "count": 9}
                f.write_text(json.dumps(d, ensure_ascii=False))
            except Exception:
                pass
            subprocess.run([sys.executable, str(BASE / "auto_post.py"), acct],
                           capture_output=True, timeout=780)
            after = api_count_today(acct)
            print(f"{acct}: 修復後 {n}→{after}本")
            if gap > 8 and after - n < 3 and not _notified_today(st, f"stall_{acct}"):
                line_push(f"🚨Threads {acct}: 投稿停止の疑い({after}本/目標{want}・CI修復でも回復小)。Mac/トークン/キューを確認してください")
                st[f"stall_{acct}"] = today
            repaired.append(acct)
    try:
        STATE.write_text(json.dumps(st, ensure_ascii=False))
    except Exception:
        pass
    print(f"watchdog_ci完了 修復対象={repaired or 'なし'}")


if __name__ == "__main__":
    main()
