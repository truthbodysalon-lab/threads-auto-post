#!/usr/bin/env python3
"""
投稿パフォーマンス分析 + 生成重み自動調整

毎朝9時に launchd から実行される。
- insights_*.json（API取得データ）とfeedback.jsonl（手動評価）を統合
- パターン別スコアを計算してweights_*.jsonを更新
- 分析レポートをObsidianに保存

使い方:
  python3 analyze_and_tune.py         # 両アカウント分析
  python3 analyze_and_tune.py truth   # truth のみ
  python3 analyze_and_tune.py --quiet # サイレント（launchd用）
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
TODAY = date.today().strftime("%Y-%m-%d")
QUIET = "--quiet" in sys.argv

MYFILES = Path("/Users/mt112/Desktop/my files/myfiles")
REPORT_DIR = MYFILES / "SNS・Threads" / "分析レポート"

ACCOUNTS = {
    "truth": {
        "name": "@truth_body_salon",
        "log": BASE / "log_truth.jsonl",
        "posted": BASE / "log_truth_posted.jsonl",
        "insights": BASE / "insights_truth.json",
        "weights": BASE / "weights_truth.json",
        "patterns": ["quote_empathy", "insight", "education", "story", "workmom", "ranking", "question"],
        "default_weights": {
            "quote_empathy": 6,
            "insight": 5,
            "education": 5,
            "story": 3,
            "workmom": 2,
            "ranking": 4,
            "question": 3,
        },
    },
    "nagaoka": {
        "name": "@truth_nagaoka",
        "log": BASE / "log_nagaoka.jsonl",
        "posted": BASE / "log_nagaoka_posted.jsonl",
        "insights": BASE / "insights_nagaoka.json",
        "weights": BASE / "weights_nagaoka.json",
        "patterns": ["keisei_target", "keisei_risk", "keisei_kyokan", "keisei_casual", "quote_empathy", "insight", "education", "story", "ranking", "question", "hochi_risk"],
        "default_weights": {
            "keisei_target": 7,   # 軽症者ターゲット（メイン）
            "keisei_risk":   5,   # 軽症放置リスク
            "keisei_kyokan": 4,   # 軽症者共感
            "keisei_casual": 5,   # 口語共感・呼びかけ（堀式参考）
            "quote_empathy": 5,
            "insight":       5,
            "education":     4,
            "story":         3,
            "ranking":       4,
            "question":      3,
            "hochi_risk":    5,
        },
    },
    "masa": {
        "name": "@masahide_takahashi_",
        "log": BASE / "log_masa.jsonl",
        "posted": BASE / "log_masa_posted.jsonl",
        "insights": BASE / "insights_masa.json",
        "weights": BASE / "weights_masa.json",
        "patterns": ["insight", "education", "story", "cta", "ranking", "question"],
        "default_weights": {
            "insight": 7,
            "education": 6,
            "story": 3,
            "cta": 3,
            "ranking": 4,
            "question": 3,
        },
    },
}

FEEDBACK_FILE = BASE / "feedback.jsonl"
FOLLOWERS_HISTORY = BASE / "followers_history.json"
LINE_HISTORY = BASE / "line_history.json"


# ── パターン検出 ───────────────────────────────────

def detect_pattern(text: str, acct: str) -> str:
    if acct == "nagaoka":
        if any(w in text for w in ["多いよね", "ちょろい", "ちょうどいい", "ほんとに", "逆ですよ", "ですよね", "「私も」"]):
            return "keisei_casual"
        if any(w in text for w in ["まだ我慢できる", "軽症のうち", "軽いうちに", "まだ大丈夫", "まだ薬を飲む"]):
            return "keisei_target"
        if any(w in text for w in ["慢性化する前", "放置は禁物", "月に2〜3回", "我慢できる範囲"]) and "[COMMENT]" in text:
            return "keisei_risk"
        if any(w in text for w in ["大げさかな", "後回しにして", "病院に行くほど", "整体に来るのは"]):
            return "keisei_kyokan"
        if any(w in text for w in ["TOP3", "3選", "3つ", "1位", "2位"]):
            return "ranking"
        if any(w in text for w in ["コメントで教えて", "当てはまりますか", "A：", "B："]):
            return "question"
        if text.startswith("「"):
            return "quote_empathy"
        if "[COMMENT]" in text and any(w in text for w in ["放置", "自律神経", "慢性化"]):
            return "hochi_risk"
        lines = [l for l in text.split("\n") if l.strip()]
        if len(lines) <= 2:
            return "insight"
        if any(c in text for c in ["▶", "◎", "①", "②"]):
            return "education"
        if any(w in text for w in ["長岡", "先日", "お客様"]):
            return "story"
        return "insight"
    elif acct == "truth":
        if any(w in text for w in ["TOP3", "3選", "3つ", "1位", "2位", "1）", "2）"]):
            return "ranking"
        if any(w in text for w in ["コメントで教えて", "当てはまりますか", "A：", "B："]):
            return "question"
        if text.startswith("「"):
            return "quote_empathy"
        lines = [l for l in text.split("\n") if l.strip()]
        if len(lines) <= 2:
            return "insight"
        if any(c in text for c in ["▶", "◎", "①", "②"]):
            return "education"
        if any(w in text for w in ["ワーママ", "育児", "家事も"]):
            return "workmom"
        if any(w in text for w in ["長岡", "先日", "お客様", "整体師"]):
            return "story"
        return "insight"
    else:
        if any(w in text for w in ["TOP3", "3選", "共通点", "1位", "2位"]):
            return "ranking"
        if any(w in text for w in ["コメントで教えて", "当てはまりますか", "A：", "どれだと思う"]):
            return "question"
        if any(c in text for c in ["①", "②", "③"]):
            return "education"
        if any(w in text for w in ["先日", "お客様"]):
            return "story"
        if any(w in text for w in ["LINE登録", "プロフィールのリンク", "相談"]):
            return "cta"
        return "insight"


# ── データ収集 ────────────────────────────────────

def load_feedback_scores(acct: str) -> dict:
    """パターン別フィードバックスコア {pattern: score} を返す"""
    if not FEEDBACK_FILE.exists():
        return {}
    scores = defaultdict(lambda: {"good": 0, "bad": 0})
    for l in FEEDBACK_FILE.read_text().splitlines():
        try:
            e = json.loads(l)
            if e.get("account") != acct:
                continue
            pattern = e.get("pattern") or detect_pattern(e.get("text", ""), acct)
            if e.get("rating") == "good":
                scores[pattern]["good"] += 1
            elif e.get("rating") == "bad":
                scores[pattern]["bad"] += 1
        except Exception:
            pass
    # スコア = good - bad*2 (badをより重く)
    return {p: v["good"] - v["bad"] * 2 for p, v in scores.items()}


def load_api_scores(acct: str) -> dict:
    """insights.json からパターン別APIスコアを計算"""
    insights_file = ACCOUNTS[acct]["insights"]
    if not insights_file.exists():
        return {}

    try:
        data = json.loads(insights_file.read_text())
    except Exception:
        return {}

    # past_posts.json から各投稿のパターンを特定してスコア付け
    past_file = ACCOUNTS[acct].get("past_posts_file") or BASE / f"past_posts_{acct}.json"
    if acct == "truth":
        past_file = BASE / "past_posts.json"
    if not past_file.exists():
        return {}

    try:
        posts = json.loads(past_file.read_text())
    except Exception:
        return {}

    avg_likes = data.get("avg_likes", 0)
    pattern_scores = defaultdict(list)

    for p in posts:
        text = p.get("text", "")
        likes = p.get("like_count", 0)
        views = p.get("views", 0)
        if not text or (likes == 0 and views == 0):
            continue
        # いいね率ベースのスコア
        score = likes + (views * 0.01)
        pattern = detect_pattern(text, acct)
        pattern_scores[pattern].append(score)

    if not pattern_scores:
        return {}

    # 全体平均との差分でスコア化
    all_scores = [s for ss in pattern_scores.values() for s in ss]
    overall_avg = sum(all_scores) / len(all_scores) if all_scores else 1

    result = {}
    for pattern, scores in pattern_scores.items():
        avg = sum(scores) / len(scores)
        # 全体平均からの乖離を-3〜+3にスケール
        diff = (avg - overall_avg) / (overall_avg + 0.001)
        result[pattern] = round(diff * 3, 2)

    return result


# ── フォロワー増加スコア（KPI直結）────────────────

def _posts_by_date(acct: str) -> dict:
    """投稿日 -> [text, ...] を返す（log_*_posted.jsonl から）"""
    posted = ACCOUNTS[acct]["posted"]
    by_date = defaultdict(list)
    if not posted.exists():
        return by_date
    for l in posted.read_text().splitlines():
        try:
            e = json.loads(l)
            d = e.get("date")
            t = e.get("text", "")
            if d and t:
                by_date[d].append(t)
        except Exception:
            pass
    return by_date


def _attribute_deltas_to_patterns(acct: str, series: list) -> dict:
    """日次デルタ系列 [{date, delta}, ...] を、その日に投稿したパターンへ
    按分し、パターン別スコア（-3〜+3程度）を返す共通処理。"""
    if len(series) < 2:
        return {}  # デルタを算出できる履歴がまだ足りない

    by_date = _posts_by_date(acct)
    if not by_date:
        return {}

    credit = defaultdict(float)
    for snap in series:
        d = snap.get("date")
        delta = snap.get("delta", 0)
        posts = by_date.get(d, [])
        if not posts or delta == 0:
            continue
        day_patterns = defaultdict(int)
        for t in posts:
            day_patterns[detect_pattern(t, acct)] += 1
        total = sum(day_patterns.values())
        for pat, cnt in day_patterns.items():
            credit[pat] += delta * (cnt / total)

    if not credit:
        return {}

    vals = list(credit.values())
    avg = sum(vals) / len(vals) if vals else 0
    spread = max(abs(max(vals) - avg), abs(min(vals) - avg), 1.0)
    return {pat: round((c - avg) / spread * 3, 2) for pat, c in credit.items()}


def load_follower_scores(acct: str) -> dict:
    """フォロワー増加デルタを、その日の投稿パターンへ按分したスコア。
    「いいね」ではなく「実際にフォロワーが増えた型」を増やすKPIシグナル。"""
    if not FOLLOWERS_HISTORY.exists():
        return {}
    try:
        history = json.loads(FOLLOWERS_HISTORY.read_text())
    except Exception:
        return {}
    return _attribute_deltas_to_patterns(acct, history.get(acct, []))


def load_line_scores(acct: str) -> dict:
    """LINE登録（友だち追加）デルタを、その日の投稿パターンへ按分したスコア。
    最終KPI=LINE登録に繋がった型。現状はmasaのみ（harness連携）。"""
    if not LINE_HISTORY.exists():
        return {}
    try:
        history = json.loads(LINE_HISTORY.read_text())
    except Exception:
        return {}
    return _attribute_deltas_to_patterns(acct, history.get(acct, []))


# ── 重み計算 ──────────────────────────────────────

def compute_new_weights(acct: str, feedback_scores: dict, api_scores: dict,
                        follower_scores: dict | None = None,
                        line_scores: dict | None = None) -> dict:
    follower_scores = follower_scores or {}
    line_scores = line_scores or {}
    defaults = ACCOUNTS[acct]["default_weights"].copy()
    patterns = ACCOUNTS[acct]["patterns"]

    new_weights = {}
    for pattern in patterns:
        base = defaults.get(pattern, 5)
        fb = feedback_scores.get(pattern, 0)
        api = api_scores.get(pattern, 0)
        fol = follower_scores.get(pattern, 0)
        line = line_scores.get(pattern, 0)
        # 優先度: LINE登録=最終KPI(係数3) > 手動FB(2)・フォロワー増(2) > APIエンゲージ(1)
        adjustment = line * 3 + fb * 2 + fol * 2 + api
        new_w = round(base + adjustment)
        # 最小1、最大20にクランプ
        new_weights[pattern] = max(1, min(20, new_w))

    return new_weights


def save_weights(acct: str, weights: dict, feedback_scores: dict, api_scores: dict,
                 follower_scores: dict | None = None, line_scores: dict | None = None):
    out = {
        "updated_at": TODAY,
        "pattern_weights": weights,
        "feedback_scores": feedback_scores,
        "api_scores": api_scores,
        "follower_scores": follower_scores or {},
        "line_scores": line_scores or {},
    }
    ACCOUNTS[acct]["weights"].write_text(
        json.dumps(out, ensure_ascii=False, indent=2)
    )


# ── レポート生成 ──────────────────────────────────

def build_report_lines(acct: str, old_weights: dict, new_weights: dict,
                        feedback_scores: dict, api_scores: dict,
                        follower_scores: dict | None = None,
                        line_scores: dict | None = None) -> list[str]:
    follower_scores = follower_scores or {}
    line_scores = line_scores or {}
    name = ACCOUNTS[acct]["name"]

    # KPIサマリー（フォロワー・LINE登録）
    summary_parts = []
    if FOLLOWERS_HISTORY.exists():
        try:
            hist = json.loads(FOLLOWERS_HISTORY.read_text()).get(acct, [])
            if hist:
                week_delta = sum(s.get("delta", 0) for s in hist[-7:])
                summary_parts.append(f"フォロワー: {hist[-1]['followers']}人 / 直近7日 {week_delta:+}")
        except Exception:
            pass
    if LINE_HISTORY.exists():
        try:
            lh = json.loads(LINE_HISTORY.read_text()).get(acct, [])
            if lh:
                week_delta = sum(s.get("delta", 0) for s in lh[-7:])
                summary_parts.append(f"LINE登録: {lh[-1]['friends']}人 / 直近7日 {week_delta:+}")
        except Exception:
            pass

    lines = [
        f"## {name}",
        "",
    ]
    if summary_parts:
        lines += [f"> {' ｜ '.join(summary_parts)}", ""]
    lines += [
        "| パターン | 旧重み | 新重み | FB | フォロワー | LINE | API |",
        "|---------|-------|-------|----|----------|------|----|",
    ]
    for pattern in ACCOUNTS[acct]["patterns"]:
        old = old_weights.get(pattern, "-")
        new = new_weights.get(pattern, "-")
        fb = feedback_scores.get(pattern, 0)
        fol = round(follower_scores.get(pattern, 0), 2)
        line = round(line_scores.get(pattern, 0), 2)
        api = round(api_scores.get(pattern, 0), 2)
        arrow = "↑" if isinstance(old, (int, float)) and isinstance(new, (int, float)) and new > old else ("↓" if isinstance(old, (int, float)) and isinstance(new, (int, float)) and new < old else ("→" if isinstance(old, (int, float)) else "?"))
        lines.append(f"| {pattern} | {old} | **{new}** {arrow} | {fb:+} | {fol:+} | {line:+} | {api:+} |")

    lines += ["", "---", ""]
    return lines


def save_obsidian_report(report_lines: list[str]):
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORT_DIR / f"分析_{TODAY}.md"
        out.write_text("\n".join(report_lines), encoding="utf-8")
        if not QUIET:
            print(f"  → Obsidian保存: {out}")
    except Exception as e:
        if not QUIET:
            print(f"  [WARN] Obsidian保存失敗: {e}")


# ── LINE登録の取得（ローカル専用）──────────────────

def refresh_line_history():
    """line_tracker を呼んで line_history.json を最新化する。
    harness DB はローカルにしか無いため、DBが無い環境では静かにスキップ。"""
    try:
        import line_tracker
        line_tracker.main()
    except Exception as e:
        if not QUIET:
            print(f"  [WARN] LINE登録取得スキップ: {e}")


def refresh_follower_history():
    """follower_tracker を呼んで followers_history.json を最新化する。
    CIのgit競合で履歴が飛ぶのを避け、ローカルの本ジョブで毎日確実に蓄積する。"""
    try:
        import follower_tracker
        follower_tracker.main()
    except Exception as e:
        if not QUIET:
            print(f"  [WARN] フォロワー取得スキップ: {e}")


# ── 重み・履歴をGitHubへ同期 ───────────────────────

def sync_to_github():
    """ローカルで更新した重み・KPI履歴をGitHubへpushし、
    GitHub Actionsのgenerate.ymlが最新の重みで生成できるようにする。"""
    import subprocess
    files = [
        "weights_truth.json", "weights_nagaoka.json", "weights_masa.json",
        "followers_history.json", "line_history.json",
    ]
    files = [f for f in files if (BASE / f).exists()]
    try:
        subprocess.run(["git", "add", *files], cwd=str(BASE), check=False)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(BASE))
        if diff.returncode == 0:
            return  # 変更なし
        subprocess.run(
            ["git", "commit", "-m", f"chore: KPI連動の重み更新 {TODAY} [skip ci]"],
            cwd=str(BASE), check=False,
        )
        for _ in range(3):
            if subprocess.run(["git", "push"], cwd=str(BASE)).returncode == 0:
                break
            subprocess.run(["git", "pull", "--rebase", "-X", "ours", "origin", "main"],
                           cwd=str(BASE), check=False)
        if not QUIET:
            print("  → 重み・KPI履歴をGitHubへ同期")
    except Exception as e:
        if not QUIET:
            print(f"  [WARN] GitHub同期スキップ: {e}")


# ── メイン ────────────────────────────────────────

def run(acct: str) -> list[str]:
    name = ACCOUNTS[acct]["name"]
    if not QUIET:
        print(f"\n{name} 分析中...")

    # 既存の重みを読む
    wfile = ACCOUNTS[acct]["weights"]
    if wfile.exists():
        old_data = json.loads(wfile.read_text())
        old_weights = old_data.get("pattern_weights", ACCOUNTS[acct]["default_weights"])
    else:
        old_weights = ACCOUNTS[acct]["default_weights"].copy()

    feedback_scores = load_feedback_scores(acct)
    api_scores = load_api_scores(acct)
    follower_scores = load_follower_scores(acct)
    line_scores = load_line_scores(acct)
    new_weights = compute_new_weights(acct, feedback_scores, api_scores, follower_scores, line_scores)
    save_weights(acct, new_weights, feedback_scores, api_scores, follower_scores, line_scores)

    if not QUIET:
        print(f"  パターン重み更新:")
        for p, w in new_weights.items():
            old = old_weights.get(p, "?")
            arrow = "↑" if isinstance(old, (int, float)) and w > old else ("↓" if isinstance(old, (int, float)) and w < old else ("→" if isinstance(old, (int, float)) else "?"))
            fol = follower_scores.get(p, 0)
            line = line_scores.get(p, 0)
            tags = []
            if line: tags.append(f"LINE寄与 {line:+}")
            if fol: tags.append(f"フォロワー寄与 {fol:+}")
            tag = f"  ({', '.join(tags)})" if tags else ""
            print(f"    {p:<18} {old} → {w} {arrow}{tag}")

    return build_report_lines(acct, old_weights, new_weights, feedback_scores, api_scores, follower_scores, line_scores)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    target = args[0].lower() if args else "all"

    # KPI（フォロワー数・LINE登録）を最新化してから採点する
    refresh_follower_history()
    refresh_line_history()

    report_lines = [
        f"# Threads 投稿分析レポート｜{TODAY}",
        f"> 更新: {datetime.now().strftime('%H:%M')}",
        "",
        "---",
        "",
    ]

    if target == "truth":
        report_lines += run("truth")
    elif target == "nagaoka":
        report_lines += run("nagaoka")
    elif target == "masa":
        report_lines += run("masa")
    else:
        report_lines += run("truth")
        report_lines += run("nagaoka")
        report_lines += run("masa")

    save_obsidian_report(report_lines)
    sync_to_github()

    if not QUIET:
        print("\n  完了。generate_remix.py は次回から新しい重みで生成します。")


if __name__ == "__main__":
    main()
