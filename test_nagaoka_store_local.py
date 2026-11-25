"""@truth_nagaoka の地域ネタ型（本業外の地元の話題・2026-11-26 masa指示「1日5本」）の検査。"""
import os
import sys

os.environ["SEGMENT_REGISTRY_DRY"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import inspector  # noqa: E402
import nagaoka_local_templates as truth_local  # noqa: E402
import nagaoka_store_local as m  # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


firsts = [t.split("\n")[0] for _, _, _, t in m.NAGAOKA_STORE_LOCAL]
check(len(firsts) == len(set(firsts)), "1行目が重複している")
truth_firsts = {t.split("\n")[0] for items in truth_local.KINDS.values() for _, _, t in items}
check(not (set(firsts) & truth_firsts), "truthの長岡市の投稿と1行目が同じ原稿がある")
for vid, topic, months, text in m.NAGAOKA_STORE_LOCAL:
    r = m.validate(text)
    check(not r, f"{vid}: {r}")
    ok, reasons = inspector.inspect_post(text, "nagaoka")
    check(ok, f"{vid}: 検品NG {reasons}")
    for w in ("店名", "株式会社", "http", "予約"):
        check(w not in text, f"{vid}: {w} が入っている")
for month in range(1, 13):
    check(len(m.eligible(month)) >= 36, f"{month}月の原稿が{len(m.eligible(month))}本（36本以上ほしい）")

import generate_remix as g  # noqa: E402
from daily_budget import front_n  # noqa: E402

front = g.generate_40_nagaoka_posts()[:front_n("nagaoka")]
hits = [i for i, t in enumerate(front) if m.is_store_local_post(t)]
check(len(front) == 30, f"nagaokaの前半が{len(front)}本（30本のはず）")
check(len(hits) >= g.NAGAOKA_STORE_LOCAL_PER_DAY - 1, f"前半30本に地域ネタ型が{len(hits)}本")
topics = [m.topic_of(front[i]) for i in hits]
check(len(topics) == len(set(topics)), f"同じ日に同じ話題が2本: {topics}")

if fails:
    print("FAIL")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print(f"PASS 地域ネタ型 {len(m.NAGAOKA_STORE_LOCAL)}本・前半30本中{len(hits)}本（位置{hits}）")
