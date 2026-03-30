#!/usr/bin/env python3
"""
QualityAgent — 生成済み投稿をブランドガイドラインでフィルタリング・スコアリング
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent

BRAND_RULES = {
    "truth": {
        "ng_patterns": [
            r"ステップ\s+英語",  # テーブル残骸
            r"チェックポイント",
            r"←|→",
            r"\s{3,}",           # 複数スペース（テーブル行）
            r"http\S+.*http\S+", # URL2つ以上
        ],
        "min_len": 20,
        "max_len": 500,
        "required_tone": [],  # チェック項目（将来拡張用）
    },
    "masa": {
        "ng_patterns": [
            r"ステップ\s+英語",
            r"チェックポイント",
            r"←|→",
            r"\s{3,}",
            r"http\S+.*http\S+",
        ],
        "min_len": 20,
        "max_len": 500,
    },
}


def score(post: str, acct: str) -> int:
    """投稿品質スコア（0-100）を返す"""
    rules = BRAND_RULES.get(acct, BRAND_RULES["truth"])
    s = 100

    # 長さチェック
    if len(post) < rules["min_len"]:
        return 0
    if len(post) > rules["max_len"]:
        s -= 20

    # NGパターンチェック
    for pat in rules["ng_patterns"]:
        if re.search(pat, post):
            return 0

    # 改行の使い方
    lines = [l for l in post.splitlines() if l.strip()]
    if len(lines) < 2:
        s -= 15  # 単調な1行
    if len(lines) > 20:
        s -= 10  # 長すぎる

    # 記号の乱用
    if post.count("！") + post.count("!") > 5:
        s -= 10

    # URL過多
    urls = re.findall(r"http\S+", post)
    if len(urls) > 1:
        s -= 30

    return max(0, s)


def filter_posts(posts: list[str], acct: str, min_score: int = 40) -> list[str]:
    """スコア閾値以下を除外"""
    filtered = []
    dropped  = 0
    for p in posts:
        s = score(p, acct)
        if s >= min_score:
            filtered.append(p)
        else:
            dropped += 1
    if dropped:
        print(f"  [QualityAgent] {dropped}本を品質フィルタで除外（{len(filtered)}本通過）",
              file=sys.stderr)
    return filtered


def dedup(posts: list[str]) -> list[str]:
    """先頭80文字で重複除去"""
    seen, result = set(), []
    for p in posts:
        key = p[:80].strip()
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


def check(posts: list[str], acct: str) -> list[str]:
    """フィルタ + 重複除去をまとめて実行"""
    posts = filter_posts(posts, acct)
    posts = dedup(posts)
    return posts


if __name__ == "__main__":
    acct = sys.argv[1] if len(sys.argv) > 1 else "truth"
    sample = [
        "肩こりが改善する人はここが違う。\n\n原因を特定している。\n\n・どこが歪んでいるのか\n・どの習慣が悪いのか\n\nこれが改善の第一歩。",
        "ステップ  英語  役割  ポイント",  # NG
        "ok",  # 短すぎ NG
    ]
    result = check(sample, acct)
    print(f"通過: {len(result)}本")
    for p in result:
        print(f"  [{score(p, acct)}点] {p[:50]}")
