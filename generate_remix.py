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
LOG_FILE_NAGAOKA = BASE / "log_nagaoka.jsonl"

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
    # ── PASONA法則：Problem→Affinity→Solution→Offer→NarrowDown→Action ──
    "pasona": {
        "desc": "PASONA法則（小川教材コア）",
        "templates": [
            # P: 痛みを言い当てる → A: 共感 → S: 解決策 → O: 提案 → N: 今すぐ感 → A: 行動
            "「{symptom}、もう仕方ない」\nそう諦めていませんか？\n\n気持ちすごく分かります。\n薬を飲んでも、マッサージに行っても\nまた繰り返す。疲れますよね。\n\n実は{symptom}の多くは\n{cause}が根本原因です。\nそこを変えると、体は劇的に変わります。\n\n施術実績1万人・改善率93.7%の\nインナー整体で根本から整えます。\n\n今なら初回カウンセリング無料。\n体を変えたいと思ったその気持ち、\n今すぐ行動に変えてください。",

            "毎日{symptom}があると\n仕事も育児も本当に辛いですよね。\n\n私もそういう患者さんをたくさん見てきました。\n「もう歳だから」と諦めてしまう方も多い。\n\nでも違います。\n{symptom}は{cause}の問題。\n年齢とは関係ありません。\n\n当院では原因を特定して\n根本から改善するアプローチをとっています。\n改善率93.7%、実績1万人。\n\n{life_scene_n}を取り戻したい方、\n一度ご相談ください。",

            "「{symptom}で{life_scene_n}が楽しめない」\nそんな思いをずっと抱えていませんか？\n\n同じ悩みを抱えていた方が\n当院に来られています。\n\n{cause}を整えることで\n多くの方が3回以内に変化を実感されています。\n\n週3の頭痛 → ほぼゼロ\n5年続いた肩こり → 2ヶ月で改善\n\n今だけ初回特別価格でご提供しています。\n体を後回しにするのは、今日でおしまいにしましょう。",
        ],
    },
    # ── PRBREP法：結論→理由→根拠→反論への反論→事例→結論 ──
    "prbrep": {
        "desc": "PRBREP法（根拠+事例で説得力UP）",
        "templates": [
            # P結論 → R理由 → B根拠（数字） → R反論への反論 → E事例 → P結論
            "{symptom}は、薬では治りません。\n\nなぜなら薬は「痛みを消す」だけで\n「原因を取り除く」ものではないから。\n\n実際、厚生労働省のデータでも\n鎮痛剤の常用は症状を慢性化させるリスクがあると示されています。\n\n「でも整体って高いし効果が出るか分からない」\nそう思う気持ちも分かります。\nでも薬代が月3,000円×12ヶ月なら3万6千円。\n根本改善にかける費用より高くなることも多い。\n\n当院で5年続いた肩こりが2ヶ月で改善した方がいます。\n\n{symptom}は変えられます。\n根本から整えると、薬も必要なくなります。",

            "{cause}が{symptom}の主な原因です。\n\nなぜなら{cause}が続くと\n筋肉が常に緊張状態になるから。\n\nこれは整体師として1万人以上を診て\n繰り返し確認してきた事実です。\n\n「でも{cause}って気をつけても直らない」\nはい、習慣を変えるのは簡単ではない。\nでもコツを知ると驚くほど短期間で変わります。\n\n実際に{cause}の習慣を変えるだけで\n3週間で{symptom}がほぼ消えた方もいます。\n\n小さな習慣の変化が\n体を根本から変えていきます。",

            "{symptom}を「歳のせい」にするのは\n早すぎます。\n\nなぜなら{symptom}の本当の原因は\n年齢ではなく「体の使い方」にあるから。\n\n30代の方でも生活習慣が悪ければ\n症状は出ます。逆に50代でも\n体の使い方を変えた方は劇的に改善します。\n\n「もう歳だから仕方ない」\nその考えが一番の敵です。\n\n先日、52歳のお客様が\n30年続いた首こりから解放されました。\n\n年齢は関係ない。\n今からでも、必ず変わります。",
        ],
    },
    # ── 等価交換型：情報提供→信頼獲得（煽りなし） ──
    "touka_koukan": {
        "desc": "等価交換（情報提供→信頼）",
        "templates": [
            "今日は{symptom}を楽にするための\n5つのセルフケアをお伝えします。\n\n① 寝る前に首を左右にゆっくり倒す（各30秒）\n② 水を1日1.5L以上飲む\n③ スマホを見る時間を1時間ごとに5分休憩\n④ 肩を前後に大きく回す（各10回）\n⑤ 深呼吸を1日3回意識する\n\nどれも今日からできることばかり。\nまずは1つだけ試してみてください。",

            "{symptom}が悪化しやすい習慣を知っていますか？\n\n① 長時間同じ姿勢でいる\n② 口で呼吸している\n③ 食いしばっている（特に睡眠中）\n④ 水分が足りていない\n⑤ 枕の高さが合っていない\n\n心当たりはありましたか？\n\nこの中の1つを変えるだけで\n体の変化を感じる方がほとんどです。\n\n情報が体を変える第一歩。\n気づいた今がチャンスです。",

            "なぜ「マッサージに通っても{symptom}が治らないのか」\n仕組みをお話しします。\n\nマッサージは筋肉の緊張をほぐすもの。\nでも緊張の「原因」は残ったまま。\n\n原因は大きく3つ：\n・{cause}による姿勢の歪み\n・深部筋の硬さ（表面だけほぐしても届かない）\n・自律神経の乱れ\n\nこの3つにアプローチしないと\n何度通っても元に戻ります。\n\n「なぜ治らないのか」を知ることが\n本当の改善への第一歩です。",
        ],
    },
    # ── 放置リスク系：問題提起→放置の末路→解決CTA（本文3〜4行＋コメント分割） ──
    "hochi_risk": {
        "desc": "放置リスク（問題提起→放置の末路→改善→CTA）",
        "templates": [
            # 肩こり放置 → 自律神経・うつ・睡眠障害
            "{nagaoka}\n\n「肩こりくらい」と放置していたら\n眠れなくなった。\n\n実は肩こりと自律神経は直結しています。\n[COMMENT]\n肩まわりの筋肉が緊張し続けると\n交感神経が過剰に働き始めます。\n\n・眠りが浅くなる\n・気分が落ち込みやすくなる\n・疲れが抜けない\n\n「ただの肩こり」じゃないんです。\n[COMMENT]\n肩こりの根本原因は{cause}にあります。\n\nマッサージで一時的に楽になっても\n原因を変えなければ何度でも戻ります。\n\n{cause}から整えると\n自律神経も安定してきます。\n[COMMENT]\nプロフィールから予約できます。\n「肩こり・自律神経が気になる」とお気軽にご相談ください。",

            # 頭痛放置 → 慢性化・薬依存・集中力低下
            "{nagaoka}\n\n「また頭痛薬飲めばいいか」\nそれ、続けると危険です。\n[COMMENT]\n鎮痛剤を週3回以上飲み続けると\n「薬物乱用頭痛」になるリスクがあります。\n\n薬が効かなくなる→量が増える→悪化する\n\nこのスパイラルにはまっている方が\n実はとても多い。\n[COMMENT]\n頭痛の原因の多くは{cause}です。\n\n・水分が足りていない\n・頸椎（首の骨）がずれている\n・{habit1}\n\nここを整えると\n頭痛薬がいらない日が増えてきます。\n[COMMENT]\n頭痛でお悩みの方はプロフィールから。\n根本から整える施術で、薬に頼らない体へ。",

            # 猫背放置 → 内臓圧迫・呼吸が浅い・老け見え
            "{nagaoka}\n\n猫背の方が増えています。\n「姿勢が悪いだけ」じゃないんです。\n放置すると体の中が変わります。\n[COMMENT]\n猫背が続くと：\n\n・肺が圧迫されて呼吸が浅くなる\n・内臓が下がって消化が悪くなる\n・顔が前に出て老け顔に見える\n\n{symptom}が出やすくなるのもそのせいです。\n[COMMENT]\n猫背の根本原因は{cause}にあります。\n\n姿勢を「直そう」とするより\n{cause}を整えると\n自然に背筋が伸びてきます。\n\n体の使い方を変えることが大切。\n[COMMENT]\n姿勢・猫背が気になる方、\nプロフィールからご相談ください。",

            # 眼精疲労 → めまい・吐き気スパイラル
            "{nagaoka}\n\n目の疲れを訴える方が急増しています。\n「スマホ疲れかな」で済ませていませんか？\n放置すると全身に影響が出ます。\n[COMMENT]\n眼精疲労が続くと：\n\n・首・肩の筋肉が慢性的に緊張\n・めまい・吐き気が起きやすくなる\n・頭痛が常態化する\n\n目の疲れは体全体のサインです。\n[COMMENT]\n目の奥の疲れには{cause}が関係しています。\n\n①スマホ・PC作業後の首こり\n②{habit1}\n③眼周りの筋肉の緊張\n\nここをほぐすと\nめまいや頭痛も楽になることが多い。\n[COMMENT]\n眼精疲労・めまいが気になる方、\nプロフィールから気軽にご相談ください。",

            # 睡眠不足 → ホルモン崩壊・免疫低下
            "{nagaoka}\n\nよく聞く「体がダルい」という声。\n睡眠不足を侮ってはいけません。\n放置すると体の根幹が崩れます。\n[COMMENT]\n睡眠不足が続くと：\n\n・成長ホルモンが出ない→回復できない\n・コルチゾール過多→免疫力低下\n・{symptom}が慢性化しやすくなる\n\n「忙しいから仕方ない」は\n体には通じません。\n[COMMENT]\n実は{cause}が睡眠の質を下げています。\n\n眠れない夜は\n・{habit1}\n・首・肩の緊張\nが原因のことが多い。\n\n体を整えると\n睡眠の質が変わる方が多いです。\n[COMMENT]\nぐっすり眠れる体を作りたい方、\nプロフィールからご予約ください。",
        ],
    },
    # ── 悩み共感→改善実績系：「○○で△△できない」→来院→Before/After ──
    "nayami_kyokan": {
        "desc": "悩み共感→改善実績（「○○で△△できない」→CTA）",
        "templates": [
            "{symptom}があって\n{life_scene_v}たかったのにできない。\n\n{nagaoka}\nよく聞く言葉です。\n[COMMENT]\n{symptom}の原因の多くは{cause}。\n\n揉んでも治らないのは\n「症状」だけ見ていて\n「原因」を変えていないから。\n[COMMENT]\n当院では{cause}から根本改善します。\n\n・週3あった{symptom} → ほぼゼロ\n・5年続いた症状 → 2ヶ月で改善\n\n諦めないでください。\n変われます。\n[COMMENT]\nプロフィールから予約できます。\n「{symptom}で困っている」とお気軽に。",

            "{nagaoka}\n\n{symptom}のせいで\n{life_scene_v}きれない。\n\nそんなお声が増えています。\n[COMMENT]\n「年齢のせい」「仕方ない」\nそう諦めていませんか？\n\n実は年齢より{cause}の影響の方が\nはるかに大きいです。\n[COMMENT]\n根本から整えた方の変化：\n\n□ {symptom}がほぼゼロになった\n□ {life_scene_n}が変わった\n□ 朝すっきり起きられるようになった\n\n体は変えられます。\n[COMMENT]\nプロフィールリンクから予約できます。\n初回カウンセリング無料です。",
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

NAGAOKA_PHRASES = [
    "長岡市で整体師をしているまぁです。",
    "長岡市で整体院を営んでいます。",
    "長岡市で1万人を施術してきました。",
    "長岡市の整体院にお越しの方へ。",
    "長岡市のワーママさんからよく聞く悩みです。",
    "長岡市で整体師として気づいたことがあります。",
]

def _ensure_nagaoka(text: str, ratio: float = 0.6) -> str:
    """truth_body_salon の投稿の約60%に「長岡市」を含める"""
    if "長岡市" in text:
        return text
    if random.random() > ratio:
        return text
    phrase = random.choice(NAGAOKA_PHRASES)
    # CONTINUATION_MARKER があれば本文の末尾（マーカー直前）に追加
    marker = "\n\n【続き】\n"
    if marker in text:
        main, rest = text.split(marker, 1)
        return main + "\n\n" + phrase + marker + rest
    return text + "\n\n" + phrase


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
            .replace("{habit3}", habits[2])
            .replace("{nagaoka}", random.choice(NAGAOKA_PHRASES)))

HOOK_CONTINUATION_MARKER = "\n\n【続き】\n"

HOOK_BODIES = [
    "{symptom}が治らない人に共通しているのは\n{cause}を放置していること。\n\n施術で一時的に楽になっても\n原因を変えなければまた戻ります。\n\n小さな習慣が、体を根本から変えます。",
    "それは「原因ではなく症状」だけを治そうとしていること。\n\n{cause}が続く限り\n{symptom}は何度でも戻ってきます。\n\n根本から変えると、体は想像以上に楽になります。",
    "共通点は3つ。\n\n▶ {habit1}\n▶ {habit2}\n▶ {habit3}\n\nどれか1つ変えるだけで\n体の状態は変わり始めます。",
    "{cause}を見直していないこと。\n\nどれだけ揉んでも\n{cause}が続く限り{symptom}は戻ります。\n\n施術と習慣の両輪が大事です。",
    "「治った」と思った翌日にまた辛くなる。\nその繰り返しをしていること。\n\n原因は{cause}にあります。\n\nここを変えると、体が根本から楽になります。",
    "自分の体のことを「年齢のせい」にしていること。\n\n{symptom}は年齢より\n{cause}による影響の方が大きい。\n\n習慣を変えれば、何歳からでも変われます。",
    "週1で整体に来ても\n日常の{cause}を変えていないこと。\n\n施術は「リセット」。\n日常が「積み上げ」です。\n\n両方揃って体は変わります。",
    "「痛くなったら行く」が習慣になっていること。\n\n{symptom}は出てからでは時間がかかります。\n\n「出る前に整える」が\n根本改善への近道です。",
]

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

    elif pattern_key == "hook_one_line":
        hook = fill(random.choice(p["templates"]))
        body = fill(random.choice(HOOK_BODIES))
        return hook + HOOK_CONTINUATION_MARKER + body

    elif pattern_key in ("story", "workmom", "ranking", "question",
                          "gyakusetsu", "aoi_style", "hori_style",
                          "pasona", "prbrep", "touka_koukan",
                          "hochi_risk", "nayami_kyokan"):
        return fill(random.choice(p["templates"]))

    return ""


def generate_30_posts() -> list[str]:
    w = _load_weights("truth")
    # 小川教材 7:2:1 比率 — 情報提供7・日常/共感2・宣伝1
    # 45本: 情報系(touka_koukan3+prbrep3+education4+insight4+aoi_style4+hori_style3+hook_one_line3+gyakusetsu3)=27
    #       宣伝/問題提起(pasona3+hochi_risk4+nayami_kyokan3)=10
    #       日常/共感(quote_empathy4+story3+workmom2+question2+ranking3)=14 → 合計51→45カット
    defaults = {
        "touka_koukan": 3,   # 等価交換（情報提供→信頼）
        "prbrep": 3,         # PRBREP（根拠+事例で説得力）
        "pasona": 3,         # PASONA（問題→共感→解決→提案→行動）
        "hochi_risk": 4,     # 放置リスク（問題提起→末路→改善）
        "nayami_kyokan": 2,  # 悩み共感→改善実績
        "hook_one_line": 3,
        "aoi_style": 4,      # 小川式
        "hori_style": 3,     # 堀式
        "gyakusetsu": 3,
        "quote_empathy": 4,
        "insight": 4,
        "education": 4,
        "story": 3,
        "workmom": 2,
        "ranking": 3,
        "question": 2,
    }
    merged = {k: int(w.get(k, v)) for k, v in defaults.items()}
    plan = (
        ["touka_koukan"] * merged["touka_koukan"] +
        ["prbrep"] * merged["prbrep"] +
        ["pasona"] * merged["pasona"] +
        ["hochi_risk"] * merged["hochi_risk"] +
        ["nayami_kyokan"] * merged["nayami_kyokan"] +
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
            post = _ensure_nagaoka(generate_post(pk))
            key = post[:100]
            if key not in seen and not _is_ng(post):
                seen.add(key)
                posts.append(post)
                break

    # feedback の追加テンプレを先頭に差し込む
    for tmpl in _load_extra_templates("truth"):
        try:
            posts.insert(0, _ensure_nagaoka(fill(tmpl)))
        except Exception:
            pass

    # 肩こりCTA 2本・頭痛CTA 2本をランダムな位置に差し込む
    cta_posts = (
        [_ensure_nagaoka(generate_cta_post("katakori")) for _ in range(2)] +
        [_ensure_nagaoka(generate_cta_post("zutsuu"))   for _ in range(2)]
    )
    for cta in cta_posts:
        pos = random.randint(0, len(posts))
        posts.insert(pos, cta)

    return posts[:65]


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
        "SNSを頑張っても集客できない本当の理由。",
        "コンテンツより先に整えるべきものがある。",
        "バズる必要はない。刺さればいい。",
        "フォロワー0でも集客できる仕組みがある。",
        "SNSを「何となく」やっている限り結果は出ない。",
        "投稿の頻度より、投稿の設計が大事。",
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
        "SNSを始めて3ヶ月、ほぼ無反応でした。\n\n変えたのは1つだけ。\n\n{point}\n\nそこから問い合わせが来るようになりました。",
        "半年間コンテンツを出し続けたのに\n集客ゼロだった方がいます。\n\n問題はコンテンツの量ではなく\n{point}でした。\n\n設計を変えた翌月、問い合わせが来ました。",
    ],
    "cta": [
        "{topic}で悩んでいる方、\nLINEで気軽に相談してください。\n\n{point}\n\n👉 https://lin.ee/8PsIHHC",
        "無料でSNS集客の相談に乗っています。\n\n{point}\n\nLINEからどうぞ 👉 https://lin.ee/8PsIHHC",
        "「{topic}、何から始めればいい？」\nそんな方はLINEに登録してみてください。\n\n一緒に整理しましょう。\n👉 https://lin.ee/8PsIHHC",
        "集客の仕組みを作りたい方へ。\n\n{point}\n\n詳しくはLINEで話しましょう。\n👉 https://lin.ee/8PsIHHC",
        "SNS集客を整えたい方、\n今なら無料で相談できます。\n\n{point}\n\nプロフィールのリンクからどうぞ。\n👉 https://lin.ee/8PsIHHC",
    ],
    # LINE誘導をさりげなく含む通常投稿（ハードCTAなし）
    "soft_line": [
        "{topic}について深掘りした内容を\nLINEで配信しています。\n\n{point}\n\n気になる方はプロフィールから。",
        "この投稿が参考になったら、\nLINEでもっと詳しい話を読んでみてください。\n\n{point}",
        "{topic}の実践ノウハウは\nLINEでこっそり共有しています。\n\n{point}",
        "SNS集客の設計について\nLINEで無料配信しています。\n\n{point}\n\nプロフィールから登録できます。",
        "「{topic}って結局どうすればいいの？」\nその答えをLINEで話しています。\n\n{point}",
    ],
    "ranking": [
        "集客できない店舗の共通点 TOP3\n\n1位：{bad1}\n2位：{bad2}\n3位：{bad3}\n\n正直、どれか当てはまってませんか？",
        "{topic}で成果が出ない人がやっていること3選\n\n① {bad1}\n② {bad2}\n③ {bad3}\n\nこれを直すだけで変わります。",
        "SNSで結果が出ている人が共通してやっていること\n\n▶ {tip1}\n▶ {tip2}\n▶ {tip3}\n\n難しいことは何もない。",
        "集客に成功している店舗が最初にやること 3つ\n\n① {tip1}\n② {tip2}\n③ {tip3}\n\n順番が大事です。",
        "SNS運用で絶対にやってはいけないこと3選\n\n① {bad1}\n② {bad2}\n③ {bad3}\n\n1つでも当てはまったら今すぐ見直してください。",
        "月商が上がった人が変えたこと TOP3\n\n① {tip1}\n② {tip2}\n③ {tip3}\n\nコンテンツの質より「設計」が変わった。",
    ],
    "question": [
        "正直に聞きます。\n\n{topic}、今どれくらい本気でやってますか？\n\nA：毎日やってる\nB：週数回\nC：なんとなく\n\nコメントで教えてください。",
        "あなたの{topic}、何が原因で伸びてないと思いますか？\n\n「コンテンツ」「継続」「設計」\n\nどれだと思う？\nコメントで教えてほしいです。",
        "これ、当てはまりますか？\n\n□ {bad1}\n□ {bad2}\n□ {bad3}\n\n1つでも当てはまったら、\n今すぐ{topic}を見直してください。",
        "SNS運用、今どこで詰まっていますか？\n\nA：発信ネタが思いつかない\nB：フォロワーが増えない\nC：投稿しても問い合わせが来ない\nD：何から始めればいいか分からない\n\nコメントで教えてください。",
        "あなたのSNS集客、何が一番の課題ですか？\n\n「時間がない」「続かない」「反応がない」\n\n一番当てはまるものを教えてください。",
    ],
    # ── PASONA法則（マーケ版）：Problem→Affinity→Solution→Offer→NarrowDown→Action ──
    "pasona": [
        "「{topic}を頑張っているのに\nなぜ結果が出ないんだろう」\nそう悩んでいませんか？\n\n気持ちよく分かります。\n毎日投稿してもフォロワーが増えない。\nいいねはつくのに問い合わせが来ない。\n本当に辛いですよね。\n\n実はその原因は「設計」にあります。\nコンテンツの質より先に\n導線と見せ方を整えることが大事。\n\n今なら無料でLINE相談を受け付けています。\n悩んでいる今が、変えるタイミングです。",

        "「SNSに時間をかけているのに\n集客できない」\nそんな状態が続いていませんか？\n\n同じ悩みを抱えていた方が\n私のLINEに集まっています。\n\n{topic}で集客できない本当の理由は\n「誰に」「何を」伝えるかが曖昧なこと。\nそこを整えるだけで、反応は変わります。\n\n無料LINEでは集客設計の基礎を配信中。\n今すぐプロフィールから登録できます。",

        "集客できないのは、あなたのせいじゃない。\n\n正しいやり方を知らないだけです。\n\n私がサポートした方の変化：\n月0件 → 月20件の問い合わせ\nフォロワー100人 → 6ヶ月で1000人\n\nやったことは{topic}の「設計を変えた」だけ。\n\n今ならLINE登録で\n集客設計の全体像を無料で学べます。\nまずは一歩、行動してみてください。",
    ],
    # ── PRBREP法（マーケ版）：結論→理由→根拠→反論→事例→結論 ──
    "prbrep": [
        "{topic}だけやっても集客はできません。\n\nなぜなら{topic}は「手段」であって\n「目的」ではないから。\n\n実際、フォロワー数と売上の相関は\nほぼないというデータもあります。\n\n「でも他の人はSNSで結果出してるじゃん」\nはい。でもその人たちは{topic}の裏に\n必ず「導線設計」があります。\n\n私が支援した店舗はフォロワー300人で\n月商80万を達成しました。\n\n{topic}は手段。\n目的から逆算した設計が、結果を作ります。",

        "SNSを頑張るより先に整えるべきことがある。\n\nなぜなら設計なしの発信は\nザルで水を汲むようなもの。\n\nこれは100店舗以上の集客を支援して\n繰り返し確認してきた事実です。\n\n「でも発信量が足りないんじゃないか」\nいいえ。毎日投稿して0件の人も\n月3本の投稿で月商100万の人もいる。\n\n変えたのは発信の「量」ではなく「設計」。\n\n正しく設計した発信は\n少ない投稿でも確実に集客につながります。",

        "広告費をかけなくても集客できます。\n\nなぜなら信頼を先に積み上げれば\nお客様は自然に来るから。\n\nこれは「等価交換の法則」と呼ばれるもので\n情報を先に与えることで\n見込み客の信頼を獲得する手法です。\n\n「でも無料で情報を出したら競合に真似される」\nはい。でも真似されても「信頼」は真似できない。\n\n実際にこの方法で\n広告費ゼロで月20件の問い合わせを実現した方がいます。\n\n集客は「信頼の積み上げ」が本質です。",
    ],
    # ── 等価交換型（マーケ版）：情報提供→信頼獲得 ──
    "touka_koukan": [
        "{topic}で結果を出す人がやっている\n3つの習慣をお伝えします。\n\n① {tip1}\n② {tip2}\n③ {tip3}\n\nどれも今日から実践できることばかり。\nまずは1つだけ取り入れてみてください。",

        "集客できない人がやりがちな\n5つの間違いを解説します。\n\n① {bad1}\n② {bad2}\n③ {bad3}\n④ ターゲットを決めずに全員に向けて発信\n⑤ 継続をやめてしまう\n\nこれを避けるだけで\n反応率は大きく変わります。\n\n知識が行動を変えます。",

        "今日は「なぜSNSで集客できないのか」\nの本質をお話しします。\n\n多くの人が「コンテンツの質が低い」と思っている。\nでも実際の原因はほぼこの3つ：\n\n・誰に届けたいか不明確\n・投稿後の導線がない\n・信頼を積み上げる前に売ろうとしている\n\nこの3つを整えると\n発信量が同じでも結果は変わります。\n\n設計を知っているかどうかの差です。",
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

# 集客できない・失敗する人の「悪い行動・共通点」（ネガティブ系テンプレート用）
MASA_BAD_HABITS = [
    "ターゲットを決めずに全員に向けて発信している",
    "投稿の目的がバラバラで一貫性がない",
    "フォロワー数だけを気にして導線を作っていない",
    "良いコンテンツを作れば売れると思っている",
    "毎回違うことを発信して軸がブレている",
    "プロフィールに何をしている人か書いていない",
    "投稿に行動喚起（CTA）が一切ない",
    "反応がないとすぐやめてしまう",
    "競合の真似ばかりで自分の強みを発信していない",
    "お客様の声・実績を出すのを遠慮している",
    "完璧を目指して投稿できない日が続く",
    "LINEやDMへの導線を作っていない",
    "数字を全く確認せず感覚で運用している",
    "広告費をコストだと思って一切使わない",
    "フォロワーが増えないと設計より発信量を増やす",
]


def generate_masa_post(pattern_key: str) -> str:
    entry = MASA_PATTERNS[pattern_key]
    # 新パターン(pasona/prbrep/touka_koukan)はリスト直接、旧パターンはdictのtemplatesキー
    templates = entry if isinstance(entry, list) else entry["templates"] if isinstance(entry, dict) and "templates" in entry else entry
    tmpl = random.choice(templates)
    tips = random.sample(MASA_TIPS, min(3, len(MASA_TIPS)))
    bads = random.sample(MASA_BAD_HABITS, min(3, len(MASA_BAD_HABITS)))
    return (tmpl
            .replace("{topic}", random.choice(MASA_TOPICS))
            .replace("{point}", random.choice(MASA_POINTS))
            .replace("{tip1}", tips[0])
            .replace("{tip2}", tips[1] if len(tips) > 1 else tips[0])
            .replace("{tip3}", tips[2] if len(tips) > 2 else tips[0])
            .replace("{bad1}", bads[0])
            .replace("{bad2}", bads[1] if len(bads) > 1 else bads[0])
            .replace("{bad3}", bads[2] if len(bads) > 2 else bads[0]))


def generate_30_masa_posts() -> list[str]:
    w = _load_weights("masa")
    # 小川教材 7:2:1 比率 — 情報提供7・日常/共感2・宣伝1
    # 45本: 情報系(touka_koukan3+prbrep3+education5+insight4+aoi_style4+hori_style3+hook_one_line4+gyakusetsu3)=29
    #       日常/共感(story3+soft_line4+ranking4+question2)=13  宣伝(pasona3+cta2)=5 → 合計47→45カット
    defaults = {
        "touka_koukan": 3,  # 等価交換（情報提供→信頼・小川教材コア）
        "prbrep": 3,        # PRBREP（根拠+事例で説得力）
        "pasona": 3,        # PASONA（問題→共感→解決→LINE誘導）
        "hook_one_line": 4,
        "aoi_style": 4,
        "hori_style": 3,
        "gyakusetsu": 3,
        "insight": 4,
        "education": 5,
        "story": 3,
        "soft_line": 4,
        "cta": 2,
        "ranking": 4,
        "question": 2,
    }
    merged = {k: int(w.get(k, v)) for k, v in defaults.items()}
    plan = (
        ["touka_koukan"] * merged["touka_koukan"] +
        ["prbrep"] * merged["prbrep"] +
        ["pasona"] * merged["pasona"] +
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
        added = False
        for _ in range(30):
            post = generate_masa_post(pk)
            key = post[:60]
            if key not in seen and not _is_ng(post):
                seen.add(key)
                posts.append(post)
                added = True
                break
        if not added:
            # 重複でもスロットを埋める
            posts.append(generate_masa_post(pk))

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
    elif "nagaoka" in account.lower():
        posts = generate_30_posts()   # truthと同じテンプレを流用
        entry = {"account": "@truth_nagaoka", "theme": "リミックス生成", "date": TODAY, "posts": posts}
        with open(LOG_FILE_NAGAOKA, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"✓ {len(posts)}本生成 → log_nagaoka.jsonl に保存")
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
