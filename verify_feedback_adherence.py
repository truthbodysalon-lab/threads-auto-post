#!/usr/bin/env python3
"""
フィードバック反映検証：feedback.json の各ルールが実装・実行されているかを2軸で検証
- A軸：実装エラー（コードレベル）
- B軸：実行ギャップ（投稿実績が期待値を下回る）
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter

BASE = Path(__file__).parent

def load_feedback():
    """feedback.json を読み込む"""
    with open(BASE / "feedback.json") as f:
        return json.load(f)

def load_posted_log(account):
    """投稿実績を読み込む（直近1ヶ月分）"""
    log_file = BASE / f"log_{account}_posted.jsonl"
    if not log_file.exists():
        return []

    cutoff = (datetime.now() - timedelta(days=30)).date()
    posts = []
    try:
        for line in log_file.read_text().splitlines():
            if not line.strip():
                continue
            post = json.loads(line)
            if post.get("date") >= str(cutoff):
                posts.append(post)
    except Exception as e:
        print(f"[ERROR] {account}の投稿ログ読み込み失敗: {e}", file=sys.stderr)

    return posts

def verify_line_listin(account):
    """LINEリストイン頻度の検証"""
    fb = load_feedback()
    posts = load_posted_log(account)

    if not posts:
        return {"rule": "LINEリストイン", "status": "❌未反映", "reason": "投稿実績なし"}

    # feedback.json ルール取得
    rule_lines = [n for n in fb.get("notes", []) if "LINEリストイン" in n.get("text", "") and n.get("account") in (account, "all")]
    if not rule_lines:
        return {"rule": "LINEリストイン", "status": "⚠️未定義", "reason": "feedback.json にルール記載なし"}

    # 頻度期待値（feedback.json から抽出）
    if account in ("truth", "nagaoka"):
        # truth: 5-7%, nagaoka: <10%
        if account == "truth":
            expected_min, expected_max = 0.05, 0.07
            expected_str = "5-7%"
        else:
            expected_min, expected_max = 0.05, 0.10
            expected_str = "<10%"
    else:
        expected_min, expected_max = 0.05, 0.10
        expected_str = "<10%"

    # 実績を計算
    line_posts = sum(1 for p in posts if "lin.ee" in p.get("text", ""))
    actual_rate = line_posts / len(posts) if posts else 0

    status = "✅反映" if expected_min <= actual_rate <= expected_max else "❌未反映"
    if expected_min <= actual_rate <= (expected_max * 1.2):
        status = "⚠️部分的"  # 20%の余裕を許容

    reason = "期待値範囲内" if "✅" in status else "期待値を超過"
    return {
        "rule": "LINEリストイン頻度",
        "status": status,
        "expected": expected_str,
        "actual": f"{actual_rate*100:.0f}%（{line_posts}/{len(posts)}）",
        "reason": reason
    }

def verify_nagaoka_mention(account):
    """長岡市言及率の検証"""
    if account != "nagaoka":
        return None

    posts = load_posted_log(account)
    if not posts:
        return None

    # feedback.json ルール: 25% (3-4投稿に1回)
    nagaoka_posts = sum(1 for p in posts if "長岡" in p.get("text", ""))
    actual_rate = nagaoka_posts / len(posts) if posts else 0

    expected_min, expected_max = 0.20, 0.30  # 20-30%の範囲を許容
    status = "✅反映" if expected_min <= actual_rate <= expected_max else "❌未反映"

    return {
        "rule": "長岡市言及率",
        "status": status,
        "expected": "25% (3-4投稿に1回)",
        "actual": f"{actual_rate*100:.0f}%（{nagaoka_posts}/{len(posts)}）",
        "reason": "言及率が高すぎる・低すぎる" if "❌" in status else "OK"
    }

def verify_first_line_hooks(account):
    """1文目がフックから始まるかの検証"""
    posts = load_posted_log(account)
    if not posts:
        return None

    # NG パターン: 長岡市/実績/自己紹介で始まる
    ng_patterns = ["長岡", "整体", "施術", "改善", "当店", "当院", "私は", "あなたは"]

    ng_count = 0
    for post in posts[-30:]:  # 直近30件をサンプル（ログは古い→新しい順に追記されるため末尾を採る）
        first_line = post.get("text", "").split("\n")[0].strip()
        if any(ng in first_line[:10] for ng in ng_patterns):
            ng_count += 1

    status = "✅反映" if ng_count == 0 else ("⚠️部分的" if ng_count <= 2 else "❌未反映")

    return {
        "rule": "1文目NG回避（フックから開始）",
        "status": status,
        "expected": "0件",
        "actual": f"{ng_count}件（直近30件中）",
        "reason": "1文目が実績・地域名で始まっている"
    }

def verify_implementation_errors():
    """実装エラー（code level）の検証"""
    errors = []

    # generate_remix.py で anchor_positions が定義されているか確認
    try:
        with open(BASE / "generate_remix.py") as f:
            content = f.read()

        if "anchor_positions" not in content:
            errors.append({
                "id": "impl:anchor",
                "detail": "generate_remix.py にアンカー位置の実装がない",
                "severity": "FAIL"
            })

        if "_monthly_line_url_count" not in content and "masa" in content:
            errors.append({
                "id": "impl:masa_line_url_limit",
                "detail": "masa の LINE URL月間2本制御が未実装",
                "severity": "WARN"
            })
    except Exception as e:
        errors.append({
            "id": "impl:read_error",
            "detail": f"generate_remix.py の読み込み失敗: {e}",
            "severity": "FAIL"
        })

    return errors

def main():
    fb = load_feedback()
    print("# Threads フィードバック反映検証レポート\n")
    print(f"生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # A軸: 実装エラー
    print("## A軸: 実装エラー検証\n")
    impl_errors = verify_implementation_errors()
    for err in impl_errors:
        print(f"- {err['id']}: {err['detail']} ({err['severity']})")
    if not impl_errors:
        print("✅ 実装エラーなし")
    print()

    # B軸: フィードバック反映
    print("## B軸: フィードバック反映検証\n")
    for account in ["truth", "nagaoka", "masa"]:
        print(f"### @{account}\n")

        # LINEリストイン
        result = verify_line_listin(account)
        print(f"- **{result['rule']}**: {result['status']}")
        print(f"  - 期待: {result['expected']}")
        print(f"  - 実績: {result['actual']}")
        print()

        # 長岡市言及（nagaokaのみ）
        if account == "nagaoka":
            result = verify_nagaoka_mention(account)
            if result:
                print(f"- **{result['rule']}**: {result['status']}")
                print(f"  - 期待: {result['expected']}")
                print(f"  - 実績: {result['actual']}")
                print()

        # 1文目フック
        result = verify_first_line_hooks(account)
        if result:
            print(f"- **{result['rule']}**: {result['status']}")
            print(f"  - 期待: {result['expected']}")
            print(f"  - 実績: {result['actual']}")
            print()

if __name__ == "__main__":
    main()
