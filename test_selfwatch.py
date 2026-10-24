#!/usr/bin/env python3
"""10/23 型（常駐は動いているのに同じ候補を見送り続けて投稿ゼロ）の模擬テスト（2026-10-24）。
gh・Discord・時刻はモック。実行: python3 test_selfwatch.py"""
import sys, tempfile, time, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auto_post as ap
import stall_check as sc
import verify_system as vs

JST = timezone(timedelta(hours=9))


class SelfWatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p1 = mock.patch.object(ap, "SELFWATCH_STATE", Path(self.tmp) / "sw.json")
        self.p1.start()
        self.sent = []
        self.p2 = mock.patch.object(ap, "_notify_discord", lambda m: self.sent.append(m) or True)
        self.p2.start()
        self.p3 = mock.patch.object(ap, "log_info", lambda a, m: None)
        self.p3.start()

    def tearDown(self):
        mock.patch.stopall()

    def test_same_candidate_excluded_after_3_in_hour(self):
        t = "自分の頭痛のタイプ、答えられますか？\n詳細"
        for _ in range(2):
            ap._note_skip("truth", t, "重複スキップ")
        self.assertNotIn(ap._firstline_key(t), ap._selfwatch_excluded("truth"))
        ap._note_skip("truth", t, "同日同文")
        self.assertIn(ap._firstline_key(t), ap._selfwatch_excluded("truth"))
        self.assertEqual(ap._selfwatch_excluded("nagaoka"), set())

    def test_old_skips_do_not_count(self):
        t = "別の候補です\n本文"
        now = time.time()
        with mock.patch("time.time", return_value=now - 7200):
            ap._note_skip("truth", t, "重複スキップ")
            ap._note_skip("truth", t, "重複スキップ")
        ap._note_skip("truth", t, "重複スキップ")
        self.assertNotIn(ap._firstline_key(t), ap._selfwatch_excluded("truth"))

    def test_streak_alert_at_3_cycles_and_cooldown(self):
        t = "自分の頭痛のタイプ、答えられますか？"
        ap._note_skip("truth", t, "重複スキップ")
        ap._selfwatch_cycle("truth", 8, 40, 40)
        ap._selfwatch_cycle("truth", 8, 40, 40)
        self.assertEqual(self.sent, [])
        ap._selfwatch_cycle("truth", 8, 40, 40)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("重複スキップ", self.sent[0])
        self.assertIn("自分の頭痛のタイプ", self.sent[0])
        ap._selfwatch_cycle("truth", 8, 40, 40)   # 2時間に1回まで
        self.assertEqual(len(self.sent), 1)

    def test_streak_resets_when_posted_or_no_need(self):
        ap._selfwatch_cycle("truth", 8, 40, 40)
        ap._selfwatch_cycle("truth", 8, 40, 41)   # 増えた→リセット
        ap._selfwatch_cycle("truth", 8, 41, 41)
        ap._selfwatch_cycle("truth", 0, 41, 41)   # 不足なし→リセット
        ap._selfwatch_cycle("truth", 8, 41, 41)
        ap._selfwatch_cycle("truth", 8, 41, 41)
        self.assertEqual(self.sent, [])


class AliveStall(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.send = lambda m: self.sent.append(m) or True
        mock.patch.object(sc, "latest_run_url", lambda: "https://example/run/1").start()
        mock.patch.object(sc, "alive_thresholds", lambda a, h=None: (60, 90)).start()

    def tearDown(self):
        mock.patch.stopall()

    def test_below_thresholds_nothing(self):
        st = {}
        self.assertIsNone(sc.handle_alive_stall("truth", 59, 9, st, self.send))
        self.assertIsNone(sc.handle_alive_stall("truth", 70, 4, st, self.send))
        self.assertEqual(self.sent, [])

    def test_60min_notifies_once_per_2h_without_restart(self):
        st = {}
        with mock.patch.object(sc, "restart_keeper") as rk:
            self.assertEqual(sc.handle_alive_stall("truth", 65, 7, st, self.send, now_ts=1e9), "notify")
            self.assertIsNone(sc.handle_alive_stall("truth", 70, 7, st, self.send, now_ts=1e9 + 600))
            self.assertEqual(sc.handle_alive_stall("truth", 70, 7, st, self.send, now_ts=1e9 + 7300), "notify")
            rk.assert_not_called()
        self.assertEqual(len(self.sent), 2)
        self.assertIn("候補の選び直しで止まっている可能性", self.sent[0])
        self.assertIn("https://example/run/1", self.sent[0])

    def test_90min_restarts_max_twice_per_day(self):
        st = {}
        with mock.patch.object(sc, "restart_keeper", return_value=(True, "ok")) as rk:
            for i in range(3):
                sc.handle_alive_stall("truth", 95, 9, st, self.send, today="2026-10-24", now_ts=1e9 + i * 8000)
            self.assertEqual(rk.call_count, 2)
        self.assertEqual(st["alive_restart_count"], 2)
        # 翌日はリセット
        with mock.patch.object(sc, "restart_keeper", return_value=(True, "ok")) as rk:
            sc.handle_alive_stall("truth", 95, 9, st, self.send, today="2026-10-25", now_ts=1e9 + 99999)
            self.assertEqual(rk.call_count, 1)

    def test_restart_does_cancel_then_dispatch(self):
        calls = []
        started = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

        def fake_gh(args, timeout=30):
            calls.append(args)
            class R: returncode = 0; stderr = ""; stdout = ""
            r = R()
            if args[0] == "run" and args[1] == "list":
                n = sum(1 for c in calls if c[:2] == ["run", "list"])
                # 1回目=稼働中あり、cancel後は in_progress/queued とも空
                r.stdout = '[{"databaseId": 111, "startedAt": "%s", "createdAt": "%s"}]' % (started, started) if n == 1 else "[]"
            return r
        with mock.patch.object(sc, "_gh_run", fake_gh):
            ok, why = sc.restart_keeper(sleep=lambda s: None)
        self.assertTrue(ok, why)
        self.assertTrue(any(c[:2] == ["run", "cancel"] and "111" in c for c in calls))
        self.assertTrue(any(c[:2] == ["workflow", "run"] and "auto_post.yml" in c for c in calls))
        cancel_i = next(i for i, c in enumerate(calls) if c[:2] == ["run", "cancel"])
        disp_i = next(i for i, c in enumerate(calls) if c[:2] == ["workflow", "run"])
        self.assertLess(cancel_i, disp_i)

    def test_restart_skips_young_run_and_chained_run(self):
        young = (datetime.now(timezone.utc) - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        calls = []
        def gh_young(args, timeout=30):
            calls.append(args)
            class R: returncode = 0; stderr = ""; stdout = '[{"databaseId": 5, "startedAt": "%s"}]' % young
            return R()
        with mock.patch.object(sc, "_gh_run", gh_young):
            ok, _ = sc.restart_keeper(sleep=lambda s: None)
        self.assertFalse(ok)
        self.assertFalse(any(c[:2] == ["run", "cancel"] for c in calls))


class VerifyC41(unittest.TestCase):
    def test_incident_1023_detected(self):
        lo = datetime(2026, 10, 23, 6, 0, tzinfo=JST); hi = lo.replace(hour=23)
        posts = [lo + timedelta(minutes=m) for m in range(30, 790, 20)] + [lo.replace(hour=22, minute=57)]
        posts = [p for p in posts if not (lo.replace(hour=19, minute=8) < p < lo.replace(hour=22, minute=57))]
        spans = [(lo - timedelta(hours=1), hi)]
        inc = vs.alive_stall_incidents(posts, spans, lo, hi)
        self.assertTrue(inc and inc[0][0] >= 200, inc)
        # 常駐が動いていなかった空白は対象外（C18の担当）
        inc2 = vs.alive_stall_incidents(posts, [(lo, lo.replace(hour=18))], lo, hi)
        self.assertEqual(inc2, [])


# ── 2026-10-24 fix1〜3（台帳照合キー pk / まとめて回復の前進 / API重複窓）─────────────────
# フィクスチャは実台帳 log_truth_posted.jsonl:7204(10/14)・:7621(10/23) とキュー原文（10/14・15・20・23 の詰まり／
# 047538d6 以前は19時台の二重投稿を起こした診断アンカー）。ネットワークには一切触れない。
SHINDAN_Q = ("自分の頭痛のタイプ、答えられますか。\n緊張型か、片頭痛か、薬の使いすぎか。\n"
             "10問・60秒でわかる無料診断を作りました👇\nhttps://truthbodysalon-lab.github.io/zutsu-shindan/")
LEDGER_1014 = {"date": "2026-10-14", "index": 6, "post_id": "18110464391607555",
               "text": "自分の頭痛のタイプ、答えられますか？\n緊張型か、片頭痛か、薬の使いすぎか。", "is_shindan": True}
LEDGER_1023 = {"date": "2026-10-23", "index": 6, "post_id": "17912037033556535",
               "text": "自分の頭痛のタイプ、答えられますか？\n緊張型か、片頭痛か、薬の使いすぎか。",
               "hk": "d237b23c52564c9e", "is_shindan": True}
NORMAL_A = "fix123テスト専用の通常投稿Aです。\n肩の力を抜く姿勢を一つだけ試してみてください。"
NORMAL_B = "fix123テスト専用の通常投稿Bです。\n首を温めるタイミングを一つだけ変えてみてください。"


class _QueueFixture(unittest.TestCase):
    DAY = "2099-01-02"

    def setUp(self):
        import json as _j
        self._j = _j
        self.tmp = Path(tempfile.mkdtemp())
        self.lg, self.pf = self.tmp / "log.jsonl", self.tmp / "posted.jsonl"
        self.pf.write_text("")
        for k, v in (("log", self.lg), ("posted", self.pf)):
            mock.patch.dict(ap.ACCOUNTS["truth"], {k: v}).start()
        mock.patch.object(ap, "_recent_api_firstlines", lambda acct, hours=12: set()).start()   # API窓外
        mock.patch.object(ap, "_hero_priority_order", lambda a, t, posts, pt: list(range(len(posts)))).start()
        mock.patch.object(ap, "_selfwatch_excluded", lambda a: set()).start()
        mock.patch.object(ap, "dg_is_duplicate", lambda n, a: False).start()
        mock.patch.object(ap, "dg_marked_today", lambda n, a: False).start()
        mock.patch.object(ap, "_line_done_today", lambda a, t: True).start()
        mock.patch.object(ap, "SELFWATCH_STATE", self.tmp / "sw.json").start()
        mock.patch.object(ap, "log_info", lambda a, m: None).start()
        ap._RUN_BLOCKED_FIRSTLINES.pop("truth", None)
        ap._BATCH_SKIP_FIRSTLINES.pop("truth", None)

    def tearDown(self):
        mock.patch.stopall()
        ap._RUN_BLOCKED_FIRSTLINES.pop("truth", None)
        ap._BATCH_SKIP_FIRSTLINES.pop("truth", None)

    def queue(self, posts, day=None):
        self.lg.write_text(self._j.dumps({"account": "truth", "date": day or self.DAY, "posts": posts},
                                         ensure_ascii=False) + "\n")

    def ledger(self, *entries):
        self.pf.write_text("".join(self._j.dumps(e, ensure_ascii=False) + "\n" for e in entries))


class Fix1LedgerKey(_QueueFixture):
    def test_old_entry_text_does_not_match_queue_key(self):
        """前提の再現: 旧エントリー(10/14・10/23 実データ)の text はキュー原文の照合キーと一致しない。"""
        norm = ap._normalize_post_key(SHINDAN_Q)
        self.ledger(LEDGER_1014, LEDGER_1023)
        self.assertNotIn(norm, ap.get_posted_texts("truth"))

    def test_mark_posted_records_pk_and_matches(self):
        clean = LEDGER_1023["text"]
        ap.mark_posted("truth", self.DAY, 6, "pid1", clean, is_shindan=True, orig_text=SHINDAN_Q)
        e = self._j.loads(self.pf.read_text().splitlines()[-1])
        self.assertEqual(e["text"], clean)                          # text は従来どおり
        self.assertEqual(e["pk"], ap._normalize_post_key(SHINDAN_Q))
        self.assertIn(ap._normalize_post_key(SHINDAN_Q), ap.get_posted_texts("truth"))
        self.assertIn(e["pk"], ap._today_ledger_pkeys("truth", self.DAY))
        self.assertEqual(ap._today_ledger_pkeys("truth", "2099-01-03"), set())

    def test_backward_compatible_without_pk(self):
        self.ledger(LEDGER_1014, {"date": "2026-10-01", "index": 1, "post_id": "p", "text": NORMAL_A.split("\n")[0]})
        texts = ap.get_posted_texts("truth")
        self.assertIn(LEDGER_1014["text"], texts)
        self.assertEqual(ap._today_ledger_pkeys("truth", "2026-10-14"), set())

    def _same_day_pk_entry(self):
        return dict(LEDGER_1023, date=self.DAY, pk=ap._normalize_post_key(SHINDAN_Q))

    def test_no_duplicate_post_same_day_even_without_firstline_guard(self):
        """047538d6 以前の挙動（同日1文目ガードも投稿直前チェックも無い）でも、pk で同日再選択＝二重投稿しない。"""
        self.queue([SHINDAN_Q, NORMAL_A])
        self.ledger(self._same_day_pk_entry())
        with mock.patch.object(ap, "_today_ledger_firstline_keys", lambda a, t: set()):
            t, _ = ap.get_next_post("truth", self.DAY)
        self.assertEqual(t, NORMAL_A)

    def test_without_fix_the_anchor_would_be_reselected(self):
        """対照: pk 無し（旧台帳）＋1文目ガード無し＋API窓外 → 診断アンカーが再選択される＝修正前の不具合。"""
        self.queue([SHINDAN_Q, NORMAL_A])
        self.ledger(dict(LEDGER_1023, date=self.DAY))
        with mock.patch.object(ap, "_today_ledger_firstline_keys", lambda a, t: set()):
            t, _ = ap.get_next_post("truth", self.DAY)
        self.assertEqual(t, SHINDAN_Q)

    def test_no_stall_anchor_not_selected_with_current_code(self):
        """詰まり側: 本日投稿済み(pk付き)なら選択段階で外れ、通常投稿へ進む（見送りループにならない）。"""
        self.queue([SHINDAN_Q, NORMAL_A, NORMAL_B])
        self.ledger(self._same_day_pk_entry())
        t, _ = ap.get_next_post("truth", self.DAY)
        self.assertEqual(t, NORMAL_A)

    def test_anchor_still_available_next_day(self):
        """pk で全期間一致しても、アンカーは翌日には緩和ルート(anchor_ok)で選べる（アンカーを永久に落とさない）。"""
        self.queue([SHINDAN_Q, NORMAL_A])
        self.ledger(dict(LEDGER_1023, pk=ap._normalize_post_key(SHINDAN_Q)))   # 10/23 に投稿済み
        t, _ = ap.get_next_post("truth", self.DAY)
        self.assertEqual(t, SHINDAN_Q)

    def test_normal_post_with_question_ending_not_reposted(self):
        q = "首こり、放っておいて大丈夫ですか。\nfix123テスト専用の本文です。"
        self.queue([q, NORMAL_A])
        ap.mark_posted("truth", "2099-01-01", 0, "pid", ap._fix_question_endings(q), orig_text=q)
        t, _ = ap.get_next_post("truth", self.DAY)
        self.assertEqual(t, NORMAL_A)


class Fix2CatchupAdvances(_QueueFixture):
    def _run_batch(self, bad_text):
        """遅れ検知（不足大）でまとめて回復を走らせる。run_account は get_next_post で選び、bad_text だけ投稿失敗。"""
        import sys as _s, types
        self.picks = []
        today = __import__("datetime").date.today().strftime("%Y-%m-%d")
        self.queue([bad_text, NORMAL_A, NORMAL_B], day=today)

        def fake_run_account(acct):
            t, i = ap.get_next_post(acct, today)
            if t is None:
                return
            ap._LAST_PICK_KEY[acct] = ap._firstline_key(t)
            self.picks.append(t)
            if t == bad_text:
                return   # 見送り・失敗（投稿されない）
            ap.mark_posted(acct, today, i, f"pid{len(self.picks)}", t.split("\n")[0], orig_text=t)
        fake_wd = types.ModuleType("watchdog_ci"); fake_wd.api_count_today = lambda a: -1
        mock.patch.dict(_s.modules, {"watchdog_ci": fake_wd}).start()
        mock.patch.object(ap, "run_account", fake_run_account).start()
        mock.patch.object(ap, "_target_cumulative_by_now", lambda h: 40).start()
        mock.patch.object(ap.time, "sleep", lambda s: None).start()
        mock.patch("account_ownership.is_local", lambda a: True).start()
        ap._run_account_batch("truth")

    def test_catchup_skips_same_candidate_and_advances(self):
        bad = "fix123テスト専用の失敗する候補です。\n本文。"
        self._run_batch(bad)
        self.assertEqual(self.picks.count(bad), 1, self.picks)    # 2回目以降は選ばない
        self.assertIn(NORMAL_A, self.picks)
        self.assertIn(NORMAL_B, self.picks)                       # 次候補へ進んで投稿できた
        self.assertEqual(ap._posted_count_today("truth"), 2)

    def test_batch_note_attempt(self):
        ap._BATCH_SKIP_FIRSTLINES["truth"] = set()
        self.assertFalse(ap._batch_note_attempt("truth", "k", True))    # 投稿できた→登録しない
        self.assertFalse(ap._batch_note_attempt("truth", None, False))  # 候補なし→登録しない
        self.assertTrue(ap._batch_note_attempt("truth", "k", False))
        self.assertIn("k", ap._BATCH_SKIP_FIRSTLINES["truth"])


class Fix3ApiWindow(unittest.TestCase):
    def test_cutoff_is_longer_of_midnight_and_12h(self):
        utc = timezone.utc
        # 10/23 19:08 JST（12h窓の始点は07:08＝朝の投稿が外れた時刻）→ 0:00 JST が始点
        now = datetime(2026, 10, 23, 19, 8, tzinfo=JST).astimezone(utc)
        self.assertEqual(ap._api_dedup_cutoff(now), datetime(2026, 10, 23, 0, 0, tzinfo=JST).astimezone(utc))
        # 22:59 JST でも 0:00 JST
        now = datetime(2026, 10, 23, 22, 59, tzinfo=JST).astimezone(utc)
        self.assertEqual(ap._api_dedup_cutoff(now), datetime(2026, 10, 23, 0, 0, tzinfo=JST).astimezone(utc))
        # 06:30 JST は 12h の方が長い（前日18:30）
        now = datetime(2026, 10, 24, 6, 30, tzinfo=JST).astimezone(utc)
        self.assertEqual(ap._api_dedup_cutoff(now), datetime(2026, 10, 23, 18, 30, tzinfo=JST).astimezone(utc))

    def _fake_pages(self, posts_jst, per_page=100):
        """新しい順の [(datetime JST, text)] を per_page 件ずつのページに分けた fetch 関数を返す。"""
        items = [{"id": str(i), "text": t, "timestamp": d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")}
                 for i, (d, t) in enumerate(posts_jst)]
        pages = [items[i:i + per_page] for i in range(0, len(items), per_page)]
        self.calls = []

        def fetch(url):
            self.calls.append(url)
            n = int(url[4:]) if url.startswith("page") else 0   # 1ページ目は実URL形式・以降は paging.next
            d = {"data": pages[n] if n < len(pages) else []}
            if n + 1 < len(pages):
                d["paging"] = {"next": f"page{n + 1}"}
            return d
        return fetch

    def test_full_day_covered_with_pagination(self):
        """1日 50本＋外部投稿で100件を超えても、0:00 JST まで遡って朝の診断アンカーを拾う（GETのみ・模擬）。"""
        base = datetime(2026, 10, 23, 22, 59, tzinfo=JST)
        posts = [(base - timedelta(minutes=7 * i), f"通常{i}") for i in range(120)]   # 新しい順に120本（〜09:06）
        posts.append((datetime(2026, 10, 23, 7, 8, tzinfo=JST), SHINDAN_Q))
        posts += [(datetime(2026, 10, 22, 23, 0, tzinfo=JST) - timedelta(minutes=10 * i), f"前日{i}") for i in range(100)]
        fetch = self._fake_pages(posts)
        with mock.patch.dict("os.environ", {"THREADS_ACCESS_TOKEN_TRUTH": "x", "THREADS_USER_ID_TRUTH": "1"}):
            got = ap._api_posts_since("truth", ap._api_dedup_cutoff(base.astimezone(timezone.utc)), fetch=fetch)
        texts = [t for _, t in got]
        self.assertTrue(any(t.startswith("自分の頭痛のタイプ") for t in texts))
        self.assertEqual(len(got), 121)
        self.assertEqual(len(self.calls), 2)          # 2ページ目(101〜200件目)で前日分に届いて停止（3ページ目は取らない）
        self.assertTrue(self.calls[0].startswith(ap.BASE_URL) and "limit=100" in self.calls[0])
        self.assertFalse(any(t.startswith("前日") for t in texts))

    def _api_window_blocks(self, now_jst):
        fetch = self._fake_pages([(datetime(2026, 10, 23, 7, 8, tzinfo=JST), SHINDAN_Q)])
        now_utc = now_jst.astimezone(timezone.utc)
        env = {"THREADS_ACCESS_TOKEN_TRUTH": "x", "THREADS_USER_ID_TRUTH": "1"}
        real = ap._api_posts_since
        cutoff = ap._api_dedup_cutoff(now_utc)
        with mock.patch.dict("os.environ", env), \
             mock.patch.object(ap, "_api_dedup_cutoff", lambda now_utc_=None, hours=12: cutoff), \
             mock.patch.object(ap, "_api_posts_since", lambda a, c, fetch_=None, **k: real(a, c, fetch=fetch)):
            return (ap._recently_posted_on_threads("truth", LEDGER_1023["text"]),
                    ap._firstline_key(SHINDAN_Q) in ap._recent_api_firstlines("truth"))

    def test_duplicate_post_blocked_after_12h(self):
        """二重投稿側: 07:08 の診断アンカーを 19:08・22:57 でも API 窓が捉え、投稿直前チェックと選択側の両方で弾く。"""
        for hh, mm in ((19, 8), (22, 57)):
            self.assertEqual(self._api_window_blocks(datetime(2026, 10, 23, hh, mm, tzinfo=JST)), (True, True))

    def test_previous_day_post_not_blocked_after_window(self):
        """翌日 19:00 には前日 07:08 の投稿は窓外（アンカーを翌日以降も出せる）。"""
        self.assertEqual(self._api_window_blocks(datetime(2026, 10, 24, 19, 0, tzinfo=JST)), (False, False))


if __name__ == "__main__":
    unittest.main(verbosity=1)


def test_c41_trailing_gap_detected():
    """10/23型: 19:07が最終投稿で、以降23時まで常駐稼働中なのに0本 → 検出されること。"""
    from datetime import datetime, timedelta, timezone
    import verify_system as vs
    JST = timezone(timedelta(hours=9))
    lo = datetime(2026, 10, 23, 6, tzinfo=JST); hi = lo.replace(hour=23)
    posts = [lo.replace(hour=h, minute=m) for h in range(6, 19) for m in (5, 25, 45)] + [lo.replace(hour=19, minute=7)]
    spans = [(lo, hi)]
    inc = vs.alive_stall_incidents(posts, spans, lo, hi)
    assert inc and max(i[0] for i in inc) >= 230, inc



def test_c41_gap_after_target_ignored():
    """50本出し終えた後の空白は検出しない。"""
    from datetime import datetime, timedelta, timezone
    import verify_system as vs
    JST = timezone(timedelta(hours=9))
    lo = datetime(2026, 10, 23, 6, tzinfo=JST); hi = lo.replace(hour=23)
    posts = [lo + timedelta(minutes=18 * i) for i in range(50)]  # 50本目は約20:42
    assert not vs.alive_stall_incidents(posts, [(lo, hi)], lo, hi)


if __name__ == "__main__":
    test_c41_trailing_gap_detected(); test_c41_gap_after_target_ignored(); print("C41 extra tests OK")
