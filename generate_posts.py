#!/usr/bin/env python3
"""
Generate Threads posts for both accounts using Claude API.

分析データ参照先:
https://docs.google.com/spreadsheets/d/1QYstUmgd6VzooZo0oSi6DlBEyDjYRgPDOFh0LuXm0Bc/

【@truth_body_salon 分析インサイト（2026-03-22時点）】
- 総投稿数 約20,107件、平均いいね1.3
- 高パフォーマンスTOP:
  1位: SnowMan/SixTONES芸能ネタ → いいね948, 閲覧22,322 (いいね率4.25%)
  2位: 長岡市の住みやすさストーリー → いいね305, 閲覧70,269
  3位: 長岡5年ストーリー → いいね263, 閲覧6,689 (いいね率3.93%)
  4位: イチロー長岡来訪ニュース → いいね230, 閲覧11,019
  5位: カルーセル「人は変わります」→ いいね190, 閲覧206,101(閲覧最多)
  6位: 体育教員免許・新潟の先生の話 → いいね129, 閲覧14,058
- 整体教育コンテンツ単体は閲覧9〜112、いいね0〜1と低エンゲージメント
- 高パフォーマンスの共通点: 個人ストーリー・長岡ローカル・共感・日常エピソード
"""

import json
import os
import sys
import random
from datetime import date
from pathlib import Path

# .env ファイルからAPIキーを読み込む
_env_file = Path(__file__).parent / '.env'
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

import anthropic

today = date.today()
day = today.day

# ─────────────────────────────────────────────
# 過去投稿を読み込む
# ─────────────────────────────────────────────
def load_past_posts(path: str, sample: int = 30) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    posts = json.loads(p.read_text())
    # テスト投稿を除外し、テキストがあるものだけ抽出
    texts = [
        p["text"] for p in posts
        if p.get("text") and "テスト投稿" not in p.get("text", "")
    ]
    sampled = random.sample(texts, min(sample, len(texts)))
    return "\n---\n".join(sampled)

PAST_POSTS_TRUTH = load_past_posts(
    Path(__file__).parent / "past_posts.json", sample=30
)

truth_themes = [
    "肩こり", "頭痛の原因", "水分不足", "姿勢の歪み", "食いしばり",
    "年齢のせいにしない", "根本改善とマッサージの違い", "自己投資の価値",
    "ワーママの体ケア", "睡眠と体の関係"
]
masa_themes = [
    "LINE登録率を上げる方法", "リールの目的", "プロフィール最適化",
    "フォロワーより導線設計", "動画広告の作り方", "コンテンツネタの見つけ方",
    "Google MEO活用", "インスタ集客マインドセット", "売上につながる投稿設計",
    "新規集客の自動化"
]

truth_theme = truth_themes[day % 10]
masa_theme = masa_themes[day % 10]

client = anthropic.Anthropic()


# ─────────────────────────────────────────────
# @truth_body_salon 投稿生成
# ─────────────────────────────────────────────
TRUTH_SYSTEM = """あなたは長岡市（新潟県）在住の整体師のSNS投稿ライターです。

【アカウントの人物像】
- 長岡市で整体院を経営
- もともと体育の教員免許を持つ
- 静岡出身、長岡在住5年以上
- 患者に寄り添うスタイル
- 地元・長岡への愛着が強い

【分析データから判明した高パフォーマンス投稿の特徴】
1. 個人ストーリー系（いいね100〜300超）
   - 整体師自身の日常・気づき・経験談
   - 長岡市・新潟県に関する地元ネタ
   - 「お客様から聞いた話」系エピソード
2. 共感・あるある系
   - 患者目線のリアルな悩みの代弁
   - 家族・夫婦・子育てとからめた体の悩み
3. 短くて刺さるインサイト
   - 「人は変わります」系の変化・希望の言葉
   - 常識を覆す切り口（「揉んでも治らない理由」等）
4. 教育コンテンツ（低エンゲージメントだが認知には有効）
   - 知識提供は簡潔に、結論ファーストで

【NG事項】
- 【テーマ①】のような番号付き構造投稿を全本数に使わない
- 「弊院」「当院」などの堅い表現
- 過度な医療的断言

【投稿フォーマット指定】
20本を以下の比率でミックスしてください：
- 教育・知識系: 5本（簡潔・結論ファースト）
- 個人ストーリー系: 5本（長岡ローカル・整体師の日常・患者エピソード）
- 共感・あるある系: 5本（患者目線・日常の悩みの代弁）
- 気づき・インサイト系: 5本（短くて刺さる・変化・希望）

各投稿は100〜250文字程度。改行を効果的に使い、読みやすくすること。
出力はJSON配列のみ: ["投稿1", "投稿2", ..., "投稿20"]"""


def generate_truth_posts(theme: str) -> list[str]:
    past_examples = PAST_POSTS_TRUTH
    prompt = f"""今日のテーマ「{theme}」で、@truth_body_salon（長岡の整体院）のThreads投稿を20本生成してください。

【実際の過去投稿（文体・トーン・構成の参考）】
{past_examples}

上記の実際の投稿を参考に、同じ人物が書いたと感じられる文体・トーンで生成してください。
テーマは軸にしつつも、全20本が「{theme}」の教育コンテンツにならないよう、
コンテンツミックス（教育5・ストーリー5・共感5・インサイト5）で作成してください。
過去投稿と同じ内容・表現は使わないでください。

JSON配列のみを出力してください。"""

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=4096,
        system=TRUTH_SYSTEM,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    # JSON配列を抽出
    start = text.find("[")
    end = text.rfind("]") + 1
    if start == -1 or end == 0:
        raise ValueError(f"JSON配列が見つかりません: {text[:200]}")
    posts = json.loads(text[start:end])
    if len(posts) < 20:
        raise ValueError(f"投稿数が不足: {len(posts)}本")
    return posts[:20]


# ─────────────────────────────────────────────
# @masahide_takahashi_ 投稿生成
# ─────────────────────────────────────────────
MASA_SYSTEM = """あなたはインスタグラム集客コンサルタントのSNS投稿ライターです。

【アカウントの人物像】
- インスタグラムでの集客・マーケティングを専門とするコンサルタント
- 実践的・具体的なノウハウを発信するスタイル
- 読者はサロン・整体院・個人事業主などの中小事業者

【投稿スタイル】
- 実践的で具体的なアドバイス
- 「なぜそうなるか」の理由も添える
- 読者の「あるある」に寄り添う表現
- 行動を促すCTA（コール・トゥ・アクション）を適度に含む

各投稿は100〜250文字程度。改行を効果的に使い、読みやすくすること。
出力はJSON配列のみ: ["投稿1", "投稿2", ..., "投稿20"]"""


def generate_masa_posts(theme: str) -> list[str]:
    prompt = f"""今日のテーマ「{theme}」で、@masahide_takahashi_（インスタ集客コンサルタント）のThreads投稿を20本生成してください。

テーマに沿いつつも、毎回同じ構成にならないよう以下のバリエーションを混ぜてください：
- 知識・Tips系: 7本
- 失敗・あるある系: 5本
- 事例・ストーリー系: 4本
- 問いかけ・共感系: 4本

JSON配列のみを出力してください。"""

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=4096,
        system=MASA_SYSTEM,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    start = text.find("[")
    end = text.rfind("]") + 1
    if start == -1 or end == 0:
        raise ValueError(f"JSON配列が見つかりません: {text[:200]}")
    posts = json.loads(text[start:end])
    if len(posts) < 20:
        raise ValueError(f"投稿数が不足: {len(posts)}本")
    return posts[:20]


# ─────────────────────────────────────────────
# 実行
# ─────────────────────────────────────────────
print(f"truth theme: {truth_theme}")
print(f"masa theme: {masa_theme}")

try:
    print("Generating @truth_body_salon posts...")
    truth_posts = generate_truth_posts(truth_theme)
    print(f"  → {len(truth_posts)}本生成完了")
except Exception as e:
    print(f"  [ERROR] truth生成失敗: {e}", file=sys.stderr)
    sys.exit(1)

try:
    print("Generating @masahide_takahashi_ posts...")
    masa_posts = generate_masa_posts(masa_theme)
    print(f"  → {len(masa_posts)}本生成完了")
except Exception as e:
    print(f"  [ERROR] masa生成失敗: {e}", file=sys.stderr)
    sys.exit(1)

truth_entry = {
    "account": "@truth_body_salon",
    "theme": truth_theme,
    "date": today.strftime('%Y-%m-%d'),
    "posts": truth_posts
}

masa_entry = {
    "account": "@masahide_takahashi_",
    "theme": masa_theme,
    "date": today.strftime('%Y-%m-%d'),
    "posts": masa_posts
}

with open('/Users/mt112/Desktop/threads-auto-post/log_truth.jsonl', 'a') as f:
    f.write(json.dumps(truth_entry, ensure_ascii=False) + '\n')

with open('/Users/mt112/Desktop/threads-auto-post/log_masa.jsonl', 'a') as f:
    f.write(json.dumps(masa_entry, ensure_ascii=False) + '\n')

print("Done.")
