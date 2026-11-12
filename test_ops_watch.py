#!/usr/bin/env python3
"""ops_watch.py のテスト（2026-11-12）。ネットワーク不使用（api / discord_send / mac_stale を差し替える）。
実行: python3 test_ops_watch.py"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["OPS_WATCH_DRY"] = ""
import ops_watch as W

NOW = int(datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc).timestamp())


def iso(sec_ago):
    return datetime.fromtimestamp(NOW - sec_ago, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(i, name="Daily Analyze", ago=600, attempt=1, wid=7):
    return {"id": i, "name": name, "created_at": iso(ago), "run_attempt": attempt, "workflow_id": wid,
            "html_url": f"https://github.com/x/y/actions/runs/{i}"}


class Fake:
    def __init__(self, newer_success=False, billing=False):
        self.calls, self.sent = [], []
        self.newer_success, self.billing = newer_success, billing
        self.run_view = {}

    def api(self, path, method="GET", body=None):
        self.calls.append((method, path))
        if "/rerun-failed-jobs" in path:
            return 201, {}
        if "/workflows/" in path and path.endswith("status=completed"):
            return 200, {"workflow_runs": [{"id": 999, "conclusion": "success", "created_at": iso(10)}] if self.newer_success else []}
        if path.endswith("per_page=30") and "/jobs" in path:
            return 200, {"jobs": [{"id": 5, "conclusion": "failure", "steps": []}] if self.billing else [{"id": 5, "conclusion": "failure", "steps": [1]}]}
        if "/annotations" in path:
            return 200, [{"message": "The job was not started because of spending limit"}]
        if "/actions/runs/" in path and method == "GET":
            rid = path.rsplit("/", 1)[-1]
            return 200, self.run_view.get(rid, {})
        return 404, None

    def send(self, text):
        self.sent.append(text)
        return True


class T(unittest.TestCase):
    def setUp(self):
        self.f = Fake()
        W.api, W.discord_send, W.mac_stale = self.f.api, self.f.send, lambda: False

    def reran_calls(self):
        return [c for c in self.f.calls if "/rerun-failed-jobs" in c[1]]

    def test_first_tick_is_baseline_only(self):
        st = {"initialized": False, "runs": {}}
        res = W.process(st, {"o/r": [run(1)]}, NOW)
        self.assertEqual(st["runs"]["1"]["s"], "baseline")
        self.assertEqual(self.reran_calls(), [])
        self.assertEqual(res["reran"], 0)

    def test_transient_failure_reran_once_only(self):
        st = {"initialized": True, "runs": {}}
        W.process(st, {"o/r": [run(2)]}, NOW)
        self.assertEqual(len(self.reran_calls()), 1)
        self.assertEqual(st["runs"]["2"]["s"], "rerun_requested")
        # 同じrunがもう一度失敗リストに出ても、再実行は増えない（完了前は様子見）
        self.f.run_view["2"] = {"status": "in_progress"}
        W.process(st, {"o/r": [run(2)]}, NOW + 1800)
        self.assertEqual(len(self.reran_calls()), 1)

    def test_recovered_after_rerun_no_notify(self):
        st = {"initialized": True, "runs": {"3": {"s": "rerun_requested", "t": NOW}}}
        self.f.run_view["3"] = {"status": "completed", "conclusion": "success", "run_attempt": 2}
        W.process(st, {"o/r": [run(3)]}, NOW + 1800)
        self.assertEqual(st["runs"]["3"]["s"], "recovered")
        self.assertEqual(self.f.sent, [])

    def test_failed_after_rerun_notifies_with_marker(self):
        st = {"initialized": True, "runs": {"4": {"s": "rerun_requested", "t": NOW}}}
        self.f.run_view["4"] = {"status": "completed", "conclusion": "failure", "run_attempt": 2}
        W.process(st, {"o/r": [run(4)]}, NOW + 1800)
        self.assertEqual(len(self.f.sent), 1)
        self.assertTrue(self.f.sent[0].rstrip().endswith("[auto-triage]"))
        self.assertEqual(st["runs"]["4"]["s"], "notified_failed_after_rerun")

    def test_one_notification_per_tick(self):
        st = {"initialized": True, "runs": {"5": {"s": "rerun_requested", "t": NOW}, "6": {"s": "rerun_requested", "t": NOW}}}
        self.f.run_view["5"] = self.f.run_view["6"] = {"status": "completed", "conclusion": "failure", "run_attempt": 2}
        W.process(st, {"o/r": [run(5), run(6)]}, NOW + 1800)
        self.assertEqual(len(self.f.sent), 1)
        self.assertEqual(st["runs"]["6"]["s"], "rerun_requested")   # 次tickへ持ち越し

    def test_posting_workflows_never_rerun(self):
        for name in ("IG Story (4x daily)", "LINE Step Mail (Cloud)", "Threads Auto Post", "Some New Post Job", "Ops Watch"):
            st = {"initialized": True, "runs": {}}
            W.process(st, {"o/r": [run(10, name=name)]}, NOW)
        self.assertEqual(self.reran_calls(), [])

    def test_no_rerun_failure_notifies_only_when_mac_down(self):
        st = {"initialized": True, "runs": {}}
        W.process(st, {"o/r": [run(11, name="IG Feed (daily carousel)")]}, NOW)   # Mac稼働中: 通知しない
        self.assertEqual(self.f.sent, [])
        self.assertEqual(st["runs"]["11"]["s"], "skipped_no_rerun_policy")
        W.mac_stale = lambda: True
        st = {"initialized": True, "runs": {}}
        W.process(st, {"o/r": [run(12, name="IG Feed (daily carousel)")]}, NOW)
        self.assertEqual(len(self.f.sent), 1)
        self.assertIn("[auto-triage]", self.f.sent[0])
        self.assertEqual(self.reran_calls(), [])

    def test_already_recovered_not_rerun(self):
        self.f.newer_success = True
        st = {"initialized": True, "runs": {}}
        W.process(st, {"o/r": [run(13)]}, NOW)
        self.assertEqual(self.reran_calls(), [])
        self.assertEqual(st["runs"]["13"]["s"], "skipped_already_recovered")

    def test_billing_block_not_rerun(self):
        self.f.billing = True
        st = {"initialized": True, "runs": {}}
        W.process(st, {"o/r": [run(14)]}, NOW)
        self.assertEqual(self.reran_calls(), [])
        self.assertEqual(st["runs"]["14"]["s"], "skipped_billing_limit")

    def test_old_failure_not_rerun(self):
        st = {"initialized": True, "runs": {}}
        W.process(st, {"o/r": [run(15, ago=7 * 3600)]}, NOW)
        self.assertEqual(self.reran_calls(), [])

    def test_attempt_gt1_not_rerun(self):
        st = {"initialized": True, "runs": {}}
        W.process(st, {"o/r": [run(16, attempt=2)]}, NOW)
        self.assertEqual(self.reran_calls(), [])

    def test_unreadable_repo_flags_inspect_failed(self):
        st = {"initialized": True, "runs": {}}
        res = W.process(st, {"o/r": None}, NOW)
        self.assertTrue(res["inspect_failed"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
