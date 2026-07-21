#!/usr/bin/env python3
"""inspector.py 単体テスト（手動実行・pytest不要、標準ライブラリのみ）。
検品ゲート導入(2026-07-21)の受け入れテスト。実行結果はコンソールに出力する。
実行: python3 test_inspector.py
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

import inspector  # noqa: E402


def _assert_ng(text, account, label):
    ok, reasons = inspector.inspect_post(text, account, log=False)
    status = "OK" if not ok else "FAIL(検出されず)"
    print(f"[{status}] {label}: reasons={reasons}")
    return not ok


def _assert_ok(text, account, label):
    ok, reasons = inspector.inspect_post(text, account, log=False)
    status = "OK" if ok else f"FAIL(誤検知: {reasons})"
    print(f"[{status}] {label}")
    return ok


def main():
    results = []

    print("=== ① 実際の破綻投稿（log_masa_posted.jsonl 実例）が検出されること ===")
    broken_examples = [
        "院集客で良いコンテンツを作れば売れると思っているを続ける限り、SNS運用をどれだけ頑張っても数字は動きません。",
        "SNS×LINE導線で消耗している院に共通しているのは良いコンテンツを作れば売れると思っているをやっていること。",
        "問い合わせが来ない投稿の共通点は、毎回違うことを発信して軸がブレていると良いコンテンツを作れば売れると思っているを同時にやっていることです。",
    ]
    for t in broken_examples:
        results.append(_assert_ng(t, "masa", f"実破綻例: {t[:30]}..."))

    print("\n=== ② 許可外の実績数字（捏造疑い）が検出されること ===")
    results.append(_assert_ng("肩こりの改善率85%です。ぜひご相談ください。", "truth", "許可外の改善率85%"))
    results.append(_assert_ng("当院は12店舗展開しています。", "truth", "許可外の店舗数"))
    results.append(_assert_ng("成約率65%を実現しています。", "masa", "許可外の成約率65%"))
    results.append(_assert_ng("施術実績28,000名を突破しました。", "truth", "許可外の施術人数"))
    results.append(_assert_ng("当院は15年以上の施術実績があります。", "truth", "許可外の年数実績"))

    print("\n=== ③ dead_openings（知識確認型疑問）が検出されること ===")
    results.append(_assert_ng("知っていましたか？肩こりの原因", "truth", "知っていましたか型"))
    results.append(_assert_ng("知ってますか？頭痛の本当の理由", "truth", "知ってますか型"))
    results.append(_assert_ng("ご存知ですか？姿勢の重要性について", "nagaoka", "ご存知ですか型"))

    print("\n=== ④ dead_openings（体験談型）が検出されること ===")
    results.append(_assert_ng("先日、お客様から嬉しい言葉をもらいました。", "truth", "先日型（truth）"))
    results.append(_assert_ng("私は整体師になって10年経ちました。", "masa", "私は型（masa・非免除パターン）"))

    print("\n=== ⑤ 助詞重複・句読点破綻が検出されること ===")
    results.append(_assert_ng("肩こりががひどい時は水分を摂ってください。", "truth", "がが重複"))
    results.append(_assert_ng("それは大事なことです。。本当に大事です。", "truth", "。。重複"))

    print("\n=== ⑥ URL過多（本文2個以上）が検出されること ===")
    results.append(_assert_ng(
        "詳しくはこちら https://example.com/a と https://example.com/b もご覧ください。",
        "truth", "URL2個"))

    print("\n=== ⑦ 誇大・規制表現が検出されること ===")
    results.append(_assert_ng("この施術で絶対治ると断言します。", "truth", "絶対治る"))
    results.append(_assert_ng("この方法で必ず稼げるようになります。", "masa", "masa: 稼げる"))

    print("\n=== ⑧ 免除パターン（jiko_kaiji/yakan_bucchake）は書き出しNGでスルーされること ===")
    # jiko_kaiji実テンプレの1行目「正直に言います。」はdead_openingsに元々該当しないため
    # 「私は」で始まる開示風の文を模擬してpattern_name免除が効くか確認する
    ok1, r1 = inspector.inspect_post("私は正直に、集客で悩んだ時期がありました。", "masa",
                                      pattern_name="jiko_kaiji", log=False)
    print(f"[{'OK' if ok1 else 'FAIL: ' + str(r1)}] jiko_kaiji免除で「私は」始まりが通ること")
    results.append(ok1)

    ok2, r2 = inspector.inspect_post("私は正直に、集客で悩んだ時期がありました。", "masa",
                                      pattern_name=None, log=False)
    print(f"[{'OK' if not ok2 else 'FAIL(免除なしなのに通過)'}] 免除なしなら同文がNGになること: {r2}")
    results.append(not ok2)

    print("\n=== ⑩ 規制表現の型（2026-07-21・第9回）が検出されること ===")
    results.append(_assert_ng("この方法なら必ず稼げます。", "masa", "必ず稼げ（断定保証型）"))
    results.append(_assert_ng("絶対に治りますと言い切れる施術です。", "truth", "絶対に治り（断定保証型）"))
    results.append(_assert_ng("誰でも痩せられるメソッドです。", "truth", "誰でも痩せ（断定保証型）"))
    results.append(_assert_ng("当院は業界No.1の実績です。", "masa", "業界No.1（比較優位型）"))
    results.append(_assert_ng("地域ナンバーワンを自負しています。", "truth", "ナンバーワン（比較優位型）"))

    print("\n=== ⑪ 個人名（truth/nagaokaのみ・さん付き完全一致）が検出されること ===")
    results.append(_assert_ng("まぁさんが担当します。", "truth", "まぁさん（truth）"))
    results.append(_assert_ng("ゆうさんに相談してください。", "nagaoka", "ゆうさん（nagaoka）"))
    results.append(_assert_ok("まぁさんの話をしよう。", "masa", "masaは個人名チェック対象外"))
    results.append(_assert_ok("まぁ、そういうこともあります。", "truth", "「まぁ、」は完全一致でないため誤検知しない"))

    print("\n=== ⑫ dead_openings_auto（自動追記分）が読めること ===")
    # 実feedback.jsonを汚さないよう、モジュール変数を一時差し替えてテスト
    import tempfile
    orig_fb_file = inspector.FEEDBACK_FILE
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
        json.dump({
            "ng_words": [],
            "dead_openings": {"all": [], "truth": [], "exempt_patterns": []},
            "dead_openings_auto": {"truth": [
                {"regex": "^テスト負けモチーフ", "motif": "テスト負けモチーフ", "auto": True, "added": "2026-07-21"},
                "^素の文字列regex",
            ]},
        }, tf, ensure_ascii=False)
        tmp_path = tf.name
    try:
        inspector.FEEDBACK_FILE = Path(tmp_path)
        ok_a, r_a = inspector.inspect_post("テスト負けモチーフで始まる投稿", "truth", log=False)
        results.append(not ok_a)
        print(f"[{'OK' if not ok_a else 'FAIL'}] dict形式の自動regexで検出: {r_a}")
        ok_b, r_b = inspector.inspect_post("素の文字列regexで始まる投稿", "truth", log=False)
        results.append(not ok_b)
        print(f"[{'OK' if not ok_b else 'FAIL'}] 文字列形式の自動regexで検出: {r_b}")
        ok_c, _ = inspector.inspect_post("普通の書き出しの投稿です。", "truth", log=False)
        results.append(ok_c)
        print(f"[{'OK' if ok_c else 'FAIL'}] 自動regex非該当は通過")
    finally:
        inspector.FEEDBACK_FILE = orig_fb_file
        Path(tmp_path).unlink(missing_ok=True)

    print("\n=== ⑨ 正常投稿（誤検知しないこと）の代表例 ===")
    normal_examples = [
        ("その肩こり、一生付き合うつもりですか？\n\n1日3分のケアで変わる人、ほんとに多いです。", "truth"),
        ("揉んでも治らない理由があります。", "truth"),
        ("週3で頭痛があった人が\n3回の施術でほぼゼロになった。\n\n何が変わったのか。姿勢の歪みを変えたから。", "truth"),
        ("フォロワーを増やすのは最後でいい。\n\nフォロワー200人で月商100万を超えた方がいます。", "masa"),
        ("何を隠そう、僕が一番数値を見てなかった。\n\n投稿して「なんか伸びないな〜」で終わってた。", "masa"),
    ]
    for t, a in normal_examples:
        results.append(_assert_ok(t, a, f"正常例({a}): {t[:20]}..."))

    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"\n=== 単体テスト結果: {passed}/{total} 合格 ===")
    return passed == total


def false_positive_rate_report():
    """extra_templates全件 + 各log_*_posted.jsonlの直近50投稿での誤検知率を計測する。"""
    print("\n\n=== 誤検知率レポート（実運用コーパス） ===")
    fb = json.loads((BASE / "feedback.json").read_text(encoding="utf-8"))

    # extra_templates 全件
    for acct, templates in fb.get("extra_templates", {}).items():
        ng_count = 0
        ng_samples = []
        for t in templates:
            ok, reasons = inspector.inspect_post(t, acct, log=False)
            if not ok:
                ng_count += 1
                ng_samples.append((t[:40], reasons))
        rate = ng_count / len(templates) * 100 if templates else 0
        print(f"\n[extra_templates:{acct}] {ng_count}/{len(templates)} NG ({rate:.1f}%)")
        for s, r in ng_samples:
            print(f"    NG: {s}... -> {r}")

    # 各アカウントの直近50投稿（log_{acct}_posted.jsonlのtextフィールド。空は除外）
    for acct in ["truth", "nagaoka", "masa"]:
        pfile = BASE / f"log_{acct}_posted.jsonl"
        if not pfile.exists():
            continue
        entries = []
        for line in pfile.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("text"):
                entries.append(e["text"])
        recent = entries[-50:]
        ng_count = 0
        ng_samples = []
        for t in recent:
            ok, reasons = inspector.inspect_post(t, acct, log=False)
            if not ok:
                ng_count += 1
                ng_samples.append((t[:40].replace("\n", " "), reasons))
        rate = ng_count / len(recent) * 100 if recent else 0
        flag = "  <-- 10%超" if rate > 10 else ""
        print(f"\n[log_{acct}_posted.jsonl 直近{len(recent)}件] {ng_count}件 NG ({rate:.1f}%){flag}")
        for s, r in ng_samples[:15]:
            print(f"    NG: {s}... -> {r}")


if __name__ == "__main__":
    ok = main()
    false_positive_rate_report()
    sys.exit(0 if ok else 1)
