#!/usr/bin/env python3
"""ヒーロー繰越（2026-10-20 オーナー指示「生成したヒーローは全て投稿する」）。

毎朝 hero_<acct>.json を新しく書いた「直後・git add の前」に実行する:
    cd /Users/mt112/threads-auto-post && python3 hero_carry.py

前回コミット版(HEAD)の hero_<acct>.json にあった「未投稿のヒーロー」(posts + carry)を、
新ファイルの "carry" に引き継ぐ。投稿済み・3日超・今日の posts と同じ1文目のものは除く。
同じ日に2回実行しても結果は変わらない（冪等）。ネットワーク通信なし。
generate_remix._load_hero_posts が carry を先頭に読み込み、auto_post.get_next_post が優先投稿する。
"""
from __future__ import annotations
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
from generate_remix import _hero_key, _hero_posted_keys, HERO_GRACE_DAYS  # noqa: E402

CARRY_MAX = 10   # 繰越の上限（溜まり続けて当日分を圧迫しないため）


def _head_version(acct: str):
    r = subprocess.run(["git", "show", f"HEAD:hero_{acct}.json"], cwd=BASE,
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def process(acct: str) -> str:
    f = BASE / f"hero_{acct}.json"
    if not f.exists():
        return f"{acct}: hero ファイルなし"
    cur = json.loads(f.read_text(encoding="utf-8"))
    prev = _head_version(acct)
    if not prev:
        return f"{acct}: 前回版なし→繰越なし"
    today = date.today()
    posted = _hero_posted_keys(acct)
    cur_keys = {_hero_key(p) for p in cur.get("posts", []) if isinstance(p, str)}
    cutoff = (today - timedelta(days=HERO_GRACE_DAYS)).isoformat()

    cands = []   # (元の日付, 本文)
    if prev.get("date") == cur.get("date"):
        # 同じ日の再実行: 前回版の carry をそのまま基準にする（posts は今日分なので繰越対象外）
        for c in prev.get("carry", []) or []:
            if isinstance(c, dict) and isinstance(c.get("text"), str):
                cands.append((c.get("date", cur.get("date")), c["text"]))
            elif isinstance(c, str):
                cands.append((cur.get("date"), c))
    else:
        for c in prev.get("carry", []) or []:
            if isinstance(c, dict) and isinstance(c.get("text"), str):
                cands.append((c.get("date", prev.get("date")), c["text"]))
            elif isinstance(c, str):
                cands.append((prev.get("date"), c))
        for p in prev.get("posts", []) or []:
            if isinstance(p, str):
                cands.append((prev.get("date"), p))

    carry, seen = [], set()
    for d, t in cands:
        k = _hero_key(t)
        if not k or k in seen or k in posted or k in cur_keys:
            continue
        if not d or str(d) < cutoff:
            continue
        seen.add(k)
        carry.append({"date": d, "text": t})
    carry = carry[:CARRY_MAX]
    cur["carry"] = carry
    f.write_text(json.dumps(cur, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"{acct}: 繰越 {len(carry)} 本（今日分 {len(cur.get('posts', []))} 本）"


if __name__ == "__main__":
    for a in ("truth", "nagaoka", "masa"):
        try:
            print(process(a))
        except Exception as e:  # 繰越に失敗しても当日分の投稿は止めない
            print(f"{a}: 繰越失敗(無視して続行): {type(e).__name__}: {e}")
