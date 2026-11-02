#!/usr/bin/env python3
"""diet_threads.py のテスト(ネットワークは使わない)。実行: python3 test_diet_threads.py"""
import json, os, random, sys, tempfile, unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diet_threads as D

JST = D.JST
NOW = datetime(2026, 11, 2, 15, 0, tzinfo=JST)
GOOD = "「食べてないのにやせない」と悩む方、多いです。まずは朝ごはんの内容を見直してみませんか。"


def ts(dt):
    return dt.astimezone(D.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")


def post(minutes_ago, text="ふつうの投稿です。今日もお疲れさまでした。", now=NOW):
    return {"id": "x", "text": text, "timestamp": ts(now - timedelta(minutes=minutes_ago))}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "log.jsonl"
        self._env = dict(os.environ)
        os.environ.pop("THREADS_ACCESS_TOKEN_DIET", None)
        os.environ.pop("THREADS_USER_ID_DIET", None)
        self._load_env = D.load_env
        self._notify = D.notify
        D.load_env = lambda: None      # .env を読まない

    def tearDown(self):
        D.load_env = self._load_env
        D.notify = self._notify
        os.environ.clear(); os.environ.update(self._env)
        self.tmp.cleanup()

    def run_(self, **kw):
        kw.setdefault("log_path", self.log)
        kw.setdefault("now", NOW)
        kw.setdefault("rng", random.Random(1))
        return D.run(**kw)


class TestTokenSkip(Base):
    def test_empty_token_skips_without_any_call(self):
        def boom(*a, **k): raise AssertionError("呼ばれてはいけない")
        r = self.run_(slot="15:00", fetch=boom, publisher=boom, generator=boom)
        self.assertEqual(r, 0)
        self.assertFalse(self.log.exists())


class TestInspector(unittest.TestCase):
    def ok(self, t, cta=False):
        self.assertEqual(D.inspect_text(t, cta), [], t)

    def ng(self, t, cta=False, key=None):
        errs = D.inspect_text(t, cta)
        self.assertTrue(errs, t)
        if key:
            self.assertTrue(any(key in e for e in errs), errs)

    def test_good(self):
        self.ok(GOOD)
        self.ok("毎朝20〜30分歩いているのに、なかなかやせない。心拍数は115〜120くらいが目安です。")

    def test_banned_phrases(self):
        for w in ["栄養講座で聞いた話ですが", "講座で学んだことですが", "ケイ先生が言っていました", "講義でも出ました",
                  "第8回の内容です", "と習いました"]:
            self.ng("僕のダイエットの話です。" + w)

    def test_numbers_and_promises(self):
        self.ng("1か月で3kg減らせます", key="数値")
        self.ng("僕のやり方なら3日で変わる気がします。ぜひ。", key="期間")
        self.ng("体脂肪が5%下がった方もいます。そんな話です。", key="数値")
        self.ng("ウエストが10cm減った方がいるんです。本当です。")
        self.ng("三日で効果が出ますよ、というお話です。", key="期間")

    def test_hashtag_url_price(self):
        self.ng("今日のひとこと #ダイエット ですね、というお話です。", key="ハッシュタグ")
        self.ng("詳しくは https://example.com を見てくださいね。", key="URL")
        self.ng("初回は4,680円でお試しいただけます、というお話。")
        self.ng("今すぐ相談してください。限定です。")

    def test_assertions(self):
        self.ng("これをやれば必ずやせます。僕が保証します。")
        self.ng("この食べ方で脂肪が落ちるんです。本当に。")
        self.ng("毎日続けると代謝が上がるんです。そうなんです。")
        self.ng("私は毎朝歩いています。それだけです。ほんとうです。", key="一人称")

    def test_water_instruction(self):
        self.ng("水分をこまめにとってくださいね。それが大切です。")
        # 事実ファイルにある「水分バランス」「水分を溜め込む」は誤検知しない
        self.ok("生理前は体に水分を溜め込みやすいので、体重が増えて見えることもあります。")

    def test_length(self):
        self.ng("あ" * 501, key="超過")

    def test_cta_exact(self):
        self.ok("相談したい方、多いです。\n" + D.CTA_TEXT, cta=True)
        self.ng("相談したい方、多いです。\n気になる方は公式LINE（@886oidmv）で『相談』と送ってください。", cta=True, key="一字一句")
        self.ng(D.CTA_TEXT + "\nそれでは、また。ありがとうございます。", cta=True, key="最後")
        self.ng("相談したい方、多いです。\n" + D.CTA_TEXT, cta=False, key="案内フラグなし")

    def test_empty(self):
        self.ng("")


class TestCtaStatus(unittest.TestCase):
    def test_two_today_forbids(self):
        rs = [post(300, "a @886oidmv"), post(200, "b @886oidmv"), post(30)]
        ok, why = D.cta_status(rs, NOW)
        self.assertFalse(ok); self.assertIn("上限", why)

    def test_latest_has_cta_forbids(self):
        ok, why = D.cta_status([post(10, "x @886oidmv"), post(100)], NOW)
        self.assertFalse(ok); self.assertIn("連続", why)

    def test_one_today_not_latest_allows(self):
        ok, _ = D.cta_status([post(30), post(200, "x @886oidmv")], NOW)
        self.assertTrue(ok)

    def test_yesterday_not_counted(self):
        old = [post(60 * 24 + 5 * 60, "a @886oidmv"), post(60 * 24 + 60, "b @886oidmv"), post(40)]
        self.assertTrue(D.cta_status(old, NOW)[0])

    def test_jst_day_boundary(self):
        now = datetime(2026, 11, 2, 0, 30, tzinfo=JST)   # UTCでは11/1
        rs = [post(10, "a @886oidmv", now), post(20, "b @886oidmv", now)]
        self.assertFalse(D.cta_status(rs, now)[0])


class TestSimilarity(unittest.TestCase):
    def test_jaccard(self):
        a = "朝ごはんを抜くと午後に疲れやすいのは自然なことです"
        self.assertGreaterEqual(D.jaccard(a, a), 0.99)
        self.assertGreaterEqual(D.jaccard(a, a + "よ"), 0.5)
        self.assertLess(D.jaccard(a, "週末の外食で崩れても気にしなくて大丈夫です"), 0.5)

    def test_regenerate_once_then_skip(self):
        dup = "毎朝歩いているのにやせないという方は、歩く速さを少し見直してみてください。"
        calls = []

        def gen(system, user):
            calls.append(1)
            return json.dumps({"text": dup, "type": "小さなコツ", "has_cta": False}, ensure_ascii=False)

        post_, reason = D.generate_post("09:00", "short", "小さなコツ", False, [dup], [dup], gen)
        self.assertIsNone(post_)
        self.assertIn("類似", reason)
        self.assertEqual(len(calls), 2)     # 作り直しは1回だけ

    def test_regenerate_succeeds(self):
        dup = "毎朝歩いているのにやせないという方は、歩く速さを少し見直してみてください。"
        new = GOOD
        seq = iter([dup, new])
        gen = lambda s, u: json.dumps({"text": next(seq), "type": "t", "has_cta": False}, ensure_ascii=False)
        p, _ = D.generate_post("09:00", "short", "t", False, [dup], [dup], gen)
        self.assertEqual(p["text"], new)

    def test_inspector_failures_retry_up_to_3_then_fail(self):
        calls = []
        gen = lambda s, u: (calls.append(1), json.dumps({"text": "#タグ付きの投稿です、よろしくお願いします。", "type": "t", "has_cta": False}, ensure_ascii=False))[1]
        p, reason = D.generate_post("09:00", "short", "t", False, [], [], gen)
        self.assertIsNone(p); self.assertEqual(len(calls), 3); self.assertIn("検品NG", reason)

    def test_cta_flag_rejected_when_not_allowed(self):
        t = "相談の入口です。迷っている方へ。\n" + D.CTA_TEXT
        gen = lambda s, u: json.dumps({"text": t, "type": "t", "has_cta": True}, ensure_ascii=False)
        p, _ = D.generate_post("09:00", "short", "t", False, [], [], gen)
        self.assertIsNone(p)


class TestGuards(Base):
    def setUp(self):
        super().setUp()
        os.environ["THREADS_ACCESS_TOKEN_DIET"] = "dummy"
        os.environ["THREADS_USER_ID_DIET"] = "123"
        self.published = []
        self.gen_calls = []

    def pub(self, token, uid, text):
        self.published.append(text); return "pid1"

    def gen(self, system, user):
        self.gen_calls.append(user)
        return json.dumps({"text": GOOD, "type": "悩みの言い当て", "has_cta": False}, ensure_ascii=False)

    def test_45min_guard_skips_when_wait_too_long(self):
        r = self.run_(slot="15:00", fetch=lambda t, n: [post(10)], publisher=self.pub, generator=self.gen,
                      sleeper=lambda s: self.fail("待ってはいけない"))
        self.assertEqual(r, 0); self.assertEqual(self.published, []); self.assertEqual(self.gen_calls, [])

    def test_45min_guard_waits_short_gap(self):
        slept = []
        self.run_(slot="15:00", fetch=lambda t, n: [post(42)], publisher=self.pub, generator=self.gen,
                  sleeper=lambda s: slept.append(s))
        self.assertEqual(len(slept), 1); self.assertEqual(len(self.published), 1)

    def test_45min_guard_ok_when_old_enough(self):
        self.run_(slot="15:00", fetch=lambda t, n: [post(50)], publisher=self.pub, generator=self.gen)
        self.assertEqual(len(self.published), 1)
        rec = D.read_log(self.log)[0]
        self.assertEqual(rec["slot"], "15:00"); self.assertEqual(rec["id"], "pid1")
        for k in ("ts", "type", "has_cta", "text_hash"):
            self.assertIn(k, rec)

    def test_slot_guard(self):
        D.append_log({"ts": NOW.isoformat(), "slot": "15:00", "id": "a", "type": "t", "has_cta": False, "text_hash": "h"}, self.log)
        self.run_(slot="15:00", fetch=lambda t, n: [], publisher=self.pub, generator=self.gen)
        self.assertEqual(self.published, [])
        # 別の枠は投稿できる
        self.run_(slot="20:00", now=NOW.replace(hour=20), fetch=lambda t, n: [], publisher=self.pub, generator=self.gen)
        self.assertEqual(len(self.published), 1)

    def test_daily_cap(self):
        for i, s in enumerate(D.SLOTS):
            D.append_log({"ts": NOW.replace(hour=9 + i).isoformat(), "slot": s, "id": str(i), "type": "t", "has_cta": False, "text_hash": "h"}, self.log)
        self.assertIn("上限", D.slot_guard(D.read_log(self.log), "15:00x", NOW))
        self.run_(slot="20:00", fetch=lambda t, n: [], publisher=self.pub, generator=self.gen)
        self.assertEqual(self.published, [])

    def test_yesterdays_log_does_not_block(self):
        y = NOW - timedelta(days=1)
        D.append_log({"ts": y.isoformat(), "slot": "15:00", "id": "a", "type": "t", "has_cta": False, "text_hash": "h"}, self.log)
        self.run_(slot="15:00", fetch=lambda t, n: [], publisher=self.pub, generator=self.gen)
        self.assertEqual(len(self.published), 1)

    def test_cta_blocked_in_prompt_when_two_today(self):
        rs = [post(300, "a @886oidmv"), post(200, "b @886oidmv"), post(100)]
        self.run_(slot="15:00", fetch=lambda t, n: rs, publisher=self.pub, generator=self.gen)
        self.assertIn("今回は入れない", self.gen_calls[0])

    def test_cta_allowed_in_prompt_in_cta_slot(self):
        self.run_(slot="15:00", fetch=lambda t, n: [post(100)], publisher=self.pub, generator=self.gen)
        self.assertIn(D.CTA_TEXT, self.gen_calls[0])

    def test_no_cta_outside_cta_slots(self):
        self.run_(slot="09:00", now=NOW.replace(hour=9), fetch=lambda t, n: [post(100, now=NOW.replace(hour=9))],
                  publisher=self.pub, generator=self.gen)
        self.assertIn("今回は入れない", self.gen_calls[0])

    def test_recent_texts_given_to_generator(self):
        self.run_(slot="15:00", fetch=lambda t, n: [post(100, "目印テキストAAA"), post(200, "目印テキストBBB")],
                  publisher=self.pub, generator=self.gen)
        self.assertIn("目印テキストAAA", self.gen_calls[0])

    def test_fetch_failure_skips(self):
        def bad(t, n): raise RuntimeError("HTTP 401")
        notes = []
        D.notify = lambda m: notes.append(m)
        self.run_(slot="15:00", fetch=bad, publisher=self.pub, generator=self.gen)
        self.assertEqual(self.published, []); self.assertEqual(len(notes), 1)

    def test_similar_to_grok_post_is_skipped(self):
        g = lambda s, u: json.dumps({"text": GOOD, "type": "t", "has_cta": False}, ensure_ascii=False)
        self.run_(slot="15:00", fetch=lambda t, n: [post(100, GOOD)], publisher=self.pub, generator=g)
        self.assertEqual(self.published, [])

    def test_publish_failure_notifies_and_does_not_log(self):
        notes = []
        D.notify = lambda m: notes.append(m)
        def bad(t, u, x): raise RuntimeError("HTTP 400")
        self.run_(slot="15:00", fetch=lambda t, n: [], publisher=bad, generator=self.gen)
        self.assertEqual(len(notes), 1); self.assertFalse(self.log.exists())


class TestDryRun(Base):
    def test_dry_run_no_api_calls_prints_text(self):
        def boom(*a, **k): raise AssertionError("API呼び出し禁止")
        gen = lambda s, u: json.dumps({"text": GOOD, "type": "悩みの言い当て", "has_cta": False}, ensure_ascii=False)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = self.run_(slot="15:00", dry_run=True, fetch=boom, publisher=boom, generator=gen)
        self.assertEqual(r, 0)
        self.assertIn(GOOD, buf.getvalue())
        self.assertFalse(self.log.exists())


class TestMisc(unittest.TestCase):
    def test_format_mix_avoids_three_in_a_row(self):
        rs = [post(10, "あ" * 20), post(60, "い" * 20)]     # short, short
        for seed in range(50):
            fmt, _ = D.choose_format_and_type(rs, "09:00", random.Random(seed))
            self.assertNotEqual(fmt, "short")

    def test_format_distribution_roughly(self):
        c = {"short": 0, "medium": 0, "long": 0}
        rng = random.Random(0)
        for _ in range(2000):
            f, _ = D.choose_format_and_type([], "13:00", rng); c[f] += 1
        self.assertGreater(c["short"] / 2000, 0.35); self.assertGreater(c["long"] / 2000, 0.28)

    def test_parse_ts(self):
        self.assertEqual(D.parse_ts("2026-11-02T03:00:00+0000").hour, 12)
        self.assertIsNone(D.parse_ts("garbage"))

    def test_prompt_contains_rules_and_facts(self):
        s, u = D.build_prompts("short", "相談の入口", "15:00", True, ["x"])
        self.assertIn("一人称は「僕」", s); self.assertIn("栄養の事実", s)
        self.assertIn("ハッシュタグ", s)

    def test_rules_file_cta_matches_constant(self):
        self.assertIn(D.CTA_TEXT, D.RULES_FILE.read_text(encoding="utf-8"))

    def test_schedule_entries_wired(self):
        t = json.loads((Path(__file__).parent / "schedule_table.json").read_text())
        es = [e for e in t["entries"] if e.get("workflow") == "diet_threads.yml"]
        self.assertEqual(sorted(e["jst_time"] for e in es), D.SLOTS)
        for e in es:
            self.assertEqual(e["inputs"]["slot"], e["jst_time"]); self.assertEqual(e["since"], "slot")
        wf = (Path(__file__).parent / ".github/workflows/diet_threads.yml").read_text()
        self.assertNotIn("cron:", wf)


if __name__ == "__main__":
    unittest.main()
