#!/usr/bin/env python3
"""
全アカウントの閲覧データをObsidianに記録し、問題点・改善点・
「1ヶ月で100万閲覧」目標の進捗を出す日次レポート。

- Threads API のアカウント別 views（日次）を取得
- 目標開始日からの累計・現状ペース・達成見込みを算出
- 問題点（弱いアカウント・投稿停滞・伸び悩み）と改善案を出す
- Obsidian（SNS・Threads/閲覧レポート/）にMarkdownでWrite

使い方:
  python3 views_report.py
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

BASE = Path(__file__).parent
GOAL_FILE = BASE / "views_goal.json"
HIST_FILE = BASE / "views_history.json"
REPORT_DIR = Path("/Users/mt112/Desktop/my files/myfiles/SNS・Threads/閲覧レポート")
ACCTS = {"truth": "TRUTH", "nagaoka": "NAGAOKA", "masa": "MASA"}
NAMES = {"truth": "@truth_body_salon", "nagaoka": "@truth_nagaoka", "masa": "@masahide_takahashi_"}

for line in (BASE / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()


def load_goal() -> dict:
    if GOAL_FILE.exists():
        try:
            g = json.loads(GOAL_FILE.read_text())
            # 後方互換: 旧 target → target_per_account
            if "target_per_account" not in g:
                g["target_per_account"] = g.get("target", 1_000_000)
            return g
        except Exception:
            pass
    g = {"start_date": date.today().isoformat(), "target_per_account": 1_000_000, "days": 30}
    GOAL_FILE.write_text(json.dumps(g, ensure_ascii=False, indent=2))
    return g


def _parse_ts(ts: str):
    try:
        return datetime.strptime(ts.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
    except Exception:
        return None


def fetch_daily_views(acct: str, since_ts: int, until_ts: int):
    """[(date_str, views), ...] を返す。失敗時は空。"""
    uid = os.environ.get(f"THREADS_USER_ID_{ACCTS[acct]}")
    tok = os.environ.get(f"THREADS_ACCESS_TOKEN_{ACCTS[acct]}")
    url = (f"https://graph.threads.net/v1.0/{uid}/threads_insights"
           f"?metric=views&since={since_ts}&until={until_ts}&access_token={tok}")
    out = []
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.loads(r.read())
        for v in data["data"][0].get("values", []):
            et = v.get("end_time", "")
            out.append((et[:10], int(v.get("value", 0))))
    except Exception:
        pass
    return out


def posted_today(acct: str) -> int:
    pf = BASE / f"log_{acct}_posted.jsonl"
    if not pf.exists():
        return 0
    t = date.today().isoformat()
    n = 0
    for line in pf.read_text(encoding="utf-8").splitlines():
        try:
            if json.loads(line).get("date") == t:
                n += 1
        except Exception:
            pass
    return n


def main():
    goal = load_goal()
    start = date.fromisoformat(goal["start_date"])
    target = goal["target_per_account"]      # 各アカウント100万
    days = goal["days"]
    deadline = start + timedelta(days=days)
    today = date.today()
    elapsed = max(1, (today - start).days + 1)
    remaining = max(0, (deadline - today).days)
    required_daily = target // days          # 各アカウント1日に必要なviews

    since = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    until = int(datetime.now(timezone.utc).timestamp())

    # アカウントごとに「自身の100万」への進捗を出す
    stat = {}
    for acct in ACCTS:
        vals = fetch_daily_views(acct, since, until)
        cum = sum(v for _, v in vals)
        d_today = vals[-1][1] if vals else 0
        d_avg = cum // elapsed
        proj = d_avg * days
        pace = (d_avg * 100 // required_daily) if required_daily else 0
        need_from_now = (max(0, target - cum) // remaining) if remaining else max(0, target - cum)
        stat[acct] = {"cum": cum, "today": d_today, "avg": d_avg, "proj": proj,
                      "pace": pace, "need": need_from_now, "posts": posted_today(acct)}

    # ── 問題点・改善点（アカウント別・データ駆動）──
    problems, actions = [], []
    for acct in ACCTS:
        s = stat[acct]
        if s["pace"] < 100:
            gap = required_daily - s["avg"]
            problems.append(f"{NAMES[acct]}: 目標ペース {s['pace']}%（日平均 {s['avg']:,} / 必要 {required_daily:,} ＝ 1日あと {gap:,} 不足）")
        if s["posts"] < 5:
            problems.append(f"{NAMES[acct]}: 本日投稿 {s['posts']}本と少ない（投稿停滞の疑い）")
    # 最弱を最優先アクションに
    weakest = min(ACCTS, key=lambda a: stat[a]["avg"])
    actions.append(f"【最優先】{NAMES[weakest]}（日平均 {stat[weakest]['avg']:,}・達成率 {stat[weakest]['pace']}%）を重点改善：勝ちパターン比率UP・投稿本数増・フック強化")
    behind = [a for a in ACCTS if stat[a]["pace"] < 100]
    if behind:
        actions.append("各アカウントとも100万には届いていない。投稿頻度（1日の投稿本数）と1投稿あたりの閲覧（フックの強さ）の両方を上げる必要がある")
        actions.append("特に閲覧を伸ばすには『保存・シェアされる切り口』『コメントを誘う問いかけ』『冒頭3秒で刺すフック』を増やす")
    else:
        actions.append("全アカウント目標ペース達成中。維持しつつ最弱を底上げ")

    # ── Obsidianレポート ──
    bar = lambda p: "█" * (min(p, 100) // 5) + "░" * (20 - min(p, 100) // 5)
    total_cum = sum(stat[a]["cum"] for a in ACCTS)
    lines = [
        f"# 閲覧レポート {today.isoformat()}",
        "",
        f"## 🎯 目標: 各アカウント {target:,} views/月（{start.isoformat()}〜{deadline.isoformat()}）",
        f"> 経過 {elapsed}日 / 残り {remaining}日 ｜ 各アカウント1日 {required_daily:,} views必要 ｜ 3アカウント累計 {total_cum:,}",
        "",
    ]
    for acct in ACCTS:
        s = stat[acct]
        pct = s["cum"] * 100 // target
        lines += [
            f"### {NAMES[acct]}",
            f"- 累計 **{s['cum']:,}** / {target:,}（{pct}%）  `{bar(pct)}`",
            f"- 日平均 **{s['avg']:,}** / 必要 {required_daily:,} ＝ ペース **{s['pace']}%** ｜ 達成見込み {s['proj']:,}",
            f"- 本日 {s['today']:,} views / 投稿 {s['posts']}本 ｜ 残りは1日 {s['need']:,} 必要",
            "",
        ]
    lines += ["## ⚠️ 問題点", ""]
    lines += [f"- {p}" for p in problems] or ["- 特になし（順調）"]
    lines += ["", "## ✅ 改善アクション", ""]
    lines += [f"- {a}" for a in actions]
    lines += ["", f"> 更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    # 後段（保存・履歴）で使う集計値
    cumulative = total_cum
    total_daily_avg = sum(stat[a]["avg"] for a in ACCTS)
    pace = min(stat[a]["pace"] for a in ACCTS)
    per_acct_cum = {a: stat[a]["cum"] for a in ACCTS}

    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / f"閲覧レポート_{today.isoformat()}.md").write_text("\n".join(lines), encoding="utf-8")
        # インデックス追記（各アカウントの達成率を併記）
        idx = REPORT_DIR / "00_INDEX.md"
        per_pct = " / ".join(f"{a}{per_acct_cum[a]*100//target}%" for a in ACCTS)
        entry = f"- [{today.isoformat()}](閲覧レポート_{today.isoformat()}.md) 各100万目標 [{per_pct}] 最低ペース{pace}%\n"
        if idx.exists():
            if today.isoformat() not in idx.read_text(encoding="utf-8"):
                idx.write_text(idx.read_text(encoding="utf-8") + entry, encoding="utf-8")
        else:
            idx.write_text("# 閲覧レポート索引\n\n" + entry, encoding="utf-8")
    except Exception as e:
        print(f"[WARN] Obsidian保存失敗: {e}")

    # 履歴も残す
    try:
        hist = json.loads(HIST_FILE.read_text()) if HIST_FILE.exists() else []
        hist = [h for h in hist if h.get("date") != today.isoformat()]
        hist.append({"date": today.isoformat(), "cumulative": cumulative, "daily_avg": total_daily_avg,
                     "pace": pace, "per_acct": per_acct_cum})
        HIST_FILE.write_text(json.dumps(hist, ensure_ascii=False, indent=2))
    except Exception:
        pass

    per_pct = " / ".join(f"{a} {per_acct_cum[a]*100//target}%" for a in ACCTS)
    print(f"閲覧レポート {today.isoformat()}: 各100万目標への達成率 [{per_pct}] "
          f"3アカウント累計{cumulative:,} 最低ペース{pace}% → Obsidian保存")


if __name__ == "__main__":
    main()
