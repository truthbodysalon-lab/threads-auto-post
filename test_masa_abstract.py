# -*- coding: utf-8 -*-
"""masa抽象度チェック(_is_abstract_masa)と固定原稿のテスト（2026-10-07）。
実行: cd ~/threads-auto-post && python3 test_masa_abstract.py"""
import os
import re
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

    def test_question_without_target_is_allowed(self):
        # 2026-11-26 masa指示「【対象】へ質問です…この内容はいらない」: 宛先の必須チェックは廃止
        t = "インスタで『機械が苦手』と、手が止まっていませんか？\n[COMMENT]\n① 「＋」を押す\n② 写真を選ぶ"
        self.assertNotIn("対象なし問いかけ", g._abstract_reasons_masa(t))
        self.assertNotIn("対象なし問いかけ", g._abstract_reasons_masa("あなたはどうですか？"))
        t2 = "リールを怖がっている人へ。\n[COMMENT]\n① 「＋」→「リール」を押す\n② 写真を3枚選ぶ"
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

    def test_uplink_style(self):
        """2026-10-17 uplink型: 1行目は宛名・質問にしない／質問で締めない／本文にリストがある。"""
        texts = list(g.INSTA_EASY_TEMPLATES) + list(g.HAKASE_TEMPLATES)
        texts += [m.build_gen_post(si, h) for h in g.INSTA_HURDLES for si in range(len(g.INSTA_GEN_STRUCTURES))]
        for t in texts:
            body = t.split("\n[COMMENT]\n")[0]
            lines = [x for x in body.split("\n") if x.strip()]
            self.assertFalse(re.search(r"[？?]$", lines[0]), t)
            self.assertFalse(re.search(r"[？?]$", lines[-1]), t)
            self.assertGreaterEqual(len(lines), 5, t)   # 1行目・対象・リスト3行以上・締め

    def test_present_cta(self):
        self.assertEqual(len(g.PRESENT_CTA_TEMPLATES), 20)
        firsts = [t.split("\n")[0] for t in g.PRESENT_CTA_TEMPLATES]
        self.assertEqual(len(firsts), len(set(firsts)))
        for t in g.PRESENT_CTA_TEMPLATES:
            self._all_ok(t)
            body = t.split("\n[COMMENT]\n")[0]
            self.assertIn("『インスタ』", body)
            self.assertNotIn("lin.ee", t)      # URLは本文にもコメントにも置かない（auto_postが投稿後に足す）
            self.assertNotRegex(t, r"円|申し?込|決済|割引|面談")

    def test_hypotheses_ip(self):
        # IP01-70は10/07の書き直し原稿(IP_REWRITES)と一致すること。IP71以降は日曜の仮説生成
        # (daily-marketing-automation 手順7)が連番で追記する仕様なので、本数は増えてよい（品質検査は同じ）。
        hyp = g._load_segment_hypotheses()["hypotheses"]
        ips = [h for h in hyp if h["id"].startswith("IP") and h.get("status") != "archived"]
        self.assertGreaterEqual(len(ips), len(m.IP_REWRITES))
        self.assertTrue(set(m.IP_REWRITES) <= {h["id"] for h in ips})
        self.assertEqual(len(ips), len({h["id"] for h in ips}), "IP仮説のID重複")
        for h in ips:
            for p in h["posts"]:
                self._all_ok(p["text"])
                if h["id"] in m.IP_REWRITES:
                    self.assertEqual(p["text"], m.IP_REWRITES[h["id"]][p["hook"]])

    def test_bridge_fallbacks(self):
        for _rx, t in m.MASA_BRIDGE_FALLBACKS:
            self.assertEqual(g._abstract_reasons_masa_comment(t), [], t)
        self.assertIn("プロフィール", m.masa_bridge_fallback("プロフィールの話"))

    def test_queue_front50(self):
        for _ in range(3):
            posts = g.generate_30_masa_posts()
            front = posts[:g.FRONT_N]   # 2026-11-11 1日20本化: 前半20本
            self.assertEqual([p for p in front if g._is_abstract_masa(p)], [])
            self.assertGreaterEqual(sum(1 for p in front if g._is_insta_theme(p)), g.INSTA_THEME_TARGET)


class TestAutoPostGate(unittest.TestCase):
    def test_gate_and_bridge(self):
        import auto_post as a
        ok, reasons = a.inspect_before_post("院長へ。\n大切なのは継続です。", "masa")   # 2026-11-26〜 宛先なしの問いかけはNGにしない
        self.assertFalse(ok)
        self.assertTrue(reasons[0].startswith("masa抽象度NG"))
        self.assertTrue(a.inspect_before_post("あなたはどうですか？", "masa")[0])
        self.assertTrue(a.inspect_before_post("院長へ。\n大切なのは継続です。", "truth")[0])  # masa以外には適用しない
        self.assertTrue(a.inspect_before_post(GOOD, "masa")[0])
        self.assertTrue(a._masa_bridge_bad("大切なのは継続です。"))
        self.assertFalse(a._masa_bridge_bad("① 「プロフィールを編集」を押す\n② 自己紹介を書く"))
        self.assertIn("プロフィール", a._bridge_default("プロフィールの話", "masa"))
        self.assertEqual(a._bridge_default("x", "truth"), a.ACCOUNTS["truth"]["bridge_text"])



class TestOwnerRules20261126(unittest.TestCase):
    """2026-11-26 masa指示: コメントは話し言葉・専門用語は言い換え・字数の上限なし・本人指定テーマ。"""

    def test_spoken_comment(self):
        t = ("ハッシュタグを増やしても、予約は増えません。\n[COMMENT]\n① 投稿のキャプション欄を開く\n② ハッシュタグを2つ付ける\n"
             "③ シェアを押す\n\n今日は、次の投稿のハッシュタグを3つに絞ってください（3分）。")
        q = g._masa_spoken(t)
        self.assertEqual(q.split("\n")[0], t.split("\n")[0])
        self.assertNotIn("①", q)
        self.assertIn("まず、投稿のキャプション欄を開く。", q)
        self.assertIn("最後に、シェアを押す。", q)
        self.assertIn("だから今日は、次の投稿のハッシュタグを3つに絞ってください。", q)
        self.assertIn("3分あれば終わります。", q)
        self.assertEqual(g._abstract_reasons_masa(q), [])

    def test_comment_lead_names_subject(self):
        # 2026-11-26 masa指摘「『難しくないです。3つだけ。』だと主語が分からない」: 1行目で何の話かを名指しする
        texts = list(g.INSTA_EASY_TEMPLATES) + list(g.HAKASE_TEMPLATES) + list(m.WINNING_HOOK_TEMPLATES)
        texts += [m.build_gen_post(0, h) for h in m.INSTA_HURDLES]
        for t in texts:
            c = g._masa_finalize([t])[0].split("[COMMENT]", 1)[1].strip("\n")
            first = c.split("\n")[0]
            self.assertNotRegex(first, r"^(難しくないです|やることは\d|手順は\d|\dつだけ)", t)
            self.assertRegex(first, r"\S{2,}(?:は|なら)、", first)
        # 表に無いコメントには主語のない1行目を足さない
        q = g._masa_spoken("x\n[COMMENT]\n① インサイトを開く\n② 保存数を見る")
        self.assertTrue(q.split("[COMMENT]", 1)[1].strip("\n").startswith("まず、"))

    def test_spoken_keeps_lists_with_heading(self):
        t = "見出し\n[COMMENT]\n【中身は3つです】\n① A\n② B\n③ C"
        self.assertIn("①", g._masa_spoken(t))

    def test_jargon_plain_except_first_line(self):
        t = "LTVの話。\n集客×成約×LTVと、MEO対策。\nhttps://example.com/LTV"
        q = g._masa_plain_jargon(t)
        self.assertTrue(q.startswith("LTVの話。"))
        self.assertIn("集客×成約×通い続けてもらう力と、グーグルマップで上に出る工夫。", q)
        self.assertIn("https://example.com/LTV", q)

    def test_no_250_cap(self):
        self.assertTrue(g._masa_len_ok("あ" * 400))
        self.assertFalse(g._masa_len_ok("あ" * 501))

    def test_owner_topics(self):
        import masa_owner_topics as ot
        texts = [t for L in (ot.DOUGA_KOUKOKU, ot.CATCH_COPY, ot.AI_INSTA) for _, t in L]
        self.assertGreaterEqual(len(ot.DOUGA_KOUKOKU), 8)
        self.assertGreaterEqual(len(ot.CATCH_COPY), 8)
        firsts = [t.split("\n")[0] for t in texts]
        self.assertEqual(len(firsts), len(set(firsts)))
        known = {t.split("\n")[0] for t in list(g.INSTA_EASY_TEMPLATES) + list(g.HAKASE_TEMPLATES)}
        self.assertFalse(set(firsts) & known, "既存原稿と1行目が同じ")
        for t in texts:
            TestFixedContent._all_ok(self, t)
            self.assertNotIn("①", t)          # コメントは話し言葉
            self.assertNotRegex(t, r"長岡|トゥルース|lin\.ee|http")

    def test_queue_front_is_mostly_instagram(self):
        posts = g.generate_30_masa_posts()[:g.FRONT_N]
        n = sum(1 for p in posts if g._is_insta_theme(p))
        self.assertGreaterEqual(n, g.INSTA_THEME_TARGET, f"前半{len(posts)}本中Instagram{n}本")
        self.assertEqual([p for p in posts if g._is_abstract_masa(p)], [])

    def test_hour_report(self):
        import masa_hour_report as hr
        posts = [{"timestamp": f"2026-11-2{d}T13:00:00+0000", "text": "x", "views": 50} for d in range(6)]   # 22時台
        posts += [{"timestamp": f"2026-11-2{d}T23:00:00+0000", "text": "y", "views": 5} for d in range(6)]   # 8時台
        r = hr.build(posts)
        self.assertEqual(r["best_hours"][0], "22時台")
        self.assertEqual(r["hours"]["08"]["avg_views"], 5.0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
