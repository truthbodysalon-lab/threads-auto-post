#!/usr/bin/env python3
"""auto_post.py のURL連続回避ロジック単体テスト（2026-07-21）。
auto_post.py自体（run_account/post_to_threads等、実投稿を伴う経路）は絶対に呼ばない。
切り出した純粋関数のみを対象にする:
  - _text_has_visible_url
  - pick_avoiding_consecutive_url
  - _last_post_had_visible_url（ファイル読み取りのみ・ネットワーク不使用）
  - get_next_post(avoid_url=...)（ファイル読み取りのみ・ネットワーク不使用）
実行: python3 test_url_avoidance.py
"""
import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

import auto_post as ap  # noqa: E402

results = []


def check(label, cond):
    print(f"[{'OK' if cond else 'FAIL'}] {label}")
    results.append(cond)


def main():
    print("=== _text_has_visible_url ===")
    check("LINEリストイン(lin.ee)はURL可視",
          ap._text_has_visible_url("頭痛の原因を知りたい方へ\nhttps://lin.ee/qbRbPAm"))
    check("頭痛タイプ診断はURL可視",
          ap._text_has_visible_url(f"診断はこちら\n{ap.SEITAI_LINE_URL if hasattr(ap, 'SEITAI_LINE_URL') else 'https://truthbodysalon-lab.github.io/zutsu-shindan/'}"))
    check("HPB CTA相当（本文にURL直書きでも[COMMENT]分離される運用のためis_line/is_shindan判定はFalse)",
          not ap._text_has_visible_url("肩こりの原因は水分不足\nhttps://beauty.hotpepper.jp/CSP/kr/reserve/?storeId=H000596246"))
    check("通常投稿（URL無し）はFalse",
          not ap._text_has_visible_url("揉んでも治らない理由があります。"))

    print("\n=== pick_avoiding_consecutive_url ===")
    url_post = ("頭痛の原因\nhttps://lin.ee/qbRbPAm", 3)
    plain_post1 = ("揉んでも治らない理由があります。", 0)
    plain_post2 = ("その症状、一生付き合うつもりですか？", 1)

    # ケース1: 直前URLなし → 先頭候補をそのまま返す（挙動不変)
    picked = ap.pick_avoiding_consecutive_url([url_post, plain_post1], last_had_url=False)
    check("直前URLなしなら先頭候補そのまま", picked == url_post)

    # ケース2: 直前URLあり・候補内にURL無しがある → URL無しを優先
    picked = ap.pick_avoiding_consecutive_url([url_post, plain_post1, plain_post2], last_had_url=True)
    check("直前URLありならURL無し候補を優先", picked == plain_post1)

    # ケース3: 直前URLあり・候補が全てURL付き → 削らずそのまま先頭を返す
    picked = ap.pick_avoiding_consecutive_url([url_post], last_had_url=True)
    check("代替が無ければURL付きでもそのまま返す（削らない）", picked == url_post)

    # ケース4: 候補が空 → None
    picked = ap.pick_avoiding_consecutive_url([], last_had_url=True)
    check("候補が空ならNone", picked is None)

    print("\n=== _last_post_had_visible_url（一時ファイルで検証・ネットワーク不使用）===")
    with tempfile.TemporaryDirectory() as td:
        tmp_posted = Path(td) / "log_test_posted.jsonl"
        orig_accounts = ap.ACCOUNTS
        ap.ACCOUNTS = dict(orig_accounts)
        ap.ACCOUNTS["truth"] = dict(orig_accounts["truth"])
        ap.ACCOUNTS["truth"]["posted"] = tmp_posted
        try:
            # 直近投稿がURLありのケース
            tmp_posted.write_text(json.dumps({
                "date": "2026-07-21", "index": 0, "post_id": "1",
                "text": "頭痛の原因\nhttps://lin.ee/qbRbPAm"}) + "\n", encoding="utf-8")
            check("直近投稿がURLありならTrue",
                  ap._last_post_had_visible_url("truth", "2026-07-21") is True)

            # 直近投稿がURLなしのケース（同日2件目・後の行が優先されること）
            with open(tmp_posted, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "date": "2026-07-21", "index": 1, "post_id": "2",
                    "text": "揉んでも治らない理由があります。"}) + "\n")
            check("直近(最終行)投稿がURLなしならFalse（最終行を見ること）",
                  ap._last_post_had_visible_url("truth", "2026-07-21") is False)

            check("本日の投稿が無ければFalse",
                  ap._last_post_had_visible_url("truth", "2099-01-01") is False)
        finally:
            ap.ACCOUNTS = orig_accounts

    print("\n=== get_next_post(avoid_url=...) 挙動チェック（ファイル読み取りのみ）===")
    # 実在の line_listin_state.json（本物のBASE直下）に日付が無い架空日付を使い、
    # LINEリストイン優先ブロックの状態に依存しないテストにする。
    TEST_DATE = "2099-01-01"
    with tempfile.TemporaryDirectory() as td:
        tmp_queue = Path(td) / "log_test.jsonl"
        tmp_posted = Path(td) / "log_test_posted.jsonl"
        tmp_queue.write_text(json.dumps({
            "date": TEST_DATE,
            "posts": [
                "頭痛の原因\nhttps://lin.ee/qbRbPAm",  # is_line -> LINE優先ブロックで先に処理される
                "肩こりの本当の理由\nhttps://beauty.hotpepper.jp/x",  # URL付きだが通常候補
                "揉んでも治らない理由があります。",  # 通常URL無し候補
            ],
        }) + "\n", encoding="utf-8")
        tmp_posted.write_text("", encoding="utf-8")

        orig_accounts = ap.ACCOUNTS
        ap.ACCOUNTS = dict(orig_accounts)
        ap.ACCOUNTS["truth"] = dict(orig_accounts["truth"])
        ap.ACCOUNTS["truth"]["log"] = tmp_queue
        ap.ACCOUNTS["truth"]["posted"] = tmp_posted
        try:
            # LINE当日未実施（架空日付）のため、LINE優先ブロックが先に候補を返す。
            # avoid_urlはLINE優先ブロックの対象外という設計どおり、両者で同じ結果になるはず。
            t1, i1 = ap.get_next_post("truth", TEST_DATE, avoid_url=False)
            t2, i2 = ap.get_next_post("truth", TEST_DATE, avoid_url=True)
            check("avoid_url有無に関わらずLINE優先ブロックの挙動は同じ（設計どおり対象外）",
                  t1 == t2 == "頭痛の原因\nhttps://lin.ee/qbRbPAm")
        finally:
            ap.ACCOUNTS = orig_accounts

    # 通常ループの avoid_url 実効テスト。
    # is_line候補はLINE優先ブロックで先取りされ通常ループには回らないため、
    # 通常ループでメイン本文にURLが残る唯一のケース＝頭痛タイプ診断(shindan)で検証する。
    # （HPB CTA・店舗アクセスは extract_url_and_cta でURLがコメントへ分離されるため対象外＝仕様どおり）
    shindan_url = "https://truthbodysalon-lab.github.io/zutsu-shindan/"
    with tempfile.TemporaryDirectory() as td:
        tmp_queue = Path(td) / "log_test2.jsonl"
        tmp_posted = Path(td) / "log_test2_posted.jsonl"
        tmp_queue.write_text(json.dumps({
            "date": TEST_DATE,
            "posts": [
                f"自分の頭痛のタイプ、答えられますか。\n{shindan_url}",  # 本文にURLが残る候補（先頭）
                "揉んでも治らない理由があります。",  # URL無し通常候補（2番目）
            ],
        }) + "\n", encoding="utf-8")
        tmp_posted.write_text("", encoding="utf-8")

        orig_accounts = ap.ACCOUNTS
        ap.ACCOUNTS = dict(orig_accounts)
        ap.ACCOUNTS["truth"] = dict(orig_accounts["truth"])
        ap.ACCOUNTS["truth"]["log"] = tmp_queue
        ap.ACCOUNTS["truth"]["posted"] = tmp_posted
        orig_line_done = ap._line_done_today
        ap._line_done_today = lambda acct, today: True  # LINE優先ブロックを無効化してテスト対象を絞る
        try:
            t3, i3 = ap.get_next_post("truth", TEST_DATE, avoid_url=False)
            check("avoid_url=Falseなら従来どおり先頭のURL付き候補（診断アンカー）を返す（挙動不変）",
                  t3 == f"自分の頭痛のタイプ、答えられますか。\n{shindan_url}")

            t4, i4 = ap.get_next_post("truth", TEST_DATE, avoid_url=True)
            check("avoid_url=TrueならURL無し候補（2番目）を優先する",
                  t4 == "揉んでも治らない理由があります。")
        finally:
            ap.ACCOUNTS = orig_accounts
            ap._line_done_today = orig_line_done

    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"\n=== 単体テスト結果: {passed}/{total} 合格 ===")
    return passed == total


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
