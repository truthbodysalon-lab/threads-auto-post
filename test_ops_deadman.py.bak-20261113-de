#!/usr/bin/env python3
"""ops_deadman.py のテスト（2026-10-18）。ネットワーク不使用（api / discord_send を差し替える）。
実行: python3 test_ops_deadman.py"""
import base64, json, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ops_deadman as O

NOW = 1_800_000_000


def iso(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Fake:
    """cloud_age: コミット経過秒(None=コミット無し) / line_age: heartbeat.json の ts 経過秒(None=ファイル無し)
    cloud_http / line_http: 0以外ならそのHTTPコード、'exc'なら通信例外"""
    def __init__(self, cloud_age=60, line_age=60, cloud_http=200, line_http=200, repo_http=200):
        self.cloud_age, self.line_age = cloud_age, line_age
        self.cloud_http, self.line_http, self.repo_http = cloud_http, line_http, repo_http

    def __call__(self, path):
        if "/commits?path=heartbeat/heartbeat.txt" in path:
            if self.cloud_http == "exc":
                raise O.ReadFailure("URLError")
            if self.cloud_http != 200:
                return self.cloud_http, None
            if self.cloud_age is None:
                return 200, []
            return 200, [{"commit": {"committer": {"date": iso(NOW - self.cloud_age)}}}]
        if "/contents/heartbeat.json" in path:
            if self.line_http == "exc":
                raise O.ReadFailure("URLError")
            if self.line_http != 200:
                return self.line_http, None
            if self.line_age is None:
                return 404, None
            body = json.dumps({"ts": iso(NOW - self.line_age)}).encode()
            return 200, {"content": base64.b64encode(body).decode()}
        if path.startswith("/repos/") and path.count("/") == 3:
            return self.repo_http, {}
        raise RuntimeError("unexpected " + path)


class T(unittest.TestCase):
    def setUp(self):
        self.sent = []
        O.discord_send = lambda text: (self.sent.append(text) or True)
        tmp = Path(tempfile.mkdtemp())
        O.STATE = tmp / "s.json"

    def run_once(self, fake, now=NOW):
        O.api = fake
        O.now_epoch = lambda: now
        O.main_state = None
        O._load_dotenv = lambda: None
        return O.main()

    def test_fresh_no_alert(self):
        self.run_once(Fake(cloud_age=21600, line_age=10800))     # しきい値ちょうどは正常(旧: -le / >)
        self.assertEqual(self.sent, [])

    def test_cloud_stale_message_and_suppress(self):
        self.run_once(Fake(cloud_age=21601, line_age=60))
        self.assertEqual(self.sent, ["🚨 Mac停止検知: ハートビートが6時間途絶。クラウドのGmail通知/医学学習は継続稼働中。マーケ学習のCodex生成はMac復旧が必要"])
        self.run_once(Fake(cloud_age=21601 + 600, line_age=60), now=NOW + 600)   # 10分後は抑制
        self.assertEqual(len(self.sent), 1)
        # 12時間後に再通知(lineのfakeはNOW固定のため line の通知は数えない)
        self.run_once(Fake(cloud_age=21601 + 43200, line_age=120), now=NOW + 43200)
        self.assertEqual(len([m for m in self.sent if m.startswith('🚨')]), 2)

    def test_line_stale_message(self):
        self.run_once(Fake(cloud_age=60, line_age=10801))
        from datetime import datetime, timezone, timedelta
        label = datetime.fromtimestamp(NOW - 10801, timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M JST")
        self.assertEqual(self.sent, [f"⚠️ Macのライン配信同期が止まっています（最終同期: {label}）。クラウド側のステップ配信は継続中です"])

    def test_line_missing_file(self):
        self.run_once(Fake(cloud_age=60, line_age=None))
        self.assertEqual(self.sent, ["⚠️ Macのライン配信同期が止まっています（最終同期: 記録なし）。クラウド側のステップ配信は継続中です"])

    def test_recovery_clears_suppression(self):
        self.run_once(Fake(cloud_age=30000, line_age=60))
        self.run_once(Fake(cloud_age=60, line_age=60), now=NOW + 600)       # 回復
        self.run_once(Fake(cloud_age=30000, line_age=60), now=NOW + 1200)   # 再停止は即通知
        self.assertEqual(len(self.sent), 2)

    def test_read_failure_warns_once_after_six(self):
        for i in range(5):
            self.run_once(Fake(cloud_http=403, line_age=60), now=NOW + i * 600)
        self.assertEqual(self.sent, [])                      # 5回では出さない
        self.run_once(Fake(cloud_http=403, line_age=60), now=NOW + 3000)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("読めていません", self.sent[0])
        self.run_once(Fake(cloud_http=403, line_age=60), now=NOW + 3600)
        self.assertEqual(len(self.sent), 1)                  # 続けて出さない
        self.run_once(Fake(line_age=60), now=NOW + 4200)     # 回復
        self.run_once(Fake(cloud_http=403, line_age=60), now=NOW + 4800)
        self.assertEqual(len(self.sent), 1)

    def test_no_access_404_is_read_failure_not_missing(self):
        # 権限の無い非公開repoは contents が404 → 『記録なし』警告にしない(読めない扱い)
        self.run_once(Fake(cloud_age=60, line_age=None, repo_http=404))
        self.assertEqual(self.sent, [])
        self.assertEqual(O.load_state()["line"]["read_fail"], 1)

    def test_exception_is_read_failure(self):
        self.run_once(Fake(cloud_http="exc", line_http="exc"))
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
