#!/usr/bin/env python3
"""type_templates.py の全テンプレが既存ガードを通るかの検査（python3 test_type_templates.py）。2026-10-24"""
import os, sys, re
os.environ.setdefault("SEGMENT_REGISTRY_DRY", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_remix as g
from type_templates import TYPE_POOLS

fails = []
firsts = {}
exp = {("truth", "ZUTSUYAKU"): 30, ("truth", "MAE_MAZU"): 30, ("nagaoka", "MAE_MAZU"): 30,
       ("nagaoka", "WEATHER"): 20, ("nagaoka", "QUESTION"): 12}
for key, n in exp.items():
    if len(TYPE_POOLS.get(key, [])) < n:
        fails.append(f"{key}: {len(TYPE_POOLS.get(key, []))}本 < {n}本")
for (acct, seg), pool in TYPE_POOLS.items():
    for t in pool:
        f = t.split("\n")[0]
        if not g._type_post_valid(t, acct):
            fails.append(f"検査NG {acct}/{seg}: {f}")
        if g._is_incoherent(t) or g._is_ng(t) or g._is_seitai_reserve_violation(t):
            fails.append(f"既存ガードNG {acct}/{seg}: {f}")
        if f in firsts:
            fails.append(f"1行目重複 {acct}/{seg}: {f} (既出 {firsts[f]})")
        firsts[f] = f"{acct}/{seg}"
        if seg == "ZUTSUYAKU" and not re.search("薬|鎮痛|痛み止め", f):
            fails.append(f"ZUTSUYAKUの1行目に薬語なし: {f}")
        if seg == "MAE_MAZU" and not re.search("前に、まず", f):
            if not re.search("まず|前に", f):
                fails.append(f"MAE_MAZUの型でない: {f}")
# 既存プール(お客様の声・習慣)との1行目重複なし
for t in list(g.CUSTOMER_VOICE_TEMPLATES_TRUTH) + list(g.CUSTOMER_VOICE_TEMPLATES_NAGAOKA):
    if t.split("\n")[0] in firsts:
        fails.append(f"お客様の声と重複: {t.split(chr(10))[0]}")
# truthのお客様の声は伸びない系を外している
for t in g._voice_pool_for("truth", g.CUSTOMER_VOICE_TEMPLATES_TRUTH):
    if re.search("目の奥|肩こり|マッサージ", t.split("\n")[0]):
        fails.append(f"truthの声に伸びない系が残存: {t.split(chr(10))[0]}")
print("OK" if not fails else "\n".join(fails))
sys.exit(1 if fails else 0)
