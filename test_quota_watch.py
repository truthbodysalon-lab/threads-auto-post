#!/usr/bin/env python3
"""quota_watch.py のテスト（2026-10-21）。ネットワーク不使用（api を差し替える）。
実行: python3 test_quota_watch.py"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import quota_watch as Q

JST = timezone(timedelta(hours=9))


def epoch(y, mo, d, h, mi):
    return int(datetime(y, mo, d, h, mi, tzinfo=JST).timestamp())


class Decide(unittest.TestCase):
    NOW = epoch(2026, 11, 10, 12, 30)

    def test_thresholds(self):
        d = lambda u, mac=None, cloud=None: Q.decide(u, "2026-11", mac or {}, cloud or {}, self.NOW)
        self.assertEqual(d(1499), [])
        self.assertEqual(d(1501), ["warn"])
        self.assertEqual(d(1749), ["warn"])
        self.assertEqual(d(1751), ["need_switch"])

    def test_mac_already_warned_or_switched(self):
        d = lambda u, mac=None, cloud=None: Q.decide(u, "2026-11", mac or {}, cloud or {}, self.NOW)
        self.assertEqual(d(1600, mac={"month": "2026-11", "warned": True}), [])               # Mac が予告済み → 二重予告しない
        self.assertEqual(d(1600, cloud={"warned_month": "2026-11"}), [])                      # 自分が予告済み
        self.assertEqual(d(1600, mac={"month": "2026-10", "warned": True}), ["warn"])         # 先月の記録は無関係
        self.assertEqual(d(1800, mac={"month": "2026-11", "switched": True}), [])             # Mac が切替済み → 何も言わない
        self.assertEqual(d(1800, mac={"month": "2026-10", "switched": True}), ["need_switch"])

    def test_need_switch_throttle(self):
        c = {"need_notice_month": "2026-11", "need_notice_at": self.NOW - 3600}
        self.assertEqual(Q.decide(1800, "2026-11", {}, c, self.NOW), [])                      # 1時間前に出した → 出さない
        c["need_notice_at"] = self.NOW - 21 * 3600
        self.assertEqual(Q.decide(1800, "2026-11", {}, c, self.NOW), ["need_switch"])         # 21時間後 → もう一度
        c = {"need_notice_month": "2026-10", "need_notice_at": self.NOW - 60}
        self.assertEqual(Q.decide(1800, "2026-11", {}, c, self.NOW), ["need_switch"])         # 先月の通知は無関係

    def test_job_minutes_same_as_mac(self):
        now = datetime(2026, 11, 1, tzinfo=timezone.utc)
        j = lambda s, e, **k: dict({"started_at": s, "completed_at": e, "labels": ["ubuntu-latest"]}, **k)
        self.assertEqual(Q.job_minutes(j("2026-11-01T00:00:00Z", "2026-11-01T00:01:01Z"), now), 2)
        self.assertEqual(Q.job_minutes(j("2026-11-01T00:00:00Z", "2026-11-01T00:00:05Z"), now), 1)
        self.assertEqual(Q.job_minutes(j("2026-11-01T00:00:00Z", "2026-11-01T00:00:00Z"), now), 0)
        self.assertEqual(Q.job_minutes(j("2026-11-01T00:00:00Z", "2026-11-01T00:03:00Z", conclusion="skipped"), now), 0)

    def test_message_has_no_secret_like_text(self):
        t = Q.messages("need_switch", 1800, {"last_check": "2026-11-10T07:05:00+09:00"}, self.NOW) + Q.messages("warn", 1600, {}, self.NOW)
        for bad in ("token", "ghp_", "https://", "webhook"):
            self.assertNotIn(bad, t.lower())


class Run(unittest.TestCase):
    """main() を環境変数だけで動かす（状態は QUOTA_WATCH_MAC_STATE / CLOUD_STATE で与え、API は一切呼ばない）。"""

    def run_main(self, now, usage, mac="{}", cloud="{}", force=True):
        tmp = tempfile.mkdtemp(prefix="qw_")
        env = dict(os.environ, QUOTA_WATCH_DRY="1", QUOTA_WATCH_NOW=str(now), QUOTA_WATCH_FAKE_USAGE=str(usage),
                   QUOTA_WATCH_MAC_STATE=mac, QUOTA_WATCH_CLOUD_STATE=cloud, QUOTA_WATCH_TMP=tmp, GH_TOKEN="dummy")
        if force:
            env["QUOTA_WATCH_FORCE"] = "1"
        else:
            env.pop("QUOTA_WATCH_FORCE", None)
        r = subprocess.run([sys.executable, str(HERE / "quota_watch.py")], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_1499_1501_1751(self):
        now = epoch(2026, 11, 10, 12, 30)
        self.assertNotIn("[DRY]", self.run_main(now, 1499))
        o = self.run_main(now, 1501)
        self.assertIn("[DRY]", o)
        self.assertIn("予告", o)
        o = self.run_main(now, 1751)
        self.assertIn("切替が必要", o)
        # Mac が切替済みの月は使用量を数えず、何も言わない
        o = self.run_main(now, 1900, mac=json.dumps({"month": "2026-11", "switched": True}))
        self.assertNotIn("[DRY]", o)
        self.assertIn("Macが切替済み", o)

    def test_month_rollover(self):
        now = epoch(2026, 12, 1, 12, 30)            # 12/1 は新しい月。先月の Mac 切替状態は無効
        o = self.run_main(now, 30, mac=json.dumps({"month": "2026-11", "switched": True}))
        self.assertNotIn("[DRY]", o)
        self.assertIn("2026-12", o)

    def test_time_gate_and_once_a_day(self):
        early = epoch(2026, 11, 10, 11, 0)
        o = self.run_main(early, 1800, force=False)
        self.assertEqual(o.strip(), "")             # 12:20 前は何もしない
        late = epoch(2026, 11, 10, 12, 30)
        o = self.run_main(late, 1800, cloud=json.dumps({"last_check_date": "2026-11-10"}), force=False)
        self.assertEqual(o.strip(), "")             # 今日すでに確認済み


class NoWriteToPrivate(unittest.TestCase):
    def test_only_get_requests_to_private_repos(self):
        """非公開repoへのAPI呼び出しは GET のみ（公開repo自身の状態ファイルだけ PUT する）"""
        src = (HERE / "quota_watch.py").read_text(encoding="utf-8")
        puts = [l for l in src.splitlines() if 'method="PUT"' in l or "method='PUT'" in l]
        self.assertEqual(len(puts), 1)
        self.assertIn("PUBLIC_REPO", puts[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
