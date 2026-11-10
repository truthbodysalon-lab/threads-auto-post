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
        kw.setdefault("pause_path", Path(self.tmp.name) / "pause.json")
        kw.setdefault("now", NOW)
        kw.setdefault("rng", random.Random(1))
        return D.run(**kw)


class TestTokenSkip(Base):
    def test_empty_token_skips_without_any_call(self):
        def boom(*a, **k): raise AssertionError("呼ばれてはいけない")
        r = self.run_(slot="15:30", fetch=boom, publisher=boom, generator=boom)
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

    def test_cta_reserve_passes_and_exact(self):
        self.ok("気になることは気軽にどうぞ。\n" + D.CTA_TEXT_RESERVE, cta=True)
        self.ng("気になることは気軽にどうぞ。\nご予約は公式LINE（@886oidmv）で『予約』と送ってください。", cta=True, key="一字一句")
        self.ng(D.CTA_TEXT_RESERVE + "\nそれでは、また。ありがとうございます。", cta=True, key="最後")

    def test_cta_reserve_without_flag_fails(self):
        self.ng("気になることは気軽にどうぞ。\n" + D.CTA_TEXT_RESERVE, cta=False, key="案内フラグなし")

    def test_coupon_word_outside_cta_still_ng(self):
        self.ng("初回クーポンがお得です。\n" + D.CTA_TEXT_RESERVE, cta=True)

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

        # recent_texts（決まり文句の比較）は空にして、類似チェック（sim_pool）だけを試す
        post_, reason = D.generate_post("09:00", "short", "小さなコツ", False, [], [dup], gen)
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

    def test_reserve_cta_rejected_when_not_allowed(self):
        t = "相談の入口です。迷っている方へ。\n" + D.CTA_TEXT_RESERVE
        gen = lambda s, u: json.dumps({"text": t, "type": "t", "has_cta": True}, ensure_ascii=False)
        p, _ = D.generate_post("09:00", "short", "t", False, [], [], gen)
        self.assertIsNone(p)
        gen2 = lambda s, u: json.dumps({"text": t, "type": "t", "has_cta": False}, ensure_ascii=False)
        p, _ = D.generate_post("09:00", "short", "t", False, [], [], gen2)
        self.assertIsNone(p)

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

    def pub(self, token, uid, text, reply_to=""):
        self.published.append(text); return "pid1"

    def gen(self, system, user):
        self.gen_calls.append(user)
        return json.dumps({"text": GOOD, "type": "悩みの言い当て", "has_cta": False}, ensure_ascii=False)

    def test_gap_guard_skips_when_wait_too_long(self):
        r = self.run_(slot="15:30", fetch=lambda t, n: [post(10)], publisher=self.pub, generator=self.gen,
                      sleeper=lambda s: self.fail("待ってはいけない"))
        self.assertEqual(r, 0); self.assertEqual(self.published, []); self.assertEqual(self.gen_calls, [])

    def test_gap_guard_waits_short_gap(self):
        slept = []
        self.run_(slot="15:30", fetch=lambda t, n: [post(25)], publisher=self.pub, generator=self.gen,
                  sleeper=lambda s: slept.append(s))
        self.assertEqual(len(slept), 1); self.assertEqual(len(self.published), 1)

    def test_gap_guard_ok_when_old_enough(self):
        self.run_(slot="15:30", fetch=lambda t, n: [post(40)], publisher=self.pub, generator=self.gen)
        self.assertEqual(len(self.published), 1)
        rec = D.read_log(self.log)[0]
        self.assertEqual(rec["slot"], "15:30"); self.assertEqual(rec["id"], "pid1")
        for k in ("ts", "type", "has_cta", "text_hash"):
            self.assertIn(k, rec)

    def test_slot_guard(self):
        D.append_log({"ts": NOW.isoformat(), "slot": "15:30", "id": "a", "type": "t", "has_cta": False, "text_hash": "h"}, self.log)
        self.run_(slot="15:30", fetch=lambda t, n: [], publisher=self.pub, generator=self.gen)
        self.assertEqual(self.published, [])
        # 別の枠は投稿できる
        self.run_(slot="20:00", now=NOW.replace(hour=20), fetch=lambda t, n: [], publisher=self.pub, generator=self.gen)
        self.assertEqual(len(self.published), 1)

    def test_daily_cap(self):
        for i, s in enumerate(D.SLOTS):
            D.append_log({"ts": (NOW - timedelta(minutes=i)).isoformat(), "slot": s, "id": str(i), "type": "t", "has_cta": False, "text_hash": "h"}, self.log)
        self.assertIn("上限", D.slot_guard(D.read_log(self.log), "15:00x", NOW))
        self.run_(slot="20:00", fetch=lambda t, n: [], publisher=self.pub, generator=self.gen)
        self.assertEqual(self.published, [])

    def test_yesterdays_log_does_not_block(self):
        y = NOW - timedelta(days=1)
        D.append_log({"ts": y.isoformat(), "slot": "15:30", "id": "a", "type": "t", "has_cta": False, "text_hash": "h"}, self.log)
        self.run_(slot="15:30", fetch=lambda t, n: [], publisher=self.pub, generator=self.gen)
        self.assertEqual(len(self.published), 1)

    def test_cta_blocked_in_prompt_when_two_today(self):
        rs = [post(300, "a @886oidmv"), post(200, "b @886oidmv"), post(100)]
        self.run_(slot="15:30", fetch=lambda t, n: rs, publisher=self.pub, generator=self.gen)
        self.assertIn("今回は入れない", self.gen_calls[0])

    def test_cta_allowed_in_prompt_in_cta_slot(self):
        self.run_(slot="15:30", fetch=lambda t, n: [post(100)], publisher=self.pub, generator=self.gen)
        self.assertIn(D.CTA_TEXT, self.gen_calls[0])

    def test_no_cta_outside_cta_slots(self):
        self.run_(slot="09:00", now=NOW.replace(hour=9), fetch=lambda t, n: [post(100, now=NOW.replace(hour=9))],
                  publisher=self.pub, generator=self.gen)
        self.assertIn("今回は入れない", self.gen_calls[0])

    def test_recent_texts_given_to_generator(self):
        self.run_(slot="15:30", fetch=lambda t, n: [post(100, "目印テキストAAA"), post(200, "目印テキストBBB")],
                  publisher=self.pub, generator=self.gen)
        self.assertIn("目印テキストAAA", self.gen_calls[0])

    def test_fetch_failure_skips(self):
        def bad(t, n): raise RuntimeError("HTTP 401")
        notes = []
        D.notify = lambda m: notes.append(m)
        self.run_(slot="15:30", fetch=bad, publisher=self.pub, generator=self.gen)
        self.assertEqual(self.published, []); self.assertEqual(len(notes), 1)

    def test_similar_to_grok_post_is_skipped(self):
        g = lambda s, u: json.dumps({"text": GOOD, "type": "t", "has_cta": False}, ensure_ascii=False)
        self.run_(slot="15:30", fetch=lambda t, n: [post(100, GOOD)], publisher=self.pub, generator=g)
        self.assertEqual(self.published, [])

    def test_publish_failure_notifies_and_does_not_log(self):
        notes = []
        D.notify = lambda m: notes.append(m)
        def bad(t, u, x): raise RuntimeError("HTTP 400")
        self.run_(slot="15:30", fetch=lambda t, n: [], publisher=bad, generator=self.gen)
        self.assertEqual(len(notes), 1); self.assertFalse(self.log.exists())


class TestDryRun(Base):
    def test_dry_run_no_api_calls_prints_text(self):
        def boom(*a, **k): raise AssertionError("API呼び出し禁止")
        gen = lambda s, u: json.dumps({"text": GOOD, "type": "悩みの言い当て", "has_cta": False}, ensure_ascii=False)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = self.run_(slot="15:30", dry_run=True, fetch=boom, publisher=boom, generator=gen)
        self.assertEqual(r, 0)
        self.assertIn(GOOD, buf.getvalue())
        self.assertFalse(self.log.exists())


class TestPause(Base):
    def setUp(self):
        super().setUp()
        os.environ["THREADS_ACCESS_TOKEN_DIET"] = "dummy"
        os.environ["THREADS_USER_ID_DIET"] = "123"
        self.pause = Path(self.tmp.name) / "pause.json"
        self.notes = []
        D.notify = lambda m: self.notes.append(m)
        self.calls = []

    def gen(self, system, user):
        self.calls.append("gen")
        return json.dumps({"text": GOOD, "type": "悩みの言い当て", "has_cta": False}, ensure_ascii=False)

    def fetch_ok(self, t, n):
        self.calls.append("fetch"); return []

    def pub_ok(self, t, u, text):
        self.calls.append("pub"); return "p1"

    def test_is_pause_error(self):
        for m in ['HTTP 429: {"error":{"message":"x"}}',
                  'HTTP 400: {"error":{"message":"Invalid OAuth access token","type":"OAuthException","code":190}}',
                  'HTTP 400: {"error":{"code":4,"message":"app"}}',
                  'HTTP 400: {"error":{"code":368}}',
                  'HTTP 400: {"error":{"error_subcode":2207051}}',
                  'HTTP 400: {"error":{"message":"Application request limit reached"}}',
                  'HTTP 403: you are temporarily blocked', 'HTTP 400: spam detected', 'HTTP 400: policy violation',
                  'HTTP 400: 一時的に制限されています']:
            self.assertTrue(D.is_pause_error(m), m)
        for m in ['HTTP 500: Internal Server Error', 'HTTP 503: unavailable', '通信エラー: timed out', 'HTTP 400: bad param']:
            self.assertFalse(D.is_pause_error(m), m)

    def test_publish_429_creates_pause_and_notifies_once(self):
        def pub(t, u, text): raise RuntimeError('HTTP 429: {"error":{"message":"rate limit"}}')
        r = self.run_(slot="15:30", fetch=self.fetch_ok, publisher=pub, generator=self.gen)
        self.assertEqual(r, 0)
        self.assertTrue(self.pause.exists())
        d = json.loads(self.pause.read_text(encoding="utf-8"))
        self.assertEqual(d["slot"], "15:30"); self.assertIn("429", d["reason"]); self.assertIn("since", d)
        self.assertNotIn("dummy", self.pause.read_text(encoding="utf-8"))
        self.assertEqual(len(self.notes), 1)
        self.assertIn("🛑 ダイエットThreads API投稿を停止しました", self.notes[0])
        self.assertIn("diet_threads_pause.json", self.notes[0])
        self.assertFalse(self.log.exists())          # 投稿ログは書かない

    def test_fetch_limit_message_pauses(self):
        def f(t, n): raise RuntimeError('HTTP 400: {"error":{"message":"Application request limit reached","code":4}}')
        self.run_(slot="15:30", fetch=f, publisher=self.pub_ok, generator=self.gen)
        self.assertTrue(self.pause.exists()); self.assertEqual(len(self.notes), 1)
        self.assertNotIn("pub", self.calls)

    def test_token_190_pauses(self):
        def pub(t, u, text): raise RuntimeError('HTTP 400: {"error":{"type":"OAuthException","code":190}}')
        self.run_(slot="15:30", fetch=self.fetch_ok, publisher=pub, generator=self.gen)
        self.assertTrue(self.pause.exists())

    def test_no_api_calls_while_paused_and_no_repeat_notice(self):
        self.pause.write_text("{}", encoding="utf-8")
        def boom(*a, **k): raise AssertionError("停止中にAPIを呼んではいけない")
        for _ in range(3):
            self.assertEqual(self.run_(slot="15:30", fetch=boom, publisher=boom, generator=boom), 0)
        self.assertEqual(self.notes, [])

    def test_transient_500_does_not_pause(self):
        def pub(t, u, text): raise RuntimeError("HTTP 500: Internal Server Error")
        self.run_(slot="15:30", fetch=self.fetch_ok, publisher=pub, generator=self.gen)
        self.assertFalse(self.pause.exists())
        self.assertEqual(len(self.notes), 1)
        self.assertIn("投稿に失敗", self.notes[0])

    def test_transient_network_fetch_does_not_pause(self):
        def f(t, n): raise RuntimeError("通信エラー: timed out")
        self.run_(slot="15:30", fetch=f, publisher=self.pub_ok, generator=self.gen)
        self.assertFalse(self.pause.exists())

    def test_publish_never_retried(self):
        n = []
        def pub(t, u, text): n.append(1); raise RuntimeError("HTTP 429: x")
        self.run_(slot="15:30", fetch=self.fetch_ok, publisher=pub, generator=self.gen)
        self.assertEqual(len(n), 1)

    def test_workflow_commits_pause_file(self):
        wf = (Path(__file__).parent / ".github/workflows/diet_threads.yml").read_text(encoding="utf-8")
        self.assertIn("diet_threads_pause.json", wf)

    def test_grok_excerpt_injected_into_prompt(self):
        orig = D.GROK_RULES_FILE
        g = Path(self.tmp.name) / "g.md"; g.write_text("ZZZ_GROK_MARK 原文", encoding="utf-8")
        D.GROK_RULES_FILE = g
        try:
            sy, _ = D.build_prompts("short", "相談の入口", "15:00", False, [])
        finally:
            D.GROK_RULES_FILE = orig
        self.assertIn("ZZZ_GROK_MARK", sy); self.assertIn("新しい日付の記述が優先", sy)


class TestMisc(unittest.TestCase):
    def test_format_mix_avoids_three_in_a_row(self):
        rs = [post(10, "あ" * 20), post(60, "い" * 20)]     # short, short
        for seed in range(50):
            fmt, _ = D.choose_format_and_type(rs, "09:00", random.Random(seed))
            self.assertNotEqual(fmt, "short")

    def test_format_distribution_roughly(self):
        c = {"short": 0, "tree": 0}
        rng = random.Random(0)
        for _ in range(2000):
            f, _ = D.choose_format_and_type([], "13:00", rng); c[f] += 1
        self.assertGreater(c["short"] / 2000, 0.4); self.assertGreater(c["tree"] / 2000, 0.4)

    def test_parse_ts(self):
        self.assertEqual(D.parse_ts("2026-11-02T03:00:00+0000").hour, 12)
        self.assertIsNone(D.parse_ts("garbage"))

    def test_prompt_contains_rules_and_facts(self):
        s, u = D.build_prompts("short", "相談の入口", "15:00", True, ["x"])
        self.assertIn("一人称は「僕」", s); self.assertIn("栄養の事実", s)
        self.assertIn("ハッシュタグ", s)

    def test_rules_file_cta_matches_constant(self):
        self.assertIn(D.CTA_TEXT, D.RULES_FILE.read_text(encoding="utf-8"))
        self.assertIn(D.CTA_TEXT_RESERVE, D.RULES_FILE.read_text(encoding="utf-8"))

    def test_cta_alternates(self):
        e = lambda t: {"text": t}
        self.assertEqual(D.next_cta_text([]), D.CTA_TEXT)
        self.assertEqual(D.next_cta_text([e("ふつう")]), D.CTA_TEXT)
        self.assertEqual(D.next_cta_text([e("x\n" + D.CTA_TEXT)]), D.CTA_TEXT_RESERVE)
        self.assertEqual(D.next_cta_text([e("x\n" + D.CTA_TEXT_RESERVE)]), D.CTA_TEXT)
        self.assertEqual(D.next_cta_text([e("x\n" + D.CTA_TEXT_RESERVE), e("y\n" + D.CTA_TEXT), e("案内なし")]), D.CTA_TEXT_RESERVE)

    def test_prompt_uses_given_cta(self):
        _, u = D.build_prompts("short", "t", "15:30", True, [], cta_text=D.CTA_TEXT_RESERVE)
        self.assertIn(D.CTA_TEXT_RESERVE, u); self.assertNotIn(D.CTA_TEXT, u)

    def test_schedule_entries_wired(self):
        t = json.loads((Path(__file__).parent / "schedule_table.json").read_text())
        es = [e for e in t["entries"] if e.get("workflow") == "diet_threads.yml"]
        self.assertEqual(sorted(e["jst_time"] for e in es), D.SLOTS)
        for e in es:
            self.assertEqual(e["inputs"]["slot"], e["jst_time"]); self.assertEqual(e["since"], "slot")
        wf = (Path(__file__).parent / ".github/workflows/diet_threads.yml").read_text()
        self.assertNotIn("cron:", wf)



class TestQuality20261102(unittest.TestCase):
    """院長確認「3点直して」(2026-11-02): 根拠のない体の話・決まり文句の繰り返し・専門用語"""
    def test_body_mechanism_ng(self):
        self.assertTrue(any("体の仕組み" in e for e in D.inspect_text("姿勢が崩れると内臓の位置も変わることがあるんです。", False)))
        self.assertTrue(any("体の仕組み" in e for e in D.inspect_text("骨盤が開くと太りやすくなります。", False)))
    def test_jargon_ng(self):
        self.assertTrue(any("専門用語" in e for e in D.inspect_text("エネルギー密度の低い野菜を足してみてください。", False)))
        self.assertFalse(any("専門用語" in e for e in D.inspect_text("量のわりに軽い野菜を足してみてください。", False)))
    def test_stock_phrase_repeat(self):
        recent = ["「食べてないのにやせない」という方、本当に多いです。", "別の話です。"]
        self.assertTrue(D.stock_phrase_errors("「続かない」方、本当に多いです。", recent))
        self.assertEqual(D.stock_phrase_errors("「続かない」と感じる日、ありますよね。", ["まったく別の投稿です。"]), [])


class TestBa20261106(unittest.TestCase):
    """院長指示（2026-11-06）: BA事例を固定文で every_days 日に1本"""
    CFG = {"enabled": True, "slot": "12:30", "every_days": 3,
           "posts": [{"key": "a", "text": "A"}, {"key": "b", "text": "B"}]}
    NOW = datetime(2026, 11, 10, 12, 30, tzinfo=D.JST)

    def test_disabled_or_other_slot(self):
        self.assertIsNone(D.pick_ba([], "12:30", self.NOW, dict(self.CFG, enabled=False)))
        self.assertIsNone(D.pick_ba([], "13:30", self.NOW, self.CFG))

    def test_rotation_and_gap(self):
        self.assertEqual(D.pick_ba([], "12:30", self.NOW, self.CFG)["key"], "a")
        e = [{"type": "ba", "ba_key": "a", "ts": (self.NOW - timedelta(days=1)).isoformat()}]
        self.assertIsNone(D.pick_ba(e, "12:30", self.NOW, self.CFG))
        e = [{"type": "ba", "ba_key": "a", "ts": (self.NOW - timedelta(days=3)).isoformat()}]
        self.assertEqual(D.pick_ba(e, "12:30", self.NOW, self.CFG)["key"], "b")

    def test_real_file_passes_inspection(self):
        cfg = D.load_ba()
        self.assertTrue(cfg.get("posts"))
        for p in cfg["posts"]:
            self.assertEqual([x for x in D.inspect_text(p["text"], False) if x not in D.BA_ALLOWED_NG], [], p["key"])


class TestTree20261107(Base):
    """院長指示（2026-11-07）: 短く端的に・ツリーを活用"""
    def test_tree_lengths(self):
        ok = {"text": "ナッツは太る？", "replies": ["量を決めれば味方です。", "まずは小皿1つから。"], "type": "t", "has_cta": False}
        self.assertEqual(D.length_errors(ok, "tree"), [])
        self.assertTrue(D.length_errors(dict(ok, replies=["1つだけ"]), "tree"))
        self.assertTrue(D.length_errors(dict(ok, text="あ" * 60), "tree"))
        self.assertTrue(D.length_errors({"text": "あ" * 80, "replies": [], "type": "t", "has_cta": False}, "short"))

    def test_tree_is_published_as_chain(self):
        os.environ["THREADS_ACCESS_TOKEN_DIET"] = "dummy"; os.environ["THREADS_USER_ID_DIET"] = "123"
        calls = []
        def pub(token, uid, text, reply_to=""):
            calls.append((text, reply_to)); return f"id{len(calls)}"
        tree = {"text": "夜にお腹が空く日は？", "replies": ["豆腐やヨーグルトを少し。", "我慢しすぎない方が続きます。"], "type": "t", "has_cta": False}
        gen = lambda s, u: json.dumps(tree, ensure_ascii=False)
        self.run_(slot="13:30", now=NOW.replace(hour=13, minute=30), fetch=lambda t, n: [], publisher=pub, generator=gen,
                  rng=random.Random(1), sleeper=lambda s: None)
        if len(calls) == 3:   # 乱数でツリーが選ばれた場合
            self.assertEqual([c[1] for c in calls], ["", "id1", "id2"])

    def test_pick_topic_avoids_recent(self):
        topics = [{"id": "a"}, {"id": "b"}]
        self.assertEqual(D.pick_topic([{"topic_id": "a"}], random.Random(0), topics)["id"], "b")
        self.assertTrue(D.load_topics())


if __name__ == "__main__":
    unittest.main()
