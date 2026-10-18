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


if __name__ == "__main__":
    unittest.main()
