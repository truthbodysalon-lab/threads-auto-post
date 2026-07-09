#!/usr/bin/env python3
"""
跳ねた投稿（breakout）の構造分析用データを作る。

従来の改善ループは「テンプレの言い回し調整」で、跳ねる投稿の再現に失敗していた。
新方式: 実閲覧TOP30（全文）とWORST20を breakout_<acct>.json に出力し、
毎朝のヒーロー投稿生成（変数無しの完成原稿）の学習源にする。

使い方: python3 analyze_breakout.py <acct> [limit=200]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
from insights import fetch_posts_with_metrics  # noqa: E402


def main(acct: str, limit: int = 200):
    posts = fetch_posts_with_metrics(acct, limit=limit)
    valid = [p for p in posts if p.get("text")]
    ranked = sorted(valid, key=lambda p: p.get("views", 0), reverse=True)
    vs = sorted((p.get("views", 0) for p in valid), reverse=True)
    n = len(vs)
    out = {
        "acct": acct,
        "sample": n,
        "median_views": vs[n // 2] if n else 0,
        "p90_views": vs[max(0, n // 10 - 1)] if n else 0,
        # 跳ねた投稿は「全文」を保持（構造・展開・締めまで学ぶため）
        "top30": [{"views": p.get("views", 0), "likes": p.get("like_count", 0),
                   "date": p.get("timestamp", "")[:10], "text": p.get("text", "")}
                  for p in ranked[:30]],
        "worst20": [{"views": p.get("views", 0), "text": p.get("text", "")[:100]}
                    for p in ranked[-20:]],
    }
    (BASE / f"breakout_{acct}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"breakout_{acct}.json 更新: {n}投稿 中央値{out['median_views']} "
          f"P90 {out['p90_views']} TOP1 {ranked[0].get('views',0) if ranked else 0}")


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "truth"
    lim = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    main(a, lim)
