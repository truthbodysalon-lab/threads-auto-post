#!/usr/bin/env python3
"""
改善ラボの分析エンジン。
実測閲覧から「勝ち投稿・勝ちフック・負けテンプレ」を抽出し、
AIオフィスの改善ラボ（localhost:8787）が読むJSONを書き出す。

使い方:
  python3 lab_analyze.py <acct>        # 分析してJSON出力＋~/ai-office/lab_<acct>.json 保存
"""
from __future__ import annotations

import datetime
import json
import re
import statistics
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

from insights import fetch_posts_with_metrics  # noqa: E402

OUT_DIR = Path.home() / "ai-office"
# 導線投稿は閲覧が低くて当然なので改善対象から外す（意図的トレードオフ）
# 導線判定はURL・固定フレーズも見る（2026-08-18修正）。旧版は全角「LINE」しか見ておらず、
# lin.ee リンクのLINE登録CTAを「負け投稿」として改善ラボに出してしまい、社長が👎を押しても
# 仕様上削除されない→また出る、という無限ループの原因になっていた。
FUNNEL_HINTS = ("ホットペッパー", "ご予約", "LINE", "友だち追加", "診断", "アクセス", "駐車場",
                "プロフィール", "lin.ee", "beauty.hotpepper.jp", "timerex", "無料でお届け",
                "セルフケア講座", "登録した人に", "1タップ")


def _first_line(t: str) -> str:
    return (t or "").strip().split("\n")[0][:60]


def analyze(acct: str) -> dict:
    posts = fetch_posts_with_metrics(acct, limit=100) or []
    rows = []
    for p in posts:
        txt = p.get("text") or p.get("caption") or ""
        v = p.get("views") or p.get("view_count") or 0
        if not txt or not v:
            continue
        dt = str(p.get("date") or p.get("timestamp") or p.get("posted_at") or "")[:10]
        rows.append({"text": txt, "views": int(v), "first": _first_line(txt), "date": dt,
                     "funnel": any(h in txt for h in FUNNEL_HINTS)})
    if not rows:
        return {"acct": acct, "error": "no data"}

    organic = [r for r in rows if not r["funnel"]] or rows
    med = int(statistics.median([r["views"] for r in organic]))
    top = sorted(organic, key=lambda r: -r["views"])[:5]
    bottom = sorted(organic, key=lambda r: r["views"])[:5]

    # 勝ちフック（1文目の型）: 上位20%の1文目から共通パターンを抽出
    n_top = max(3, len(organic) // 5)
    winners = sorted(organic, key=lambda r: -r["views"])[:n_top]
    hook_kinds = {
        "問いかけ": lambda s: s.rstrip().endswith(("？", "?", "か。", "ませんか。")),
        "断定・逆説": lambda s: ("じゃない" in s or "ではありません" in s or "は間違い" in s),
        "数字入り": lambda s: bool(re.search(r"\d", s)),
        "リスト予告": lambda s: bool(re.search(r"\d+(選|つ|個|パターン)", s)),
        "引用・セリフ": lambda s: s.strip().startswith("「"),
        "呼びかけ": lambda s: ("あなた" in s or "みなさん" in s or "経営者" in s),
    }
    hooks = []
    for name, fn in hook_kinds.items():
        w = [r for r in winners if fn(r["first"])]
        allm = [r for r in organic if fn(r["first"])]
        if allm:
            hooks.append({
                "name": name,
                "n": len(allm),
                "avg": int(statistics.mean([r["views"] for r in allm])),
                "win_rate": round(len(w) / max(1, len(allm)) * 100),
            })
    hooks.sort(key=lambda h: -h["avg"])

    out = {
        "acct": acct,
        "updated": datetime.datetime.now().isoformat(timespec="minutes"),
        "median": med,
        "n_posts": len(rows),
        "n_organic": len(organic),
        "top": [{"first": r["first"], "views": r["views"], "date": r["date"], "text": r["text"][:280]} for r in top],
        "bottom": [{"first": r["first"], "views": r["views"], "date": r["date"], "text": r["text"][:280]} for r in bottom],
        "hooks": hooks,
    }
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"lab_{acct}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "masa"
    print(json.dumps(analyze(a), ensure_ascii=False, indent=1)[:2000])
