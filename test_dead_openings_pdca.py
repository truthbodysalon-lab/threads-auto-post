#!/usr/bin/env python3
"""analyze_and_tune.py の負け投稿→dead_openings自動追記PDCA テスト（2026-07-21）。

安全設計:
- sync_to_github（git commit/push）は呼ばない
- save_obsidian_report は一時ディレクトリへ差し替え（本番Obsidianの当日レポートを壊さない）
- feedback.json の実ファイルは汚さない（一時コピーに対して追記検証）
実行: python3 test_dead_openings_pdca.py
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

import analyze_and_tune as at  # noqa: E402

results = []


def check(label, cond):
    print(f"[{'OK' if cond else 'FAIL'}] {label}")
    results.append(cond)


def test_run_weights_not_broken():
    """既存の重み調整（run）がクラッシュせず、weights_*.jsonの形式が保たれること。
    Obsidianレポートは書かない（run自体はレポート行を返すだけ）。"""
    print("=== run(acct): 重み調整がクラッシュしないこと ===")
    for acct in ["truth", "nagaoka", "masa"]:
        try:
            lines = at.run(acct)
            ok = isinstance(lines, list) and len(lines) > 3
            wfile = at.ACCOUNTS[acct]["weights"]
            wdata = json.loads(wfile.read_text(encoding="utf-8"))
            ok = ok and "pattern_weights" in wdata and wdata["pattern_weights"]
            ok = ok and all(1 <= w <= 20 for w in wdata["pattern_weights"].values())
            check(f"run({acct}) 正常終了・weights形式OK", ok)
        except Exception as e:
            check(f"run({acct}) 例外なし", False)
            print(f"    exception: {e}")


def test_real_data_pdca():
    """実データ（past_posts*.json）で自動追記を検証。実feedback.jsonは汚さず
    一時コピーへ追記させ、追記が発生した場合は内容を報告する。"""
    print("\n=== 実データでの自動追記（一時feedback.jsonコピーに対して） ===")
    with tempfile.TemporaryDirectory() as td:
        tmp_fb = Path(td) / "feedback.json"
        shutil.copy(BASE / "feedback.json", tmp_fb)
        for acct in ["truth", "nagaoka", "masa"]:
            added = at.update_dead_openings_from_losers(acct, feedback_path=tmp_fb)
            print(f"  {acct}: 追記 {len(added)}件")
            for a in added:
                print(f"    + {a['motif']} ({a['losses']}敗) -> {a['regex']}")
            check(f"{acct}: 1実行2件以内", len(added) <= at.DEAD_AUTO_MAX_PER_RUN)
        # 追記後のJSONが妥当であること
        fb = json.loads(tmp_fb.read_text(encoding="utf-8"))
        check("追記後のfeedback.jsonがJSONとして妥当", isinstance(fb, dict))
        total_auto = at._count_auto_total(fb)
        check(f"自動追記の累計({total_auto})が上限{at.DEAD_AUTO_MAX_TOTAL}以内", total_auto <= at.DEAD_AUTO_MAX_TOTAL)


def test_synthetic_pdca():
    """擬似データで仕様どおりの動作を単体検証:
    ①3敗以上のモチーフのみ追記 ②導線投稿は除外 ③重複防止 ④1回2件上限 ⑤累計30件上限"""
    print("\n=== 擬似データでの仕様検証 ===")
    from datetime import datetime, timedelta, timezone

    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S+0000")
    new_ts = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S+0000")

    def mk(text, views, ts=old_ts):
        return {"text": text, "views": str(views), "timestamp": ts,
                "like_count": "0", "replies_count": "0", "id": "x"}

    posts = []
    # 高views層（中央値を作る）: 40件 x views=100
    for i in range(40):
        posts.append(mk(f"勝ちパターンの投稿その{i}です。\n本文。", 100))
    # 負け層: 同一モチーフ「負けモチーフABC」×4件 views=1（下位10%かつ中央値25%未満）
    for i in range(4):
        posts.append(mk(f"負けモチーフABCの投稿{i}\n本文。", 1))
    # 負けだが2件のみのモチーフ（追記されないこと）
    for i in range(2):
        posts.append(mk(f"二敗だけモチーフの投稿{i}\n本文。", 1))
    # 負けているが導線投稿（除外されること）: 4件
    for i in range(4):
        posts.append(mk(f"導線負けモチーフの投稿{i}\nhttps://lin.ee/qbRbPAm", 0))
    # 負けているが3日以内（対象外であること）: 4件
    for i in range(4):
        posts.append(mk(f"新しい負けモチーフの投稿{i}\n本文。", 0, ts=new_ts))
    # もう1つの3敗モチーフ + さらに3敗モチーフ（1回2件上限の検証用に計3モチーフ目）
    for i in range(3):
        posts.append(mk(f"第二の負けモチーフ投稿{i}\n本文。", 1))
    for i in range(3):
        posts.append(mk(f"第三の負けモチーフ投稿{i}\n本文。", 1))

    with tempfile.TemporaryDirectory() as td:
        tmp_base = Path(td)
        # past_posts.json（truth用パス）と feedback.json を擬似生成
        (tmp_base / "past_posts.json").write_text(
            json.dumps(posts, ensure_ascii=False), encoding="utf-8")
        tmp_fb = tmp_base / "feedback.json"
        tmp_fb.write_text(json.dumps({
            "ng_words": [],
            "dead_openings": {"all": [], "truth": ["^既にある負けモチーフ"], "exempt_patterns": []},
            "notes": [],
        }, ensure_ascii=False), encoding="utf-8")

        orig_base = at.BASE
        at.BASE = tmp_base
        try:
            added = at.update_dead_openings_from_losers("truth", feedback_path=tmp_fb)
        finally:
            at.BASE = orig_base

        motifs = [a["motif"] for a in added]
        check("1回2件上限が守られる（3モチーフ負けでも2件）", len(added) == 2)
        check("最多敗モチーフ（4敗）が最優先で追記される", any("負けモチーフA" in m for m in motifs))
        check("2敗のみのモチーフは追記されない", not any("二敗だけ" in m for m in motifs))
        check("導線投稿（lin.ee）は除外される", not any("導線負け" in m for m in motifs))
        check("3日以内の投稿は対象外", not any("新しい負け" in m for m in motifs))

        fb = json.loads(tmp_fb.read_text(encoding="utf-8"))
        auto = fb.get("dead_openings_auto", {}).get("truth", [])
        check("dead_openings_autoに書き込まれている", len(auto) == 2)
        check("メタ情報(auto/added/losses)が付与されている",
              all(a.get("auto") is True and a.get("added") and a.get("losses") for a in auto))
        check("updated_atが更新されている", bool(fb.get("updated_at")))

        # 再実行 → 同じモチーフは重複追記されないこと（第三のモチーフのみ追記可能）
        at.BASE = tmp_base
        try:
            added2 = at.update_dead_openings_from_losers("truth", feedback_path=tmp_fb)
        finally:
            at.BASE = orig_base
        motifs2 = [a["motif"] for a in added2]
        check("再実行で既追記モチーフは重複しない", not (set(motifs) & set(motifs2)))
        fb2 = json.loads(tmp_fb.read_text(encoding="utf-8"))
        auto2 = fb2.get("dead_openings_auto", {}).get("truth", [])
        regexes = [a["regex"] for a in auto2]
        check("累積してもregexの重複なし", len(regexes) == len(set(regexes)))

        # 累計30件上限: autoを29件に膨らませて再実行 → 追記は1件までに制限される
        fb2["dead_openings_auto"]["truth"] = [
            {"regex": f"^ダミー{i}", "motif": f"ダミー{i}", "losses": 3, "auto": True, "added": "2026-07-21"}
            for i in range(29)
        ]
        tmp_fb.write_text(json.dumps(fb2, ensure_ascii=False), encoding="utf-8")
        at.BASE = tmp_base
        try:
            added3 = at.update_dead_openings_from_losers("truth", feedback_path=tmp_fb)
        finally:
            at.BASE = orig_base
        fb3 = json.loads(tmp_fb.read_text(encoding="utf-8"))
        check("累計30件上限が守られる", at._count_auto_total(fb3) <= at.DEAD_AUTO_MAX_TOTAL)


def main():
    test_run_weights_not_broken()
    test_real_data_pdca()
    test_synthetic_pdca()
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"\n=== PDCAテスト結果: {passed}/{total} 合格 ===")
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
