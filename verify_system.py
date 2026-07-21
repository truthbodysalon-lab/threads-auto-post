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
import os
import subprocess
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
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
            "duplicate_guard", "token_manager", "git_sync", "inspector"]
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


# ── B'. ルール衛生（検品ゲート導入・2026-07-21）───────────────
def check_rule_hygiene():
    """feedback.json notesの肥大化・inspection_log.jsonlのNG急増を検知する。
    Brain記事(aoi_ai 2026-07-20)実測: ルール1万字肥大→矛盾→品質低下、
    3,536字へ剪定で回復。noteは足すより削る運用を維持できているかの健全性チェック。"""
    try:
        fb = json.loads((BASE / "feedback.json").read_text(encoding="utf-8"))
        notes = fb.get("notes", [])
        total_chars = sum(len(n.get("text", "")) for n in notes)
        n_count = len(notes)
        status = "PASS" if (n_count <= 35 and total_chars <= 20000) else "WARN"
        add("hygiene:notes", "B.ルール反映", status,
            f"notes {n_count}件・合計{total_chars}字" +
            ("（ルール剪定要）" if status == "WARN" else ""))
    except Exception as e:
        add("hygiene:notes", "B.ルール反映", "WARN", f"確認失敗: {e}")

    try:
        log_file = BASE / "inspection_log.jsonl"
        if not log_file.exists():
            add("hygiene:inspection_ng", "B.ルール反映", "PASS", "inspection_log.jsonl なし（NGなし）")
        else:
            today = date.today().strftime("%Y-%m-%d")
            n_today = 0
            for line in log_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    if json.loads(line).get("date") == today:
                        n_today += 1
                except Exception:
                    pass
            status = "PASS" if n_today <= 20 else "WARN"
            add("hygiene:inspection_ng", "B.ルール反映", status,
                f"本日の検品NG {n_today}件" +
                ("（誤検知 or 生成品質劣化の疑い）" if status == "WARN" else ""))
    except Exception as e:
        add("hygiene:inspection_ng", "B.ルール反映", "WARN", f"確認失敗: {e}")


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

    # C5: 今日のキューが存在するか（朝の生成時間帯=8時より前は未生成でも正常）
    today = date.today().strftime("%Y-%m-%d")
    before_gen = datetime.now().hour < 8  # 生成は朝6〜7時。8時前は未生成でもOK
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
        if has:
            add(f"exec:queue:{acct}", "C.実行ギャップ", "PASS", "今日のキュー あり")
        elif before_gen:
            add(f"exec:queue:{acct}", "C.実行ギャップ", "PASS", "今日のキュー未生成（朝の生成前で正常）")
        else:
            add(f"exec:queue:{acct}", "C.実行ギャップ", "WARN", "今日のキュー なし（生成失敗の疑い）")

    # C6: 誘導投稿（ホットペッパー予約・店舗アクセス）がキュー前半(<50)に固定されているか
    #     ランダム高位置だと1日約50本の消費に届かず未投稿になるバグの再発検知。
    for acct in ("truth", "nagaoka"):
        f = BASE / f"log_{acct}.jsonl"
        try:
            lines = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
            posts = json.loads(lines[-1]).get("posts", []) if lines else []
            hpb = [i for i, p in enumerate(posts) if "beauty.hotpepper.jp" in p]
            acc = [i for i, p in enumerate(posts) if "専用駐車場" in p or "長岡駅から車で5分" in p]
            bad = [x for x in hpb + acc if x >= 50]
            present = len(hpb) >= 1 and len(acc) >= 1
            if not present:
                add(f"exec:funnel:{acct}", "C.実行ギャップ", "WARN",
                    f"誘導投稿がキューに不足（HPB{len(hpb)}/アクセス{len(acc)}）")
            elif bad:
                add(f"exec:funnel:{acct}", "C.実行ギャップ", "FAIL",
                    f"誘導投稿が位置50超に配置（未投稿リスク）: {bad}")
            else:
                add(f"exec:funnel:{acct}", "C.実行ギャップ", "PASS",
                    f"誘導投稿は前半固定 HPB{hpb} アクセス{acc}")
        except Exception as e:
            add(f"exec:funnel:{acct}", "C.実行ギャップ", "WARN", f"キュー確認不可: {e}")

    # 共通ヘルパー: 昨日(JST)の実投稿数を外形API（Threads API）で数える。C8/C10で共有。
    _api_count_cache: dict = {}
    def _yesterday_posts_api_c8(acct: str) -> int:
        """昨日の本体投稿数。スコープ不足トークンはリプライ込み概算。取得不可なら-1。"""
        if acct in _api_count_cache:
            return _api_count_cache[acct]
        import urllib.request as _ur
        jst = timezone(timedelta(hours=9))
        y0 = datetime.now(jst).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        envf = BASE / ".env"
        if envf.exists():
            for _l in envf.read_text().splitlines():
                if "=" in _l and not _l.startswith("#"):
                    _k, _v = _l.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip())
        uid = os.environ.get(f"THREADS_USER_ID_{acct.upper()}")
        tok = os.environ.get(f"THREADS_ACCESS_TOKEN_{acct.upper()}")
        if not uid or not tok:
            _api_count_cache[acct] = -1
            return -1
        url = (f"https://graph.threads.net/v1.0/{uid}/threads?fields=id,is_reply"
               f"&since={int(y0.timestamp())}&until={int((y0 + timedelta(days=1)).timestamp())}"
               f"&limit=100&access_token={tok}")
        n = -1
        try:
            with _ur.urlopen(url, timeout=20) as r:
                data = json.loads(r.read())
            n = sum(1 for x in data.get("data", []) if not x.get("is_reply"))
        except Exception:
            try:
                with _ur.urlopen(url.replace("id,is_reply", "id"), timeout=20) as r:
                    data = json.loads(r.read())
                n = len(data.get("data", []))  # リプライ込み概算（過大側）
            except Exception:
                n = -1
        _api_count_cache[acct] = n
        return n

    # C8: 1日50投稿の遵守（昨日の実績）。ユーザー必須ルール。
    # 2026-07-13改修: ローカルログはCIコミット経由の不完全ミラーで過少カウント（偽FAIL）を
    # 出すため、外形API実測（C10と同じ_yesterday_posts_api）を正とし、API不可時のみログで代替。
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    def _count_from_log(acct: str) -> int:
        pfile = BASE / f"log_{acct}_posted.jsonl"
        n = 0
        if pfile.exists():
            for line in pfile.read_text(encoding="utf-8").splitlines():
                try:
                    if json.loads(line).get("date") == yesterday:
                        n += 1
                except Exception:
                    pass
        return n
    for acct in ACCTS:
        n = _yesterday_posts_api_c8(acct)
        src = "外形API実測"
        if n < 0:
            n = _count_from_log(acct)
            src = "ローカルログ（API不可・過少の可能性）"
        add(f"exec:daily50:{acct}", "C.実行ギャップ",
            "PASS" if n >= 50 else "FAIL",
            f"昨日({yesterday})の投稿 {n}本 / 必須50本（{src}）")

    # C9: プレイブック（修正基準の正本）が毎朝検証・更新されているか。
    #     基準そのものを向上させるメタPDCAが止まると改善が旧基準で空回りする。
    for acct in ACCTS:
        f = BASE / f"playbook_{acct}.json"
        try:
            pb = json.loads(f.read_text(encoding="utf-8"))
            upd = pb.get("updated", "2000-01-01")
            days_old = (date.today() - date.fromisoformat(upd)).days
            active = sum(1 for r in pb.get("rules", []) if r.get("status") == "active")
            testing = sum(1 for r in pb.get("rules", []) if r.get("status") == "testing")
            if days_old > 2:
                add(f"exec:playbook:{acct}", "C.実行ギャップ", "WARN",
                    f"プレイブック未検証{days_old}日（毎朝検証・updated更新が必須）")
            elif active == 0:
                add(f"exec:playbook:{acct}", "C.実行ギャップ", "WARN",
                    f"activeルール0件（testing{testing}件。昇格判定が機能していない疑い）")
            else:
                add(f"exec:playbook:{acct}", "C.実行ギャップ", "PASS",
                    f"v{pb.get('version','?')} 更新{upd} active{active}/testing{testing}")
        except Exception as e:
            add(f"exec:playbook:{acct}", "C.実行ギャップ", "WARN", f"playbook読込不可: {e}")

    # C10: 昨日の実投稿数チェック（外形監視=Threads APIを正とする。2026-07-12改修）
    #      旧実装はautopost_*.log（CIコミット経由の不完全ミラー）を数えており、
    #      ログ遅延で「18本しか投稿していない」等の偽FAILを出していた。
    #      時間帯の偏りはCIバースト設計（memory: project_mac_independence）どおりのため検査しない。
    for acct in ACCTS:
        try:
            n = _yesterday_posts_api_c8(acct)
            if n < 0:
                add(f"exec:pacing:{acct}", "C.実行ギャップ", "WARN",
                    "外形API取得不可（トークンのスコープ不足等）。実投稿数はCI watchdogの外形監視が正")
            elif n < 40:
                add(f"exec:pacing:{acct}", "C.実行ギャップ", "FAIL",
                    f"投稿数不足: 昨日の実投稿{n}本 < 40本（目標50本/日の8割・外形API実測）")
            else:
                add(f"exec:pacing:{acct}", "C.実行ギャップ", "PASS",
                    f"昨日の実投稿{n}本（外形API実測・目標50本/日）")
        except Exception as e:
            add(f"exec:pacing:{acct}", "C.実行ギャップ", "WARN", f"配分検査不可: {e}")

    # C11: watchdogが直近24hに動いているか（見張り番自体の死活監視）
    ok = _recent(BASE / "watchdog.log", 1)
    add("exec:watchdog", "C.実行ギャップ", "PASS" if ok else "WARN",
        "watchdog稼働中" if ok else "watchdog.logが24h以上更新なし（launchd要確認）")

    # C7: 月100万ペース（常時アラーム）。views_action.json を読み、未達アカウントを明示。
    #     達成は野心的目標のため未達は WARN（恒常監視・改善誘導が目的。FAILにはしない）。
    try:
        va = json.loads((BASE / "views_action.json").read_text())
        per = va.get("per_acct", {})
        behind = va.get("behind", [])
        for acct in ACCTS:
            s = per.get(acct, {})
            pace = s.get("pace", 0)
            status = "PASS" if pace >= 100 else "WARN"
            add(f"exec:views_pace:{acct}", "C.実行ギャップ", status,
                f"月次pace {pace}%（30日{s.get('v30',0):,} / 1投稿{s.get('avg_post',0)}→必要{s.get('need_avg',0)}）")
        if behind:
            add("exec:views_weakest", "C.実行ギャップ", "WARN",
                f"月100万未達 {len(behind)}件・最弱={va.get('weakest')}（優先テコ入れ要）")
        # C7b: 期限ランプ（2026-09-03に月100万）の軌道チェック
        ramp = va.get("ramp", {})
        off = [a for a, r in ramp.items() if not r.get("on_track")]
        if ramp:
            add("exec:views_ramp", "C.実行ギャップ",
                "PASS" if not off else "WARN",
                ("全アカウント軌道内" if not off else
                 "軌道遅れ: " + ", ".join(
                     f"{a}({ramp[a]['actual_avg_post_7d']}<{ramp[a]['target_avg_post_7d']})" for a in off)
                 + f" → 週{next(iter(ramp.values()))['week']}/8・期限{va.get('deadline')}"))
    except Exception as e:
        add("exec:views_pace", "C.実行ギャップ", "WARN", f"views_action.json未生成: {e}（views_report.py要実行）")


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
            # リトライ/フォールバックで回復する一時的エラーは除外し、最終失敗のみ問題視
            # ネットワーク一時エラー（接続リセット・DNS解決失敗・タイムアウト・SSL切断・投稿失敗code=0）も除外
            transient = (
                "リトライ", "fallback", "retrying", "後にリトライ",
                "Connection reset", "Connection Reset",
                "nodename nor servname", "Errno 8", "Errno 54", "Errno 60",
                "Operation timed out", "EOF occurred in violation",
                "投稿失敗 (code=0)",
            )
            errs = [l for l in lines
                    if any(k in l for k in ("Traceback", "Error", "ERROR", "Exception", "失敗"))
                    and not any(t in l for t in transient)]
            add(f"log:{lg}", "D.ログ", "PASS" if not errs else "WARN",
                f"未回復エラー {len(errs)}件" + (f" 例: {errs[-1][:80]}" if errs else ""))
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
    check_rule_hygiene()
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

    # Obsidianへレポート保存（--report）
    if "--report" in sys.argv:
        try:
            rd = Path("/Users/mt112/Desktop/my files/myfiles/システム検証")
            rd.mkdir(parents=True, exist_ok=True)
            lines = [f"# システム検証 {summary['checked_at']}", "",
                     f"総合: **{summary['overall']}** | PASS {summary['pass']} / WARN {summary['warn']} / FAIL {summary['fail']}",
                     "", "## 要対応・注意（FAIL/WARN）", ""]
            nonpass = [r for r in results if r["status"] != "PASS"]
            if nonpass:
                for r in nonpass:
                    lines.append(f"- **{r['status']}** [{r['category']}] `{r['id']}`: {r['detail']}")
            else:
                lines.append("- なし（全項目PASS）")
            (rd / f"検証_{date.today().strftime('%Y-%m-%d')}.md").write_text("\n".join(lines), encoding="utf-8")
        except Exception:
            pass

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
