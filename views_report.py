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
            return json.loads(GOAL_FILE.read_text())
        except Exception:
            pass
    g = {"start_date": date.today().isoformat(), "target": 1_000_000, "days": 30}
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
    target = goal["target"]
    days = goal["days"]
    deadline = start + timedelta(days=days)
    today = date.today()
    elapsed = max(1, (today - start).days + 1)
    remaining = max(0, (deadline - today).days)

    since = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    until = int(datetime.now(timezone.utc).timestamp())

    per_acct_cum, daily_today, daily_avg = {}, {}, {}
    for acct in ACCTS:
        vals = fetch_daily_views(acct, since, until)
        per_acct_cum[acct] = sum(v for _, v in vals)
        daily_today[acct] = vals[-1][1] if vals else 0
        daily_avg[acct] = per_acct_cum[acct] // max(1, len(vals))

    cumulative = sum(per_acct_cum.values())
    total_daily_avg = cumulative // elapsed
    required_daily = target // days
    projection = total_daily_avg * days
    pace = (total_daily_avg * 100 // required_daily) if required_daily else 0
    remaining_needed = max(0, target - cumulative)
    need_daily_from_now = (remaining_needed // remaining) if remaining else remaining_needed

    # ── 問題点・改善点（データ駆動）──
    problems, actions = [], []
    # 弱いアカウント
    weakest = min(ACCTS, key=lambda a: daily_avg[a])
    share = {a: daily_avg[a] * 100 // max(1, total_daily_avg * 0 + sum(daily_avg.values())) for a in ACCTS}
    if daily_avg[weakest] * 3 < sum(daily_avg.values()):
        problems.append(f"{NAMES[weakest]} の閲覧が弱い（日平均 {daily_avg[weakest]:,} / 全体の {share[weakest]}%）")
        actions.append(f"{NAMES[weakest]} のテコ入れ最優先：勝ちパターンの比率UP・投稿本数増・フック改善")
    # ペース
    if pace < 100:
        problems.append(f"目標ペースに対し {pace}%（不足。1ヶ月見込み {projection:,} < 目標 {target:,}）")
        actions.append(f"残り{remaining}日は1日 {need_daily_from_now:,} views必要（現状日平均 {total_daily_avg:,}）。投稿頻度と質を引き上げる")
    else:
        actions.append(f"目標ペース達成中（{pace}%）。この調子を維持しつつ弱いアカウントを底上げ")
    # 投稿停滞
    for acct in ACCTS:
        pc = posted_today(acct)
        if pc < 5:
            problems.append(f"{NAMES[acct]} の本日投稿が {pc}本と少ない（投稿停滞の疑い）")
            actions.append(f"{NAMES[acct]} の投稿パイプライン確認（CI throttle/Mac休止/キュー枯渇）")

    # ── Obsidianレポート ──
    bar = lambda p: "█" * (min(p, 100) // 5) + "░" * (20 - min(p, 100) // 5)
    lines = [
        f"# 閲覧レポート {today.isoformat()}",
        "",
        f"## 🎯 目標進捗（{start.isoformat()}〜{deadline.isoformat()} で {target:,} views）",
        "",
        f"- 累計: **{cumulative:,}** / {target:,} views（{cumulative*100//target}%）",
        f"- `{bar(cumulative*100//target)}`",
        f"- 経過 {elapsed}日 / 残り {remaining}日",
        f"- 現状ペース: 日平均 **{total_daily_avg:,}** views（目標は 1日 {required_daily:,} 必要）",
        f"- 達成見込み: 1ヶ月で **{projection:,}** views（目標比 {pace}%）",
        f"- 残り達成には今後1日あたり **{need_daily_from_now:,}** views必要",
        "",
        "## 📊 アカウント別（目標期間の累計 / 本日 / 日平均）",
        "",
        "| アカウント | 累計views | 本日 | 日平均 | 本日投稿数 |",
        "|---|---|---|---|---|",
    ]
    for acct in ACCTS:
        lines.append(f"| {NAMES[acct]} | {per_acct_cum[acct]:,} | {daily_today[acct]:,} | {daily_avg[acct]:,} | {posted_today(acct)} |")
    lines += [
        "",
        "## ⚠️ 問題点",
        "",
    ]
    lines += [f"- {p}" for p in problems] or ["- 特になし（順調）"]
    lines += ["", "## ✅ 改善アクション", ""]
    lines += [f"- {a}" for a in actions]
    lines += ["", f"> 更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}"]

    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / f"閲覧レポート_{today.isoformat()}.md").write_text("\n".join(lines), encoding="utf-8")
        # インデックス追記
        idx = REPORT_DIR / "00_INDEX.md"
        entry = f"- [{today.isoformat()}](閲覧レポート_{today.isoformat()}.md) 累計{cumulative:,}/{target:,}({cumulative*100//target}%) ペース{pace}%\n"
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

    print(f"閲覧レポート {today.isoformat()}: 累計{cumulative:,}/{target:,}({cumulative*100//target}%) "
          f"日平均{total_daily_avg:,} ペース{pace}% → Obsidian保存")


if __name__ == "__main__":
    main()
