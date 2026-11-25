#!/usr/bin/env python3
"""長岡市の地域ネタ型(nagaoka_local_templates.py)の検査（python3 test_nagaoka_local.py）。2026-11-25"""
import os, sys, re
os.environ.setdefault("SEGMENT_REGISTRY_DRY", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_remix as g
import inspector
import nagaoka_local_templates as n
from daily_budget import front_n

fails = []
firsts = {}
for vid, months, text in n.NAGAOKA_LOCAL_TRUTH:
    f = text.split("\n")[0]
    for r in n.validate(text):
        fails.append(f"型NG {vid}: {r}")
    ok, why = inspector.inspect_post(text, "truth")
    if not ok:
        fails.append(f"検品NG {vid}: {why}")
    if g._is_ng(text) or g._is_seitai_reserve_violation(text):
        fails.append(f"既存ガードNG {vid}: {f}")
    if any(ph in text for ph in g.NAGAOKA_PHRASES):
        fails.append(f"決まり文句 {vid}: {f}")
    if re.search(r"\d+人以上|改善率|必ず|絶対", text):
        fails.append(f"実績・保証の表現 {vid}: {f}")
    if f in firsts:
        fails.append(f"1行目重複 {vid}: {f}（既出 {firsts[f]}）")
    firsts[f] = vid

# 冬(12〜1月)は1日6本、原稿が少ない月でも3本は出せる
for m in range(1, 13):
    per = len(n.eligible(m)) // g.NAGAOKA_LOCAL_REUSE_DAYS
    if per < 3 or (m in (12, 1) and per < 6):
        fails.append(f"{m}月の原稿不足: 1日{per}本ぶん")

# キューに入るか・決まり文句が残っていないか・導線が前半から押し出されていないか
g.TODAY = "2026-12-10"
import daily_budget
daily_budget._today_jst = lambda: "2026-12-10"   # 20本の日として測る（11/26から20本）
front = front_n("truth")
for _ in range(3):
    posts = g.generate_30_posts()[:front]
    local = [p for p in posts if n.is_local_post(p)]
    if len(local) < 6:
        fails.append(f"前半{front}本中の地域ネタ型が{len(local)}本（6本のはず）")
    if any(ph in p for p in posts for ph in g.NAGAOKA_PHRASES):
        fails.append("truthに決まり文句の差し込みが残っている")
    if len({p.split(chr(10))[0] for p in posts}) != len(posts):
        fails.append("前半に1行目の重複")
    if not any("beauty.hotpepper.jp" in p for p in posts):
        fails.append("ホットペッパー予約の案内が前半から押し出された")
    if not any("lin.ee/qbRbPAm" in p for p in posts):
        fails.append("頭痛LINEの案内が前半から押し出された")
    if not any("lin.ee/GwQ0FSx" in p for p in posts):
        fails.append("整体LINEの案内が前半から押し出された")
    if not any("zutsu-shindan" in p for p in posts):
        fails.append("頭痛タイプ診断の案内が前半から押し出された")
    if not any("長岡駅" in p for p in posts):
        fails.append("アクセスの案内が前半から押し出された")

# nagaoka アカウントは今回の変更の対象外（地域ネタ型を入れない）
if any(n.is_local_post(p) for p in g.generate_40_nagaoka_posts()):
    fails.append("nagaokaに地域ネタ型が入った（対象はtruthだけ）")

if fails:
    print("FAIL")
    for x in fails:
        print(" -", x)
    sys.exit(1)
print(f"OK: 地域ネタ型 {len(n.NAGAOKA_LOCAL_TRUTH)}本・全検査通過")
