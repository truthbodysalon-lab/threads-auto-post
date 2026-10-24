# -*- coding: utf-8 -*-
"""勝ち型1行目の判定と転載の優先順位、勝ち型固定原稿20本のテスト（2026-10-24）。
実行: cd ~/threads-auto-post && python3 test_uplink_hooks.py"""
import os
import sys
import unittest

os.environ.setdefault("SEGMENT_REGISTRY_DRY", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uplink_repost as ur  # noqa: E402


class TestHooks(unittest.TestCase):
    def test_hits(self):
        for t in ["売上が伸びない店ほど、これをやっています。\n本文",
                  "サロン経営で結果を出す人がやっているのは、たった3つ。",
                  "値引きしても、お客様は増えません。",
                  "【サロン経営で絶対必要な習慣9選】\n1.",
                  "売上を倍にするのに、新しいノウハウは要りません。",
                  "集客は2つだけ。"]:
            self.assertTrue(ur.is_winning_hook(t), t)

    def test_miss(self):
        for t in ["今日は天気がいいですね。", "本文の途中に\n【習慣9選】", "お客様は大切です。\nたった3つ"]:
            self.assertFalse(ur.is_winning_hook(t), t)

    def test_next_item_prefers_winner(self):
        items = [{"id": 1, "status": "queued", "text": "普通の投稿", "views": 900},
                 {"id": 2, "status": "queued", "text": "値引きしても、お客様は増えません。", "views": 200}]
        self.assertEqual(ur.next_item(items)["id"], 2)

    def test_daily_default(self):
        if "UPLINK_REPOST_DAILY" not in os.environ:
            self.assertEqual(ur.DAILY_N, 3)


class TestWinningTemplates(unittest.TestCase):
    def test_all(self):
        import generate_remix as g
        import masa_insta_content as m
        T = m.WINNING_HOOK_TEMPLATES
        self.assertEqual(len(T), 20)
        firsts = [t.split("\n")[0] for t in T]
        self.assertEqual(len(firsts), len(set(firsts)))
        all_first = {x.split("\n")[0] for x in list(g.INSTA_EASY_TEMPLATES) + list(g.HAKASE_TEMPLATES) + list(g.PRESENT_CTA_TEMPLATES)}
        self.assertFalse(set(firsts) & all_first)
        for t in T:
            self.assertTrue(ur.is_winning_hook(t), t)
            self.assertEqual(g._abstract_reasons_masa(t), [], t)
            self.assertFalse(g._is_ng(t), t)
            self.assertFalse(g._is_masa_sales_ng(t), t)
            self.assertFalse(g._is_masa_yokokoku_ng(t), t)
            self.assertTrue(g._inspect_ok(t, "masa"), t)
            self.assertTrue(g._masa_len_ok(t), t)
            self.assertLessEqual(len(g._masa_split_body_comment(t)[0]), 250, t)
            self.assertTrue(g._is_insta_theme(t), t)


if __name__ == "__main__":
    unittest.main()
