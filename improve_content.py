#!/usr/bin/env python3
"""
自律的コンテンツ改善の安全層。
新しい多様テンプレ候補を「ルール検証」してから variety_templates.py に追記する。
質を担保しつつプールを増やし続けるための土台（サブエージェントが毎朝呼ぶ）。

検証内容（1つでも外れたら不採用）:
  - 数回埋めて変数が全て埋まる（{xxx} 残りなし）
  - NGワードを含まない（generate_remix._is_ng）
  - 1文目が地域名/実績/自己紹介で始まらない（truth/nagaoka）
  - 既存テンプレと完全一致でない
  - 長すぎない（500字以内）

プール上限（質の希釈・肥大防止）: 各アカウント最大120件。超過時は古い順に削る。

使い方:
  python3 improve_content.py <account> <candidates.json>
    candidates.json = ["テンプレ1", "テンプレ2", ...]
  → 採用数/不採用数を表示し、variety_templates.py を更新
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 未確認の年数実績（「10年以上診て」「20年の経験」等）を弾く正規表現
_YEAR_CLAIM = re.compile(r"\d+\s*年(以上|間)?\s*[^\n]{0,6}(診|施術|経験|やっ|続け|運営)")

BASE = Path(__file__).parent
VFILE = BASE / "variety_templates.py"
POOL_CAP = 120
POOL_VAR = {"truth": "TRUTH_VARIETY", "nagaoka": "NAGAOKA_VARIETY", "masa": "MASA_VARIETY"}

import generate_remix as g


def _fill(acct: str, tmpl: str) -> str:
    """アカウントに応じてテンプレの変数を埋める（検証用サンプル）"""
    if acct == "masa":
        import random
        tips = g.MASA_TIPS[:3] if len(g.MASA_TIPS) >= 3 else (g.MASA_TIPS + g.MASA_TIPS + g.MASA_TIPS)[:3]
        bads = g.MASA_BAD_HABITS[:3] if len(g.MASA_BAD_HABITS) >= 3 else (g.MASA_BAD_HABITS * 3)[:3]
        return (tmpl
                .replace("{topic}", g.MASA_TOPICS[0])
                .replace("{point}", g.MASA_POINTS[0])
                .replace("{tip1}", tips[0]).replace("{tip2}", tips[1]).replace("{tip3}", tips[2])
                .replace("{bad1}", bads[0]).replace("{bad2}", bads[1]).replace("{bad3}", bads[2]))
    return g.fill(tmpl)


def validate(acct: str, tmpl: str, existing: set) -> bool:
    if not isinstance(tmpl, str) or not tmpl.strip():
        return False
    if tmpl in existing:
        return False
    if len(tmpl) > 480:
        return False
    if _YEAR_CLAIM.search(tmpl):   # 未確認の年数実績はNG
        return False
    # 複数回埋めて検証（フィルのランダム性をカバー）
    for _ in range(4):
        s = _fill(acct, tmpl)
        if "{" in s or "}" in s:      # 変数未埋め
            return False
        if g._is_ng(s):                # NGワード
            return False
        first = s.split("\n")[0].strip()
        if acct in ("truth", "nagaoka") and first.startswith(("長岡市", "施術実績", "実績", "整体師の", "整体師です")):
            return False
    return True


def load_pools() -> dict:
    import importlib, variety_templates as vt
    importlib.reload(vt)
    return {a: list(getattr(vt, POOL_VAR[a], [])) for a in POOL_VAR}


def write_pools(pools: dict):
    lines = [
        "# 多様性拡張テンプレ（1文目の被りを減らし、7日重複ガード通過数を増やす）",
        "# improve_content.py により自律追記される。手動編集も可。",
        "",
    ]
    for a, var in POOL_VAR.items():
        lines.append(f"{var} = " + json.dumps(pools[a], ensure_ascii=False, indent=4))
        lines.append("")
    VFILE.write_text("\n".join(lines), encoding="utf-8")


def main():
    if len(sys.argv) < 3:
        print("usage: python3 improve_content.py <account> <candidates.json>")
        sys.exit(1)
    acct = sys.argv[1].lower()
    if acct not in POOL_VAR:
        print(f"unknown account: {acct}")
        sys.exit(1)
    candidates = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))

    pools = load_pools()
    existing = set(pools[acct])
    accepted, rejected = [], 0
    for c in candidates:
        if validate(acct, c, existing | set(accepted)):
            accepted.append(c)
        else:
            rejected += 1

    if accepted:
        pools[acct] = pools[acct] + accepted
        # 上限超過分は古い順に削る（質の希釈・肥大防止）
        if len(pools[acct]) > POOL_CAP:
            pools[acct] = pools[acct][-POOL_CAP:]
        write_pools(pools)

    print(f"[{acct}] 採用 {len(accepted)}件 / 不採用 {rejected}件 / プール総数 {len(pools[acct])}件")


if __name__ == "__main__":
    main()
