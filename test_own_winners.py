#!/usr/bin/env python3
"""own_winners.py の判定キーのテスト（python3 test_own_winners.py）"""
import sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
import own_winners as ow

CTX = {"fl": {ow.comp("自動の一行目です、これは長い")}, "b80": {ow.comp("自動の本文その1")[:80]},
       "skel": __import__("collections").Counter({"Sが続く人は要注意です": 3}), "posted_b80": set(),
       "templates": [], "prefixes": set()}

def test_exact_body():
    assert ow.is_automated("自動の本文\nその1", CTX) == "exact_body80"

def test_line1_and_own():
    assert ow.is_automated("自動の一行目です、これは長い\n続き", CTX) == "exact_line1"
    assert ow.is_automated("こういう道を車で走るの好きです！\n共感してくれる人いますかー？？", CTX) is None

def test_skeleton_masks_symptom():
    assert ow.is_automated("肩こりが続く人は要注意です\nなにか", CTX) == "skeleton"

def test_tail_sig():
    assert ow.is_automated("本人が書いた風\n長岡市で整体院を兄妹で運営しています", CTX) == "tail_sig"

def test_recycled_goes_back_to_own():
    posts = [{"id": str(i), "text": "使い回し本文です、台帳に無い\nあ", "is_reply": False} for i in range(3)]
    ctx = dict(CTX, b80={ow.comp(posts[0]["text"])[:80]})
    rows = ow.classify_posts(posts, ctx)
    assert all(r["auto"] is None and r["recycled"] for r in rows)

def test_replies_excluded():
    assert ow.classify_posts([{"id": "1", "text": "x", "is_reply": True}], CTX) == []

def test_winners_threshold_and_unique():
    rows = [{"id": "a", "text": "A\nb", "auto": None, "views": 300, "timestamp": "2026-10-01T00:00", "media_type": "IMAGE"},
            {"id": "b", "text": "A\nb", "auto": None, "views": 100, "timestamp": "2026-10-02T00:00", "media_type": "IMAGE"},
            {"id": "c", "text": "C", "auto": None, "views": 100, "timestamp": "2026-10-02T00:00", "media_type": "TEXT_POST"},
            {"id": "d", "text": "D", "auto": "exact_body80", "views": 900, "timestamp": "2026-10-02T00:00", "media_type": "TEXT_POST"}]
    w = ow.pick_winners(rows, 100)
    assert [x["id"] for x in w] == ["a"] and w[0]["repeats_in_window"] == 2

def test_labels():
    assert "画像・動画つき" in ow.type_labels("夫婦に間違えられます。兄妹です。", "IMAGE")
    assert "リスト型" in ow.type_labels("・肩が重い\n・首が回らない", "TEXT_POST")

if __name__ == "__main__":
    n = 0
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v(); n += 1
    print(f"OK {n} tests")
