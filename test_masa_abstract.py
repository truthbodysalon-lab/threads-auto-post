# -*- coding: utf-8 -*-
"""masa抽象度チェック(_is_abstract_masa)と固定原稿のテスト（2026-10-07）。
実行: cd ~/threads-auto-post && python3 test_masa_abstract.py"""
import os
import sys
import unittest

os.environ.setdefault("SEGMENT_REGISTRY_DRY", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import generate_remix as g  # noqa: E402
import masa_insta_content as m  # noqa: E402

GOOD = ("院長へ質問です。\n投稿に必要な文字数は、何文字だと思いますか？\n答えは、20文字で足ります。\n[COMMENT]\n"
        "① 写真を選んだら、キャプション欄を開く\n② 「今日は肩の相談が続きました」と1行書く\n③ ハッシュタグを2つ付けて、シェアを押す\n\n"
        "今日は、キャプション欄に20文字だけ書いて、1分で出してください。")


class TestAbstractMasa(unittest.TestCase):
    def test_good_passes(self):
        self.assertEqual(g._abstract_reasons_masa(GOOD), [])

    def test_question_without_target(self):
        t = "インスタで『機械が苦手』と、手が止まっていませんか？\n[COMMENT]\n① 「＋」を押す\n② 写真を選ぶ"
        self.assertIn("対象なし問いかけ", g._abstract_reasons_masa(t))
        self.assertIn("対象なし問いかけ", g._abstract_reasons_masa("あなたはどうですか？"))

    def test_bare_address(self):
        t = "リールを怖がっている人へ。\n[COMMENT]\n① 「＋」→「リール」を押す\n② 写真を3枚選ぶ"
        self.assertIn("宛名が「人」だけ", g._abstract_reasons_masa(t))
        t2 = t.replace("リールを怖がっている人へ", "リールを怖がっているサロンオーナーへ")
        self.assertEqual(g._abstract_reasons_masa(t2), [])

    def test_quoted_question_is_not_a_question(self):
        t = "サロン経営者から、よく聞かれます。\n「AIに任せたら、冷たくなりませんか？」\nなりません。"
        self.assertNotIn("対象なし問いかけ", g._abstract_reasons_masa(t))

    def test_abstract_words(self):
        for w in ("大切なのは継続です。", "意識してみてください。", "工夫が必要です。", "価値を届けましょう。", "SNSは「場所取り」より「信頼貯金」"):
            r = g._abstract_reasons_masa("院長へ。\n" + w)
            self.assertTrue(any(x.startswith("抽象語") for x in r), w)

    def test_abstract_comment_old_style(self):
        # 実投稿で見つかった悪い例（2026-10-07診断）
        for t in ("院長へ質問です。インスタで手が止まっていませんか？\n\n全部やらなくていい。1つに絞れば続きます。\n\n今日は、やることを、投稿1つだけにしてください。",
                  "インスタで「数字が苦手」。\n[COMMENT]\n見る数字は、保存と来店の2つだけです。\n今月の来店数を1つだけ数えてください。"):
            self.assertTrue(g._is_abstract_masa(t), t)

    def test_comment_needs_feature_and_steps(self):
        no_feature = "インスタの院長へ。\n[COMMENT]\n① 朝に考える\n② 夜に直す"
        self.assertIn("コメントが抽象的（手順/具体例＋機能名なし）", g._abstract_reasons_masa(no_feature))
        self.assertFalse(g._masa_concrete_ok("プロフィールを見直します。"))
        self.assertTrue(g._masa_concrete_ok("① 「プロフィールを編集」を押す\n② 自己紹介を書く"))
        self.assertTrue(g._masa_concrete_ok("リールの冒頭1秒に「肩が上がらない人へ」と入れる"))

    def test_comment_helper(self):
        self.assertTrue(g._is_abstract_masa_comment("大切なのは続けることです。"))
        self.assertFalse(g._is_abstract_masa_comment("① インサイトを開く\n② リーチを見る"))

    def test_split_matches_auto_post(self):
        self.assertEqual(g._masa_split_body_comment("a\n[COMMENT]\nb"), ("a", "b"))
        self.assertEqual(g._masa_split_body_comment("a\n\nb\nc"), ("a", "b\nc"))
        self.assertEqual(g._masa_split_body_comment("a"), ("a", ""))


class TestFixedContent(unittest.TestCase):
    def _all_ok(self, text):
        self.assertEqual(g._abstract_reasons_masa(text), [], text)
        self.assertFalse(g._is_ng(text), text)
        self.assertFalse(g._is_masa_sales_ng(text), text)
        self.assertFalse(g._is_masa_yokokoku_ng(text), text)
        self.assertTrue(g._inspect_ok(text, "masa"), text)
        self.assertTrue(g._masa_len_ok(text), text)
        self.assertTrue(g._is_insta_theme(text), text)

    def test_easy_and_hakase(self):
        self.assertEqual(len(g.INSTA_EASY_TEMPLATES), 80)
        for t in list(g.INSTA_EASY_TEMPLATES) + list(g.HAKASE_TEMPLATES):
            self._all_ok(t)
        firsts = [t.split("\n")[0] for t in list(g.INSTA_EASY_TEMPLATES) + list(g.HAKASE_TEMPLATES)]
        self.assertEqual(len(firsts), len(set(firsts)), "1行目重複")

    def test_gen_posts(self):
        for h in g.INSTA_HURDLES:
            for si in range(len(g.INSTA_GEN_STRUCTURES)):
                self._all_ok(m.build_gen_post(si, h))
        for _ in range(50):
            self._all_ok(g.generate_insta_easy_gen_post())

    def test_hypotheses_ip(self):
        hyp = g._load_segment_hypotheses()["hypotheses"]
        ips = [h for h in hyp if h["id"].startswith("IP") and h.get("status") != "archived"]
        self.assertEqual(len(ips), 70)
        for h in ips:
            for p in h["posts"]:
                self._all_ok(p["text"])
                self.assertEqual(p["text"], m.IP_REWRITES[h["id"]][p["hook"]])

    def test_bridge_fallbacks(self):
        for _rx, t in m.MASA_BRIDGE_FALLBACKS:
            self.assertEqual(g._abstract_reasons_masa_comment(t), [], t)
        self.assertIn("プロフィール", m.masa_bridge_fallback("プロフィールの話"))

    def test_queue_front50(self):
        for _ in range(3):
            posts = g.generate_30_masa_posts()
            front = posts[:50]
            self.assertEqual([p for p in front if g._is_abstract_masa(p)], [])
            self.assertGreaterEqual(sum(1 for p in front if g._is_insta_theme(p)), 32)


class TestAutoPostGate(unittest.TestCase):
    def test_gate_and_bridge(self):
        import auto_post as a
        ok, reasons = a.inspect_before_post("あなたはどうですか？", "masa")
        self.assertFalse(ok)
        self.assertTrue(reasons[0].startswith("masa抽象度NG"))
        self.assertTrue(a.inspect_before_post("あなたはどうですか？", "truth")[0])  # masa以外には適用しない
        self.assertTrue(a.inspect_before_post(GOOD, "masa")[0])
        self.assertTrue(a._masa_bridge_bad("大切なのは継続です。"))
        self.assertFalse(a._masa_bridge_bad("① 「プロフィールを編集」を押す\n② 自己紹介を書く"))
        self.assertIn("プロフィール", a._bridge_default("プロフィールの話", "masa"))
        self.assertEqual(a._bridge_default("x", "truth"), a.ACCOUNTS["truth"]["bridge_text"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
