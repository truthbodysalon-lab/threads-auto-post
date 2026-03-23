#!/usr/bin/env python3
"""
過去投稿をベースに新しい投稿を生成する（API不要）
past_posts.json から構造・文体を学習してリミックス生成。
"""

import json
import random
import re
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).parent
PAST_FILE = BASE / "past_posts.json"
LOG_FILE = BASE / "log_truth.jsonl"

TODAY = date.today().strftime("%Y-%m-%d")

# ── myfilesから素材を読み込む（失敗時はデフォルト使用）──────────
try:
    from myfiles_loader import load_truth_materials, load_masa_materials
    _truth_mat = load_truth_materials()
    _masa_mat = load_masa_materials()
    _MYFILES_LOADED = True
except Exception:
    _MYFILES_LOADED = False
    _truth_mat = {}
    _masa_mat = {}

# ── 投稿パターン定義 ──────────────────────────────
# 各パターンは (冒頭テンプレ, 中間テンプレ, 締めテンプレ) のリスト
PATTERNS = {
    "quote_empathy": {
        "desc": "共感引用系（「〜」で始まる）",
        "openers": [
            "「{symptom}、もう仕方ない」\nって諦めてませんか？",
            "「{symptom}くらい」\nって後回しにしてませんか？",
            "「また{symptom}が...」\nそう思った瞬間がケアのサインです。",
            "「{symptom}で{life_scene_n}が楽しめない」\nそんな声をよく聞きます。",
            "「忙しいし{symptom}は我慢するしかない」\nそう思っていませんか？",
        ],
        "middles": [
            "\n{symptom}は{cause}が原因のことが多いです。\n揉んでも治らないのは、そこを変えていないから。",
            "\n{cause}が続くと、体はどんどん硬くなります。\n気づいたときが、変えるタイミングです。",
            "\n実は{cause}を見直すだけで、\nかなり楽になるケースが多いです。",
        ],
        "closers": [
            "\n体を後回しにしないでください。",
            "\n自分の体への投資、惜しまないでほしい。",
            "\n根本から変えると、毎日が変わります。",
            "\n{life_scene_n}を思いっきり楽しめる体に。",
        ],
    },
    "insight": {
        "desc": "短いインサイト系",
        "templates": [
            "{symptom}は年齢のせいじゃない。\n体の使い方の問題。",
            "揉んでも、また張る。\nそれは原因を取り除いていないから。",
            "{symptom}は「仕方ない」じゃない。\n変えられる。",
            "{cause}だけで{symptom}はガチガチになる。\n見直してみて。",
            "姿勢を治しても、姿勢が悪くなる理由を治さないと、\nまた戻る。",
            "体が変わると、気持ちも変わる。\n本当にそうです。",
            "{symptom}がなくなると、\n{life_scene_n}がこんなに変わるのかと驚く人が多い。",
        ],
    },
    "education": {
        "desc": "知識・教育系",
        "openers": [
            "{symptom}がひどい人に共通していること",
            "なぜデスクワークで{symptom}になるのか？",
            "{symptom}の本当の原因は、",
            "{symptom}を悪化させる習慣があります。",
        ],
        "bodies": [
            "\n\n▶ {habit1}\n▶ {habit2}\n▶ {habit3}\n\n全部、生活習慣で変えられます。",
            "\n\n◎{cause}が続くと、筋肉が常に緊張状態に\n◎それが{symptom}として現れる\n\n根本から変えると、体が驚くほど楽になります。",
            "\n\n{cause}が主な原因です。\n施術だけでなく、日常の習慣を変えることが大切。",
        ],
    },
    "story": {
        "desc": "ストーリー・日常系",
        "templates": [
            "{life_scene_n}を楽しみたいのに、\n{symptom}があって気力が出ない。\n\nそんな声をよく聞きます。\n\n体が楽になると、\n{life_scene_n}への向き合い方が変わります。",
            "先日、お客様から\n「{symptom}がなくなって、{life_scene_n}が変わった」\nと言ってもらいました。\n\n体を整えることは、\n生活の質を上げることだと改めて感じました。",
            "長岡の季節の変わり目は体が縮こまりやすい。\n\n筋肉が緊張し、\n{symptom}が悪化しやすい時期です。\n\n今こそ根本から整えるチャンスです。",
            "整体師になって気づいたこと。\n\n{symptom}で悩む人のほとんどが、\n{cause}を見直していない。\n\n小さな習慣が、体を大きく変えます。",
        ],
    },
    "workmom": {
        "desc": "ワーママ共感系",
        "templates": [
            "家事も育児も仕事も頑張ってるのに、\n{symptom}で余裕がなくなる。\n\nそれ、頑張りすぎのサインかもしれません。\n\n体を整えることは、\n自分だけでなく家族のためにもなります。",
            "{life_scene_n}を大切にしたいのに、\n{symptom}でしんどい。\n\nそんなお母さんに来てほしい。\n\n体が変わると、\n子どもへの接し方まで変わります。",
            "「自分にお金をかけていいのかな」\nって思うワーママ、多いです。\n\nでも聞いてください。\n\n{symptom}があると、仕事も家事も{life_scene_n}も\nすべての効率が下がります。\n\n自分の体への投資は、家族への投資でもある。",
        ],
    },
}

# ── 素材（myfilesロード済みならそちらを優先）────────────────────
SYMPTOMS = _truth_mat.get("symptoms") or [
    "肩こり", "頭痛", "首こり", "肩の重さ", "慢性的な肩こり", "頭の重さ"
]
CAUSES = _truth_mat.get("causes") or [
    "水分不足", "口呼吸", "食いしばり", "姿勢の歪み",
    "スマホの見すぎ", "デスクワーク", "運動不足", "睡眠不足"
]
# 動詞句（〜したい、〜できないに続く形）
LIFE_SCENES_VERB = _truth_mat.get("life_scenes_verb") or [
    "子どもと思いっきり遊び", "休日を元気に過ごし", "仕事に集中し",
    "朝すっきり起き", "家族と笑って過ごし", "趣味を楽しみ",
    "料理や家事をこなし", "子どもの行事に参加し"
]
# 名詞句（〜が変わる、〜を楽しむに続く形）
LIFE_SCENES_NOUN = _truth_mat.get("life_scenes_noun") or [
    "子どもとの時間", "休日の過ごし方", "仕事への集中力",
    "朝の時間の使い方", "家族との時間", "趣味の時間",
    "日常の家事", "子どもの行事"
]
HABITS = _truth_mat.get("habits") or [
    "口呼吸になっている", "食いしばりがある", "水分が足りていない",
    "スマホを長時間見ている", "猫背で座っている", "運動習慣がない",
    "睡眠が浅い", "呼吸が浅くなっている"
]


def fill(template: str) -> str:
    return (template
            .replace("{symptom}", random.choice(SYMPTOMS))
            .replace("{cause}", random.choice(CAUSES))
            .replace("{life_scene_v}", random.choice(LIFE_SCENES_VERB))
            .replace("{life_scene_n}", random.choice(LIFE_SCENES_NOUN))
            .replace("{habit1}", random.choice(HABITS))
            .replace("{habit2}", random.choice(HABITS))
            .replace("{habit3}", random.choice(HABITS)))


def generate_post(pattern_key: str) -> str:
    p = PATTERNS[pattern_key]

    if pattern_key == "quote_empathy":
        opener = fill(random.choice(p["openers"]))
        middle = fill(random.choice(p["middles"]))
        closer = fill(random.choice(p["closers"]))
        return opener + middle + closer

    elif pattern_key == "insight":
        return fill(random.choice(p["templates"]))

    elif pattern_key == "education":
        opener = fill(random.choice(p["openers"]))
        body = fill(random.choice(p["bodies"]))
        return opener + body

    elif pattern_key in ("story", "workmom"):
        return fill(random.choice(p["templates"]))

    return ""


def generate_30_posts() -> list[str]:
    # 分布: 共感8・インサイト8・教育6・ストーリー5・ワーママ3
    plan = (
        ["quote_empathy"] * 8 +
        ["insight"] * 8 +
        ["education"] * 6 +
        ["story"] * 5 +
        ["workmom"] * 3
    )
    random.shuffle(plan)

    posts = []
    seen = set()
    for pk in plan:
        for _ in range(10):  # 重複回避リトライ
            post = generate_post(pk)
            if post not in seen:
                seen.add(post)
                posts.append(post)
                break

    return posts


# ══════════════════════════════════════════════
# @masahide_takahashi_ 投稿生成
# ══════════════════════════════════════════════

LOG_FILE_MASA = BASE / "log_masa.jsonl"

MASA_TOPICS = _masa_mat.get("topics") or [
    "動画集客", "Instagram運用", "LINE集客", "広告費の考え方",
    "プロフィール設計", "コンテンツ設計", "動画広告", "MEO対策",
    "フォロワーより導線", "売上につながる投稿"
]

MASA_PATTERNS = {
    "insight": [
        "「{topic}」を難しく考えすぎていませんか？\n\nやることはシンプルです。\n{point}\n\nまずは一歩踏み出すことが大切。",
        "{topic}で結果が出ない人の共通点。\n\n{point}\n\nここを変えるだけで、大きく変わります。",
        "{topic}の本質は、\n\n{point}\n\nこれだけです。難しく考えなくていい。",
        "「{topic}をやっているのに集客できない」\n\nその原因のほとんどは、\n{point}\n\nここを見直してみてください。",
    ],
    "education": [
        "{topic}で大切な3つのこと。\n\n① {tip1}\n② {tip2}\n③ {tip3}\n\nこの順番で整えると、結果が出やすくなります。",
        "売れる{topic}と売れない{topic}の違い。\n\nそれは「{point}」があるかどうか。\n人は感情で動きます。ロジックより想いを伝えてください。",
        "{topic}を始める前に確認してほしいこと。\n\n{point}\n\nこれがないまま始めても、遠回りになるだけです。",
    ],
    "story": [
        "先日、お客様から\n「{topic}を見直したら問い合わせが増えた」\nと連絡をもらいました。\n\n{point}\n\n小さな改善が、大きな結果につながります。",
        "正直に言います。\n\n{topic}は魔法じゃない。\n{point}\n\n地道にやり続けた人だけが、結果を手にします。",
        "集客に悩んでいる方へ。\n\n{point}\n\n{topic}は手段です。ゴールから逆算して設計してください。",
    ],
    "cta": [
        "{topic}で悩んでいる方、\nLINEで気軽に相談してください。\n\n{point}\n\n👉 https://lin.ee/8PsIHHC",
        "無料でインスタ集客の相談に乗っています。\n\n{point}\n\nLINEからどうぞ 👉 https://lin.ee/8PsIHHC",
        "「{topic}、何から始めればいい？」\nそんな方はLINEに登録してみてください。\n\n一緒に整理しましょう。\n👉 https://lin.ee/8PsIHHC",
        "集客の仕組みを作りたい方へ。\n\n{point}\n\n詳しくはLINEで話しましょう。\n👉 https://lin.ee/8PsIHHC",
    ],
    # LINE誘導をさりげなく含む通常投稿（ハードCTAなし）
    "soft_line": [
        "{topic}について深掘りした内容を\nLINEで配信しています。\n\n{point}\n\n気になる方はプロフィールから。",
        "この投稿が参考になったら、\nLINEでもっと詳しい話を読んでみてください。\n\n{point}",
        "{topic}の実践ノウハウは\nLINEでこっそり共有しています。\n\n{point}",
    ],
}

# テーブル行・長すぎる行を除外してクリーンなinsightsだけ使う
_raw_insights = _masa_mat.get("insights") or []
import re as _re
_clean_insights = [
    s for s in _raw_insights
    if not _re.search(r'\s{2,}|[←→]|ステップ|チェックポイント|収益性', s)
    and len(s) <= 35
]

MASA_POINTS = _clean_insights or [
    "ターゲットを絞ることが最優先です",
    "まず「誰に」「何を」伝えるかを決めること",
    "完璧を目指さず、まず公開することが大事",
    "最初の3秒で興味を引けるかがすべて",
    "フォロワー数より問い合わせ導線の設計が重要",
    "投稿の目的を明確にすること",
    "お客様の声・実績を積極的に発信する",
    "継続こそが最大の差別化になる",
    "行動喚起（CTA）を必ず入れること",
    "広告は「コスト」ではなく「投資」として考える",
]

MASA_TIPS = _masa_mat.get("tips") or [
    "ターゲットを明確にする",
    "一貫したメッセージを発信する",
    "行動喚起を入れる",
    "お客様の実績・声を見せる",
    "継続して発信する",
    "プロフィールを最適化する",
    "ストーリーで感情に訴える",
    "数値で結果を示す",
]


def generate_masa_post(pattern_key: str) -> str:
    templates = MASA_PATTERNS[pattern_key]
    tmpl = random.choice(templates)
    return (tmpl
            .replace("{topic}", random.choice(MASA_TOPICS))
            .replace("{point}", random.choice(MASA_POINTS))
            .replace("{tip1}", random.choice(MASA_TIPS))
            .replace("{tip2}", random.choice(MASA_TIPS))
            .replace("{tip3}", random.choice(MASA_TIPS)))


def generate_30_masa_posts() -> list[str]:
    # 配分: 価値提供20本 / さりげないLINE誘導5本 / 直接CTA3本 / ストーリー2本
    plan = (
        ["insight"] * 10 +
        ["education"] * 8 +
        ["story"] * 4 +
        ["soft_line"] * 5 +  # さりげないLINE言及（URL不記載）
        ["cta"] * 3          # 直接LINE URL付きCTA（30本中3本のみ）
    )
    random.shuffle(plan)

    posts = []
    seen = set()
    for pk in plan:
        for _ in range(10):
            post = generate_masa_post(pk)
            if post not in seen:
                seen.add(post)
                posts.append(post)
                break

    return posts


# ══════════════════════════════════════════════
# メイン
# ══════════════════════════════════════════════

def main():
    account = sys.argv[1] if len(sys.argv) > 1 else "truth"

    if "masa" in account.lower():
        posts = generate_30_masa_posts()
        entry = {"account": "@masahide_takahashi_", "theme": "リミックス生成", "date": TODAY, "posts": posts}
        with open(LOG_FILE_MASA, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"✓ {len(posts)}本生成 → log_masa.jsonl に保存")
    else:
        posts = generate_30_posts()
        entry = {"account": "@truth_body_salon", "theme": "リミックス生成", "date": TODAY, "posts": posts}
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"✓ {len(posts)}本生成 → log_truth.jsonl に保存")

    for i, p in enumerate(posts, 1):
        print(f"\n--- {i} ---")
        print(p)


if __name__ == "__main__":
    main()
