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


def _load_weights(acct: str) -> dict:
    """weights_*.json から生成比率を読み込む。なければデフォルト値を返す。"""
    wfile = BASE / f"weights_{acct}.json"
    if wfile.exists():
        try:
            return json.loads(wfile.read_text()).get("pattern_weights", {})
        except Exception:
            pass
    return {}

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
    "hook_one_line": {
        "desc": "1行フック系（超短・引き）",
        "templates": [
            "{symptom}の本当の原因、知ってますか？",
            "揉んでも治らない理由があります。",
            "整体より大事なこと、あります。",
            "{symptom}は{cause}の問題です。",
            "{symptom}が治らない人の共通点。",
            "薬より先にやることがある。",
            "根本改善と対症療法は違います。",
            "体が楽になると、人生が変わります。",
            "改善率93.7%のワケ、話します。",
            "{symptom}を「年齢のせい」にしている間は変わりません。",
        ],
    },
    "gyakusetsu": {
        "desc": "逆説・反骨系（意外性フック）",
        "templates": [
            "{symptom}は、{symptom}じゃないところが原因です。\n\n{cause}が乱れると\n全身に影響が出ます。\n\nどこを揉んでも変わらないのは、そのため。",
            "整体に通っても変わらない人には\n共通点があります。\n\n{cause}を変えていない。\n\n施術で緩めても\n同じ生活を続けると元に戻る。\n根本は生活習慣にあります。",
            "「高い整体より薬の方が早い」\nそれは本当でしょうか。\n\n薬は痛みを消す。\n整体は原因を変える。\n\n目的が違います。",
            "マッサージは気持ちいい。\nでも{symptom}は変わらない。\n\n筋肉を揉んでも\n{cause}は変わらないから。\n\n必要なのは根本へのアプローチ。",
            "技術より大事なことがあります。\n\n日常の{cause}を変えること。\n\n週1の施術より\n毎日の習慣が体を変えます。",
        ],
    },
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
    "ranking": {
        "desc": "ランキング・TOP形式（内山式）",
        "templates": [
            "{symptom}を悪化させる習慣 TOP3\n\n1位：{habit1}\n2位：{habit2}\n3位：{habit3}\n\n心当たりある人、多いはず。",
            "整体師が見てきた\n{symptom}が治らない人の共通点3つ\n\n① {habit1}\n② {habit2}\n③ {habit3}\n\nこのどれかに当てはまっていませんか？",
            "体が楽になった人がやめたこと\n\n▶ {habit1}\n▶ {habit2}\n▶ {habit3}\n\nたったこれだけで変わる人が多いです。",
            "{symptom}に効果があった習慣 3選\n\n1）{cause}を意識する\n2）{cause}を見直す\n3）{cause}から改善する\n\n全部無料でできます。",
        ],
    },
    "question": {
        "desc": "問いかけ・コメント誘導系（内山式）",
        "templates": [
            "正直に聞きます。\n\n{symptom}、今どのくらいひどいですか？\n\nA：毎日ある\nB：週に数回\nC：たまにある\n\nコメントで教えてください。",
            "{symptom}の原因、知ってますか？\n\nほとんどの人が答えられないんですよ。\n\n実は{cause}が一番の原因です。\n\n知らなかった人、「知らなかった」ってコメントしてみて。",
            "これ、あなたの体に当てはまりますか？\n\n□ {habit1}\n□ {habit2}\n□ {habit3}\n\n2つ以上当てはまったら、\n今すぐ{symptom}のケアを始めてください。",
            "質問です。\n\n{symptom}が続いているのに、\nなぜ放置してしまうんだと思いますか？\n\n「お金」「時間」「めんどくさい」\n……どれですか？",
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


URL_KATAKORI = "https://beauty.hotpepper.jp/CSP/kr/reserve/?storeId=H000596246&couponId=CP00000008152494&add=0"
URL_ZUTSUU   = "https://beauty.hotpepper.jp/CSP/kr/reserve/?storeId=H000596246&couponId=CP00000008707618&add=0"

SYMPTOMS_KATAKORI = ["肩こり", "肩の重さ", "首こり", "慢性的な肩こり", "肩の張り"]
SYMPTOMS_ZUTSUU   = ["頭痛", "頭の重さ", "慢性頭痛", "頭がズキズキする", "頭が重い"]

CTA_KATAKORI_TEMPLATES = [
    "肩こりで毎日がしんどいあなたへ。\n\n{cause}を変えるだけで、\n体が驚くほど楽になります。\n\n▶ 肩こり専門の施術はこちら\n{url}",
    "「肩こり、もう諦めてた」\n\nそんな方こそ来てほしい。\n\n{cause}が原因のことが多く、\n根本から変えられます。\n\n▶ 予約はこちら\n{url}",
    "肩こりを「年齢のせい」にする前に、\n一度試してみてください。\n\n改善率93.7%、施術実績1万人。\n根本から整えます。\n\n▶ {url}",
    "慢性的な肩こりで\n{life_scene_n}が思い切り楽しめていない方へ。\n\n体を整えると、日常が変わります。\n\n▶ 肩こり改善の予約はこちら\n{url}",
]

CTA_ZUTSUU_TEMPLATES = [
    "週に何度も頭痛薬を飲んでいませんか？\n\n薬は一時しのぎ。\n{cause}を根本から変えると、\n頭痛の頻度が下がります。\n\n▶ 頭痛専門の施術はこちら\n{url}",
    "「また頭痛だ」が口癖になっているなら、\n一度体を診てもらってください。\n\n原因を探ると、意外なところにあります。\n\n▶ 予約はこちら\n{url}",
    "頭痛を「仕方ない」と思っている方へ。\n\n{cause}を整えると、\n頭痛が出にくい体になります。\n\n▶ {url}",
    "頭痛があると、\n{life_scene_n}が半減しますよね。\n\n根本から改善した方が\n長い目でみてずっと楽です。\n\n▶ 頭痛改善の予約はこちら\n{url}",
]

def fill(template: str, symptom: str = None) -> str:
    s = symptom or random.choice(SYMPTOMS)
    habits = random.sample(HABITS, min(3, len(HABITS)))
    causes = random.sample(CAUSES, min(3, len(CAUSES)))
    return (template
            .replace("{symptom}", s)
            .replace("{cause}", causes[0])
            .replace("{life_scene_v}", random.choice(LIFE_SCENES_VERB))
            .replace("{life_scene_n}", random.choice(LIFE_SCENES_NOUN))
            .replace("{habit1}", habits[0])
            .replace("{habit2}", habits[1])
            .replace("{habit3}", habits[2]))

def generate_cta_post(target: str) -> str:
    """target: 'katakori' or 'zutsuu'"""
    if target == "katakori":
        tmpl = random.choice(CTA_KATAKORI_TEMPLATES)
        symptom = random.choice(SYMPTOMS_KATAKORI)
        return fill(tmpl.replace("{url}", URL_KATAKORI), symptom=symptom)
    else:
        tmpl = random.choice(CTA_ZUTSUU_TEMPLATES)
        symptom = random.choice(SYMPTOMS_ZUTSUU)
        return fill(tmpl.replace("{url}", URL_ZUTSUU), symptom=symptom)


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

    elif pattern_key in ("story", "workmom", "ranking", "question",
                          "hook_one_line", "gyakusetsu"):
        return fill(random.choice(p["templates"]))

    return ""


def generate_30_posts() -> list[str]:
    w = _load_weights("truth")
    # 45本生成: 共感9・インサイト7・教育7・ストーリー4・ワーママ3・ランキング6・問いかけ5 + CTA4 = 45本
    defaults = {
        "hook_one_line": 5,   # 1行フック（新）
        "gyakusetsu": 4,       # 逆説・反骨（新）
        "quote_empathy": 6,
        "insight": 5,
        "education": 5,
        "story": 3,
        "workmom": 2,
        "ranking": 4,
        "question": 2,
    }
    merged = {k: w.get(k, v) for k, v in defaults.items()}
    plan = (
        ["hook_one_line"] * merged["hook_one_line"] +
        ["gyakusetsu"] * merged["gyakusetsu"] +
        ["quote_empathy"] * merged["quote_empathy"] +
        ["insight"] * merged["insight"] +
        ["education"] * merged["education"] +
        ["story"] * merged["story"] +
        ["workmom"] * merged["workmom"] +
        ["ranking"] * merged["ranking"] +
        ["question"] * merged["question"]
    )
    random.shuffle(plan)

    posts = []
    seen = set()
    for pk in plan:
        for _ in range(30):
            post = generate_post(pk)
            key = post[:60]  # 先頭60文字で重複判定（完全一致より緩く）
            if key not in seen:
                seen.add(key)
                posts.append(post)
                break
        else:
            # 30回試みても重複回避できない場合は強制追加
            posts.append(generate_post(pk))

    # 肩こりCTA 2本・頭痛CTA 2本をランダムな位置に差し込む
    cta_posts = (
        [generate_cta_post("katakori") for _ in range(2)] +
        [generate_cta_post("zutsuu")   for _ in range(2)]
    )
    for cta in cta_posts:
        pos = random.randint(0, len(posts))
        posts.insert(pos, cta)

    return posts[:45]


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
    "hook_one_line": [
        "フォロワーが増えても売上は増えない。",
        "良い動画より大事なことがある。",
        "集客で詰まる人には、共通点があります。",
        "{topic}だけやっても集客できない理由。",
        "正直に言います。{topic}は手段です。",
        "売れる人と売れない人、違いは1つです。",
        "月商が変わった人は、ここを変えていた。",
        "努力より設計の問題です。",
    ],
    "gyakusetsu": [
        "フォロワーを増やすより先にやることがある。\n\n{point}\n\n順番を間違えると、どれだけ発信しても集客できません。",
        "{topic}を頑張っているのに結果が出ない。\n\nその理由はシンプルです。\n\n{point}\n\nツールの問題じゃなく、設計の問題。",
        "コンサルに高いお金を払う前に、\nまず自分でやってみてください。\n\n{point}\n\n知識より実践の数が成果を決めます。",
        "SNSより先に整えるべきものがあります。\n\n{point}\n\nこれがないまま発信しても、反応は取れません。",
    ],
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
    "ranking": [
        "集客できない店舗の共通点 TOP3\n\n1位：{tip1}\n2位：{tip2}\n3位：{tip3}\n\n正直、どれか当てはまってませんか？",
        "{topic}で成果が出ない人がやっていること3選\n\n① {tip1}\n② {tip2}\n③ {tip3}\n\nこれを直すだけで変わります。",
        "フォロワー1万人超えの人が共通してやっていること\n\n▶ {tip1}\n▶ {tip2}\n▶ {tip3}\n\n難しいことは何もない。",
        "集客に成功している店舗が最初にやること 3つ\n\n① {tip1}\n② {tip2}\n③ {tip3}\n\n順番が大事です。",
    ],
    "question": [
        "正直に聞きます。\n\n{topic}、今どれくらい本気でやってますか？\n\nA：毎日やってる\nB：週数回\nC：なんとなく\n\nコメントで教えてください。",
        "あなたの{topic}、何が原因で伸びてないと思いますか？\n\n「コンテンツ」「継続」「設計」\n\nどれだと思う？\nコメントで教えてほしいです。",
        "これ、当てはまりますか？\n\n□ {tip1}できていない\n□ {tip2}が曖昧\n□ {tip3}を後回しにしてる\n\n1つでも当てはまったら、\n今すぐ{topic}を見直してください。",
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
    tips = random.sample(MASA_TIPS, min(3, len(MASA_TIPS)))
    return (tmpl
            .replace("{topic}", random.choice(MASA_TOPICS))
            .replace("{point}", random.choice(MASA_POINTS))
            .replace("{tip1}", tips[0])
            .replace("{tip2}", tips[1] if len(tips) > 1 else tips[0])
            .replace("{tip3}", tips[2] if len(tips) > 2 else tips[0]))


def generate_30_masa_posts() -> list[str]:
    w = _load_weights("masa")
    # 45本生成: フック5・逆説4・インサイト7・教育7・ストーリー4・LINE誘導6・CTA4・ランキング5・問いかけ3 = 45本
    defaults = {
        "hook_one_line": 5,
        "gyakusetsu": 4,
        "insight": 7,
        "education": 7,
        "story": 4,
        "soft_line": 6,
        "cta": 4,
        "ranking": 5,
        "question": 3,
    }
    merged = {k: w.get(k, v) for k, v in defaults.items()}
    plan = (
        ["hook_one_line"] * merged["hook_one_line"] +
        ["gyakusetsu"] * merged["gyakusetsu"] +
        ["insight"] * merged["insight"] +
        ["education"] * merged["education"] +
        ["story"] * merged["story"] +
        ["soft_line"] * merged["soft_line"] +
        ["cta"] * merged["cta"] +
        ["ranking"] * merged["ranking"] +
        ["question"] * merged["question"]
    )
    random.shuffle(plan)

    posts = []
    seen = set()
    for pk in plan:
        for _ in range(30):
            post = generate_masa_post(pk)
            key = post[:60]  # 先頭60文字で重複判定
            if key not in seen:
                seen.add(key)
                posts.append(post)
                break
        else:
            posts.append(generate_masa_post(pk))

    return posts[:45]


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
