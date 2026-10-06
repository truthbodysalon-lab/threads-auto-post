#!/usr/bin/env python3
"""uplink(uplink0117)の閲覧200以上の新着をプールへ追加する（Macのみ・毎朝 daily-marketing-automation から実行）。

uplinkのトークンは ~/threads-uplink/threads_config.json にあり、GitHub Secretsには無い（=CIから読めない）ため
プール更新はMac側。既存エントリ(status含む)は保持し、新しい1行目だけ queued で追加する。
使い方:
  python3 uplink_repost_refresh.py --push          # 直近8日を取得→判定→追加→git push
  python3 uplink_repost_refresh.py --from-file F   # 取得済みJSON([{id,text,views,timestamp}])から投入（初期シード用）
  python3 uplink_repost_refresh.py --dry           # 追加せず件数だけ表示
"""
import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import uplink_repost as ur

BASE = Path(__file__).parent
CFG = Path.home() / "threads-uplink" / "threads_config.json"
API = "https://graph.threads.net/v1.0"


def fetch_recent(days: int) -> list:
    tok = json.loads(CFG.read_text(encoding="utf-8"))["access_token"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    posts, url = [], f"{API}/me/threads"
    params = {"fields": "id,text,timestamp,media_type", "limit": 100, "access_token": tok}
    done = False
    while url and not done and len(posts) < 4000:
        r = requests.get(url, params=params, timeout=30)
        params = None
        d = r.json()
        if "data" not in d:
            print(f"ERR 取得失敗 status={r.status_code}")
            sys.exit(1)
        for p in d["data"]:
            ts = datetime.strptime(p["timestamp"].replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
            if ts < cutoff:
                done = True
                break
            posts.append(p)
        print(f"fetched {len(posts)}", flush=True)
        url = d.get("paging", {}).get("next")
        time.sleep(0.2)

    def ins(p):
        for _ in range(2):
            try:
                r = requests.get(f"{API}/{p['id']}/insights", params={"metric": "views", "access_token": tok}, timeout=30).json()
                p["views"] = {x["name"]: x["values"][0]["value"] for x in r.get("data", [])}.get("views")
                return p
            except Exception:
                time.sleep(1)
        p["views"] = None
        return p

    out = []
    with ThreadPoolExecutor(8) as ex:
        for i, p in enumerate(ex.map(ins, posts)):
            out.append(p)
            if i % 50 == 0:
                print(f"insights {i}/{len(posts)}", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--from-file")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    if a.push:
        subprocess.run(["git", "pull", "-q", "--rebase", "--autostash", "-X", "theirs", "origin", "main"], cwd=BASE, timeout=120)
    if a.from_file:
        src = json.loads(Path(a.from_file).read_text(encoding="utf-8"))
    else:
        if not CFG.exists():
            print("SKIP: uplinkトークンなし（Mac以外の環境）")
            return
        src = fetch_recent(a.days)

    pool = ur.load_pool()
    known_ids = {i["id"] for i in pool}
    pool_firsts = [ur._norm_first(i["text"]) for i in pool]
    posted_firsts = ur.masa_posted_firstlines()
    cand = [p for p in src if (p.get("views") or 0) >= ur.MIN_VIEWS and p.get("text") and p["id"] not in known_ids]
    added = 0
    stat = {"judge": 0, "既出": 0, "近似": 0}
    for p in sorted(cand, key=lambda p: -p["views"]):
        t = p["text"].strip()
        first = ur._norm_first(t)
        if first in posted_firsts or first in pool_firsts:
            stat["既出"] += 1
            continue
        if ur.judge(t):
            stat["judge"] += 1
            continue
        if ur.near_duplicate(first, pool_firsts):
            stat["近似"] += 1
            continue
        pool.append({"id": p["id"], "text": t, "views": p["views"], "uplink_timestamp": p.get("timestamp", ""),
                     "status": "queued", "posted_at": None, "masa_post_id": None})
        pool_firsts.append(first)
        added += 1
    print(f"候補{len(cand)} → 追加{added}（除外 {stat}）／プールqueued {ur.queued_count(pool)}")
    if a.dry or not added:
        return
    ur.save_pool(pool)
    if a.push:
        subprocess.run(["git", "add", "uplink_repost_pool.json"], cwd=BASE, timeout=60)
        subprocess.run(["git", "commit", "-q", "-m", f"chore: uplink_repost_pool +{added}件 [skip ci]"], cwd=BASE, timeout=60)
        for _ in range(3):
            if subprocess.run(["git", "push", "-q"], cwd=BASE, timeout=120).returncode == 0:
                break
            subprocess.run(["git", "pull", "-q", "--rebase", "--autostash", "-X", "theirs", "origin", "main"], cwd=BASE, timeout=120)


if __name__ == "__main__":
    main()
