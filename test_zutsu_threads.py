#!/usr/bin/env python3
"""zutsu_threads.py のテスト(ネットワークは使わない)。実行: python3 test_zutsu_threads.py"""
import json
import re, os, random, sys, tempfile, unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zutsu_threads as D

JST = D.JST
NOW = datetime(2026, 11, 5, 20, 30, tzinfo=JST)
GOOD = "「夕方になると頭が重い」。そんな日は、パソコンの画面を目線のやや下に下げてみてください。"
LONG_GOOD = ("「頭が痛いから、とりあえず首を温めよう」。じつはこれ、頭痛のタイプによっては逆になることがあります。\n\n"
             "頭の両側が締めつけられるような痛みは、首や肩のこわばりが関わっていると言われていて、ホットタオルで首の後ろを温めると楽になりやすいです。"
             "一方で、片側がズキズキ脈打って、動くと響く痛みは片頭痛のことがあり、温めるより暗く静かな場所で休むのが向いています。\n\n"
             "まずは自分の痛みがどちらに近いか、痛む場所ときっかけをメモしてみてください。\n" + D.SAFETY_LINE)



def with_safety(user, text):
    """2026-11-26: 指示された※の1行を入れる（本物のLLMと同じ動き。型が増えて乱数の流れが変わり、※つきのテーマに当たる枠が出たため）"""
    m = re.search(r"次の1行をそのまま入れる: (※[^\n]+)", user or "")
    return text + "\n" + m.group(1) if m else text

def ts(dt):
    return dt.astimezone(D.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")


def post(minutes_ago, text="ふつうの投稿です。今日もお疲れさまでした。", now=NOW):
    return {"id": "x", "text": text, "timestamp": ts(now - timedelta(minutes=minutes_ago))}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "log.jsonl"
        self._env = dict(os.environ)
        os.environ.pop("THREADS_ACCESS_TOKEN_ZUTSU", None)
        os.environ.pop("THREADS_USER_ID_ZUTSU", None)
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
        r = self.run_(slot="20:15", fetch=boom, publisher=boom, generator=boom)
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
        self.ok(LONG_GOOD)
        self.ok("ドライブの帰り道、こめかみが重くなる日はありますか。")   # 「イブ」の誤検知をしない

    def test_cure_and_effect_claims(self):
        self.ng("この方法で頭痛が治ります。ぜひ試してください。", key="治")
        self.ng("首を温めるのは頭痛にとても効く方法です。ぜひどうぞ。", key="効く")
        self.ng("3回で頭痛がなくなる方もいます。ご相談ください。")
        self.ng("頭痛が消えるストレッチを紹介します。今日からどうぞ。", key="消える")
        self.ng("これをやれば必ず楽になります。僕が保証します。")

    def test_medicine(self):
        self.ng("痛み止めをやめると頭痛は減っていきます。がんばりましょう。", key="中止")
        self.ng("ロキソニンを飲む日が増えていませんか。気になるところです。", key="銘柄")
        self.ng("イブを毎日飲んでいる方へ。少し立ち止まってみましょう。", key="銘柄")
        self.ng("それは薬物乱用頭痛かもしれません。気をつけてください。")
        self.ng("頭痛薬に頼らない体をつくりましょう。一緒にがんばりましょう。")
        # 「医師・薬剤師に相談」までは書いてよい
        self.ok("痛み止めを飲む日が増えてきたら、一度医師や薬剤師に相談してみてください。")

    def test_diagnosis_and_body_mechanism(self):
        self.ng("あなたは片頭痛です。まず冷やしてください。お大事に。", key="決めつけ")
        self.ng("頭痛の原因は骨盤の歪みにあることが多いんです。", key="体の仕組み")
        self.ng("CGRPという物質が関わっていると言われています。", key="専門用語")

    def test_numbers_price_hashtag_url(self):
        self.ng("改善率が高い整体院です。ぜひご相談ください。", key="改善率")
        self.ng("13,000人以上の方を施術してきました。ご相談ください。", key="実績")
        self.ng("初回は4,680円でお試しいただけます、というお話。")
        self.ng("今日のひとこと #頭痛 ですね、というお話です。", key="ハッシュタグ")
        self.ng("詳しくは https://lin.ee/qbRbPAm を見てくださいね。", key="URL")
        self.ng("今すぐ相談してください。限定です。")
        self.ng("私は毎朝首を回しています。それだけです。ほんとうです。", key="一人称")

    def test_length(self):
        self.ng("あ" * 501, key="超過")

    def test_cta_exact(self):
        self.ok("頭痛のタイプ、気になりませんか。\n" + D.CTA_TEXT, cta=True)
        self.ng("頭痛のタイプ、気になりませんか。\n公式LINE（@561yryuk）で『相談』と送ってください。", cta=True, key="一字一句")
        self.ng(D.CTA_TEXT + "\nそれでは、また。ありがとうございます。", cta=True, key="最後")
        self.ng("頭痛のタイプ、気になりませんか。\n" + D.CTA_TEXT, cta=False, key="案内フラグなし")

    def test_cta_keyword_is_live_auto_reply(self):
        # 『診断』は頭痛LINEの自動応答(頭痛タイプ診断)の言葉。自由文の相談へ誘導しない
        self.assertIn("『診断』", D.CTA_TEXT); self.assertNotIn("相談", D.CTA_TEXT)

    def test_empty(self):
        self.ng("")

class TestCtaStatus(unittest.TestCase):
    def test_two_today_forbids(self):
        rs = [post(300, "a @561yryuk"), post(200, "b @561yryuk"), post(30)]
        ok, why = D.cta_status(rs, NOW)
        self.assertFalse(ok); self.assertIn("上限", why)

    def test_latest_has_cta_forbids(self):
        ok, why = D.cta_status([post(10, "x @561yryuk"), post(100)], NOW)
        self.assertFalse(ok); self.assertIn("連続", why)

    def test_one_today_not_latest_allows(self):
        ok, _ = D.cta_status([post(30), post(200, "x @561yryuk")], NOW)
        self.assertTrue(ok)

    def test_yesterday_not_counted(self):
        old = [post(60 * 24 + 5 * 60, "a @561yryuk"), post(60 * 24 + 60, "b @561yryuk"), post(40)]
        self.assertTrue(D.cta_status(old, NOW)[0])

    def test_jst_day_boundary(self):
        now = datetime(2026, 11, 2, 0, 30, tzinfo=JST)   # UTCでは11/1
        rs = [post(10, "a @561yryuk", now), post(20, "b @561yryuk", now)]
        self.assertFalse(D.cta_status(rs, now)[0])


class TestSimilarity(unittest.TestCase):
    def test_jaccard(self):
        a = "朝ごはんを抜くと午後に疲れやすいのは自然なことです"
        self.assertGreaterEqual(D.jaccard(a, a), 0.99)
        self.assertGreaterEqual(D.jaccard(a, a + "よ"), 0.5)
        self.assertLess(D.jaccard(a, "週末の外食で崩れても気にしなくて大丈夫です"), 0.5)

    def test_regenerate_once_then_skip(self):
        dup = "夕方になると頭が重くなる方は、画面の高さを少し見直してみてください。"
        calls = []

        def gen(system, user):
            calls.append(1)
            return json.dumps({"text": dup, "type": "小さなコツ", "has_cta": False}, ensure_ascii=False)

        # recent_texts（決まり文句の比較）は空にして、類似チェック（sim_pool）だけを試す
        post_, reason = D.generate_post("07:30", "short", "小さなコツ", False, [], [dup], gen)
        self.assertIsNone(post_)
        self.assertIn("類似", reason)
        self.assertEqual(len(calls), 2)     # 作り直しは1回だけ

    def test_regenerate_succeeds(self):
        dup = "夕方になると頭が重くなる方は、画面の高さを少し見直してみてください。"
        new = "雨の前の日は、天気アプリで先回りして早めに寝る。"
        seq = iter([dup, new])
        gen = lambda s, u: json.dumps({"text": next(seq), "type": "t", "has_cta": False}, ensure_ascii=False)
        p, _ = D.generate_post("07:30", "short", "t", False, [dup], [dup], gen)
        self.assertEqual(p["text"], new)

    def test_inspector_failures_retry_up_to_3_then_fail(self):
        calls = []
        gen = lambda s, u: (calls.append(1), json.dumps({"text": "#タグ付きの投稿です、よろしくお願いします。", "type": "t", "has_cta": False}, ensure_ascii=False))[1]
        p, reason = D.generate_post("07:30", "short", "t", False, [], [], gen)
        self.assertIsNone(p); self.assertEqual(len(calls), D.MAX_ATTEMPTS); self.assertIn("検品NG", reason)

    def test_cta_flag_rejected_when_not_allowed(self):
        t = "相談の入口です。迷っている方へ。\n" + D.CTA_TEXT
        gen = lambda s, u: json.dumps({"text": t, "type": "t", "has_cta": True}, ensure_ascii=False)
        p, _ = D.generate_post("07:30", "short", "t", False, [], [], gen)
        self.assertIsNone(p)


class TestGuards(Base):
    def setUp(self):
        super().setUp()
        os.environ["THREADS_ACCESS_TOKEN_ZUTSU"] = "dummy"
        os.environ["THREADS_USER_ID_ZUTSU"] = "123"
        self.published = []
        self.gen_calls = []

    def pub(self, token, uid, text):
        self.published.append(text); return "pid1"

    def gen(self, system, user):
        self.gen_calls.append(user)
        return json.dumps({"text": with_safety(user, GOOD), "type": "悩みの言い当て", "has_cta": False}, ensure_ascii=False)

    def test_gap_guard_skips_when_wait_too_long(self):
        r = self.run_(slot="20:15", fetch=lambda t, n: [post(10)], publisher=self.pub, generator=self.gen,
                      sleeper=lambda s: self.fail("待ってはいけない"))
        self.assertEqual(r, 0); self.assertEqual(self.published, []); self.assertEqual(self.gen_calls, [])

    def test_gap_guard_waits_short_gap(self):
        slept = []
        self.run_(slot="20:15", fetch=lambda t, n: [post(25)], publisher=self.pub, generator=self.gen,
                  sleeper=lambda s: slept.append(s))
        self.assertEqual(len(slept), 1); self.assertEqual(len(self.published), 1)

    def test_gap_guard_ok_when_old_enough(self):
        self.run_(slot="20:15", fetch=lambda t, n: [post(40)], publisher=self.pub, generator=self.gen)
        self.assertEqual(len(self.published), 1)
        rec = D.read_log(self.log)[0]
        self.assertEqual(rec["slot"], "20:15"); self.assertEqual(rec["id"], "pid1")
        for k in ("ts", "type", "has_cta", "text_hash"):
            self.assertIn(k, rec)

    def test_slot_guard(self):
        D.append_log({"ts": NOW.isoformat(), "slot": "20:15", "id": "a", "type": "t", "has_cta": False, "text_hash": "h"}, self.log)
        self.run_(slot="20:15", fetch=lambda t, n: [], publisher=self.pub, generator=self.gen)
        self.assertEqual(self.published, [])
        # 別の枠は投稿できる
        self.run_(slot="22:30", now=NOW.replace(hour=22, minute=30), fetch=lambda t, n: [], publisher=self.pub, generator=self.gen)
        self.assertEqual(len(self.published), 1)

    def test_daily_cap(self):
        # 2026-11-12 上限30＞枠数のため、全枠＋予備名で上限の本数まで埋めて確かめる
        for i, s in enumerate((list(D.SLOTS) + [f"extra{k}" for k in range(D.DAILY_CAP)])[:D.DAILY_CAP]):
            D.append_log({"ts": (NOW - timedelta(minutes=i)).isoformat(), "slot": s, "id": str(i), "type": "t", "has_cta": False, "text_hash": "h"}, self.log)
        self.assertIn("上限", D.slot_guard(D.read_log(self.log), "20:15x", NOW))
        self.run_(slot="22:30", fetch=lambda t, n: [], publisher=self.pub, generator=self.gen)
        self.assertEqual(self.published, [])

    def test_yesterdays_log_does_not_block(self):
        y = NOW - timedelta(days=1)
        D.append_log({"ts": y.isoformat(), "slot": "20:15", "id": "a", "type": "t", "has_cta": False, "text_hash": "h"}, self.log)
        self.run_(slot="20:15", fetch=lambda t, n: [], publisher=self.pub, generator=self.gen)
        self.assertEqual(len(self.published), 1)

    def test_cta_blocked_in_prompt_when_two_today(self):
        rs = [post(300, "a @561yryuk"), post(200, "b @561yryuk"), post(100)]
        self.run_(slot="20:15", fetch=lambda t, n: rs, publisher=self.pub, generator=self.gen)
        self.assertIn("今回は入れない", self.gen_calls[0])

    def test_cta_allowed_in_prompt_in_cta_slot(self):
        self.run_(slot="20:15", fetch=lambda t, n: [post(100)], publisher=self.pub, generator=self.gen)
        self.assertIn(D.CTA_TEXT, self.gen_calls[0])

    def test_no_cta_outside_cta_slots(self):
        # 2026-11-26: 17:30 は案内を付けてよい枠になったので、案内の無い枠(18:15)で確かめる
        self.run_(slot="18:15", now=NOW.replace(hour=18, minute=15), fetch=lambda t, n: [post(100, now=NOW.replace(hour=18, minute=15))],
                  publisher=self.pub, generator=self.gen)
        self.assertIn("今回は入れない", self.gen_calls[0])

    def test_recent_texts_given_to_generator(self):
        self.run_(slot="20:15", fetch=lambda t, n: [post(100, "目印テキストAAA"), post(200, "目印テキストBBB")],
                  publisher=self.pub, generator=self.gen)
        self.assertIn("目印テキストAAA", self.gen_calls[0])

    def test_fetch_failure_skips(self):
        def bad(t, n): raise RuntimeError("HTTP 401")
        notes = []
        D.notify = lambda m: notes.append(m)
        self.run_(slot="20:15", fetch=bad, publisher=self.pub, generator=self.gen)
        self.assertEqual(self.published, []); self.assertEqual(len(notes), 1)

    def test_similar_to_manual_post_is_skipped(self):
        g = lambda s, u: json.dumps({"text": with_safety(u, GOOD), "type": "t", "has_cta": False}, ensure_ascii=False)
        self.run_(slot="20:15", fetch=lambda t, n: [post(100, GOOD)], publisher=self.pub, generator=g)
        self.assertEqual(self.published, [])

    def test_publish_failure_notifies_and_does_not_log(self):
        notes = []
        D.notify = lambda m: notes.append(m)
        def bad(t, u, x): raise RuntimeError("HTTP 400")
        self.run_(slot="20:15", fetch=lambda t, n: [], publisher=bad, generator=self.gen)
        self.assertEqual(len(notes), 1); self.assertFalse(self.log.exists())


class TestDryRun(Base):
    def test_dry_run_no_api_calls_prints_text(self):
        def boom(*a, **k): raise AssertionError("API呼び出し禁止")
        gen = lambda s, u: json.dumps({"text": with_safety(u, GOOD), "type": "悩みの言い当て", "has_cta": False}, ensure_ascii=False)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = self.run_(slot="20:15", dry_run=True, fetch=boom, publisher=boom, generator=gen)
        self.assertEqual(r, 0)
        self.assertIn(GOOD, buf.getvalue())
        self.assertFalse(self.log.exists())


class TestPause(Base):
    def setUp(self):
        super().setUp()
        os.environ["THREADS_ACCESS_TOKEN_ZUTSU"] = "dummy"
        os.environ["THREADS_USER_ID_ZUTSU"] = "123"
        self.pause = Path(self.tmp.name) / "pause.json"
        self.notes = []
        D.notify = lambda m: self.notes.append(m)
        self.calls = []

    def gen(self, system, user):
        self.calls.append("gen")
        return json.dumps({"text": with_safety(user, GOOD), "type": "悩みの言い当て", "has_cta": False}, ensure_ascii=False)

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
        r = self.run_(slot="20:15", fetch=self.fetch_ok, publisher=pub, generator=self.gen)
        self.assertEqual(r, 0)
        self.assertTrue(self.pause.exists())
        d = json.loads(self.pause.read_text(encoding="utf-8"))
        self.assertEqual(d["slot"], "20:15"); self.assertIn("429", d["reason"]); self.assertIn("since", d)
        self.assertNotIn("dummy", self.pause.read_text(encoding="utf-8"))
        self.assertEqual(len(self.notes), 1)
        self.assertIn("🛑 頭痛Threads API投稿を停止しました", self.notes[0])
        self.assertIn("zutsu_threads_pause.json", self.notes[0])
        self.assertFalse(self.log.exists())          # 投稿ログは書かない

    def test_fetch_limit_message_pauses(self):
        def f(t, n): raise RuntimeError('HTTP 400: {"error":{"message":"Application request limit reached","code":4}}')
        self.run_(slot="20:15", fetch=f, publisher=self.pub_ok, generator=self.gen)
        self.assertTrue(self.pause.exists()); self.assertEqual(len(self.notes), 1)
        self.assertNotIn("pub", self.calls)

    def test_token_190_pauses(self):
        def pub(t, u, text): raise RuntimeError('HTTP 400: {"error":{"type":"OAuthException","code":190}}')
        self.run_(slot="20:15", fetch=self.fetch_ok, publisher=pub, generator=self.gen)
        self.assertTrue(self.pause.exists())

    def test_no_api_calls_while_paused_and_no_repeat_notice(self):
        self.pause.write_text("{}", encoding="utf-8")
        def boom(*a, **k): raise AssertionError("停止中にAPIを呼んではいけない")
        for _ in range(3):
            self.assertEqual(self.run_(slot="20:15", fetch=boom, publisher=boom, generator=boom), 0)
        self.assertEqual(self.notes, [])

    def test_transient_500_does_not_pause(self):
        def pub(t, u, text): raise RuntimeError("HTTP 500: Internal Server Error")
        self.run_(slot="20:15", fetch=self.fetch_ok, publisher=pub, generator=self.gen)
        self.assertFalse(self.pause.exists())
        self.assertEqual(len(self.notes), 1)
        self.assertIn("投稿に失敗", self.notes[0])

    def test_transient_network_fetch_does_not_pause(self):
        def f(t, n): raise RuntimeError("通信エラー: timed out")
        self.run_(slot="20:15", fetch=f, publisher=self.pub_ok, generator=self.gen)
        self.assertFalse(self.pause.exists())

    def test_publish_never_retried(self):
        n = []
        def pub(t, u, text): n.append(1); raise RuntimeError("HTTP 429: x")
        self.run_(slot="20:15", fetch=self.fetch_ok, publisher=pub, generator=self.gen)
        self.assertEqual(len(n), 1)

    def test_workflow_commits_pause_file(self):
        wf = (Path(__file__).parent / ".github/workflows/zutsu_threads.yml").read_text(encoding="utf-8")
        self.assertIn("zutsu_threads_pause.json", wf)


class TestMisc(unittest.TestCase):
    def test_format_mix_avoids_three_in_a_row(self):
        rs = [post(10, "あ" * 20), post(60, "い" * 20)]     # short, short
        for seed in range(50):
            fmt, _ = D.choose_format_and_type(rs, "07:30", random.Random(seed))
            self.assertNotEqual(fmt, "short")

    def test_format_distribution_roughly(self):
        # 2026-11-26 masa指示「長文になる場合はツリー投稿で」: 短文4割・中くらい3割・ツリー3割
        c = {"short": 0, "medium": 0, "tree": 0}
        rng = random.Random(0)
        for _ in range(2000):
            f, _ = D.choose_format_and_type([], "17:30", rng); c[f] += 1
        self.assertGreater(c["short"] / 2000, 0.32); self.assertGreater(c["medium"] / 2000, 0.22)
        self.assertGreater(c["tree"] / 2000, 0.22)
        self.assertNotIn("long", D.FORMATS)    # 2026-11-10「文章長すぎ」で長文はやめた

    def test_parse_ts(self):
        self.assertEqual(D.parse_ts("2026-11-02T03:00:00+0000").hour, 12)
        self.assertIsNone(D.parse_ts("garbage"))

    def test_prompt_contains_rules_and_facts(self):
        s, u = D.build_prompts("short", "相談の入口", "20:15", True, ["x"])
        self.assertIn("一人称は「僕」", s); self.assertIn("頭痛の事実", s)
        self.assertIn("ハッシュタグ", s)

    def test_rules_file_cta_matches_constant(self):
        self.assertIn(D.CTA_TEXT, D.RULES_FILE.read_text(encoding="utf-8"))

    def test_schedule_entries_wired(self):
        t = json.loads((Path(__file__).parent / "schedule_table.json").read_text())
        es = [e for e in t["entries"] if e.get("workflow") == "zutsu_threads.yml"]
        self.assertEqual(sorted(e["jst_time"] for e in es), D.SLOTS)
        for e in es:
            self.assertEqual(e["inputs"]["slot"], e["jst_time"]); self.assertEqual(e["since"], "slot")
        wf = (Path(__file__).parent / ".github/workflows/zutsu_threads.yml").read_text()
        self.assertNotIn("cron:", wf)



class TestSafetyLine(unittest.TestCase):
    """中くらい・長文には受診の目安の1行を必ず入れる（guides/headache_facts.md #23）"""
    def gen_(self, text):
        return lambda s, u: json.dumps({"text": text, "type": "t", "has_cta": False}, ensure_ascii=False)

    T = D.THEMES[0]   # 緊張型（※を付けるテーマ）

    def test_medium_without_safety_line_rejected(self):
        p, reason = D.generate_post("07:30", "medium", "t", False, [], [], self.gen_(MEDIUM_GOOD), self.T, D.SAFETY_LINE)
        self.assertIsNone(p); self.assertIn("受診の目安", reason)

    def test_medium_with_safety_line_ok(self):
        p, _ = D.generate_post("07:30", "medium", "t", False, [], [], self.gen_(MEDIUM_GOOD + "\n" + D.SAFETY_LINE),
                               self.T, D.SAFETY_LINE)
        self.assertIsNotNone(p)

    def test_safety_line_rejected_when_not_asked(self):
        """2026-11-10 masa指示「※で同じ文章を毎回上げすぎ」"""
        p, reason = D.generate_post("07:30", "medium", "t", False, [], [], self.gen_(MEDIUM_GOOD + "\n" + D.SAFETY_LINE))
        self.assertIsNone(p); self.assertIn("※", reason)

    def test_too_long_medium_rejected(self):
        p, reason = D.generate_post("07:30", "medium", "t", False, [], [], self.gen_(LONG_GOOD))
        self.assertIsNone(p); self.assertIn("長すぎる", reason)

    def test_short_does_not_need_it(self):
        p, _ = D.generate_post("07:30", "short", "t", False, [], [], self.gen_(GOOD))
        self.assertIsNotNone(p)

    def test_safety_only_for_type_themes_once_a_day(self):
        self.assertEqual(D.safety_line_for("short", self.T, [], NOW), "")
        self.assertEqual(D.safety_line_for("medium", D.THEMES[6], [], NOW), "")          # 枕の話には付けない
        self.assertEqual(D.safety_line_for("medium", D.EMERGENCY_THEME, [], NOW), "")    # 相談先は本文がその話
        self.assertEqual(D.safety_line_for("medium", self.T, [], NOW), D.SAFETY_LINES[0])
        today = [{"text": "本文\n" + D.SAFETY_LINES[0], "timestamp": ts(NOW - timedelta(hours=3))}]
        self.assertEqual(D.safety_line_for("medium", self.T, today, NOW), "")             # 1日1本まで
        yday = [{"text": "本文\n" + D.SAFETY_LINES[0], "timestamp": ts(NOW - timedelta(days=1))}]
        self.assertEqual(D.safety_line_for("medium", self.T, yday, NOW), D.SAFETY_LINES[1])   # 言い方を替える

    def test_prompt_asks_for_safety_line_only_when_given(self):
        _, u = D.build_prompts("medium", "t", "07:30", False, [], safety=D.SAFETY_LINES[1])
        self.assertIn(D.SAFETY_LINES[1], u)
        _, u = D.build_prompts("medium", "t", "07:30", False, [])
        self.assertNotIn(D.SAFETY_LINE, u); self.assertIn("※", u)

    def test_rules_and_facts_files_have_safety_line(self):
        r = D.RULES_FILE.read_text(encoding="utf-8")
        for x in D.SAFETY_LINES:
            self.assertIn(x, r)

    def test_token_registered_for_refresh(self):
        import token_manager, auth
        self.assertEqual(token_manager.ACCOUNTS["zutsu"]["token_key"], "THREADS_ACCESS_TOKEN_ZUTSU")
        self.assertEqual(auth.ACCOUNT_CONFIG["zutsu"]["name"], "@truth_nagaoka_zutsuu")
        wf = (Path(__file__).parent / ".github/workflows/auto_post.yml").read_text(encoding="utf-8")
        self.assertIn("THREADS_ACCESS_TOKEN_ZUTSU=${{ secrets.THREADS_ACCESS_TOKEN_ZUTSU }}", wf)

class TestTrialRun20261105(unittest.TestCase):
    """2026-11-05 試し生成(5本中公開OK 0本)ですり抜けた言い回しとテーマの偏り"""
    def errs(self, t):
        return D.inspect_text(t, False)

    def test_slipped_phrases_now_ng(self):
        self.assertTrue(any("あおる" in e for e in self.errs("雨の前に頭が重い。いつもの頭痛と違う何かが起きているのかもしれません。")))
        self.assertTrue(any("示唆" in e for e in self.errs("起きる時刻を整えると楽になる人も多いんです。そうなんです。")))
        self.assertTrue(any("体験" in e for e in self.errs("僕も気圧の変化には敏感です。だから分かります。")))
        self.assertTrue(any("誘導" in e for e in self.errs("気になることがあれば一度相談してみませんか。")))
        self.assertTrue(any("対処" in e for e in self.errs("早めに寝て、朝日を浴びてみてください。それだけです。")))
        self.assertTrue(any("示唆" in e for e in self.errs("寝る前に首を温めると、朝の重さが違ってくることもあります。")))
        self.assertTrue(any("対処" in e for e in self.errs("タオルを濡らしてレンジで30秒〜1分温めます。")))
        self.assertEqual(self.errs("20分ごとに20秒、遠くを見てみてください。目が休まります。"), [])

    def test_doctor_consult_still_ok(self):
        self.assertEqual(self.errs("痛み止めの日が増えたら、医師や薬剤師に相談してみてください。"), [])

    def test_theme_not_repeated_same_day(self):
        th = [t for t in D.THEMES if t not in D.STORE_THEMES]   # 2026-11-26: 店・周りの市のテーマは地域の枠だけ
        entries = [{"ts": NOW.isoformat(), "theme": t} for t in th[:-1]]
        for seed in range(20):
            self.assertEqual(D.choose_theme(entries, NOW, random.Random(seed)), th[-1])

    def test_yesterdays_theme_can_be_reused(self):
        y = NOW - timedelta(days=1)
        entries = [{"ts": y.isoformat(), "theme": t} for t in D.THEMES]
        self.assertIn(D.choose_theme(entries, NOW, random.Random(0)), D.THEMES)

    def test_theme_in_prompt_and_log(self):
        _, u = D.build_prompts("short", "t", "07:30", False, [], theme=D.THEMES[0])
        self.assertIn(D.THEMES[0], u)

    def test_no_free_text_consult_type(self):
        self.assertNotIn("相談の入口", D.TYPES)


MEDIUM_GOOD = ("デスクワークの夕方、頭の両側が締めつけられる。\n"
               "そんな日は、20分ごとに20秒、遠くを見てみてください。\n"
               "画面は目線のやや下に置くのが目安と言われています。")


class TestCliche20261110(unittest.TestCase):
    """2026-11-10 masa指示「ありきたりな文章使いすぎ」"""
    def test_cliches_always_rejected(self):
        # 型の名前は常にNG
        self.assertTrue(D.stock_phrase_errors("今日からできる小さなコツは、遠くを見ること。", []))

    def test_soft_phrases_rotate_20261126(self):
        # 2026-11-26 masa指示「使わないという選択肢を持ってもいいけど話し言葉で回してみて」: 本人が挙げた言い回しは直近10本に無ければ可
        t = "暗い場所で休むのが一番です。"
        self.assertEqual([e for e in D.stock_phrase_errors(t, []) if e.startswith("言い回し")], [])
        self.assertTrue([e for e in D.stock_phrase_errors(t, ["静かな部屋で休むのが一番です。"]) if e.startswith("言い回し")])
        self.assertEqual([e for e in D.stock_phrase_errors(t, ["x"] * 10 + ["静かな部屋で休むのが一番です。"]) if e.startswith("言い回し")], [])

    def test_similar_opening_rejected(self):
        recent = ["スマホを見ていると、首の片側が痛くなる。そんなときは枕を。"]
        errs = D.stock_phrase_errors("スマホを見ていると、首の片側が重くなる。画面は目線のやや下に。", recent)
        self.assertTrue(any("書き出し" in e for e in errs))

    def test_theme_avoids_last_two_days(self):
        th = [t for t in D.THEMES if t not in D.STORE_THEMES]
        e = [{"ts": ts(NOW - timedelta(days=1)), "theme": t} for t in th[:-1]]
        for seed in range(20):
            self.assertEqual(D.choose_theme(e, NOW, random.Random(seed)), th[-1])


class TestFacts7119(unittest.TestCase):
    def test_7119_must_be_niigata_pref(self):
        self.assertTrue(D.inspect_text("夜に迷ったら長岡市の#7119へ。電話で確かめてから。", False))
        self.assertFalse(any("7119" in e for e in D.inspect_text("夜に迷ったら新潟県の救急医療電話相談 #7119 へ。", False)))


class TestNagaokaLocal20261110(unittest.TestCase):
    """2026-11-10 masa指示「文章長すぎ」「長岡市で頭痛に悩んでいる人のためになる情報も発信するようにして」"""
    def test_local_slot_uses_local_theme(self):
        for seed in range(30):
            self.assertIn(D.choose_theme([], NOW, random.Random(seed), D.LOCAL_SLOT), D.LOCAL_THEMES)

    def test_emergency_theme_at_most_weekly(self):
        e = [{"ts": (NOW - timedelta(days=3)).isoformat(), "theme": D.EMERGENCY_THEME}]
        for seed in range(30):
            self.assertNotEqual(D.choose_theme(e, NOW, random.Random(seed), D.LOCAL_SLOT), D.EMERGENCY_THEME)
        old = [{"ts": (NOW - timedelta(days=8)).isoformat(), "theme": D.EMERGENCY_THEME}]
        self.assertIn(D.EMERGENCY_THEME, {D.choose_theme(old, NOW, random.Random(s), D.LOCAL_SLOT) for s in range(60)})

    def test_7119_allowed_but_hashtag_ng(self):
        self.assertEqual(D.inspect_text("夜に急な頭痛で迷ったら、新潟県の#7119で相談できます。", False), [])
        self.assertTrue(any("ハッシュタグ" in x for x in D.inspect_text("長岡の冬は #頭痛 が増える季節です。", False)))

    def test_local_prompt_mentions_region(self):
        _, u = D.build_prompts("medium", "t", D.LOCAL_SLOT, False, [], theme=D.LOCAL_THEMES[0])
        self.assertIn("長岡", u)
        self.assertIn("#7119", D.FACTS_FILE.read_text(encoding="utf-8"))


class TestFixedCta20261117(unittest.TestCase):
    """2026-11-17 20:15枠: LLMが案内の固定文を一字一句写せず10回NG→見送り。固定文はプログラムで付ける"""
    def test_paraphrased_cta_is_replaced(self):
        for t in ["雨の前は予定を軽めに。気になる方は公式LINEで『診断』と送ってね。",
                  "雨の前は予定を軽めに。\n自分の頭痛のタイプが気になる方は、公式LINE(@561yryuk)で「診断」と送ってください。",
                  "雨の前は予定を軽めに。\n" + D.CTA_TEXT + "\n" + D.SAFETY_LINE]:
            r = D.attach_fixed_cta(t)
            self.assertTrue(r.endswith(D.CTA_TEXT))
            self.assertEqual(r.count(D.CTA_TEXT), 1)
            self.assertEqual([e for e in D.inspect_text(r, True) if "案内" in e], [])

    def test_generate_post_passes_with_paraphrased_cta(self):
        raw = json.dumps({"text": GOOD + "\n気になる方は公式LINEで診断と送ってください。", "type": "tips", "has_cta": True}, ensure_ascii=False)
        post, reason = D.generate_post("20:15", "medium", "tips", True, [], [], lambda s, u: raw, "", "")
        self.assertIsNotNone(post, reason)
        self.assertTrue(post["text"].endswith(D.CTA_TEXT))


class TestRules20261126(Base):
    """2026-11-26 masa指示: 20本・ツリー・締めの試し分け・フォロー/保存の誘い。"""
    def setUp(self):
        super().setUp()
        os.environ["THREADS_ACCESS_TOKEN_ZUTSU"] = "dummy"
        os.environ["THREADS_USER_ID_ZUTSU"] = "123"
        self.published, self.replies = [], []

    def pub(self, token, uid, text):
        self.published.append(text); return "pid1"

    def rep(self, token, uid, text, reply_to):
        self.replies.append((reply_to, text)); return f"r{len(self.replies)}"

    def test_twenty_slots(self):
        self.assertEqual(len(D.SLOTS), 20); self.assertEqual(D.DAILY_CAP, 20)
        mins = [int(x[:2]) * 60 + int(x[3:]) for x in D.SLOTS]
        self.assertEqual(mins, sorted(mins))
        self.assertGreaterEqual(min(b - a for a, b in zip(mins, mins[1:])), D.MIN_GAP_MIN)
        self.assertTrue(set(D._OLD_SLOTS_20261114) <= set(D.SLOTS))

    def test_tree_publishes_replies_in_chain(self):
        def gen(system, user):
            return json.dumps({"text": "「夕方になると頭が重い」。その重さ、2つに分かれます。",
                               "replies": [with_safety(user, "締めつけられる重さは、肩や首のこりがきっかけのことがあります。"),
                                           "ズキズキして動くと響くなら、暗く静かな場所で休むのが合うと言われています。"],
                               "type": "頭痛の種類と見分け方の情報", "has_cta": False}, ensure_ascii=False)
        rng = random.Random(3)
        orig = D.choose_format_and_type
        D.choose_format_and_type = lambda recent, slot, r: ("tree", "頭痛の種類と見分け方の情報")
        try:
            self.run_(slot="13:15", now=NOW.replace(hour=13, minute=15), fetch=lambda t, n: [post(100, now=NOW.replace(hour=13, minute=15))],
                      publisher=self.pub, generator=gen, reply_publisher=self.rep, rng=rng)
        finally:
            D.choose_format_and_type = orig
        self.assertEqual(len(self.published), 1)
        self.assertEqual([r[0] for r in self.replies], ["pid1", "r1"])   # 親→返信1→返信2 の順につなぐ
        rec = D.read_log(self.log)[0]
        self.assertEqual(rec["fmt"], "tree"); self.assertEqual(rec["reply_ids"], ["r1", "r2"])

    def test_line_cta_variant_least_used(self):
        now = NOW
        entries = [{"ts": (now - timedelta(days=1)).isoformat(), "cta_id": k} for k in ("L1", "L2", "L3")]
        self.assertEqual(D.choose_cta_variant("line", entries, now, random.Random(0)), "L4")
        for v in D.LINE_CTA_VARIANTS.values():
            self.assertIn("@561yryuk", v); self.assertIn("『診断』", v)
            self.assertNotIn("相談", v)   # 自由文の相談には誘わない
            self.assertEqual(D.inspect_text("頭痛のタイプ、気になりますね。\n" + v, True), [])

    def test_other_cta_attached_and_logged(self):
        gen = lambda s, u: json.dumps({"text": with_safety(u, GOOD), "type": "t", "has_cta": False}, ensure_ascii=False)
        t = NOW.replace(hour=8, minute=30)
        self.run_(slot="08:30", now=t, fetch=lambda tk, n: [post(100, now=t)], publisher=self.pub, generator=gen)
        rec = D.read_log(self.log)[0]
        self.assertIn(rec["cta_id"], D.OTHER_CTA_VARIANTS)
        self.assertTrue(self.published[0].rstrip().endswith(D.OTHER_CTA_VARIANTS[rec["cta_id"]]))
        for v in D.OTHER_CTA_VARIANTS.values():
            self.assertEqual(D.inspect_text(GOOD + "\n\n" + v, False), [], v)

    def test_other_cta_daily_max(self):
        t = NOW.replace(hour=22, minute=30)
        entries = [{"ts": (t - timedelta(hours=k + 1)).isoformat(), "cta_id": "F1"} for k in range(D.OTHER_CTA_DAILY_MAX)]
        self.assertFalse(D.other_cta_ok(entries, [post(100, now=t)], "22:30", t))
        self.assertFalse(D.other_cta_ok([], [post(100, now=t)], "18:15", t))   # 枠の外

    def test_local_slots_use_local_and_store_themes(self):
        for slot in D.LOCAL_SLOTS:
            th = D.choose_theme([], NOW, random.Random(0), slot)
            self.assertIn(th, D.LOCAL_THEMES)
        self.assertTrue(any("長岡駅から車で5分" in x for x in D.LOCAL_THEMES))
        self.assertTrue(any("見附" in x for x in D.LOCAL_THEMES))


if __name__ == "__main__":
    unittest.main()


class TestImagePosts20261111(Base):
    """2026-11-11 masa指示「Thredsの投稿を1日10投稿を目標にして その際に画像の解説も取り入れて投稿するようにして」"""
    FIG = {"title": "画面と目の疲れ", "points": ["画面は目線のやや下に置く", "20分ごとに20秒、遠くを見る", "首の後ろを温める"]}
    TEXT = "夕方、画面を見続けた日の頭の重さ。目の疲れがきっかけになることがあります。図の3つを、作業の区切りに順番に試す。画面の高さは今日のうちに。"

    def setUp(self):
        super().setUp()
        os.environ["THREADS_ACCESS_TOKEN_ZUTSU"] = "t"; os.environ["THREADS_USER_ID_ZUTSU"] = "u"
        self._base, self._push = D.BASE, D.push_figure
        D.BASE = Path(self.tmp.name)
        D.notify = lambda m: None

    def tearDown(self):
        D.BASE, D.push_figure = self._base, self._push
        super().tearDown()

    def gen(self, fig=FIG):
        return lambda s, u: json.dumps({"text": self.TEXT, "type": "t", "has_cta": False, "figure": fig}, ensure_ascii=False)

    def test_ten_slots_three_images(self):
        # 2026-11-26 masa指示「投稿は20投稿にして」で10→20枠。画像つきは3枠のまま
        self.assertEqual(len(D.SLOTS), 20); self.assertEqual(D.DAILY_CAP, 20)
        self.assertTrue(D.IMAGE_SLOTS <= set(D.SLOTS)); self.assertEqual(len(D.IMAGE_SLOTS), 3)
        self.assertEqual(D.choose_format_and_type([], "14:30", random.Random(0))[0], "image")

    def test_image_slot_posts_figure_with_explanation(self):
        D.push_figure = lambda p: True
        got = {}
        def img_pub(tok, uid, text, url):
            got["url"], got["text"] = url, text; return "IMG1"
        def txt_pub(*a): raise AssertionError("文字だけで出してはいけない")
        self.run_(slot="14:30", now=NOW.replace(hour=14, minute=30), fetch=lambda t, n: [],
                  generator=self.gen(), publisher=txt_pub, image_publisher=img_pub)
        self.assertTrue(got["url"].startswith(D.RAW_IMG) and got["url"].endswith(".png"))
        e = json.loads(self.log.read_text().splitlines()[-1])
        self.assertEqual(e["id"], "IMG1"); self.assertEqual(e["fmt"], "image"); self.assertIn("image", e)
        self.assertTrue((D.BASE / "images" / "zutsu" / e["image"]).exists())

    def test_image_failure_falls_back_to_text(self):
        D.push_figure = lambda p: True
        def img_pub(*a): raise RuntimeError("image fetch failed")
        sent = []
        self.run_(slot="14:30", now=NOW.replace(hour=14, minute=30), fetch=lambda t, n: [],
                  generator=self.gen(), publisher=lambda t, u, x: sent.append(x) or "TXT1", image_publisher=img_pub)
        self.assertEqual(sent, [self.TEXT])
        self.assertNotIn("image", json.loads(self.log.read_text().splitlines()[-1]))

    def test_bad_figure_rejected(self):
        p, reason = D.generate_post("14:30", "image", "t", False, [], [], self.gen({"title": "", "points": ["1つだけ"]}))
        self.assertIsNone(p); self.assertIn("figure", reason)
        p, reason = D.generate_post("14:30", "image", "t", False, [], [],
                                    self.gen({"title": "頭痛が治る3つの方法", "points": ["枕を変える", "温める"]}))
        self.assertIsNone(p); self.assertIn("図の文字", reason)

    def test_figure_renders(self):
        from zutsu_figure import render
        out = render(self.FIG, Path(self.tmp.name) / "f.png")
        from PIL import Image
        self.assertEqual(Image.open(out).size, (1080, 1350))
