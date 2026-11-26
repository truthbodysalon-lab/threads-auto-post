#!/usr/bin/env python3
"""頭痛Threads(@truth_nagaoka_zutsuu) の締め（CTA）の試し分けを、閲覧数で比べる（2026-11-26 masa指示「→他のCTAで試して」）。

log_zutsu_posted.jsonl の cta_id（L1〜L4=公式LINEの『診断』案内の言い方・F1/F2=フォロー・S1=保存・空=締めなし）ごとに、
直近14日の投稿の閲覧数（Threads API の views）を集め、zutsu_cta_report.json に平均・中央値・本数を書く。
起動: zutsu_threads.yml の 23:15 枠の最後（1日1回）。手で: python3 zutsu_cta_report.py
外部通信は Threads API の読み取りだけ（timeout付き・失敗した投稿は数えない）。投稿・削除はしない。
"""
from __future__ import annotations

import json
import os
import statistics
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

import zutsu_threads as D

OUT = Path(__file__).parent / "zutsu_cta_report.json"
DAYS = 14
MAX_CALLS = 150


def views_of(token: str, media_id: str) -> int | None:
    q = urllib.parse.urlencode({"metric": "views", "access_token": token})
    try:
        d = D._http(f"{D.BASE_URL}/{media_id}/insights?{q}")
    except Exception:
        return None
    for m in d.get("data") or []:
        if m.get("name") == "views":
            vals = m.get("values") or [{}]
            return int(vals[0].get("value") or 0)
    return None


def build(entries: list[dict], getter, now: datetime) -> dict:
    since = now - timedelta(days=DAYS)
    groups: dict[str, list[int]] = {}
    calls = 0
    for e in entries:
        ts = D.parse_ts(e.get("ts") or "")
        if not ts or ts < since or not e.get("id") or ts > now - timedelta(hours=12):
            continue   # 12時間たっていない投稿は閲覧がまだ伸びるので数えない
        if calls >= MAX_CALLS:
            break
        calls += 1
        v = getter(e["id"])
        if v is None:
            continue
        groups.setdefault(e.get("cta_id") or "なし", []).append(v)
    names = {**D.LINE_CTA_VARIANTS, **D.OTHER_CTA_VARIANTS, "なし": "締めなし"}
    rows = {k: {"posts": len(v), "avg_views": round(sum(v) / len(v), 1), "median_views": statistics.median(v),
                "text": names.get(k, "")} for k, v in sorted(groups.items())}
    return {"updated_at": now.strftime("%Y-%m-%d %H:%M"), "days": DAYS, "by_cta": rows,
            "note": "本数が5本未満の締めは比べる材料が足りない。良かった言い方は zutsu_threads.py の LINE_CTA_VARIANTS / OTHER_CTA_VARIANTS に残す"}


def main() -> int:
    D.load_env()
    token = os.environ.get("THREADS_ACCESS_TOKEN_ZUTSU", "").strip()
    if not token:
        print("トークン未設定のため集計しない")
        return 0
    now = datetime.now(D.JST)
    result = build(D.read_log(), lambda mid: views_of(token, mid), now)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for k, r in result["by_cta"].items():
        print(f"{k}: {r['posts']}本 平均{r['avg_views']} 中央値{r['median_views']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
