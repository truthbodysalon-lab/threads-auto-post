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
