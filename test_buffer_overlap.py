#!/usr/bin/env python3
"""Buffer枠前後の同テーマ回避（masaのみ・2026-10-23）の単体テスト。
実投稿・実API・実ファイル書き込みは一切しない（ネットワーク関数と投稿関数は全て差し替え、台帳は一時ファイル）。
実行: python3 test_buffer_overlap.py
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

import auto_post as ap  # noqa: E402
import buffer_overlap as bo  # noqa: E402

results = []
D = "2026-10-23"


def check(label, cond):
    print(f"[{'OK' if cond else 'FAIL'}] {label}")
    results.append(bool(cond))


def _no_network(*a, **k):
    raise AssertionError("ネットワーク呼び出し禁止（テスト）")


KUCHIKOMI = "【テスト】口コミの返信は、3行で十分です。\nGoogleマップの口コミ返信で迷う店主へ。\n\n①お礼\n②内容をなぞる\n③次の楽しみ"
INSTA = "【テスト】結論、加工しない写真の方が、お店は伝わる。\nインスタ運用で、写真の編集ができない店主へ。\n\n①窓際の自然光\n②明るさだけ上げる"
AI = "【テスト】定型文を、AIと一緒に作れます。\n予約前の案内、来店後のお礼、お休みのお知らせ。\n今日は、よく送る文を1つAIに頼んでください。"
URL_POST = "【テスト】診断はこちらから。\nあなたのお店に合う集客の順番がわかります。\nhttps://example.com/x"
OPINION = "【テスト】値段で選ばれている時点で、もう負けています。\n選ばれる理由を先につくる。\n経営に近道はない。でも、正しい順番はあります。"

BUF_GMAP = ("Googleマップの口コミ、返信は「ありがとうございます」だけで終わらせないほうがいいと僕は思います。\n"
            "おすすめは3行です。返信は書いた本人だけでなく、これから来店を考えている人も読んでいます。")
BUF_SNS = ("お店のInstagramやLINE、最初は張り切って始めたのに、だんだん更新が止まってしまう。"
           "そんな経験はありませんか。どこでつまずいたか、コメントで教えてください。")


class Env:
    """masa の台帳・ログを一時ファイルへ差し替え、ネットワークを遮断する。"""
    def __init__(self, td, queue, posted=None, acct="masa"):
        self.td = Path(td)
        self.acct = acct
        self.queue = queue
        self.posted = posted or []

    def __enter__(self):
        self.orig = {k: getattr(ap, k) for k in ("ACCOUNTS", "_recent_api_firstlines", "_line_done_today",
                                               "ERROR_LOG", "_hero_priority_order", "post_to_threads",
                                               "post_reply_to_threads", "_text_has_visible_url")}
        self.orig_bo = {k: getattr(bo, k) for k in ("load_config", "active_slot", "_fetch_recent_api")}
        q = self.td / "q.jsonl"
        q.write_text(json.dumps({"account": self.acct, "theme": "t", "date": D, "posts": self.queue},
                                ensure_ascii=False) + "\n", encoding="utf-8")
        pf = self.td / "posted.jsonl"
        pf.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self.posted), encoding="utf-8")
        ap.ACCOUNTS = {k: dict(v) for k, v in self.orig["ACCOUNTS"].items()}
        for a in ap.ACCOUNTS:
            ap.ACCOUNTS[a]["log"] = q
            ap.ACCOUNTS[a]["posted"] = pf
            ap.ACCOUNTS[a]["autopost_log"] = self.td / f"autopost_{a}.log"
        ap.ERROR_LOG = self.td / "error.log"
        ap._recent_api_firstlines = lambda acct, hours=12: set()
        ap._line_done_today = lambda acct, today: True
        ap._hero_priority_order = lambda acct, today, all_posts, posted_texts: list(range(len(all_posts)))
        ap.post_to_threads = _no_network
        ap.post_reply_to_threads = _no_network
        bo._fetch_recent_api = _no_network
        bo._CACHE.clear()
        return self

    def __exit__(self, *a):
        for k, v in self.orig.items():
            setattr(ap, k, v)
        for k, v in self.orig_bo.items():
            setattr(bo, k, v)
        bo._CACHE.clear()


def set_buffer(items_text, active=True, log_paths=None, fetch_fail=False, extra_api=None):
    """Buffer直近投稿（API経由とみなす）と枠の有無を差し替える。"""
    cfg = {"enabled": True, "account": "masa", "_slots": [(7, 10)], "window_minutes": 30,
           "lookback_hours": 4, "max_recent": 3, "similarity_threshold": 0.3, "max_scan": 60,
           "use_api": True, "log_paths": log_paths or []}
    bo.load_config = lambda path=None: cfg
    bo.active_slot = (lambda now, c: "07:10") if active else (lambda now, c: None)
    now = datetime.now(bo.JST)

    def fake_fetch(token, uid, since):
        if fetch_fail:
            raise TimeoutError("timed out")
        out = [{"ts": now - timedelta(minutes=10 + i), "text": t, "is_reply": False} for i, t in enumerate(items_text)]
        return out + (extra_api or [])
    bo._fetch_recent_api = fake_fetch
    bo._CACHE.clear()


def main():
    print("=== 枠判定（±30分・設定ファイル） ===")
    cfg = bo.load_config(BASE / "buffer_slots_masa.json")
    check("設定ファイルが読めて有効・10枠", cfg.get("enabled") and len(cfg["_slots"]) == 10)
    t = lambda h, m: datetime(2026, 10, 23, h, m, tzinfo=bo.JST)
    check("06:39 は枠外", bo.active_slot(t(6, 39), cfg) is None)
    check("06:40 は 07:10枠", bo.active_slot(t(6, 40), cfg) == "07:10")
    check("07:40 は 07:10枠", bo.active_slot(t(7, 40), cfg) == "07:10")
    check("09:00 は枠外", bo.active_slot(t(9, 0), cfg) is None)
    check("22:40 は 22:10枠", bo.active_slot(t(22, 40), cfg) == "22:10")
    check("設定ファイル無し → 無効", bo.load_config(BASE / "__no_such__.json") == {"enabled": False})

    print("=== テーマ・型の採点 ===")
    items = [{"text": BUF_GMAP, "type": "実践Tips", "tags": bo.theme_tags(BUF_GMAP)}]
    check("口コミ候補はテーマ重複(2)", bo.overlap_score(KUCHIKOMI, items)[0] == 2)
    check("インスタ写真候補は型一致のみ(1)", bo.overlap_score(INSTA, items)[0] == 1)
    check("意見候補は重なりなし(0)", bo.overlap_score(OPINION, items)[0] == 0)

    print("=== threads2-post-log.md の防御的パース ===")
    sample = ("# log\n## 2026-10-23 07:18 JST｜7時 通常投稿（型: つぶやき）｜投稿済み\n- 本文:\n  > リピートは「いつもどおり」の積み重ね。\n"
              "- 次の型: 意見（使える実体験があれば実体験）\n## 壊れた見出し\n## 2026-10-23 08:00 JST｜下書き｜未投稿\n  > 出ていない\n")
    its, nt = bo.parse_post_log(sample)
    check("投稿済みだけ1件・型=つぶやき・次の型=意見", len(its) == 1 and its[0]["type"] == "つぶやき" and nt == "意見")
    check("空・壊れた入力でも例外なし", bo.parse_post_log("") == ([], None) and bo.parse_post_log("##\n>\n") == ([], None))

    queue = [KUCHIKOMI, INSTA, AI, OPINION]
    with tempfile.TemporaryDirectory() as td:
        with Env(td, queue):
            print("=== get_next_post（masa） ===")
            set_buffer([BUF_GMAP], active=False)
            check("枠外は従来どおり先頭（口コミ）", ap.get_next_post("masa", D)[0] == KUCHIKOMI)

            set_buffer([BUF_GMAP], active=True)
            txt, idx = ap.get_next_post("masa", D)
            check(f"枠内・Buffer直近=口コミ → 口コミ以外を選ぶ（選択={txt.splitlines()[0][:16]}）",
                  txt != KUCHIKOMI and bo.overlap_score(txt, [{"text": BUF_GMAP, "type": None}])[0] < 2)
            check("後回しは消費済みにしない（枠外になれば口コミが先頭に戻る）",
                  (set_buffer([BUF_GMAP], active=False) or True) and ap.get_next_post("masa", D)[0] == KUCHIKOMI)

            set_buffer([BUF_GMAP, BUF_SNS], active=True)
            txt, _ = ap.get_next_post("masa", D)
            check(f"Buffer直近=口コミ+SNS → インスタ・口コミを避ける（選択={txt.splitlines()[0][:16]}）",
                  txt not in (KUCHIKOMI, INSTA))

            set_buffer([BUF_GMAP], active=True, fetch_fail=True)
            check("API失敗・ログ無し → 従来どおり先頭（止まらない）", ap.get_next_post("masa", D)[0] == KUCHIKOMI)

            set_buffer([BUF_GMAP], active=True)
            bo._CACHE.clear()
            only_overlap = [KUCHIKOMI]
        with Env(td, [KUCHIKOMI, "【テスト】Googleマップの口コミは、返信の早さで差がつきます。\n口コミ返信を後回しにしている店主へ。"]):
            set_buffer([BUF_GMAP], active=True)
            txt, _ = ap.get_next_post("masa", D)
            check("全候補が重なる時も1本は出す（本数を削らない）", txt == KUCHIKOMI)

        with Env(td, [URL_POST, KUCHIKOMI, OPINION]):
            set_buffer([BUF_GMAP], active=True)
            ap._text_has_visible_url = lambda t: "https://" in (t or "")   # 判定対象を広げてURL回避の併用だけを検証
            txt, _ = ap.get_next_post("masa", D, avoid_url=True)
            check("URL連続回避と併用: URL無し・重なり無しを選ぶ", txt == OPINION)

        print("=== Buffer投稿の見分け（API） ===")
        with Env(td, queue, posted=[{"date": D, "index": 0, "post_id": "1", "text": OPINION}]):
            now = datetime.now(bo.JST)
            extra = [
                {"ts": now - timedelta(minutes=3), "text": OPINION, "is_reply": False},          # 自系統の投稿
                {"ts": now - timedelta(minutes=2), "text": "① 窓際で撮る\n② 明るさを上げる\n③ シェアを押す、までを今日やってみてください。お店の雰囲気が伝わります。", "is_reply": True},
                {"ts": now - timedelta(minutes=1), "text": "詳しくはこちら 👇", "is_reply": True},
                {"ts": now - timedelta(minutes=1), "text": "▶ 受け取りはこちら（公式LINE）\nhttps://lin.ee/8PsIHHC", "is_reply": None},
                {"ts": now - timedelta(hours=6), "text": BUF_SNS, "is_reply": False},              # 4時間より古い
            ]
            set_buffer([BUF_GMAP], active=True, extra_api=extra)
            ctx = ap._buffer_ctx("masa")
            texts = [i["text"] for i in ctx["items"]] if ctx else []
            check(f"Buffer分だけ拾う（自系統・返信・URL・古い投稿は除外）: {len(texts)}件", texts == [BUF_GMAP])

        print("=== 他アカウントは一切変わらない ===")
        with Env(td, queue):
            set_buffer([BUF_GMAP], active=True)
            check("truth は _buffer_ctx=None", ap._buffer_ctx("truth") is None)
            check("nagaoka は _buffer_ctx=None", ap._buffer_ctx("nagaoka") is None)
            check("truth は枠内でも先頭（口コミ）", ap.get_next_post("truth", D)[0] == KUCHIKOMI)
            check("nagaoka は枠内でも先頭（口コミ）", ap.get_next_post("nagaoka", D)[0] == KUCHIKOMI)

        print("=== uplink追加枠（masa） ===")
        import uplink_repost as ur
        orig_ur = {k: getattr(ur, k) for k in ("count_today", "want_cumulative", "load_pool", "next_item", "judge", "save_pool")}
        for text, expect_post in ((KUCHIKOMI, False), (OPINION, True)):
            with Env(td, queue):
                calls = []
                item = {"id": "u1", "text": text, "status": "queued", "views": 300}
                ur.count_today = lambda: 0
                ur.want_cumulative = lambda h: 8
                ur.load_pool = lambda: [item]
                ur.next_item = lambda pool: item
                ur.judge = lambda t, s=None, c=None: []
                ur.save_pool = lambda pool: None
                ap.post_to_threads = lambda acct, t, skip_dup=False: calls.append(t) or "FAKE_ID"
                set_buffer([BUF_GMAP], active=True)
                hour_ok = ap.POST_HOUR_START <= datetime.now().hour < ap.POST_HOUR_END
                ap._run_uplink_repost()
                if hour_ok:
                    check(f"uplink {'重なり無し→投稿(偽)' if expect_post else '口コミ重なり→見送り・状態queuedのまま'}",
                          (len(calls) == 1) == expect_post and (expect_post or item["status"] == "queued"))
                else:
                    print("[SKIP] 投稿時間外のため uplink テストは省略")
        for k, v in orig_ur.items():
            setattr(ur, k, v)

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
