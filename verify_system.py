#!/usr/bin/env python3
"""
システム自己検証バッテリー（実装エラー＋フィードバック反映＋実行ギャップの検出）

「実装したつもりで実際は動いていない」を自動で炙り出すための検査群。
サブエージェントがこれを実行し、FAIL/WARN を解釈・修正判断する土台。

カテゴリ:
  A. コードエラー   … 全モジュールimport・3アカウント生成が例外なく通るか
  B. ルール反映     … NGワード・1文目ルール・短文が生成物で守られているか
  C. 実行ギャップ   … 直近で実際に投稿/調整/取得できているか（最重要）
  D. ログエラー     … error.log 等に直近のエラーが無いか
  E. 同期           … ローカルの未pushが溜まっていないか

使い方:
  python3 verify_system.py            # 人間可読レポート
  python3 verify_system.py --json     # JSON（サブエージェント用）
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
ACCTS = ["truth", "nagaoka", "masa"]
results = []  # {"id","category","status","detail"}  status: PASS/WARN/FAIL


def add(cid, category, status, detail):
    results.append({"id": cid, "category": category, "status": status, "detail": detail})


def _recent(path: Path, days: int) -> bool:
    if not path.exists():
        return False
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return mtime >= datetime.now() - timedelta(days=days)
    except Exception:
        return False


# ── A. コードエラー ───────────────────────────────
def check_imports():
    mods = ["generate_remix", "myfiles_loader", "analyze_and_tune",
            "auto_post", "insights", "follower_tracker", "line_tracker",
            "duplicate_guard", "token_manager", "git_sync"]
    for m in mods:
        try:
            importlib.import_module(m)
            add(f"import:{m}", "A.コード", "PASS", "import OK")
        except Exception as e:
            add(f"import:{m}", "A.コード", "FAIL", f"{type(e).__name__}: {e}")


def check_generation():
    try:
        import generate_remix as g
        fns = {"truth": g.generate_30_posts, "nagaoka": g.generate_40_nagaoka_posts,
               "masa": g.generate_30_masa_posts}
        for acct, fn in fns.items():
            try:
                posts = fn()
                if posts:
                    add(f"gen:{acct}", "A.コード", "PASS", f"{len(posts)}本生成")
                else:
                    add(f"gen:{acct}", "A.コード", "FAIL", "0本（生成失敗）")
            except Exception as e:
                add(f"gen:{acct}", "A.コード", "FAIL", f"{type(e).__name__}: {e}")
    except Exception as e:
        add("gen:all", "A.コード", "FAIL", f"generate_remix読込失敗: {e}")


# ── B. ルール反映（フィードバックが効いているか）──────────
def check_rules():
    try:
        import generate_remix as g
        fb = json.loads((BASE / "feedback.json").read_text())
        ng_words = fb.get("ng_words", [])
        fns = {"truth": g.generate_30_posts, "nagaoka": g.generate_40_nagaoka_posts,
               "masa": g.generate_30_masa_posts}
        for acct, fn in fns.items():
            ng_hit, first_bad = 0, 0
            for _ in range(3):
                for p in fn():
                    if g._is_ng(p):
                        ng_hit += 1
                    first = p.split("\n")[0].strip()
                    if acct in ("truth", "nagaoka") and first.startswith(("長岡市", "施術実績", "実績")):
                        first_bad += 1
            add(f"rule:ng:{acct}", "B.ルール反映",
                "PASS" if ng_hit == 0 else "FAIL",
                f"NGワード混入 {ng_hit}件（3回生成）")
            if acct in ("truth", "nagaoka"):
                add(f"rule:1文目:{acct}", "B.ルール反映",
                    "PASS" if first_bad == 0 else "FAIL",
                    f"1文目NG違反 {first_bad}件（3回生成）")
        add("rule:ng_words_count", "B.ルール反映", "PASS" if ng_words else "WARN",
            f"NGワード登録 {len(ng_words)}件")
    except Exception as e:
        add("rule:all", "B.ルール反映", "FAIL", f"検査失敗: {e}")


# ── C. 実行ギャップ（実装≠実行 を検出）──────────────
def _posted_recent_texts(acct: str, days: int):
    pfile = BASE / f"log_{acct}_posted.jsonl"
    cutoff = (datetime.now() - timedelta(days=days)).date()
    out = []
    if not pfile.exists():
        return out
    for line in pfile.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
            d = datetime.strptime(e.get("date", ""), "%Y-%m-%d").date()
            if d >= cutoff:
                out.append(e.get("text", ""))
        except Exception:
            pass
    return out


def check_execution_gaps():
    # C1: LINEリストインが直近で実際に投稿されているか（今回のバグの再発検知）
    for acct in ("truth", "nagaoka"):
        texts = _posted_recent_texts(acct, 3)
        line_cnt = sum(1 for t in texts if any(
            m in t for m in ("LINEで配信", "LINEで無料", "LINEへ", "lin.ee", "LINEで毎日", "LINEでお届け")))
        status = "PASS" if line_cnt >= 1 else "FAIL"
        add(f"exec:line_listin:{acct}", "C.実行ギャップ", status,
            f"直近3日のLINEリストイン投稿 {line_cnt}件（{len(texts)}投稿中）")

    # C2: 重みが直近で更新されているか（チューニング稼働）
    for acct in ACCTS:
        f = BASE / f"weights_{acct}.json"
        ok = _recent(f, 2)
        add(f"exec:weights:{acct}", "C.実行ギャップ", "PASS" if ok else "WARN",
            f"weights更新 {'直近2日内' if ok else '2日以上前 or なし'}")

    # C3: insightsに実データ（avg_views>0）が入っているか（null取得バグ検知）
    for acct in ACCTS:
        try:
            d = json.loads((BASE / f"insights_{acct}.json").read_text())
            av = d.get("avg_views", 0)
            add(f"exec:insights:{acct}", "C.実行ギャップ",
                "PASS" if av and av > 0 else "WARN",
                f"平均閲覧 {av}（0なら指標取得不全の疑い）")
        except Exception as e:
            add(f"exec:insights:{acct}", "C.実行ギャップ", "WARN", f"読込不可: {e}")

    # C4: フォロワー履歴が直近更新されているか
    ok = _recent(BASE / "followers_history.json", 2)
    add("exec:followers", "C.実行ギャップ", "PASS" if ok else "WARN",
        f"フォロワー履歴更新 {'直近2日内' if ok else '停滞の疑い'}")

    # C5: 今日のキューが存在するか
    today = date.today().strftime("%Y-%m-%d")
    for acct in ACCTS:
        f = BASE / f"log_{acct}.jsonl"
        has = False
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                    if e.get("date") == today and e.get("posts"):
                        has = True
                except Exception:
                    pass
        add(f"exec:queue:{acct}", "C.実行ギャップ", "PASS" if has else "WARN",
            f"今日のキュー {'あり' if has else 'なし'}")


# ── D. ログエラー ─────────────────────────────────
def check_logs():
    logs = ["error.log", "gitsync_error.log"]
    cutoff = datetime.now() - timedelta(days=1)
    for lg in logs:
        f = BASE / lg
        if not f.exists():
            add(f"log:{lg}", "D.ログ", "PASS", "ログなし（エラー記録なし）")
            continue
        try:
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]
            errs = [l for l in lines if any(k in l for k in ("Traceback", "Error", "ERROR", "Exception", "失敗"))]
            add(f"log:{lg}", "D.ログ", "PASS" if not errs else "WARN",
                f"直近のエラー痕跡 {len(errs)}件" + (f" 例: {errs[-1][:80]}" if errs else ""))
        except Exception as e:
            add(f"log:{lg}", "D.ログ", "WARN", f"読込失敗: {e}")


# ── E. 同期 ───────────────────────────────────────
def check_sync():
    try:
        subprocess.run(["git", "fetch", "origin", "main"], cwd=str(BASE),
                       capture_output=True, timeout=30)
        r = subprocess.run(["git", "rev-list", "--count", "origin/main..HEAD"],
                           cwd=str(BASE), capture_output=True, text=True)
        n = int(r.stdout.strip() or "0")
        add("sync:unpushed", "E.同期", "PASS" if n == 0 else "WARN",
            f"未pushコミット {n}件" + ("（溜まっている）" if n > 3 else ""))
    except Exception as e:
        add("sync:unpushed", "E.同期", "WARN", f"確認失敗: {e}")


def run_all():
    check_imports()
    check_generation()
    check_rules()
    check_execution_gaps()
    check_logs()
    check_sync()


def main():
    try:
        run_all()
    except Exception:
        add("verify:crash", "A.コード", "FAIL", traceback.format_exc()[-300:])

    fails = [r for r in results if r["status"] == "FAIL"]
    warns = [r for r in results if r["status"] == "WARN"]
    summary = {
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(results), "fail": len(fails), "warn": len(warns),
        "pass": len(results) - len(fails) - len(warns),
        "overall": "FAIL" if fails else ("WARN" if warns else "PASS"),
    }

    if "--json" in sys.argv:
        print(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2))
    else:
        print(f"=== システム検証 {summary['checked_at']} ===")
        print(f"総合: {summary['overall']} | PASS {summary['pass']} / WARN {summary['warn']} / FAIL {summary['fail']}\n")
        for r in results:
            mark = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}[r["status"]]
            if r["status"] != "PASS":
                print(f"  {mark} [{r['category']}] {r['id']}: {r['detail']}")
        if not fails and not warns:
            print("  全項目PASS")

    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
