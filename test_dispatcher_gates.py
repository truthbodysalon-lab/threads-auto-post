#!/usr/bin/env python3
"""dispatcher.py の pause_file / queue_file ゲートのテスト（2026-10-18）。
実行: python3 test_dispatcher_gates.py   （ネットワークは使わない。api を差し替える）"""
import base64, json, os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatcher as D

REPO = "o/r"


def b64(obj):
    return {"content": base64.b64encode(json.dumps(obj).encode()).decode()}


class Fake:
    def __init__(self, files, runs=0):
        self.files, self.runs, self.posts = files, runs, []

    def __call__(self, method, path, body=None):
        if method == "POST":
            self.posts.append((path, body))
            return 204, {}
        if "/actions/workflows/" in path and "/runs" in path:
            return 200, {"total_count": self.runs}
        if path.startswith(f"/repos/{REPO}/contents/"):
            rel = path.split("/contents/", 1)[1]
            if rel not in self.files:
                raise RuntimeError("404")
            return 200, b64(self.files[rel])
        if path == f"/repos/{REPO}":
            return 200, {"default_branch": "main"}
        raise RuntimeError("unexpected " + path)


def run_main(entry, files, now, runs=0):
    tmp = Path(tempfile.mkdtemp())
    (tmp / "t.json").write_text(json.dumps({"mode": "live", "entries": [entry]}))
    D.TABLE, D.STATE = tmp / "t.json", tmp / "s.json"
    D._branch_cache.clear()
    fake = Fake(files, runs)
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

    def test_verify_has_no_primary_cloud_slots(self):
        # verify の定刻4枠はMac常設へ。クラウドは保険(catchup)のみ
        ids = [i for i in self.by if i.startswith("ig-verify@")]
        self.assertEqual(ids, [])
        self.assertEqual(sorted(i for i in self.by if i.startswith("ig-verify-catchup@")),
                         ["ig-verify-catchup@15:21", "ig-verify-catchup@20:21", "ig-verify-catchup@23:11"])

    def test_story_daily_gate(self):
        for i in ("ig-story@07:31", "ig-story@12:05", "ig-story@17:31", "ig-story@21:05"):
            self.assertEqual(self.by[i].get("gate"), "daily", i)

    def test_catchup_window_does_not_cross_midnight(self):
        # local_done は『今日』の記録で判定する。日付をまたぐ窓だと翌日の判定になり二重起動しうる
        for e in self.t["entries"]:
            if e.get("catchup"):
                hh, mm = map(int, e["jst_time"].split(":"))
                self.assertLess(hh * 60 + mm + int(e.get("window_min", 90)), 24 * 60 + 1, e["id"])


if __name__ == "__main__":
    unittest.main()
