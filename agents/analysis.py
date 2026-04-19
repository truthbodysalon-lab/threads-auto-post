#!/usr/bin/env python3
"""
AnalysisAgent — 投稿パフォーマンスを分析してパターン重みを自動更新
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

BASE  = Path(__file__).parent.parent
TODAY = date.today().strftime("%Y-%m-%d")

# パターン判定キーワード
PATTERN_SIGNATURES = {
    "aoi_style":      [r"はここが違う", r"これを作ることが大事", r"これだけ"],
    "hori_style":     [r"正直、.*差だけ", r"→.*→", r"変えたのは「"],
    "hook_one_line":  [r"^[^。\n]{5,20}[。]?\s*$"],  # 短い1行
    "quote_empathy":  [r"「.*」\nって", r"「.*」\nそう思", r"「.*」\nと思"],
    "education":      [r"①.*②.*③", r"◎.*\n◎", r"▶.*\n▶"],
    "story":          [r"お客様から", r"先日", r"気づいたこと"],
    "workmom":        [r"家事も育児も", r"ワーママ", r"お母さん"],
    "cta":            [r"https?://", r"予約はこちら", r"LINEから"],
    # nagaoka専用パターン
    "keisei_target":  [r"まだ我慢できる", r"軽症のうち", r"軽いうちに", r"まだ大丈夫", r"まだ薬を飲む"],
    "keisei_risk":    [r"慢性化する前", r"放置は禁物", r"月に2〜3回.*放置", r"我慢できる範囲"],
    "keisei_kyokan":  [r"大げさかな", r"後回しにして", r"病院に行くほど", r"整体に来るのは"],
    "hochi_risk":     [r"自律神経.*直結", r"薬物乱用頭痛", r"猫背.*内臓", r"眼精疲労.*めまい"],
}


def detect_pattern(post: str) -> str:
    """投稿テキストからパターンを推定"""
    for pat, sigs in PATTERN_SIGNATURES.items():
        for sig in sigs:
            if re.search(sig, post, re.MULTILINE):
                return pat
    return "other"


def load_posted_with_insights(acct: str) -> list[dict]:
    """posted.jsonl と insights を結合"""
    posted_file  = BASE / f"log_{acct}_posted.jsonl"
    insights_file = BASE / f"insights_{acct}.json"

    if not posted_file.exists():
        return []

    posted = [json.loads(l) for l in posted_file.read_text().splitlines() if l.strip()]

    # インサイトデータ（post_id → metrics）
    metrics_map = {}
    if insights_file.exists():
        try:
            data = json.loads(insights_file.read_text())
            for p in data.get("posts", []):
                metrics_map[p.get("id", "")] = {
                    "likes": p.get("like_count", 0),
                    "views": p.get("views", 0),
                    "replies": p.get("replies_count", 0),
                }
        except Exception:
            pass

    result = []
    for entry in posted:
        pid  = entry.get("post_id", "")
        text = entry.get("text", "")
        m = metrics_map.get(pid, {"likes": 0, "views": 0, "replies": 0})
        result.append({
            "date":    entry.get("date", ""),
            "text":    text,
            "pattern": detect_pattern(text),
            **m,
        })
    return result


def compute_pattern_weights(records: list[dict], base_weight: float = 1.0) -> dict:
    """パターン別の平均いいね数でウェイトを算出"""
    pattern_stats = defaultdict(lambda: {"likes": 0, "count": 0})

    for r in records:
        pat = r["pattern"]
        pattern_stats[pat]["likes"] += r["likes"]
        pattern_stats[pat]["count"] += 1

    avg_likes_per_pattern = {}
    for pat, s in pattern_stats.items():
        if s["count"] > 0:
            avg_likes_per_pattern[pat] = s["likes"] / s["count"]

    if not avg_likes_per_pattern:
        return {}

    overall_avg = sum(avg_likes_per_pattern.values()) / len(avg_likes_per_pattern)
    if overall_avg == 0:
        return {p: base_weight for p in avg_likes_per_pattern}

    # overall_avg を 1.0 として正規化（0.5〜2.0 にクランプ）
    weights = {}
    for pat, avg in avg_likes_per_pattern.items():
        w = avg / overall_avg
        weights[pat] = round(max(0.5, min(2.0, w)), 2)

    return weights


def update_feedback_weights(acct: str, weights: dict) -> bool:
    """feedback.json のパターン重みを更新"""
    fb_file = BASE / "feedback.json"
    try:
        fb = json.loads(fb_file.read_text()) if fb_file.exists() else {}
    except Exception:
        fb = {}

    current = fb.get("pattern_weights", {})
    updated = False

    for pat, w in weights.items():
        if pat in current and abs(current[pat] - w) > 0.1:
            current[pat] = w
            updated = True
        elif pat not in current:
            current[pat] = w
            updated = True

    if updated:
        fb["pattern_weights"] = current
        from datetime import datetime
        fb["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        fb_file.write_text(json.dumps(fb, ensure_ascii=False, indent=2))

    return updated


def run(acct: str, verbose: bool = False) -> dict:
    records = load_posted_with_insights(acct)

    # 過去30日に絞る
    cutoff = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    recent = [r for r in records if r.get("date", "") >= cutoff]

    if len(recent) < 5:
        print(f"  [AnalysisAgent] {acct}: データ不足（{len(recent)}件）— スキップ",
              file=sys.stderr)
        return {}

    weights = compute_pattern_weights(recent)
    updated = update_feedback_weights(acct, weights)

    if verbose:
        print(f"\n[AnalysisAgent] {acct} パターン分析（過去30日 {len(recent)}件）")
        for pat, w in sorted(weights.items(), key=lambda x: -x[1]):
            bar = "█" * int(w * 5)
            print(f"  {pat:<18} {w:.2f}  {bar}")
        if updated:
            print("  → feedback.json を更新しました")

    return weights


if __name__ == "__main__":
    acct = sys.argv[1] if len(sys.argv) > 1 else "truth"
    run(acct, verbose=True)
