#!/usr/bin/env python3
"""
各アカウントが「常に1ヶ月あたり100万閲覧以上」を担保できているかを毎日チェックする。

設計（2026-06-22 作り直し）:
- 旧版は「開始日からの累計 / 60日」方式で pace が過大表示され、真の月次レートを隠していた。
- 新版は **直近30日の実閲覧（=本当の月次レート）vs 100万** で正直に測る。
- views = 投稿数 × 1投稿あたり閲覧。どちらのレバーが不足かを出し、改善先を明示する。
- 投稿数は1日最大50（月1500）が上限なので、現実的な主レバーは「1投稿あたり閲覧」。
- 未達アカウントは views_action.json に記録し、毎朝のサブエージェントEが最優先テコ入れに使う。

使い方:
  python3 views_report.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import sqlite3
import urllib.request
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

BASE = Path(__file__).parent
# LINE登録数を測るharness DB（ローカルのみ）。truth/nagaokaは共有LINE(qbRbPAm)。
_LINE_DBS = {
    "truth_nagaoka": "/Users/mt112/Desktop/line-harness-zutsu/data.db",  # qbRbPAm 共有
    "masa": "/Users/mt112/Desktop/line-harness/data.db",                 # 8PsIHHC（現状不在の可能性）
}


def follower_deltas() -> dict:
    """followers_history.json から各acctの現在値と7日デルタ。無ければ空。"""
    try:
        h = json.loads((BASE / "followers_history.json").read_text())
    except Exception:
        return {}
    out = {}
    for acct in ("truth", "nagaoka", "masa"):
        pts = [(e.get("date"), e.get(acct)) for e in h if isinstance(e, dict) and e.get(acct) is not None]
        if not pts:
            continue
        cur = pts[-1][1]
        week_ago = next((v for d, v in reversed(pts) if d and d <= (date.today() - timedelta(days=7)).isoformat()), None)
        out[acct] = {"now": cur, "d7": (cur - week_ago) if isinstance(week_ago, (int, float)) else None}
    return out


def mendan_count() -> dict:
    """masa harness DBの「面談」受信件数（累計/直近7日）。ローカルのみ。"""
    db = "/Users/mt112/Desktop/line-harness/data.db"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        total = con.execute("SELECT count(*) FROM messages WHERE direction='incoming' AND content LIKE '%面談%'").fetchone()[0]
        d7 = con.execute("SELECT count(*) FROM messages WHERE direction='incoming' AND content LIKE '%面談%' AND created_at >= date('now','-7 days')").fetchone()[0]
        con.close()
        return {"total": total, "d7": d7}
    except Exception:
        return {}


def line_registrations() -> dict:
    """各LINEの累計/直近7日/直近30日の登録数。DB不在時は None を値に。"""
    out = {}
    for key, path in _LINE_DBS.items():
        p = Path(path)
        if not p.exists():
            out[key] = None
            continue
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            total = con.execute("SELECT count(*) FROM friends").fetchone()[0]
            d7 = con.execute("SELECT count(*) FROM friends WHERE added_at >= date('now','-7 days')").fetchone()[0]
            d30 = con.execute("SELECT count(*) FROM friends WHERE added_at >= date('now','-30 days')").fetchone()[0]
            con.close()
            out[key] = {"total": total, "d7": d7, "d30": d30}
        except Exception:
            out[key] = None
    return out
GOAL_FILE = BASE / "views_goal.json"
HIST_FILE = BASE / "views_history.json"
ACTION_FILE = BASE / "views_action.json"     # 毎朝のテコ入れ用フラグ
REPORT_DIR = Path("/Users/mt112/Desktop/my files/myfiles/SNS・Threads/閲覧レポート")
ACCTS = {"truth": "TRUTH", "nagaoka": "NAGAOKA", "masa": "MASA"}
NAMES = {"truth": "@truth_body_salon", "nagaoka": "@truth_nagaoka", "masa": "@masahide_takahashi_"}

for line in (BASE / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()


def load_goal() -> dict:
    g = {"target_per_account": 1_000_000, "mode": "monthly_rolling",
         "window_days": 30, "max_posts_per_day": 50}
    if GOAL_FILE.exists():
        try:
            g.update(json.loads(GOAL_FILE.read_text()))
        except Exception:
            pass
    return g


def fetch_window_views(acct: str, days: int) -> tuple[int, int, int]:
    """直近 days 日の (合計views, 取得日数, 当日views) を返す。
    3回リトライしても失敗した場合は (-1, 0, 0) を返す（呼び出し側で異常終了させる）。
    旧実装は失敗時に (0,0,0) を黙って返し「30日閲覧0」の偽データが
    views_action.json→verify_system→日次検証レポートまで汚染していた（2026-07-12監査で修正。鉄則5: サイレント失敗禁止）。"""
    uid = os.environ.get(f"THREADS_USER_ID_{ACCTS[acct]}")
    tok = os.environ.get(f"THREADS_ACCESS_TOKEN_{ACCTS[acct]}")
    until = int(datetime.now(timezone.utc).timestamp())
    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    url = (f"https://graph.threads.net/v1.0/{uid}/threads_insights"
           f"?metric=views&since={since}&until={until}&access_token={tok}")
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                data = json.loads(r.read())
            vals = data["data"][0].get("values", [])
            total = sum(int(v.get("value", 0)) for v in vals)
            today = int(vals[-1].get("value", 0)) if vals else 0
            return total, len(vals), today
        except Exception as e:
            last_err = e
            time.sleep(15 * (attempt + 1))
    print(f"[ERROR] views API取得失敗 acct={acct} days={days}: {last_err}", file=sys.stderr)
    return -1, 0, 0


def posts_in_window(acct: str, days: int) -> int:
    """直近 days 日の投稿本数（log_<acct>_posted.jsonl ベース）。"""
    pf = BASE / f"log_{acct}_posted.jsonl"
    if not pf.exists():
        return 0
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    n = 0
    for line in pf.read_text(encoding="utf-8").splitlines():
        try:
            if json.loads(line).get("date", "") >= cutoff:
                n += 1
        except Exception:
            pass
    return n


def posted_today(acct: str) -> int:
    pf = BASE / f"log_{acct}_posted.jsonl"
    if not pf.exists():
        return 0
    t = date.today().isoformat()
    return sum(1 for line in pf.read_text(encoding="utf-8").splitlines()
               if line.strip() and json.loads(line).get("date") == t)


def ramp_target(goal: dict, acct: str, today: date) -> tuple[int, int]:
    """(今週のランプ目標avg_post, 経過週) を返す。目標 = base*(666/base)^(week/8) の指数ランプ。"""
    base = goal.get("baseline_avg_post_7d", {}).get(acct, 100) or 100
    tgt = goal.get("target_avg_post", 666)
    weeks = goal.get("ramp_weeks", 8)
    start = date.fromisoformat(goal.get("ramp_start", today.isoformat()))
    week = min(weeks, max(0, (today - start).days) // 7 + 1)   # 1週目から始動
    return int(base * (tgt / base) ** (week / weeks)), week


def main():
    goal = load_goal()
    target = goal["target_per_account"]          # 各アカウント月100万
    win = goal.get("window_days", 30)
    cap = goal.get("max_posts_per_day", 50)
    req_daily = target // win                     # 1日に必要な閲覧 = 33,334
    today = date.today()
    deadline = goal.get("deadline", "")

    stat = {}
    fetch_failed: list[str] = []
    for acct in ACCTS:
        v30, vdays, vtoday = fetch_window_views(acct, win)
        p30 = posts_in_window(acct, win)
        # 7日速報（軌道管理のメイン指標。30日窓はラグが大きく週次PDCAに使えない）
        v7, v7days, _ = fetch_window_views(acct, 7)
        p7 = posts_in_window(acct, 7)
        avg_post_7d = (v7 // p7) if p7 else 0
        if v30 < 0 or v7 < 0:
            fetch_failed.append(acct)
            v30 = max(v30, 0); v7 = max(v7, 0)
        rt, week = ramp_target(goal, acct, today)
        avg_post = (v30 // p30) if p30 else 0      # 1投稿あたり閲覧（実績）
        pace = (v30 * 100 // target) if target else 0
        d_avg = v30 // max(1, vdays)
        gap_daily = max(0, req_daily - d_avg)      # 1日あたり不足views
        # レバー分析: 月100万に必要な「1投稿あたり閲覧」（投稿数=直近実績ベース）
        need_avg_at_current_posts = (target // p30) if p30 else 0
        # 月100万に必要な「投稿数」（1投稿あたり閲覧=現状維持の場合）
        need_posts_at_current_avg = (target // avg_post) if avg_post else 0
        need_posts_per_day = need_posts_at_current_avg / win if need_posts_at_current_avg else 0
        stat[acct] = dict(v30=v30, vdays=vdays, vtoday=vtoday, p30=p30, avg_post=avg_post,
                          pace=pace, d_avg=d_avg, gap_daily=gap_daily,
                          need_avg=need_avg_at_current_posts,
                          need_posts_day=need_posts_per_day, posts_today=posted_today(acct),
                          v7=v7, p7=p7, avg_post_7d=avg_post_7d,
                          ramp_target=rt, ramp_week=week,
                          on_track=avg_post_7d >= rt)

    # ── API取得失敗時: 偽0を書き込まず通知して異常終了（前回の正常な action/history を温存）──
    if fetch_failed:
        msg = (f"【views_report】Threads views API取得失敗: {', '.join(fetch_failed)}\n"
               f"views_action.json は更新せず前回値を温存。ネットワーク/トークンを確認してください。")
        print(msg, file=sys.stderr)
        try:
            subprocess.run(["/Users/mt112/.claude/scripts/line-push-masahide.sh"],
                           input=msg.encode(), timeout=60, check=False)
        except Exception as e:
            print(f"[WARN] LINE通知も失敗: {e}", file=sys.stderr)
        sys.exit(2)

    # ── 問題点・改善アクション（レバーを名指しで）──
    problems, actions = [], []
    behind = []
    for acct in ACCTS:
        s = stat[acct]
        if s["pace"] < 100:
            behind.append(acct)
            problems.append(
                f"{NAMES[acct]}: 月次レート {s['v30']:,}/{target:,}（{s['pace']}%）"
                f" ｜ 1日あと {s['gap_daily']:,} 不足")
        if not s["on_track"]:
            problems.append(
                f"{NAMES[acct]}: 【軌道遅れ】1投稿あたり(7日) {s['avg_post_7d']:,} < 週{s['ramp_week']}目標 {s['ramp_target']:,}"
                f"（{deadline}までに666必要）")
        if today.isoformat() and s["posts_today"] < 5 and datetime.now().hour >= 12:
            problems.append(f"{NAMES[acct]}: 本日投稿 {s['posts_today']}本（投稿停滞の疑い）")

    weakest = min(ACCTS, key=lambda a: stat[a]["pace"]) if ACCTS else None
    for acct in behind:
        s = stat[acct]
        # 主レバー判定: 投稿数は cap(月 cap*win) が上限。avg_post を上げる方が現実的かを示す
        max_posts_month = cap * win
        if s["avg_post"] and s["need_posts_day"] > cap:
            lever = (f"投稿数据え置きでは月{max_posts_month:,}本でも届かない。"
                     f"1投稿あたり閲覧を {s['avg_post']:,}→{s['need_avg']:,} へ引き上げる"
                     f"（フック強化・勝ちパターン比率UP）のが必須")
        else:
            lever = (f"1投稿あたり閲覧 {s['avg_post']:,} を維持しても、"
                     f"月{int(s['need_posts_day'])}本/日でも到達可。投稿数の安定確保が効く")
        actions.append(f"{NAMES[acct]}（{s['pace']}%）: {lever}")
    if weakest:
        actions.insert(0, f"【最優先テコ入れ】{NAMES[weakest]}（pace {stat[weakest]['pace']}%・"
                          f"1投稿 {stat[weakest]['avg_post']:,}views）を今日の重点に")
    if not behind:
        actions.append("全アカウント月100万ペース達成 ✅ 維持しつつ最弱を底上げ")

    # ── Obsidian レポート ──
    bar = lambda p: "█" * (min(p, 100) // 5) + "░" * (20 - min(p, 100) // 5)
    lines = [
        f"# 閲覧レポート {today.isoformat()}（常時・月100万チェック）",
        "",
        f"## 🎯 目標: 各アカウント **常に直近{win}日で {target:,} views 以上**",
        f"> 1日あたり必要 {req_daily:,} views ｜ 投稿上限 {cap}本/日（月{cap*win:,}本）",
        f"> 月100万に必要な1投稿あたり閲覧 = {target//(cap*win):,}（上限投稿時）",
        "",
    ]
    for acct in ACCTS:
        s = stat[acct]
        trk = "🟢軌道内" if s["on_track"] else "🔴軌道遅れ"
        lines += [
            f"### {NAMES[acct]} — pace **{s['pace']}%**  `{bar(s['pace'])}`",
            f"- **{deadline}期限ランプ {trk}**: 1投稿あたり(7日速報) **{s['avg_post_7d']:,}** / 週{s['ramp_week']}目標 **{s['ramp_target']:,}**（最終目標666）",
            f"- 直近{win}日: **{s['v30']:,}** views / {target:,}（日平均 {s['d_avg']:,} / 必要 {req_daily:,}）",
            f"- 投稿 {s['p30']:,}本 → **1投稿あたり {s['avg_post']:,} views** ｜ 本日 {s['vtoday']:,}views・{s['posts_today']}投稿",
            f"- 月100万に必要: 現投稿数なら1投稿 **{s['need_avg']:,}** views / 現品質なら **{s['need_posts_day']:.0f}** 本/日",
            "",
        ]
    # ── ゴール階層KPI（goals.json準拠。閲覧は上流指標）──
    fol = follower_deltas()
    mdn = mendan_count()
    lines += ["## 🏁 ゴールKPI（goals.json の優先順位）", ""]
    if mdn:
        lines.append(f"- masa【最優先】個別面談: 「面談」受信 累計 **{mdn.get('total',0)}** / 直近7日 **+{mdn.get('d7',0)}**")
    for acct in ("truth", "masa"):
        f = fol.get(acct)
        if f:
            d7f = f.get("d7")
            lines.append(f"- {NAMES[acct]} フォロワー: **{f['now']:,}**（7日 {'+' if (d7f or 0)>=0 else ''}{d7f if d7f is not None else '?'}）")
    lines.append("")

    # ── LINE登録（本当のゴール＝成果KPI）──
    line_reg = line_registrations()
    lines += ["## 🎯 LINE登録（成果KPI｜閲覧はこれに繋げる手段）", ""]
    lr = line_reg.get("truth_nagaoka")
    if lr:
        lines.append(f"- truth/nagaoka 共有LINE(頭痛講座): 累計 **{lr['total']}** / 直近7日 **+{lr['d7']}** / 直近30日 +{lr['d30']}")
    else:
        lines.append("- truth/nagaoka 共有LINE: DB読取不可")
    mr = line_reg.get("masa")
    if mr:
        lines.append(f"- masa LINE(集客): 累計 **{mr['total']}** / 直近7日 **+{mr['d7']}** / 直近30日 +{mr['d30']}")
    else:
        lines.append("- ⚠️ masa LINE: harness DB不在で**計測不能**（/Users/mt112/Desktop/line-harness/data.db が無い）")
    if lr is not None and lr["d7"] < 3:
        lines.append("- ⚠️ 閲覧に対しLINE登録がほぼ伸びていない。プロフィールのLINE導線・リードマグネット・LINE誘導投稿の質/量を要改善")
    lines.append("")

    lines += ["## ⚠️ 問題点", ""] + ([f"- {p}" for p in problems] or ["- 特になし（順調）"])
    lines += ["", "## ✅ 改善アクション（レバー指定）", ""] + [f"- {a}" for a in actions]
    lines += ["", f"> 更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}"]

    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / f"閲覧レポート_{today.isoformat()}.md").write_text("\n".join(lines), encoding="utf-8")
        idx = REPORT_DIR / "00_INDEX.md"
        per = " / ".join(f"{a}{stat[a]['pace']}%" for a in ACCTS)
        entry = f"- [{today.isoformat()}](閲覧レポート_{today.isoformat()}.md) 月100万 [{per}]\n"
        if idx.exists():
            if today.isoformat() not in idx.read_text(encoding="utf-8"):
                idx.write_text(idx.read_text(encoding="utf-8") + entry, encoding="utf-8")
        else:
            idx.write_text("# 閲覧レポート索引\n\n" + entry, encoding="utf-8")
    except Exception as e:
        print(f"[WARN] Obsidian保存失敗: {e}")

    # ── 履歴（真の月次レートで記録）──
    try:
        hist = json.loads(HIST_FILE.read_text()) if HIST_FILE.exists() else []
        hist = [h for h in hist if h.get("date") != today.isoformat()]
        hist.append({"date": today.isoformat(),
                     "per_acct_30d": {a: stat[a]["v30"] for a in ACCTS},
                     "per_acct_pace": {a: stat[a]["pace"] for a in ACCTS},
                     "per_acct_avg_post": {a: stat[a]["avg_post"] for a in ACCTS}})
        HIST_FILE.write_text(json.dumps(hist, ensure_ascii=False, indent=2))
    except Exception:
        pass

    # ── テコ入れフラグ（毎朝サブエージェントEが読む）──
    try:
        ACTION_FILE.write_text(json.dumps({
            "date": today.isoformat(),
            "target_per_account": target,
            "weakest": weakest,
            "behind": behind,
            "per_acct": {a: {"pace": stat[a]["pace"], "v30": stat[a]["v30"],
                             "avg_post": stat[a]["avg_post"], "need_avg": stat[a]["need_avg"],
                             "gap_daily": stat[a]["gap_daily"]} for a in ACCTS},
            "priority_order": sorted(ACCTS, key=lambda a: stat[a]["pace"]),
            "line_registrations": line_reg,
            "followers": fol,
            "mendan": mdn,
            # ── 期限付きランプ（2ヶ月で月100万）と本日の修正指示 ──
            "deadline": deadline,
            "ramp": {a: {"week": stat[a]["ramp_week"], "target_avg_post_7d": stat[a]["ramp_target"],
                         "actual_avg_post_7d": stat[a]["avg_post_7d"], "on_track": stat[a]["on_track"]}
                     for a in ACCTS},
            "directives": [
                (f"{a}: 軌道遅れ（{stat[a]['avg_post_7d']}<{stat[a]['ramp_target']}）→ "
                 f"①prune_templates.pyで負けテンプレ削除 ②新テンプレ24本(通常の2倍)を"
                 f"top10_by_viewsの勝ち構造で生成 ③フック全面刷新")
                if not stat[a]["on_track"] else
                f"{a}: 軌道内 → 維持（新テンプレ12本・勝ちパターン比率キープ）"
                for a in sorted(ACCTS, key=lambda x: stat[x]["avg_post_7d"] - stat[x]["ramp_target"])
            ],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    per = " / ".join(f"{a} {stat[a]['pace']}%(1投稿{stat[a]['avg_post']:,})" for a in ACCTS)
    lr = line_reg.get("truth_nagaoka") or {}
    print(f"月100万チェック {today.isoformat()}: [{per}] "
          f"最弱={NAMES.get(weakest,'-')} 未達={len(behind)}件 ｜ "
          f"LINE登録 truth系累計{lr.get('total','?')}(+{lr.get('d7','?')}/7日) "
          f"masa={'計測不能' if line_reg.get('masa') is None else line_reg['masa']['total']} "
          f"→ Obsidian保存・views_action.json更新")


if __name__ == "__main__":
    main()
