#!/usr/bin/env python3
"""HPBの空き状況をThreadsに画像で告知する（2026-10-14 masa指示）。

週2回・アカウントごとに違う曜日（truth=月・木／nagaoka=水・土。ワークフロー hpb_availability.yml）。
1) --render : HPB公開予約ページから空き状況を取得し images/hpb/<acct>_<日付>.jpg と本文を作る
   （取れなければ exit 1＝投稿しない。推測の空き状況は出さない）
2) ワークフローが画像を commit/push（raw URLで公開）
3) --post   : 画像投稿 → 返信コメントにHPB予約URL（本文にURLを入れないルール）→ 状態を記録

DRY_RUN=1 なら --post はAPIを呼ばない。R1: HTTPは全て timeout 付き（hpb_availability / auto_post / image_post）。
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import hpb_availability as ha

BASE = Path(__file__).resolve().parent
IMG_DIR = BASE / "images" / "hpb"
STATE = BASE / "hpb_availability_state.json"
RAW = "https://raw.githubusercontent.com/truthbodysalon-lab/threads-auto-post/main/images/hpb"
DRY_RUN = os.environ.get("DRY_RUN", "").strip().lower() not in ("", "0", "false")


def today():
    return datetime.now(ha.JST).strftime("%Y-%m-%d")


def load_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def notify(text):
    """masa本人への通知はDiscordのみ（LINE禁止）。失敗しても本処理は止めない"""
    hook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if DRY_RUN or not hook:
        print(f"[通知スキップ] {text}")
        return
    import urllib.request
    try:
        req = urllib.request.Request(hook, data=json.dumps({"content": text[:1900]}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "threads-hpb/1.0"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:  # noqa: BLE001
        print(f"Discord通知失敗: {e}")


def do_render(acct):
    handle = ha.ACCOUNTS[acct]["handle"]
    if load_state().get(acct, {}).get("date") == today():
        print(f"{handle} 本日は投稿済み")
        return 2
    try:
        coupon, avail, at = ha.fetch_availability(7)
    except Exception as e:  # noqa: BLE001
        notify(f"⚠️Threads空き状況告知({handle}): HPBの空き状況を取得できず見送り: {str(e)[:150]}")
        return 1
    rows = ha.summary(avail)
    if not any(r["n"] for r in rows):
        notify(f"ℹ️Threads空き状況告知({handle}): 7日間ネット予約の空きが無いため見送り")
        return 2
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{acct}_{datetime.now(ha.JST).strftime('%Y%m%d_%H%M')}.jpg"
    ha.render(rows, str(IMG_DIR / name), acct, coupon, at, "threads")
    (IMG_DIR / f"{acct}_pending.json").write_text(json.dumps({
        "image": name, "text": ha.caption_text(rows, "threads", acct),
        "open_days": [r["label"] for r in rows if r["n"]]}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"画像: images/hpb/{name}")
    return 0


def do_post(acct):
    from auto_post import ACCOUNTS, load_env, post_reply_to_threads
    from image_post import post_image
    handle = ha.ACCOUNTS[acct]["handle"]
    pend_f = IMG_DIR / f"{acct}_pending.json"
    if not pend_f.exists():
        print("投稿待ちなし")
        return 0
    pend = json.loads(pend_f.read_text(encoding="utf-8"))
    url = f"{RAW}/{pend['image']}"
    print(f"URL: {url}\n{pend['text']}")
    if DRY_RUN:
        return 0
    load_env()
    post_id = None
    for attempt in range(3):  # raw URLの反映待ち
        post_id = post_image(acct, url, pend["text"])
        if post_id:
            break
        time.sleep(30)
    if not post_id:
        notify(f"⚠️Threads空き状況告知({handle}): 画像投稿に失敗しました（{url}）")
        return 1
    time.sleep(5)
    try:
        post_reply_to_threads(acct, post_id, f"{ACCOUNTS[acct]['bridge_text']}\n{ha.RESERVE_URL}")
    except Exception as e:  # noqa: BLE001
        notify(f"⚠️Threads空き状況告知({handle}): 本投稿は成功、予約URLの返信に失敗: {str(e)[:120]}")
    st = load_state()
    st[acct] = {"date": today(), "post_id": post_id, "image": pend["image"]}
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    pend_f.unlink()
    notify(f"📅Threads空き状況告知({handle})を投稿しました: 空きあり {len(pend['open_days'])}/7日")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["render", "post"])
    ap.add_argument("--account", required=True, choices=list(ha.ACCOUNTS))
    a = ap.parse_args()
    sys.exit(do_render(a.account) if a.mode == "render" else do_post(a.account))


if __name__ == "__main__":
    main()
