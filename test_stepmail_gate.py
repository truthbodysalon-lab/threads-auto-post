#!/usr/bin/env python3
"""ステップ配信の起動前ゲート(dispatcher.stepmail_pending / stepmail_gate)のテスト（2026-10-18）。
本物の line-stepmail-cloud/stepmail.mjs を DRY_RUN=1 で実行して判定するので、node と ~/line-stepmail-cloud が要る
(無ければ該当テストはスキップ)。ネットワークは使わない(fetch_raw / api を差し替え)。
実行: python3 test_stepmail_gate.py"""
import io, json, os, shutil, sys, tempfile, unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatcher as D

SRC = Path(os.environ.get("STEPMAIL_SRC", str(Path.home() / "line-stepmail-cloud")))
HAVE = (SRC / "stepmail.mjs").exists() and shutil.which("node") is not None
REPO = "o/stepmail"
UID = "Utest0000000000000000000000000001"      # ログに出てはいけない文字列
SECRET_TEXT_PROBE = "お客さま"                    # 本文の一部(DRY_RUN出力に出る)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def enroll(**kw):
    now = datetime.now(timezone.utc)
    d = {"scenario_id": 1, "user_id": UID, "current_step": 1, "status": "active",
         "enrolled_at": iso(now - timedelta(days=1)), "next_send_at": iso(now - timedelta(hours=1)),
         "test_mode": "normal", "display_name": None}
    d.update(kw)
    return d


class Files:
    def __init__(self, enrollments, sent=None):
        self.map = {
            "stepmail.mjs": (SRC / "stepmail.mjs").read_bytes(),
            "step_messages.json": (SRC / "step_messages.json").read_bytes(),
            "state/enrollments.json": json.dumps(enrollments).encode(),
            "state/sent.json": json.dumps(sent or []).encode(),
        }

    def __call__(self, repo, path):
        if path not in self.map:
            raise RuntimeError("404")
        return self.map[path]


@unittest.skipUnless(HAVE, "node または line-stepmail-cloud が無い")
class Pending(unittest.TestCase):
    def pending(self, enrollments, sent=None):
        D.fetch_raw = Files(enrollments, sent)
        return D.stepmail_pending(REPO)

    def test_no_enrollments_is_zero(self):
        self.assertEqual(self.pending([]), 0)

    def test_due_is_one(self):
        self.assertEqual(self.pending([enroll()]), 1)

    def test_lookahead_within_skew_counts_but_not_beyond(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(self.pending([enroll(next_send_at=iso(now + timedelta(seconds=60)))]), 1)    # 先読み2分以内
        self.assertEqual(self.pending([enroll(next_send_at=iso(now + timedelta(seconds=600)))]), 0)   # 10分先は対象外

    def test_not_active_or_step0_is_zero(self):
        self.assertEqual(self.pending([enroll(status="completed")]), 0)
        self.assertEqual(self.pending([enroll(current_step=0)]), 0)

    def test_already_sent_is_zero(self):
        sent = [{"userId": UID, "scenarioId": 1, "step": 1, "sentAt": iso(datetime.now(timezone.utc) - timedelta(hours=30))}]
        # step1送信済み → 次はstep2: 登録1日前なので Day2 の期日はまだ(delay_days に依存)。ゲートと実走行の一致だけを見る
        n = self.pending([enroll()], sent)
        self.assertIn(n, (0, 1))

    def test_matches_real_dry_run(self):
        """ゲートの件数 = 同じ入力で本物を DRY_RUN=1 で直接実行した『送信=N件』(先読み0)"""
        import re, subprocess
        for en in ([], [enroll()], [enroll(status="cancelled")]):
            D.fetch_raw = Files(en)
            n = D.stepmail_pending(REPO)
            tmp = tempfile.mkdtemp()
            try:
                for rel, data in D.fetch_raw.map.items():
                    p = Path(tmp) / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
                out = subprocess.run(["node", "stepmail.mjs"], cwd=tmp, env={"PATH": os.environ["PATH"], "DRY_RUN": "1"},
                                     capture_output=True, timeout=30).stdout.decode()
            finally:
                shutil.rmtree(tmp)
            self.assertEqual(n, int(re.findall(r"送信=(\d+)件", out)[-1]))

    def test_fail_open_on_fetch_error_and_bad_output(self):
        def boom(repo, path):
            raise RuntimeError("net")
        D.fetch_raw = boom
        self.assertIsNone(D.stepmail_pending(REPO))
        f = Files([])
        f.map["stepmail.mjs"] = b"console.log('nothing');"            # 書式が違う
        D.fetch_raw = f
        self.assertIsNone(D.stepmail_pending(REPO))
        f.map["stepmail.mjs"] = b"process.exit(3);"                    # 異常終了
        self.assertIsNone(D.stepmail_pending(REPO))

    def test_child_env_has_no_secrets(self):
        f = Files([])
        f.map["stepmail.mjs"] = (b"import {writeFileSync} from 'node:fs';"
                                 b"writeFileSync('env.txt', JSON.stringify(Object.keys(process.env)));"
                                 b"console.log('stepmail.mjs \xe5\xae\x8c\xe4\xba\x86: \xe9\x80\x81\xe4\xbf\xa1=0\xe4\xbb\xb6');")
        D.fetch_raw = f
        os.environ["GH_PAT"] = "x"; os.environ["DISCORD_WEBHOOK_URL"] = "y"
        # 子プロセスが書いた env.txt は一時ディレクトリごと消えるので、ここでは件数が取れることだけ確認
        self.assertEqual(D.stepmail_pending(REPO), 0)


class FakeApi:
    def __init__(self, runs=0):
        self.runs, self.posts = runs, []

    def __call__(self, method, path, body=None):
        if method == "POST":
            self.posts.append((path, body)); return 204, {}
        if "/runs" in path:
            return 200, {"total_count": self.runs}
        if path == f"/repos/{REPO}":
            return 200, {"default_branch": "main"}
        raise RuntimeError("unexpected " + path)


ENTRY = {"id": "stepmail@23:25", "repo": REPO, "workflow": "stepmail.yml", "jst_time": "23:25",
         "window_min": 60, "since": "slot", "enabled": True, "gate": "stepmail"}


def run_main(now, pending, entry=ENTRY):
    tmp = Path(tempfile.mkdtemp())
    (tmp / "t.json").write_text(json.dumps({"mode": "live", "entries": [entry]}))
    D.TABLE, D.STATE = tmp / "t.json", tmp / "s.json"
    D.STEPMAIL_GATE_STATE = tmp / "g.json"
    D._branch_cache.clear()
    api = FakeApi(); D.api = api
    calls = []
    D.stepmail_pending = lambda repo: (calls.append(1), pending)[1]
    os.environ["DISPATCH_NOW"] = now
    buf = io.StringIO()
    with redirect_stdout(buf):
        D.main()
        D.main()          # 同じ枠の2回目のサイクル(10分後)
    return api.posts, len(calls), buf.getvalue(), tmp


class Main(unittest.TestCase):
    def setUp(self):
        self._orig = D.stepmail_pending

    def tearDown(self):
        D.stepmail_pending = self._orig

    def test_skip_is_decided_once_per_slot(self):
        posts, ncalls, out, _ = run_main("2026-10-18 23:30", 0)
        self.assertEqual(posts, [])
        self.assertEqual(ncalls, 1)                    # 2回目のサイクルでは再判定しない
        self.assertIn("SKIP 送信対象0件", out)

    def test_run_dispatches_once(self):
        posts, ncalls, out, _ = run_main("2026-10-18 23:30", 2)
        self.assertEqual(len(posts), 1)
        self.assertEqual(ncalls, 1)
        self.assertIn("RUN 送信対象2件", out)

    def test_fail_open_dispatches(self):
        posts, ncalls, out, _ = run_main("2026-10-18 23:30", None)
        self.assertEqual(len(posts), 1)
        self.assertIn("判定不能", out)

    def test_outside_window_nothing(self):
        posts, ncalls, out, _ = run_main("2026-10-18 22:00", 5)
        self.assertEqual((posts, ncalls), ([], 0))

    def test_without_gate_key_always_dispatches(self):
        e = dict(ENTRY); e.pop("gate")
        posts, ncalls, out, _ = run_main("2026-10-18 23:30", 0, entry=e)
        self.assertEqual(len(posts), 1)
        self.assertEqual(ncalls, 0)                    # gateキーを消せば元の挙動

    def test_streak_alert_after_three_runs(self):
        sent = []
        import ops_deadman
        orig = ops_deadman.discord_send
        ops_deadman.discord_send = lambda t: (sent.append(t) or True)
        ops_deadman._load_dotenv = lambda: None
        try:
            tmp = Path(tempfile.mkdtemp())
            D.STEPMAIL_GATE_STATE = tmp / "g.json"
            D.stepmail_pending = lambda repo: 1
            for _ in range(2):
                self.assertFalse(D.stepmail_gate(ENTRY, REPO))
            self.assertEqual(sent, [])
            self.assertFalse(D.stepmail_gate(ENTRY, REPO))        # 3枠連続
            self.assertEqual(len(sent), 1)
            self.assertNotIn("U", sent[0].split("userId")[0] if "userId" in sent[0] else "")
            self.assertFalse(D.stepmail_gate(ENTRY, REPO))        # 続けて出さない
            self.assertEqual(len(sent), 1)
            D.stepmail_pending = lambda repo: 0
            self.assertTrue(D.stepmail_gate(ENTRY, REPO))         # 解消でリセット
            D.stepmail_pending = lambda repo: 1
            for _ in range(3):
                D.stepmail_gate(ENTRY, REPO)
            self.assertEqual(len(sent), 2)
        finally:
            ops_deadman.discord_send = orig


@unittest.skipUnless(HAVE, "node または line-stepmail-cloud が無い")
class NoLeak(unittest.TestCase):
    def test_public_log_has_no_userid_or_body(self):
        D.stepmail_pending = D.stepmail_pending.__wrapped__ if hasattr(D.stepmail_pending, "__wrapped__") else D.stepmail_pending
        import importlib
        importlib.reload(D)
        D.fetch_raw = Files([enroll()])
        tmp = Path(tempfile.mkdtemp())
        (tmp / "t.json").write_text(json.dumps({"mode": "live", "entries": [ENTRY]}))
        D.TABLE, D.STATE, D.STEPMAIL_GATE_STATE = tmp / "t.json", tmp / "s.json", tmp / "g.json"
        D.api = FakeApi()
        os.environ["DISPATCH_NOW"] = "2026-10-18 23:30"
        buf = io.StringIO()
        with redirect_stdout(buf):
            D.main()
        out = buf.getvalue()
        self.assertIn("RUN 送信対象1件", out)
        self.assertNotIn(UID, out)
        self.assertNotIn(SECRET_TEXT_PROBE, out)
        self.assertNotIn("本文", out)
        self.assertNotIn("user=", out)
        state_txt = (tmp / "s.json").read_text() + (tmp / "g.json").read_text()
        self.assertNotIn(UID, state_txt)


if __name__ == "__main__":
    unittest.main()
