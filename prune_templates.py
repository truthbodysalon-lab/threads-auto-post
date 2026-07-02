#!/usr/bin/env python3
"""
実測閲覧データで「負けテンプレ」を自動削除する修正エンジン。

2ヶ月で月100万閲覧の軌道（views_goal.json のランプ）に乗せるための
「分析→修正」の修正側。毎朝サブエージェントEが軌道遅れのアカウントに実行する。

仕組み:
1. Threads APIから直近100投稿の実閲覧(views)を取得
2. variety_templates の各テンプレの1文目を正規表現化（{var}→ワイルドカード）し、
   実投稿とマッチング → テンプレごとの平均閲覧を実測
3. サンプル2件以上 かつ 平均閲覧がアカウント中央値の40%未満 のテンプレを削除
   （1回の実行で最大15件まで。プールを空にしない下限60件ガード付き）

使い方:
  python3 prune_templates.py <acct>          # 実行（削除する）
  python3 prune_templates.py <acct> --dry    # 判定のみ表示
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

from improve_content import load_pools, write_pools, POOL_CAP  # noqa: E402
from insights import fetch_posts_with_metrics  # noqa: E402

MAX_PRUNE_PER_RUN = 15
MIN_POOL = 60           # これ以下にはしない（多様性の下限）
MIN_SAMPLES = 2         # 実測2件未満のテンプレは判定しない（データ不足）
LOSER_RATIO = 0.4       # 中央値の40%未満 = 負け


def _tmpl_regex(tmpl: str) -> re.Pattern | None:
    """テンプレ1文目 → 実投稿1文目とのマッチ用正規表現（{var}はワイルドカード）。"""
    fl = tmpl.split("\n")[0].strip()
    if len(fl.replace(" ", "")) < 6:
        return None
    pat = re.escape(fl)
    pat = re.sub(r"\\\{[a-z0-9_]+\\\}", ".{1,20}", pat)
    try:
        return re.compile("^" + pat + "$")
    except re.error:
        return None


def prune(acct: str, dry: bool = False) -> dict:
    posts = fetch_posts_with_metrics(acct, limit=100)
    scored = [(p.get("text", "").split("\n")[0].strip(), p.get("views", 0))
              for p in posts if p.get("text")]
    views_all = sorted(v for _, v in scored)
    if len(views_all) < 20:
        return {"error": f"実測データ不足（{len(views_all)}件）。削除は行わない"}
    median = views_all[len(views_all) // 2]
    threshold = max(10, int(median * LOSER_RATIO))

    pools = load_pools()
    pool = pools.get(acct, [])
    results = []           # (tmpl, n_samples, avg_views)
    for tmpl in pool:
        rx = _tmpl_regex(tmpl)
        if rx is None:
            continue
        hits = [v for fl, v in scored if rx.match(fl)]
        if len(hits) >= MIN_SAMPLES:
            results.append((tmpl, len(hits), sum(hits) // len(hits)))

    losers = sorted([r for r in results if r[2] < threshold], key=lambda r: r[2])
    can_remove = max(0, len(pool) - MIN_POOL)
    losers = losers[:min(MAX_PRUNE_PER_RUN, can_remove)]

    if not dry and losers:
        remove = {t for t, _, _ in losers}
        pools[acct] = [t for t in pool if t not in remove]
        write_pools(pools)

    return {
        "acct": acct, "median": median, "threshold": threshold,
        "measured": len(results), "pruned": len(losers),
        "pool": f"{len(pool)}→{len(pool)-len(losers)}",
        "losers": [{"first_line": t.split(chr(10))[0][:40], "n": n, "avg_views": av}
                   for t, n, av in losers],
        "dry": dry,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("truth", "nagaoka", "masa"):
        print("usage: python3 prune_templates.py <truth|nagaoka|masa> [--dry]")
        sys.exit(1)
    r = prune(sys.argv[1], dry="--dry" in sys.argv)
    print(json.dumps(r, ensure_ascii=False, indent=2))
