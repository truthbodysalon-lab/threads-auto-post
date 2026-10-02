#!/usr/bin/env python3
"""
投稿停止の外形検知（共通ロジック）。watchdog.py（Mac）と watchdog_ci.py（クラウド）が使う。

背景（2026-10-02 管理体制強化）: 直近3回の投稿停止障害は、いずれも「GitHub上はsuccess」の
まま止まり、人が気付くまで数時間かかった。
  (1) 9/23-24 masa 24時間0本  (2) 9/29 truth 4時間0本  (3) 10/2 全アカ5.5時間0本(cron間引き)
そこで「ペース（累計本数）」とは別軸の「最後の投稿からの経過時間」を外形APIで直接見る。

提供関数:
  last_post_gap_minutes(acct)  投稿時間帯の「最後の自前投稿→現在」の経過分（時間帯外は0）。失敗時None
  max_gap_today(acct)          本日(6時JST起点)の最大投稿間隔(分)。失敗時None
  keeper_status()              auto_post.yml 常駐runの稼働数と直近24hの空白時間最大値
  discord_send(text)           Discord Webhook送信（env DISCORD_WEBHOOK_URL。未設定ならFalse）
  latest_run_url()             auto_post.yml の直近runのURL
nagaoka は外部ツールが毎時投稿するため、log_nagaoka_posted.jsonl（post_id/本文）と突合して
自動化側の投稿のみで計算する。全HTTPにtimeout=付き。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).parent
JST = timezone(timedelta(hours=9))
ACCTS = {"truth": "TRUTH", "nagaoka": "NAGAOKA", "masa": "MASA"}
POST_HOUR_START, POST_HOUR_END = 6, 23
GOAL_REACHED = {"truth": 50, "masa": 50, "nagaoka": 40}  # 自前投稿の本日目標
STALL_MIN = 75          # 投稿時間帯にこれ以上空いたら停止疑い
GH_REPO_DEFAULT = "truthbodysalon-lab/threads-auto-post"


def _load_env():
    try:
        for line in (BASE / ".env").read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


_load_env()


def _now() -> datetime:
    return datetime.now(JST)


def _parse_ts(ts: str):
    try:
        return datetime.strptime(ts.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z").astimezone(JST)
    except Exception:
        return None


def _own_ledger(acct: str):
    """自動化側の本日の投稿 (post_id集合, 本文先頭30字集合)。"""
    ids, heads = set(), set()
    f = BASE / f"log_{acct}_posted.jsonl"
    try:
        for line in f.read_text(encoding="utf-8").splitlines()[-400:]:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("post_id"):
                ids.add(str(d["post_id"]))
            t = (d.get("text") or "").strip()
            if t:
                heads.add(t[:30])
    except Exception:
        pass
    return ids, heads


def post_times_today(acct: str):
    """本日(JST)の非リプライ投稿時刻(昇順)。nagaokaは自前投稿のみ。API失敗時None。"""
    uid = os.environ.get(f"THREADS_USER_ID_{ACCTS[acct]}")
    tok = os.environ.get(f"THREADS_ACCESS_TOKEN_{ACCTS[acct]}")
    if not uid or not tok:
        return None
    today = _now().date()
    fields = "id,timestamp,text,is_reply"
    url = f"https://graph.threads.net/v1.0/{uid}/threads?fields={fields}&limit=100&access_token={tok}"

    def _fetch(u):
        with urllib.request.urlopen(u, timeout=20) as r:
            return json.loads(r.read())
    try:
        try:
            _fetch(url.replace("limit=100", "limit=1"))
        except Exception:
            # threads_read_replies 無しトークン: is_reply抜きで継続（リプライ込み概算）
            url = url.replace(",is_reply", "")
        own_ids, own_heads = _own_ledger(acct) if acct == "nagaoka" else (None, None)
        times = []
        for _ in range(3):
            d = _fetch(url)
            stop = False
            for p in d.get("data", []):
                if p.get("is_reply"):
                    continue
                dt = _parse_ts(p.get("timestamp", ""))
                if dt is None:
                    continue
                if dt.date() != today:
                    stop = True
                    continue
                if own_ids is not None:
                    head = (p.get("text") or "").strip()[:30]
                    if str(p.get("id")) not in own_ids and head not in own_heads:
                        continue
                times.append(dt)
            nxt = d.get("paging", {}).get("next")
            if stop or not nxt:
                break
            url = nxt
        return sorted(times)
    except Exception as e:
        print(f"stall_check {acct}: API取得失敗 {e}")
        return None


def _window_start(now: datetime) -> datetime:
    return now.replace(hour=POST_HOUR_START, minute=0, second=0, microsecond=0)


def in_post_window(now: datetime = None) -> bool:
    now = now or _now()
    return POST_HOUR_START <= now.hour < POST_HOUR_END


def last_post_gap_minutes(acct: str):
    """投稿時間帯内で、最後の自前投稿(本日6時より前なら6時起点)から現在までの経過分。
    時間帯外は0。API失敗時None。"""
    now = _now()
    if not in_post_window(now):
        return 0.0
    times = post_times_today(acct)
    if times is None:
        return None
    if len(times) >= GOAL_REACHED.get(acct, 50):
        return 0.0  # 本日の目標到達後の停止は正常（誤検知防止）
    ref = max(times[-1], _window_start(now)) if times else _window_start(now)
    return round((now - ref).total_seconds() / 60, 1)


def max_gap_today(acct: str):
    """本日6時JST起点〜現在の最大投稿間隔(分)。失敗時None。"""
    now = _now()
    times = post_times_today(acct)
    if times is None:
        return None
    pts = [_window_start(now)] + [t for t in times if t >= _window_start(now)] + [now]
    return round(max((b - a).total_seconds() / 60 for a, b in zip(pts, pts[1:])), 1)


# ── GitHub ─────────────────────────────────────────────
def _gh_api(path: str):
    repo = os.environ.get("GH_REPO", GH_REPO_DEFAULT)
    url = f"https://api.github.com/repos/{repo}/{path}"
    tok = os.environ.get("GH_PAT", "")
    if tok:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}",
                                                   "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    gh = shutil.which("gh") or "/opt/homebrew/bin/gh"
    r = subprocess.run([gh, "api", f"repos/{repo}/{path}"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:120])
    return json.loads(r.stdout)


def keeper_status():
    """auto_post.yml 常駐runの状態。
    {'in_progress': 稼働中数, 'queued': 待機数, 'max_gap_min_24h': 直近24hで常駐runが無かった最大空白(分),
     'ok': API取得成否}。常駐runは「稼働中、または20分以上動いた完了run」とみなす（skip即終了を除外）。"""
    try:
        now = datetime.now(timezone.utc)
        since = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        d = _gh_api(f"actions/workflows/auto_post.yml/runs?per_page=100&created=%3E%3D{since}")
        runs = d.get("workflow_runs", [])
        ip = sum(1 for r in runs if r.get("status") == "in_progress")
        qd = sum(1 for r in runs if r.get("status") in ("queued", "waiting", "pending"))
        ivs = []
        for r in runs:
            st = r.get("run_started_at") or r.get("created_at")
            if not st or r.get("status") in ("queued", "waiting", "pending"):
                continue
            s = datetime.strptime(st, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if r.get("status") == "in_progress":
                e = now
            else:
                e = datetime.strptime(r["updated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if (e - s).total_seconds() < 20 * 60:
                    continue
            ivs.append((s, e))
        ivs.sort()
        cur = now - timedelta(hours=24)
        worst = 0.0
        for s, e in ivs:
            if s > cur:
                worst = max(worst, (s - cur).total_seconds() / 60)
            cur = max(cur, e)
        worst = max(worst, (now - cur).total_seconds() / 60)
        return {"in_progress": ip, "queued": qd, "max_gap_min_24h": round(worst, 1), "ok": True}
    except Exception as e:
        print(f"stall_check keeper_status失敗: {e}")
        return {"in_progress": -1, "queued": -1, "max_gap_min_24h": None, "ok": False}


def latest_run_url() -> str:
    try:
        d = _gh_api("actions/workflows/auto_post.yml/runs?per_page=1")
        return (d.get("workflow_runs") or [{}])[0].get("html_url", "")
    except Exception:
        return ""


def stall_message(acct: str, gap: float, keeper: dict) -> str:
    if keeper.get("ok") and keeper["in_progress"] == 0 and keeper["queued"] == 0:
        cause = "常駐停止（常駐runが0本）"
    elif keeper.get("ok"):
        cause = "選択ループ/例外（常駐は稼働中なのに投稿が出ていない）"
    else:
        cause = "不明（GitHub API取得失敗）"
    url = latest_run_url()
    return (f"🚨Threads {acct}: 最後の投稿から{int(gap)}分。原因候補: {cause}。"
            f"直近run: {url or '取得不可'}")


def discord_send(text: str) -> bool:
    """Discord Webhook送信。env DISCORD_WEBHOOK_URL 未設定ならFalse（呼び出し側で別途記録）。"""
    hook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not hook:
        return False
    try:
        req = urllib.request.Request(hook, data=json.dumps({"content": text[:1900]}).encode(),
                                     method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "threads-stall-check"})
        urllib.request.urlopen(req, timeout=20)
        return True
    except Exception as e:
        print(f"discord_send失敗: {e}")
        return False


if __name__ == "__main__":
    for a in ACCTS:
        print(a, "gap=", last_post_gap_minutes(a), "max_gap_today=", max_gap_today(a))
    print("keeper=", keeper_status())
