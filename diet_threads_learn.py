#!/usr/bin/env python3
"""ダイエットThreads(@truth_nagaoka_daietto) の閲覧データを毎日集めて、投稿の作り方を自動で直す＋100万閲覧の進み具合を報告する。

2026-11-07 院長指示「Thredsの投稿に関してはデータを分析して改善改良して。１ヶ月で閲覧１００万を目標にして実行して」。

毎朝（schedule_table.json の diet-threads-learn）:
  1. 直近30日の自分の投稿（Grok Bot の分も含む）と、それぞれの閲覧・いいね・返信・再投稿・引用を Threads API から取る
  2. API投稿（log_diet_posted.jsonl に形式・テーマ・枠がある）の閲覧を、形式・テーマ・枠ごとに平均して重みにする
     （本数が少ないうちは事前の値に寄せる＝ベイズ平均。どの選択肢も最低15%は残して試し続ける）
  3. 直近14日でよく読まれた投稿の書き出しを「書き方の参考」として diet_threads_weights.json に入れる（diet_threads.py が読む）
  4. 1日の閲覧・直近30日の合計・100万までの進み具合を diet_threads_metrics.jsonl に残し、Discord に報告する
失敗しても投稿は止めない（重みファイルが古いまま使われるだけ）。
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import diet_threads as D

BASE = Path(__file__).resolve().parent
WEIGHTS_FILE = BASE / "diet_threads_weights.json"
METRICS_FILE = BASE / "diet_threads_metrics.jsonl"
GOAL_30D = 1_000_000          # 1ヶ月（30日）の閲覧目標
GOAL_START = "2026-11-07"      # 目標の起点（この日から30日）
PRIOR_N = 5                   # ベイズ平均の重み（この本数ぶん全体平均に寄せる）
FLOOR = 0.15                  # どの選択肢も最低この割合は残す（試し続ける）
METRICS = "views,likes,replies,reposts,quotes"


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def fetch_posts(token: str, uid: str, days: int = 30) -> list[dict]:
    since = datetime.now(D.JST) - timedelta(days=days)
    url = f"{D.BASE_URL}/{uid}/threads?" + urllib.parse.urlencode(
        {"fields": "id,text,timestamp,is_quote_post", "limit": 100, "access_token": token})
    out = []
    while url and len(out) < 2000:
        d = _get(url)
        for p in d.get("data", []):
            t = D.parse_ts(p.get("timestamp", ""))
            if t and t < since:
                return out
            out.append(p)
        url = (d.get("paging") or {}).get("next")
    return out


def add_insights(token: str, posts: list[dict]) -> None:
    for p in posts:
        try:
            d = _get(f"{D.BASE_URL}/{p['id']}/insights?" + urllib.parse.urlencode({"metric": METRICS, "access_token": token}))
            for x in d.get("data", []):
                p[x["name"]] = (x.get("values") or [{}])[0].get("value", 0)
        except Exception as e:  # noqa: BLE001
            p["insights_error"] = str(e)[:100]


def smoothed(groups: dict, overall: float) -> dict:
    return {k: (sum(v) + PRIOR_N * overall) / (len(v) + PRIOR_N) for k, v in groups.items()}


def to_weights(scores: dict, keys: list[str]) -> dict:
    vals = {k: max(scores.get(k, 0.0), 0.0) for k in keys}
    tot = sum(vals.values())
    if tot <= 0:
        return {k: round(1 / len(keys), 3) for k in keys}
    raw = {k: v / tot for k, v in vals.items()}
    floor = FLOOR * 2 / len(keys)        # 最低割合（2択なら15%・5択なら6%）を確保して、低い選択肢も試し続ける
    w = {k: max(floor, r) for k, r in raw.items()}
    s = sum(w.values())
    return {k: round(v / s, 3) for k, v in w.items()}


def clean_example(text: str) -> str:
    """お手本から、今のルールで使えない語（栄養講座・講師・習った 等）を含む文を落とす"""
    keep = [x for x in D._sentences(text.replace("\n", "")) if not D.inspect_text(x, False) or
            all(e.startswith(("短すぎる",)) for e in D.inspect_text(x, False))]
    return "".join(keep).strip()


def learn(posts: list[dict], entries: list[dict], now: datetime) -> dict:
    by_id = {e.get("id"): e for e in entries if e.get("id")}
    old_enough = [p for p in posts if (D.parse_ts(p.get("timestamp", "")) or now) <= now - timedelta(hours=20)]
    api = [(p, by_id[p["id"]]) for p in old_enough if p["id"] in by_id and by_id[p["id"]].get("type") != "ba"]
    overall = statistics.mean([p.get("views", 0) for p, _ in api]) if api else 0.0
    g_fmt, g_type, g_slot = defaultdict(list), defaultdict(list), defaultdict(list)
    for p, e in api:
        v = p.get("views", 0)
        g_fmt[e.get("fmt") or D.classify_format(p.get("text") or "")].append(v)
        if e.get("type") in D.TYPES:
            g_type[e["type"]].append(v)
        g_slot[e.get("slot")].append(v)
    recent = [p for p in old_enough if (D.parse_ts(p.get("timestamp", "")) or now) >= now - timedelta(days=14)]
    top = sorted(recent, key=lambda p: -p.get("views", 0))[:5]
    examples = [{"views": p.get("views", 0), "text": clean_example(p.get("text") or "")[:160]} for p in top if p.get("views", 0) > 0]
    examples = [e for e in examples if len(e["text"]) >= 10]
    return {
        "updated": now.isoformat(timespec="seconds"),
        "n_api_posts": len(api), "api_mean_views": round(overall, 1),
        "fmt": to_weights(smoothed(g_fmt, overall), list(D.FORMATS)),
        "type": to_weights(smoothed(g_type, overall), D.TYPES),
        "slot_mean_views": {k: round(v, 1) for k, v in sorted(smoothed(g_slot, overall).items())},
        "examples": examples,
    }


def summarize(posts: list[dict], now: datetime) -> dict:
    day0 = (now - timedelta(days=1)).date()
    v_day = sum(p.get("views", 0) for p in posts if (D.parse_ts(p.get("timestamp", "")) or now).date() == day0)
    start = datetime.strptime(GOAL_START, "%Y-%m-%d").replace(tzinfo=D.JST)
    v_goal = sum(p.get("views", 0) for p in posts if (D.parse_ts(p.get("timestamp", "")) or now) >= start)
    days = max(1, (now - start).days)
    return {"date": now.strftime("%Y-%m-%d"), "posts_30d": len(posts), "views_30d": sum(p.get("views", 0) for p in posts),
            "views_yesterday_posts": v_day, "views_since_goal": v_goal, "goal_days_elapsed": days,
            "pace_needed_per_day": round(GOAL_30D / 30), "engagement_30d": sum(p.get(k, 0) for p in posts for k in ("likes", "replies", "reposts", "quotes"))}


def report(m: dict, w: dict) -> str:
    left = max(0, GOAL_30D - m["views_since_goal"])
    rest_days = max(1, 30 - m["goal_days_elapsed"])
    best = max(w["type"], key=w["type"].get) if w.get("type") else "-"
    ex = (w.get("examples") or [{}])[0]
    return ("📊 閲覧の進み具合（目標: 30日で100万）\n"
            f"・昨日の投稿の閲覧: {m['views_yesterday_posts']:,}（必要ペース 1日{m['pace_needed_per_day']:,}）\n"
            f"・目標開始からの合計: {m['views_since_goal']:,} / 1,000,000（残り{left:,}・あと{rest_days}日なら1日{left // rest_days:,}）\n"
            f"・直近30日: 投稿{m['posts_30d']}本・閲覧{m['views_30d']:,}・反応{m['engagement_30d']}\n"
            f"・今日の重み: 形式 {w.get('fmt')} / いちばん読まれているテーマ「{best}」\n"
            f"・最近いちばん読まれた投稿（{ex.get('views', 0)}回）: {(ex.get('text') or '')[:50]}")


def main() -> int:
    D.load_env()
    token = os.environ.get("THREADS_ACCESS_TOKEN_DIET", "").strip()
    uid = os.environ.get("THREADS_USER_ID_DIET", "").strip()
    if not token:
        print("トークン未設定のためスキップ")
        return 0
    now = datetime.now(D.JST)
    try:
        posts = fetch_posts(token, uid)
        add_insights(token, posts)
    except Exception as e:  # noqa: BLE001
        D.notify(f"閲覧データの取得に失敗（投稿は続けます）: {str(e)[:150]}")
        return 0
    w = learn(posts, D.read_log(), now)
    WEIGHTS_FILE.write_text(json.dumps(w, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    m = summarize(posts, now)
    with open(METRICS_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(m, ensure_ascii=False) + "\n")
    msg = report(m, w)
    print(msg)
    if "--no-notify" not in sys.argv:
        D.notify(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
