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
    """feedback.json → weights_*.json の順でパターン重みを読み込む"""
    # feedback.json を優先
    fb_file = BASE / "feedback.json"
    if fb_file.exists():
        try:
            fb = json.loads(fb_file.read_text())
            w = fb.get("pattern_weights", {})
            if w:
                return w
        except Exception:
            pass
    wfile = BASE / f"weights_{acct}.json"
    if wfile.exists():
        try:
            return json.loads(wfile.read_text()).get("pattern_weights", {})
        except Exception:
            pass
    return {}

def _load_ng_words() -> list[str]:
    """feedback.json からNGワードを読み込む"""
    fb_file = BASE / "feedback.json"
    if fb_file.exists():
        try:
            return json.loads(fb_file.read_text()).get("ng_words", [])
        except Exception:
            pass
    return []

def _load_extra_templates(acct: str) -> list[str]:
    """feedback.json から追加テンプレを読み込む"""
    fb_file = BASE / "feedback.json"
    if fb_file.exists():
        try:
            return json.loads(fb_file.read_text()).get("extra_templates", {}).get(acct, [])
        except Exception:
            pass
    return []

def _is_ng(text: str) -> bool:
    """NGワードを含む投稿を除外"""
    ng = _load_ng_words()
    return any(w in text for w in ng)

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
        "desc": "1行フック系（超短・引き）+ コメント解説",
        "hooks": [
            ("{symptom}の本当の原因、知ってますか？",
             "多くの方が筋肉の問題だと思ってますが、\n実は{cause}が根本の原因であることが多いです。\n\nここを変えずに揉むだけでは\n一時的に楽になっても、すぐ戻ります。\n\n原因を知ることが、改善の第一歩。"),
            ("揉んでも治らない理由があります。",
             "{symptom}は筋肉だけの問題じゃない。\n\n{cause}が続く限り\n揉んでも翌日には元通りです。\n\n根本から変えるには\n生活習慣を見直すことが必要です。"),
            ("整体より大事なこと、あります。",
             "施術で体を整えることは大切です。\n\nでもそれ以上に、\n日常の{cause}を変えることの方が\n体に与える影響は大きい。\n\n週1回の施術より\n毎日の小さな習慣。\nここが体を変えるカギです。"),
            ("{symptom}は{cause}の問題です。",
             "薬で痛みを抑えても\n{cause}が続く限り繰り返します。\n\n逆に言えば\n{cause}を見直すだけで\n体は驚くほど変わる。\n\n難しいことじゃないです。\nまず知ることから始めてください。"),
            ("{symptom}が治らない人の共通点。",
             "それは「施術だけ」に頼っていること。\n\n{cause}を変えずに\nプロに任せるだけでは\n根本は変わりません。\n\n施術 × 日常の習慣改善。\nこの両輪で、体は本当に変わります。"),
            ("薬より先にやることがある。",
             "薬は痛みを消すもの。\n原因を消すものじゃない。\n\nまずは{cause}を見直すこと。\n\nここを変えるだけで\n{symptom}が出にくい体になります。\n\n根本から向き合ってほしい。"),
            ("根本改善と対症療法は違います。",
             "対症療法 → その場の痛みを消す\n根本改善 → 痛みが出ない体をつくる\n\nどちらが長い目で見て楽か。\n答えは明らかです。\n\n{cause}から見直すこと。\nそれが根本改善の第一歩。"),
            ("体が楽になると、人生が変わります。",
             "大げさじゃなく、本当にそうです。\n\n{symptom}がなくなると\n{life_scene_n}が変わる。\n\n体が軽いだけで\n気持ちも行動もまったく違う。\n\n体への投資は\n人生への投資です。"),
            ("改善率93.7%のワケ、話します。",
             "特別な技術があるわけじゃない。\n\nやっていることはシンプル。\n{cause}の原因を見つけて\n根本からアプローチすること。\n\n対症療法じゃなく\n原因を変えるから結果が出る。\nそれだけの話です。"),
            ("{symptom}を「年齢のせい」にしている間は変わりません。",
             "年齢は関係ありません。\n\n{cause}を見直した方は\n60代でも70代でも改善しています。\n\n「年だから仕方ない」は\n一番もったいない思い込みです。\n\n体は何歳からでも変わります。"),
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
    # ── 小川式：1行謎かけ → 箇条書き解説 → 1行断言 ──
    "aoi_style": {
        "desc": "小川式（謎かけ→箇条書き→断言）",
        "templates": [
            "{symptom}が改善する人はここが違う。\n\n原因を特定している。\n\n・どこが歪んでいるのか\n・どの習慣が悪いのか\n\nこの2つが分かるだけで\nアプローチが変わります。\n\n原因を知ること。\nこれが改善の第一歩。",
            "リピートされる整体師はここが違う。\n\n施術後に何を伝えるか決まっている。\n\n・なぜ体が変わったのか\n・何を続ければ維持できるか\n\nこの説明があるだけで\n信頼感は大きく変わります。\n\n信頼される理由。\nこれを作ることが大事。",
            "薬で治らない{symptom}はここが違う。\n\n根本の原因が残っている。\n\n・{cause}が続いている\n・体の使い方を変えていない\n\nここを変えないと\n何度通っても元に戻ります。\n\n習慣を変えること。\nこれが根本改善。",
            "{symptom}が長引く人はここが違う。\n\nセルフケアが足りていない。\n\n・施術の間に何もしていない\n・{cause}を放置している\n\nプロの施術と日常習慣の両輪が\n体を変えます。\n\nケアは毎日。\nこれを忘れないでほしい。",
        ],
    },
    # ── 堀式：逆説1行 → 数字実績 → 口語断定 ──
    "hori_style": {
        "desc": "堀式（逆説→数字実績→口語断定）",
        "templates": [
            "{symptom}の壁って、施術の回数じゃないです。\n\n{cause}を変えた人は\n\n週1通院 → 3回で改善\nマッサージだけ → 根本改善\n\nみんな体は同じでした。\n変えたのは「日常習慣」だけです。\n\n正直、知ってるか知らないかの差だけですよ。",
            "{symptom}は、もう諦めなくていい。\n\n長岡の整体院でこんな変化が起きています。\n\n週3の頭痛 → ほぼゼロ\n5年続いた肩こり → 2ヶ月で改善\n慢性疲労 → 朝すっきり起きられるように\n\nやったことは生活習慣の見直しだけ。\n\n根本から変えると、体は想像以上に変わります。",
            "整体より先にやることがあります。\n\n施術実績1万人を超えて気づいたこと。\n\n改善が早い人 → {cause}をすぐ変える\n改善が遅い人 → 施術だけ頼る\n\n施術で緩めるだけでは元に戻る。\n生活習慣を変えた人だけが、本当に楽になります。\n\n正直、これだけの話です。",
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
        "desc": "短いインサイト系 + コメント解説",
        "hooks": [
            ("{symptom}は年齢のせいじゃない。\n体の使い方の問題。",
             "{cause}が続くと\n年齢関係なく体は固まります。\n\n逆に使い方を変えれば\n何歳でも体は楽になる。\n\n「年だから」と諦めないでほしい。"),
            ("揉んでも、また張る。\nそれは原因を取り除いていないから。",
             "{cause}が根本にある場合\n揉むだけでは翌日には戻ります。\n\n大事なのは\nなぜ張るのかを突き止めること。\n\n原因にアプローチすれば\n揉まなくても楽な体が手に入る。"),
            ("{symptom}は「仕方ない」じゃない。\n変えられる。",
             "実際に{cause}を見直した方は\n数週間で変化を実感されています。\n\n「仕方ない」は\n正しい情報を知らないだけ。\n\n知るだけで、選択肢は広がります。"),
            ("{cause}だけで{symptom}はガチガチになる。\n見直してみて。",
             "多くの{symptom}は\n日常の{cause}の積み重ねで起こっています。\n\n逆に言えば\nそこを少し意識するだけで\n体の状態はかなり変わる。\n\n特別なことは何もいらない。"),
            ("姿勢を治しても、姿勢が悪くなる理由を治さないと、\nまた戻る。",
             "姿勢が崩れる原因は\n{cause}にあることがほとんど。\n\n形だけ整えても\n根っこが変わらないと再発する。\n\n原因から治すこと。\nこれが本当の姿勢改善。"),
            ("体が変わると、気持ちも変わる。\n本当にそうです。",
             "{symptom}がなくなった方の多くが\n「気持ちが前向きになった」と言います。\n\n体と心はつながっている。\n\n体を整えることは\nメンタルケアでもあるんです。"),
            ("{symptom}がなくなると、\n{life_scene_n}がこんなに変わるのかと驚く人が多い。",
             "体が楽になると\n行動の質がまるで違う。\n\n{life_scene_n}を我慢していた方が\n思いっきり楽しめるようになる。\n\n体への投資は\n生活の質への投資です。"),
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
    "肩こり", "頭痛", "首こり", "肩の重さ", "慢性的な肩こり", "頭の重さ",
    "眼精疲労", "背中のこり", "腰の重さ", "首の張り", "慢性疲労", "呼吸の浅さ",
    "体のだるさ", "睡眠の浅さ", "顎の疲れ"
]
CAUSES = _truth_mat.get("causes") or [
    "水分不足", "口呼吸", "食いしばり", "姿勢の歪み",
    "スマホの見すぎ", "デスクワーク", "運動不足", "睡眠不足",
    "呼吸の浅さ", "顎の緊張", "ストレス過多", "血流の悪さ",
    "寝姿勢の悪さ", "首の前傾", "深夜のスマホ操作"
]
# 動詞句（〜したい、〜できないに続く形）
LIFE_SCENES_VERB = _truth_mat.get("life_scenes_verb") or [
    "子どもと思いっきり遊び", "休日を元気に過ごし", "仕事に集中し",
    "朝すっきり起き", "家族と笑って過ごし", "趣味を楽しみ",
    "料理や家事をこなし", "子どもの行事に参加し",
    "ぐっすり眠り", "仕事終わりに余裕を持って過ごし",
    "休日に外出し", "好きな音楽を楽しみ"
]
# 名詞句（〜が変わる、〜を楽しむに続く形）
LIFE_SCENES_NOUN = _truth_mat.get("life_scenes_noun") or [
    "子どもとの時間", "休日の過ごし方", "仕事への集中力",
    "朝の時間の使い方", "家族との時間", "趣味の時間",
    "日常の家事", "子どもの行事",
    "夜の睡眠の質", "仕事後のリラックス時間",
    "週末の外出", "自分だけのリフレッシュ時間"
]
HABITS = _truth_mat.get("habits") or [
    "口呼吸になっている", "食いしばりがある", "水分が足りていない",
    "スマホを長時間見ている", "猫背で座っている", "運動習慣がない",
    "睡眠が浅い", "呼吸が浅くなっている",
    "枕の高さが合っていない", "寝る直前までスマホを見ている",
    "デスクワークが1日6時間以上ある", "顎に力が入りやすい",
    "休憩なしで集中し続けている", "水を1日1L以下しか飲んでいない",
    "ストレッチを全くしていない", "同じ姿勢で2時間以上作業している"
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


FOLLOWUP_SEP = "===FOLLOWUP==="

def generate_post(pattern_key: str) -> str:
    p = PATTERNS[pattern_key]

    # フック+フォローアップ形式（hook_one_line, insight等）
    if "hooks" in p:
        hook_text, followup_text = random.choice(p["hooks"])
        return fill(hook_text) + f"\n{FOLLOWUP_SEP}\n" + fill(followup_text)

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
                          "hook_one_line", "gyakusetsu",
                          "aoi_style", "hori_style"):
        return fill(random.choice(p["templates"]))

    return ""


def generate_30_posts() -> list[str]:
    w = _load_weights("truth")
    # 45本生成: 共感9・インサイト7・教育7・ストーリー4・ワーママ3・ランキング6・問いかけ5 + CTA4 = 45本
    defaults = {
        "hook_one_line": 4,
        "aoi_style": 5,    # 小川式：謎かけ→箇条書き→断言
        "hori_style": 4,   # 堀式：逆説→数字実績→口語断定
        "gyakusetsu": 3,
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
        ["aoi_style"] * merged["aoi_style"] +
        ["hori_style"] * merged["hori_style"] +
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
        for _ in range(50):
            post = generate_post(pk)
            key = post[:100]
            if key not in seen and not _is_ng(post):
                seen.add(key)
                posts.append(post)
                break

    # feedback の追加テンプレを先頭に差し込む
    for tmpl in _load_extra_templates("truth"):
        try:
            posts.insert(0, fill(tmpl))
        except Exception:
            pass

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
        ("フォロワーが増えても売上は増えない。",
         "フォロワー3000人で月商20万の人もいれば\nフォロワー200人で月商80万の人もいる。\n\n違いは「導線設計」だけ。\n\nフォロワーを増やす前に\n問い合わせにつながる仕組みを作ること。\nこれが最優先です。"),
        ("良い動画より大事なことがある。",
         "どんなにクオリティの高い動画でも\n導線がなければ集客にはつながりません。\n\n大事なのは\n見た人が「次に何をするか」を設計すること。\n\nコンテンツの質より\n行動の導線です。"),
        ("集客で詰まる人には、共通点があります。",
         "それは「ターゲットが曖昧」なこと。\n\n誰に届けるかが決まっていないから\n投稿の内容もブレる。\n\nまず「誰の、何を、どう変えるか」を\n一文で言えるようにしてみてください。\n\nそこが全ての起点です。"),
        ("{topic}だけやっても集客できない理由。",
         "ツールは手段であって目的じゃない。\n\n{topic}をやる前に\n「誰に」「何を」伝えるかを決めること。\n\nここが曖昧なまま始めると\nどれだけ頑張っても反応は取れません。\n\n設計が先、実行は後。"),
        ("正直に言います。{topic}は手段です。",
         "目的は「集客」であって\n{topic}をやることじゃない。\n\n手段に振り回されると\n本質を見失います。\n\nまずゴールを決めて\nそこから逆算して設計する。\nこれだけで結果は変わります。"),
        ("売れる人と売れない人、違いは1つです。",
         "それは「導線があるかどうか」。\n\n投稿を見て\n興味を持って\n問い合わせにつながる。\n\nこの流れが設計されているかどうか。\n\nコンテンツの質じゃない。\n仕組みの有無が結果を分けます。"),
        ("月商が変わった人は、ここを変えていた。",
         "投稿の質じゃない。\n導線の設計を変えただけ。\n\nプロフィール → 投稿 → CTA → 問い合わせ\n\nこの流れを整えた人から\n結果が出ています。\n\n難しいことは何もない。\n知っているかどうかの差です。"),
        ("努力より設計の問題です。",
         "毎日投稿しても結果が出ない人は\n努力が足りないんじゃない。\n\n設計が足りていないだけ。\n\n誰に届けるか\n何を伝えるか\n次に何をしてもらうか\n\nこの3つを決めるだけで\n同じ努力でも結果がまったく変わります。"),
    ],
    # ── 小川式：謎かけ → 箇条書き → 断言 ──
    "aoi_style": [
        "集客できる人はここが違う。\n\n投稿の目的が明確。\n\n・誰に届けるのか\n・何を変えてほしいのか\n\nこの2つが決まっているだけで\n反応率は大きく変わります。\n\n届く投稿。\nこれを作ることが大事。",
        "フォロワーが増えない人はここが違う。\n\n発信の軸がブレている。\n\n・今日はこれ、明日はあれ\n・ターゲットが毎回違う\n\nこれをやると\n誰にも刺さらない投稿になります。\n\n一貫性。\nこれだけで変わります。",
        "問い合わせが来る人はここが違う。\n\n行動のハードルを下げている。\n\n・次に何をすればいいか明確\n・連絡先が分かりやすい\n・最初の一歩が簡単\n\nこれがないと\n見てもらっても動いてもらえません。\n\n導線設計。\nこれを作ることが大事。",
        "売上が伸びる人はここが違う。\n\n{topic}の目的が明確。\n\n・集客なのか\n・信頼構築なのか\n・販売なのか\n\n目的によってやることが変わります。\n\n目的から逆算すること。\nこれが成果の出る設計。",
    ],
    # ── 堀式：逆説1行 → 数字実績 → 口語断定 ──
    "hori_style": [
        "フォロワー数より大事なものがある。\n\nインスタ集客を支援してきて気づいたこと。\n\nフォロワー200人 → 月商80万\nフォロワー3000人 → 月商20万\n\nどっちが伸びているかは明らかです。\n\n変えたのは「導線設計」だけ。\n\n正直、設計を知ってるか知らないかの差だけですよ。",
        "{topic}の壁は、センスじゃないです。\n\nサポートしたお客様の変化。\n\n月0件 → 月20件の問い合わせ\n反応ゼロ → フォロワー500人増\n\nみんなコンテンツの質は最初から変わってない。\n変えたのは「仕組み」だけです。\n\n正直、やり方を知ってるか知らないかの話です。",
        "SNSを頑張っても結果が出ないのは、\n才能の話じゃないです。\n\nビフォーアフターを見てください。\n\n投稿しても無反応 → 毎日コメントが来る\n半年フォロワー50人 → 3ヶ月で1000人\n\nやったことは設計を変えただけ。\n\n知識より仕組み。\nこれだけです。",
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


def _fill_masa(tmpl: str) -> str:
    tips = random.sample(MASA_TIPS, min(3, len(MASA_TIPS)))
    return (tmpl
            .replace("{topic}", random.choice(MASA_TOPICS))
            .replace("{point}", random.choice(MASA_POINTS))
            .replace("{tip1}", tips[0])
            .replace("{tip2}", tips[1] if len(tips) > 1 else tips[0])
            .replace("{tip3}", tips[2] if len(tips) > 2 else tips[0]))


def generate_masa_post(pattern_key: str) -> str:
    items = MASA_PATTERNS[pattern_key]
    item = random.choice(items)

    # タプル形式 = (フック, フォローアップ)
    if isinstance(item, tuple):
        hook_text, followup_text = item
        return _fill_masa(hook_text) + f"\n{FOLLOWUP_SEP}\n" + _fill_masa(followup_text)

    return _fill_masa(item)


def generate_30_masa_posts() -> list[str]:
    w = _load_weights("masa")
    # 45本生成: フック4・小川式5・堀式4・逆説3・インサイト6・教育6・ストーリー3・LINE誘導5・CTA4・ランキング3・問いかけ2 = 45本
    defaults = {
        "hook_one_line": 4,
        "aoi_style": 5,
        "hori_style": 4,
        "gyakusetsu": 3,
        "insight": 6,
        "education": 6,
        "story": 3,
        "soft_line": 5,
        "cta": 4,
        "ranking": 3,
        "question": 2,
    }
    merged = {k: w.get(k, v) for k, v in defaults.items()}
    plan = (
        ["hook_one_line"] * merged["hook_one_line"] +
        ["aoi_style"] * merged["aoi_style"] +
        ["hori_style"] * merged["hori_style"] +
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
            key = post[:60]
            if key not in seen and not _is_ng(post):
                seen.add(key)
                posts.append(post)
                break

    # feedback の追加テンプレを先頭に差し込む
    for tmpl in _load_extra_templates("masa"):
        try:
            posts.insert(0, generate_masa_post.__wrapped__(tmpl) if hasattr(generate_masa_post, "__wrapped__") else tmpl)
        except Exception:
            posts.insert(0, tmpl)

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
