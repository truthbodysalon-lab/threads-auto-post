#!/usr/bin/env python3
"""dispatcher.py の pause_file / queue_file ゲートのテスト（2026-10-18）。
実行: python3 test_dispatcher_gates.py   （ネットワークは使わない。api を差し替える）"""
import base64, json, os, sys, tempfile, unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatcher as D

REPO = "o/r"


def b64(obj):
    return {"content": base64.b64encode(json.dumps(obj).encode()).decode()}


class Fake:
    def __init__(self, files, runs=0, latest=None):
        self.files, self.runs, self.posts, self.latest = files, runs, [], latest

    def __call__(self, method, path, body=None):
        if method == "POST":
            self.posts.append((path, body))
            return 204, {}
        if "/actions/workflows/" in path and "/runs" in path:
            d = {"total_count": self.runs}
            if self.latest:
                d["workflow_runs"] = [self.latest]
            return 200, d
        if path.startswith(f"/repos/{REPO}/contents/"):
            rel = path.split("/contents/", 1)[1]
            if rel not in self.files:
                raise RuntimeError("404")
            return 200, b64(self.files[rel])
        if path == f"/repos/{REPO}":
            return 200, {"default_branch": "main"}
        raise RuntimeError("unexpected " + path)


def run_main(entry, files, now, runs=0, latest=None, tmp=None):
    tmp = tmp or Path(tempfile.mkdtemp())
    (tmp / "t.json").write_text(json.dumps({"mode": "live", "entries": [entry]}))
    D.TABLE, D.STATE = tmp / "t.json", tmp / "s.json"
    D._branch_cache.clear()
    fake = Fake(files, runs, latest)
    D.api = fake
    os.environ["DISPATCH_NOW"] = now
    D.main()
    return fake.posts


E1 = {"id": "feed", "repo": REPO, "workflow": "f.yml", "jst_time": "11:33", "window_min": 90, "since": "day",
      "enabled": True, "pause_file": "state/feed_pause.json"}
E2 = {"id": "reels", "repo": REPO, "workflow": "r.yml", "jst_time": "08:05", "window_min": 90, "since": "day",
      "enabled": True, "queue_file": "state/reels_queue.json"}


class T(unittest.TestCase):
    def test_pause_until_inclusive(self):
        f = {"state/feed_pause.json": {"until": "2026-11-09"}}
        self.assertEqual(run_main(E1, f, "2026-11-09 11:40"), [])      # 当日まで停止
        self.assertEqual(len(run_main(E1, f, "2026-11-10 11:40")), 1)  # 翌日から再開

    def test_pause_early_release_follows_file(self):
        # feed_pause.json の until を前倒ししたら、固定日付に縛られず再開する
        f = {"state/feed_pause.json": {"until": "2026-10-25"}}
        self.assertEqual(len(run_main(E1, f, "2026-10-26 11:40")), 1)

    def test_pause_file_missing_or_broken_fails_open(self):
        self.assertEqual(len(run_main(E1, {}, "2026-10-20 11:40")), 1)
        self.assertEqual(len(run_main(E1, {"state/feed_pause.json": ["x"]}, "2026-10-20 11:40")), 1)
        self.assertEqual(len(run_main(E1, {"state/feed_pause.json": {}}, "2026-10-20 11:40")), 1)

    def test_queue_has_today(self):
        f = {"state/reels_queue.json": {"items": [{"date": "2026-11-09"}, {"date": "2026-11-08"}]}}
        self.assertEqual(len(run_main(E2, f, "2026-11-09 08:10")), 1)
        self.assertEqual(run_main(E2, f, "2026-11-10 08:10"), [])      # 連載終了後は起動しない

    def test_queue_fail_open(self):
        self.assertEqual(len(run_main(E2, {}, "2026-11-10 08:10")), 1)                       # 取得失敗
        self.assertEqual(len(run_main(E2, {"state/reels_queue.json": {"x": 1}}, "2026-11-10 08:10")), 1)  # 形式違い

    def test_existing_run_still_wins(self):
        f = {"state/reels_queue.json": {"items": [{"date": "2026-11-09"}]}}
        self.assertEqual(run_main(E2, f, "2026-11-09 08:10", runs=1), [])


E3 = {"id": "sk", "repo": REPO, "workflow": "s.yml", "jst_time": "09:39", "window_min": 90, "since": "day",
      "enabled": True, "skip_until": "2026-11-01 09:30"}
E4 = dict(E3, skip_until="2026-10-23")


class SkipUntil(unittest.TestCase):
    """skip_until: 日付のみ(従来)と 'YYYY-MM-DD HH:MM'(JST・時刻つき)の両方（2026-10-20 Actions無料枠切れ）"""

    def test_datetime_before_resume(self):
        self.assertEqual(run_main(E3, {}, "2026-10-31 09:40"), [])   # 停止中
        self.assertEqual(run_main(dict(E3, jst_time="07:25"), {}, "2026-11-01 07:30"), [])  # 11/1の朝はまだ失敗する時間帯
        self.assertEqual(run_main(dict(E3, jst_time="08:43"), {}, "2026-11-01 09:29"), [])  # 09:30未満は停止

    def test_datetime_at_resume(self):
        self.assertEqual(len(run_main(dict(E3, jst_time="08:43"), {}, "2026-11-01 09:30")), 1)  # 09:30ちょうどから再開
        self.assertEqual(len(run_main(E3, {}, "2026-11-02 09:40")), 1)

    def test_date_only_unchanged(self):
        self.assertEqual(run_main(E4, {}, "2026-10-22 09:40"), [])
        self.assertEqual(len(run_main(E4, {}, "2026-10-23 09:40")), 1)

    def test_broken_value_fails_open(self):
        self.assertEqual(len(run_main(dict(E3, skip_until=12345), {}, "2026-10-20 09:40")), 1)


E5 = {"id": "ig-verify-catchup@15:21", "repo": REPO, "workflow": "ig-verify.yml", "jst_time": "15:21", "window_min": 60,
      "since": "slot", "enabled": True, "local_done": "ig-verify", "local_done_since": "12:30", "catchup": True}


class LocalDone(unittest.TestCase):
    """local_done: Mac常設ジョブが今日済ませていればクラウドは起動しない／済んでいない時だけ拾う（2026-10-21 Actions無料枠対策）"""

    def test_mac_done_after_since_skips(self):
        f = {"state/local_done.json": {"ig-verify": {"ts": "2026-10-21T13:25:11+09:00"}}}
        self.assertEqual(run_main(E5, f, "2026-10-21 15:25"), [])

    def test_mac_done_but_before_since_still_runs(self):
        # Macの最後の成功が 09:25（since=12:30より前）→ 午後の点検はMacがやっていない → クラウドが拾う
        f = {"state/local_done.json": {"ig-verify": {"ts": "2026-10-21T09:25:11+09:00"}}}
        self.assertEqual(len(run_main(E5, f, "2026-10-21 15:25")), 1)

    def test_yesterdays_record_does_not_count(self):
        f = {"state/local_done.json": {"ig-verify": {"ts": "2026-10-20T22:30:00+09:00"}}}
        self.assertEqual(len(run_main(E5, f, "2026-10-21 15:25")), 1)

    def test_fail_open(self):
        self.assertEqual(len(run_main(E5, {}, "2026-10-21 15:25")), 1)                                  # ファイル取得失敗
        self.assertEqual(len(run_main(E5, {"state/local_done.json": ["x"]}, "2026-10-21 15:25")), 1)     # 形式違い
        self.assertEqual(len(run_main(E5, {"state/local_done.json": {"other": {"ts": "2026-10-21T14:00:00+09:00"}}}, "2026-10-21 15:25")), 1)
        self.assertEqual(len(run_main(E5, {"state/local_done.json": {"ig-verify": {"ts": "garbage"}}}, "2026-10-21 15:25")), 1)
        self.assertEqual(len(run_main(E5, {"state/local_done.json": {"ig-verify": "x"}}, "2026-10-21 15:25")), 1)

    def test_naive_timestamp_is_jst(self):
        f = {"state/local_done.json": {"ig-verify": {"ts": "2026-10-21T13:25:11"}}}
        self.assertEqual(run_main(E5, f, "2026-10-21 15:25"), [])

    def test_entry_without_local_done_unchanged(self):
        e = {k: v for k, v in E5.items() if k not in ("local_done", "local_done_since")}
        f = {"state/local_done.json": {"ig-verify": {"ts": "2026-10-21T14:00:00+09:00"}}}
        self.assertEqual(len(run_main(e, f, "2026-10-21 15:25")), 1)

    def test_custom_file_and_default_since(self):
        e = dict(E5, local_done="x", local_done_file="output/local_done.json")
        e.pop("local_done_since")
        f = {"output/local_done.json": {"x": {"ts": "2026-10-21T05:00:00+09:00"}}}
        self.assertEqual(run_main(e, f, "2026-10-21 15:25"), [])

    def test_marker_prevents_recheck_but_existing_run_check_first(self):
        f = {"state/local_done.json": {"ig-verify": {"ts": "2026-10-21T13:25:11+09:00"}}}
        self.assertEqual(run_main(E5, f, "2026-10-21 15:25", runs=1), [])


class DailyGateFetch(unittest.TestCase):
    """gate=daily は state/story_plan.json(アカウント別ではない)を読む"""

    def test_fetch_plan_masa_path(self):
        paths = []

        def fake(method, path, body=None):
            paths.append(path)
            return 200, b64({"date": "2026-10-21", "posted": 1})
        D.api = fake
        self.assertEqual(D.fetch_plan(REPO, "masa")["posted"], 1)
        self.assertTrue(paths[-1].endswith("/contents/state/story_plan.json"))
        D.fetch_plan(REPO, "nagaoka")
        self.assertTrue(paths[-1].endswith("/contents/state/nagaoka/story_plan.json"))

    def test_story_gate_is_synced_with_origin_copy(self):
        # ig-autopost-cloud 本体と同一であること（同期は実行時にも gate_in_sync が照合する）
        other = Path.home() / "ig-autopost-cloud" / "story_gate.py"
        if other.exists():
            self.assertEqual(other.read_text(encoding="utf-8"), (Path(__file__).resolve().parent / "story_gate.py").read_text(encoding="utf-8"))


class ScheduleTableInvariants(unittest.TestCase):
    """schedule_table.json の取り決め（2026-10-21）を壊さないための番人"""

    @classmethod
    def setUpClass(cls):
        cls.t = json.loads((Path(__file__).resolve().parent / "schedule_table.json").read_text(encoding="utf-8"))
        cls.by = {e["id"]: e for e in cls.t["entries"]}

    def test_catchup_entries_have_local_done_and_are_not_slot_collisions(self):
        for e in self.t["entries"]:
            if e.get("catchup"):
                self.assertTrue(e.get("local_done"), e["id"])
                self.assertTrue(e.get("window_min", 90) >= 30, e["id"])      # ディスパッチャは約10分周期
                self.assertIn("skip_until", e, e["id"])                      # 無料枠が戻る前は起動しない

    def test_verify_has_no_cloud_slots_in_quota_account(self):
        # 2026-10-21(577c9145): @masahide_takahashi_ の story/reels/feed/verify/analyze は別アカウント
        # (minetownmasa-dotcom/instagram-marketing・独自の無料枠)へ移した。truthbodysalon-lab 側の ig-verify は
        # Mac 単独(clinic-video-check@22:21・ig-verify.yml は disabled_manually)で、クラウドの保険枠(旧 ig-verify-catchup@*)は無い。
        self.assertEqual([i for i in self.by if i.startswith("ig-verify@") or i.startswith("ig-verify-catchup@")], [])
        self.assertEqual([i for i in self.by if i.startswith("ig-story@") or i.startswith("ig-reels") or i.startswith("ig-feed@")], [])
        ids = sorted(i for i, e in self.by.items() if e["repo"].endswith("/instagram-marketing") and e["workflow"] == "ig-verify.yml")
        self.assertEqual(ids, ["marketing-verify@09:21", "marketing-verify@13:21", "marketing-verify@18:55", "marketing-verify@22:21"])

    def test_story_daily_gate(self):
        for i in ("marketing-story@07:31", "marketing-story@12:05", "marketing-story@17:31", "marketing-story@21:05"):
            self.assertEqual(self.by[i].get("gate"), "daily", i)

    def test_marketing_account_is_not_quota_stopped(self):
        # 別アカウントは自アカウントの無料枠切れ(skip_until)で止めない。止めると Mac 代行の無い投稿が黙って止まる
        for i, e in self.by.items():
            if e["repo"].startswith("minetownmasa-dotcom/"):
                self.assertNotIn("skip_until", e, i)
                self.assertTrue(e.get("enabled", True), i)

    def test_catchup_window_does_not_cross_midnight(self):
        # local_done は『今日』の記録で判定する。日付をまたぐ窓だと翌日の判定になり二重起動しうる
        for e in self.t["entries"]:
            if e.get("catchup"):
                hh, mm = map(int, e["jst_time"].split(":"))
                self.assertLess(hh * 60 + mm + int(e.get("window_min", 90)), 24 * 60 + 1, e["id"])


ER = {"id": "case", "repo": REPO, "workflow": "case.yml", "jst_time": "19:02", "window_min": 90, "since": "day",
      "enabled": True, "retry_failed": {"after_min": 40, "until_min": 300}}


def run_utc(jst_str):
    """JST 'YYYY-MM-DD HH:MM' を GitHub の updated_at 形式(UTC)にする"""
    d = datetime.strptime(jst_str, "%Y-%m-%d %H:%M") - timedelta(hours=9)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def mk(status, concl, end_jst):
    return {"status": status, "conclusion": concl, "updated_at": run_utc(end_jst), "html_url": "https://x/runs/1"}


class RetryFailed(unittest.TestCase):
    """retry_failed: 1日1回の投稿が失敗で終わった日だけ、終了の約40分後に1回だけ再起動する（2026-10-21）"""

    def seq(self, steps, entry=ER):
        """steps=[(now, runs, latest)] を同じ state で順に流し、各回の dispatch 数を返す"""
        tmp = Path(tempfile.mkdtemp())
        return [len(run_main(entry, {}, now, runs=r, latest=l, tmp=tmp)) for now, r, l in steps]

    def test_failed_run_is_retried_once_after_40min(self):
        fail = mk("completed", "failure", "2026-10-21 19:10")
        out = self.seq([
            ("2026-10-21 19:05", 0, None),            # 通常起動
            ("2026-10-21 19:15", 1, mk("in_progress", None, "2026-10-21 19:15")),   # 実行中: 待つ
            ("2026-10-21 19:25", 1, fail),            # 失敗終了から15分: まだ待つ
            ("2026-10-21 19:45", 1, fail),            # 35分: まだ
            ("2026-10-21 19:55", 1, fail),            # 45分: 再起動
            ("2026-10-21 20:05", 2, fail),            # 再起動済み(run 2本): もう起動しない
            ("2026-10-21 20:15", 1, fail),            # 一覧の反映が遅れて1本に見えても、retryマーカーで二度目はしない
        ])
        self.assertEqual(out, [1, 0, 0, 0, 1, 0, 0])

    def test_success_or_cancelled_or_many_runs_are_not_retried(self):
        for latest, runs in ((mk("completed", "success", "2026-10-21 19:10"), 1),
                             (mk("completed", "cancelled", "2026-10-21 19:10"), 1),
                             (mk("completed", "failure", "2026-10-21 19:10"), 2)):
            out = self.seq([("2026-10-21 19:05", 0, None), ("2026-10-21 20:05", runs, latest)])
            self.assertEqual(out, [1, 0], (latest["conclusion"], runs))

    def test_dispatched_but_not_listed_yet_does_not_double_dispatch(self):
        out = self.seq([("2026-10-21 19:05", 0, None), ("2026-10-21 19:15", 0, None), ("2026-10-21 19:25", 0, None)])
        self.assertEqual(out, [1, 0, 0])

    def test_without_retry_failed_unchanged(self):
        e = {k: v for k, v in ER.items() if k != "retry_failed"}
        fail = mk("completed", "failure", "2026-10-21 19:10")
        out = self.seq([("2026-10-21 19:05", 0, None), ("2026-10-21 19:55", 1, fail)], entry=e)
        self.assertEqual(out, [1, 0])
        # 窓(90分)を過ぎたら何もしない
        self.assertEqual(self.seq([("2026-10-21 20:40", 0, None)], entry=e), [0])

    def test_retry_only_while_before_until(self):
        fail = mk("completed", "failure", "2026-10-21 19:10")
        out = self.seq([("2026-10-21 19:05", 0, None), ("2026-10-22 00:30", 1, fail)])
        self.assertEqual(out, [1, 0])        # 日をまたいだら対象外（slot+300分=24:02 だが日付が変わる）

    def test_retry_respects_pause_file(self):
        e = dict(ER, pause_file="state/p.json")
        tmp = Path(tempfile.mkdtemp())
        fail = mk("completed", "failure", "2026-10-21 19:10")
        self.assertEqual(len(run_main(e, {}, "2026-10-21 19:05", tmp=tmp)), 1)
        f = {"state/p.json": {"until": "2026-10-25"}}
        self.assertEqual(run_main(e, f, "2026-10-21 19:55", runs=1, latest=fail, tmp=tmp), [])


if __name__ == "__main__":
    unittest.main()
