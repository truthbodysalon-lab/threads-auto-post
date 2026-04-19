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
        "patterns": ["keisei_target", "keisei_risk", "keisei_kyokan", "quote_empathy", "insight", "education", "story", "ranking", "question", "hochi_risk"],
        "default_weights": {
            "keisei_target": 7,   # 軽症者ターゲット（メイン）
            "keisei_risk":   5,   # 軽症放置リスク
            "keisei_kyokan": 4,   # 軽症者共感
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


# ── パターン検出 ───────────────────────────────────

def detect_pattern(text: str, acct: str) -> str:
    if acct == "nagaoka":
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


# ── 重み計算 ──────────────────────────────────────

def compute_new_weights(acct: str, feedback_scores: dict, api_scores: dict) -> dict:
    defaults = ACCOUNTS[acct]["default_weights"].copy()
    patterns = ACCOUNTS[acct]["patterns"]

    new_weights = {}
    for pattern in patterns:
        base = defaults.get(pattern, 5)
        fb = feedback_scores.get(pattern, 0)
        api = api_scores.get(pattern, 0)
        # フィードバックを優先（係数2）、APIスコアを補助（係数1）
        adjustment = fb * 2 + api
        new_w = round(base + adjustment)
        # 最小1、最大20にクランプ
        new_weights[pattern] = max(1, min(20, new_w))

    return new_weights


def save_weights(acct: str, weights: dict, feedback_scores: dict, api_scores: dict):
    out = {
        "updated_at": TODAY,
        "pattern_weights": weights,
        "feedback_scores": feedback_scores,
        "api_scores": api_scores,
    }
    ACCOUNTS[acct]["weights"].write_text(
        json.dumps(out, ensure_ascii=False, indent=2)
    )


# ── レポート生成 ──────────────────────────────────

def build_report_lines(acct: str, old_weights: dict, new_weights: dict,
                        feedback_scores: dict, api_scores: dict) -> list[str]:
    name = ACCOUNTS[acct]["name"]
    lines = [
        f"## {name}",
        "",
        "| パターン | 旧重み | 新重み | FB調整 | API調整 |",
        "|---------|-------|-------|--------|---------|",
    ]
    for pattern in ACCOUNTS[acct]["patterns"]:
        old = old_weights.get(pattern, "-")
        new = new_weights.get(pattern, "-")
        fb = feedback_scores.get(pattern, 0)
        api = round(api_scores.get(pattern, 0), 2)
        arrow = "↑" if isinstance(old, (int, float)) and isinstance(new, (int, float)) and new > old else ("↓" if isinstance(old, (int, float)) and isinstance(new, (int, float)) and new < old else ("→" if isinstance(old, (int, float)) else "?"))
        lines.append(f"| {pattern} | {old} | **{new}** {arrow} | {fb:+} | {api:+} |")

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
    new_weights = compute_new_weights(acct, feedback_scores, api_scores)
    save_weights(acct, new_weights, feedback_scores, api_scores)

    if not QUIET:
        print(f"  パターン重み更新:")
        for p, w in new_weights.items():
            old = old_weights.get(p, "?")
            arrow = "↑" if isinstance(old, (int, float)) and w > old else ("↓" if isinstance(old, (int, float)) and w < old else ("→" if isinstance(old, (int, float)) else "?"))
            print(f"    {p:<18} {old} → {w} {arrow}")

    return build_report_lines(acct, old_weights, new_weights, feedback_scores, api_scores)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    target = args[0].lower() if args else "all"

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

    if not QUIET:
        print("\n  完了。generate_remix.py は次回から新しい重みで生成します。")


if __name__ == "__main__":
    main()
