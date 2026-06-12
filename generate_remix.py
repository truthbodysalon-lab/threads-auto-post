#!/usr/bin/env python3
"""
過去投稿をベースに新しい投稿を生成する（API不要）
past_posts.json から構造・文体を学習してリミックス生成。
"""

import json
import random
import re
import sys
from datetime import date, datetime, timedelta
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

def _get_recent_first_lines(acct: str, days: int = 4) -> set:
    """直近N日間に投稿した1文目のset（生成時の重複排除用）"""
    pfile = BASE / f"log_{acct}_posted.jsonl"
    if not pfile.exists():
        return set()
    cutoff = (datetime.now() - timedelta(days=days)).date()
    result = set()
    for l in pfile.read_text().splitlines():
        if not l.strip():
            continue
        try:
            entry = json.loads(l)
            d = entry.get("date", "")
            t = entry.get("text", "")
            if d and t:
                entry_date = datetime.strptime(d, "%Y-%m-%d").date()
                if entry_date >= cutoff:
                    first_line = t.split("\n")[0].strip()
                    if first_line:
                        result.add(first_line)
        except Exception:
            pass
    return result

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

# masa専用: 予告型NGパターンチェック（feedback.json 2026-05-13ルール）
_MASA_YOKOKOKU_NG = [
    "をお伝えします",
    "について解説します",
    "についてお話しします",
    "をご紹介します",
]

def _is_masa_yokokoku_ng(text: str) -> bool:
    """masa投稿の予告型NG（「〇〇をお伝えします」等）を除外。
    1文目だけでなく2文目もチェック（2行セットで予告型になるテンプレ対策）。"""
    lines = text.split("\n")
    # 1文目・2文目（空行を除いた最初の2行）をチェック
    non_empty = [l.strip() for l in lines if l.strip()]
    check_lines = non_empty[:2]  # 最初の2行を対象
    return any(ng in line for ng in _MASA_YOKOKOKU_NG for line in check_lines)

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

# ── インスタハカセ理論：「自分は何者か」×「気づき1つ」（truth/nagaoka共通）──
# Threadsの7割はこの構文でOK。権威性＋短い気づきで最高のパフォーマンス
NANIMONO_KIZUKI_TEMPLATES = [
    # 修正: 「整体師として」で始まるテンプレは1文目NG（実績・自己紹介禁止ルール違反）
    # 代わりに「問いかけ」「ベネフィット」で始まるパターンに置き換え

    "{symptom}を「年齢のせい」にしている人が\n一番変わるのが遅い。\n\nなぜなら年齢より{cause}の影響の方が\nはるかに大きいから。",

    "{symptom}は揉んでも治らない。\n\n原因を変えていないから、ということ。\n\n施術で一時的に楽になっても\n生活習慣を変えなければ何度でも戻ります。",

    "改善が早い人と遅い人の違いは何か。\n\n施術の間も{cause}を変えているかどうか。\n\nこれだけの差です。",

    "改善率93.7%。\n\n{symptom}の根本は{cause}にある。\nそこを変えない限り、何度でも戻ります。\n\nこれは1万人を診てきて確認した事実です。",

    "週3で頭痛があった人が\n3回の施術でほぼゼロになった。\n\n何が変わったのか。{cause}を変えたから。\n\nこれが当院でよく起きることです。",

    "「もう仕方ない」と諦めた瞬間に\n体は本当に変わらなくなる。\n\nその諦めが一番もったいない。\n\n体は想像以上に変えられます。",

    "一番大事なのは、実は施術じゃない。\n\n「日常の{cause}を変えること」が9割。\n\nこれは1万人以上を診てきて\n繰り返し確認してきたことです。",

    "{symptom}は変えられる。\n\n年齢のせいにしている間は変わらないだけ。\n\nこれは断言できることです。",
]

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
            # 削除: "根本改善と対症療法は違います。" (1文目で実績/自己紹介NG)
            "体が楽になると、人生が変わります。",
            "改善率93.7%のワケ、話します。",
            "{symptom}を「年齢のせい」にしている間は変わりません。",
            "「もう治らない」は本当だろうか。",
            "施術より習慣が大事。知ってましたか？",
            "薬で治らない理由は、原因が違うから。",
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
            "「肩こりくらい」と放置していたら\n眠れなくなった。\n\n実は肩こりと自律神経は直結しています。\n[COMMENT]\n肩まわりの筋肉が緊張し続けると\n交感神経が過剰に働き始めます。\n\n・眠りが浅くなる\n・気分が落ち込みやすくなる\n・疲れが抜けない\n\n「ただの肩こり」じゃないんです。\n[COMMENT]\n肩こりの根本原因は{cause}にあります。\n\nマッサージで一時的に楽になっても\n原因を変えなければ何度でも戻ります。\n\n{cause}から整えると\n自律神経も安定してきます。\n[COMMENT]\nプロフィールから予約できます。\n「肩こり・自律神経が気になる」とお気軽にご相談ください。",

            # 頭痛放置 → 慢性化・薬依存・集中力低下
            "「また頭痛薬飲めばいいか」\nそれ、続けると危険です。\n[COMMENT]\n鎮痛剤を週3回以上飲み続けると\n「薬物乱用頭痛」になるリスクがあります。\n\n薬が効かなくなる→量が増える→悪化する\n\nこのスパイラルにはまっている方が\n実はとても多い。\n[COMMENT]\n頭痛の原因の多くは{cause}です。\n\n・水分が足りていない\n・頸椎（首の骨）がずれている\n・{habit1}\n\nここを整えると\n頭痛薬がいらない日が増えてきます。\n[COMMENT]\n頭痛でお悩みの方はプロフィールから。\n根本から整える施術で、薬に頼らない体へ。",

            # 猫背放置 → 内臓圧迫・呼吸が浅い・老け見え
            "猫背の方が増えています。\n「姿勢が悪いだけ」じゃないんです。\n放置すると体の中が変わります。\n[COMMENT]\n猫背が続くと：\n\n・肺が圧迫されて呼吸が浅くなる\n・内臓が下がって消化が悪くなる\n・顔が前に出て老け顔に見える\n\n{symptom}が出やすくなるのもそのせいです。\n[COMMENT]\n猫背の根本原因は{cause}にあります。\n\n姿勢を「直そう」とするより\n{cause}を整えると\n自然に背筋が伸びてきます。\n\n体の使い方を変えることが大切。\n[COMMENT]\n姿勢・猫背が気になる方、\nプロフィールからご相談ください。",

            # 眼精疲労 → めまい・吐き気スパイラル
            "目の疲れを訴える方が急増しています。\n「スマホ疲れかな」で済ませていませんか？\n放置すると全身に影響が出ます。\n[COMMENT]\n眼精疲労が続くと：\n\n・首・肩の筋肉が慢性的に緊張\n・めまい・吐き気が起きやすくなる\n・頭痛が常態化する\n\n目の疲れは体全体のサインです。\n[COMMENT]\n目の奥の疲れには{cause}が関係しています。\n\n①スマホ・PC作業後の首こり\n②{habit1}\n③眼周りの筋肉の緊張\n\nここをほぐすと\nめまいや頭痛も楽になることが多い。\n[COMMENT]\n眼精疲労・めまいが気になる方、\nプロフィールから気軽にご相談ください。",

            # 睡眠不足 → ホルモン崩壊・免疫低下
            "よく聞く「体がダルい」という声。\n睡眠不足を侮ってはいけません。\n放置すると体の根幹が崩れます。\n[COMMENT]\n睡眠不足が続くと：\n\n・成長ホルモンが出ない→回復できない\n・コルチゾール過多→免疫力低下\n・{symptom}が慢性化しやすくなる\n\n「忙しいから仕方ない」は\n体には通じません。\n[COMMENT]\n実は{cause}が睡眠の質を下げています。\n\n眠れない夜は\n・{habit1}\n・首・肩の緊張\nが原因のことが多い。\n\n体を整えると\n睡眠の質が変わる方が多いです。\n[COMMENT]\nぐっすり眠れる体を作りたい方、\nプロフィールからご予約ください。",
        ],
    },
    # ── 悩み共感→改善実績系：「○○で△△できない」→来院→Before/After ──
    "nayami_kyokan": {
        "desc": "悩み共感→改善実績（「○○で△△できない」→CTA）",
        "templates": [
            "{symptom}があって\n{life_scene_v}たかったのにできない。\n\n{nagaoka}\nよく聞く言葉です。\n[COMMENT]\n{symptom}の原因の多くは{cause}。\n\n揉んでも治らないのは\n「症状」だけ見ていて\n「原因」を変えていないから。\n[COMMENT]\n当院では{cause}から根本改善します。\n\n・週3あった{symptom} → ほぼゼロ\n・5年続いた症状 → 2ヶ月で改善\n\n諦めないでください。\n変われます。\n[COMMENT]\nプロフィールから予約できます。\n「{symptom}で困っている」とお気軽に。",

            "{symptom}のせいで\n{life_scene_v}きれない。\n\nそんなお声が増えています。\n[COMMENT]\n「年齢のせい」「仕方ない」\nそう諦めていませんか？\n\n実は年齢より{cause}の影響の方が\nはるかに大きいです。\n[COMMENT]\n根本から整えた方の変化：\n\n□ {symptom}がほぼゼロになった\n□ {life_scene_n}が変わった\n□ 朝すっきり起きられるようになった\n\n体は変えられます。\n[COMMENT]\nプロフィールリンクから予約できます。\n初回カウンセリング無料です。",
        ],
    },
    # ── 親しみ口語×時短セルフケア（@goodbody0614 うっちゃん 参考）──
    # カジュアルで温度感のある口語＋「1日◯分」の低ハードル提案＋ベネフィット直球。
    # 1文目は問いかけ/共感/ベネフィットで始める（地域名・実績・自己紹介NG）。短文。
    "selfcare_casual": {
        "desc": "親しみ口語×時短セルフケア（問いかけ→小さな一歩→ベネフィット）",
        "templates": [
            "その{symptom}、一生付き合うつもりですか？\n\n1日3分のケアで変わる人、ほんとに多いです。",

            "「忙しくて時間ない」人ほど聞いてほしい。\n\n{symptom}対策は、寝る前のたった3分でいけます。",

            "薬を飲む前に、まず{cause}を見直してみて。\n\n「あれ、ラクかも」ってなる人、けっこういます。",

            "1日たった4分でいいんです。\n\n{cause}を整えるだけで、{symptom}がスッと軽くなる人が多い。",

            "{symptom}がなくなると、体ってこんなに軽いんだ。\n\nそう言う人、本当に多いんです。",

            "毎日{habit1}になってませんか？\n\nそれ、{symptom}の地味な原因。やめるだけでも変わります。",

            "頑張らなくていいんです。\n\n1日1分、{cause}を整えるだけ。{symptom}は積み重ねで変わります。",

            "「{symptom}くらい大丈夫」が一番こわい。\n\n軽いうちに3分のケア、それだけで先が変わります。",
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
    # ── 3つの問い：何のお店?・行くとどうなる?・他と何が違う? ──
    # 新規顧客の「知りたい」に直接答えるシリーズ
    "store_identity": {
        "desc": "店舗アイデンティティ（何のお店?・どうなる?・他と違う?）",
        "templates": [
            # Q1: 何のお店ですか？（逆説フック）
            "揉んでほしいだけなら、うちじゃなくていいです。\n\n肩こり・頭痛の「なぜ起きるのか」を一緒に探して\n根本から変えるのが私たちの仕事です。\n[COMMENT]\nインナー整体×骨格の歪み×生活習慣。\nこの3つをまとめて整える整体院です。\n\n施術実績1万人・改善率93.7%。\nご予約はプロフィールから👆",

            # Q1: 何のお店ですか？（共感フック）
            "「マッサージに行っても、また戻る」\nその繰り返し、うちで終わりにしませんか。\n[COMMENT]\n薬でもなく、揉むだけでもない。\n\n姿勢・歪み・日常習慣を丸ごと変えるから\n「もう仕方ない」が「こんなに楽になるんだ」に変わります。\n[COMMENT]\n肩こり・頭痛を根本から変える整体院です。\nプロフィールのリンクからご予約👆",

            # Q2: 行くとどうなる？（数字Before/After）
            "週3で頭痛薬を飲んでいた人が\n3回の施術でほぼ飲まなくなった。\n\nこれが当院でよく起きることです。\n[COMMENT]\n5年続いた肩こりが2ヶ月で改善。\n朝起きるのが楽しみになった。\n仕事帰りでもまだ余裕がある。\n\nそういう変化を一緒に作ります。\n[COMMENT]\n施術実績1万人・改善率93.7%。\nプロフィールから予約できます👆",

            # Q2: 行くとどうなる？（生活変化）
            "体が変わると、子どもへの接し方まで変わる。\n\nそれくらい、毎日の頭痛・肩こりは\n生活全体を消耗させています。\n[COMMENT]\n当院に来た方がよく言う言葉があります。\n\n「なんで今まで我慢してたんだろう」\n\n痛みがなくなるだけじゃなく\n「自分のことを後回しにしなくなった」という変化が\n一番大きいと思っています。\n[COMMENT]\nご予約はプロフィールから👆",

            # Q3: 他と何が違う？（NG客設定型）
            "正直に言います。\nうちに向いていない人がいます。\n\n・1回で完全に治したい人\n・揉んでもらうだけで終わりたい人\n・生活習慣を変える気がない人\n[COMMENT]\n逆に、こういう方に来てほしい。\n\n「なぜ毎回繰り返すのか知りたい」\n「根本から変えたい」\n「子どもが成人するまで元気でいたい」\n[COMMENT]\n本気で変わりたい方のための整体院です。\nプロフィールから👆",

            # Q3: 他と何が違う？（施術哲学）
            "一般的な整体は「ほぐす」。\nうちは「なぜ硬くなるのか」を変える。\n\nこの違いが、通い続けるか卒業できるかを分けます。\n[COMMENT]\nインナー整体×骨格の歪み×栄養指導。\n\nこの3つをセットで見るから\n「施術後に戻らない体」になっていきます。\n[COMMENT]\n「また来てください」より「もう来なくていいですよ」と\n言える関係性を目指しています。\n\n施術実績1万人・改善率93.7%。\nプロフィールから👆",
        ],
    },
}

# ── 素材（myfilesロード済みならそちらを優先）────────────────────
SYMPTOMS = _truth_mat.get("symptoms") or [
    "肩こり", "頭痛", "首こり", "肩の重さ", "頭の重さ",
    "眼精疲労", "背中のこり", "腰の重さ", "首の張り", "疲労", "呼吸の浅さ",
    "体のだるさ", "睡眠の浅さ", "顎の疲れ"
    # 削除: "慢性的な肩こり", "慢性疲労" (1文目として使用されるとNG: 「慢性的〜」で始まるのは実績/自己紹介NG)
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
    "{cause}、見直したことありますか？\n\nそれだけで肩こりが楽になる方、\n実はとても多いんです。\n\n▶ 肩こり専門の施術はこちら\n{url}",
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

# ── 公式LINEリストイン（頭痛改善の専門情報配信）──────────────────
# truth / nagaoka 共通。頭痛改善の無料情報を提供する公式LINEへ登録を促す。
# ルール: 1文目はフックから（地域名/実績/自己紹介で始めない）・短文・URLは本文末尾に残す。
# _ensure_nagaoka / _enforce_short_body は通さず直接挿入する（URLをコメントへ流さない）。
LINE_LISTIN_URL = "https://lin.ee/qbRbPAm"

LINE_LISTIN_TEMPLATES = [
    "薬に頼らず頭痛を減らすコツ、\n毎日LINEで配信しています。\n\n「また痛くなりそう」の前に読んでおくと違います。\n登録は無料です👇\n{url}",
    "頭痛持ちさんへ。\n\n病院では教えてくれない\n「頭痛を繰り返さない体の作り方」を\nLINEで無料配信中です👇\n{url}",
    "「天気が崩れると頭が痛い」\nそれ、対策できます。\n\n頭痛専門の改善情報をLINEでお届け中。\n登録は無料👇\n{url}",
    "鎮痛剤を飲む回数が増えてきた人ほど\n読んでほしい。\n\n薬を減らしていくための頭痛ケアを\nLINEで配信しています👇\n{url}",
    "頭痛のタイプは人それぞれ。\nだから自分のタイプを知るのが第一歩です。\n\nセルフチェック＆改善法をLINEで配信中👇\n{url}",
    "「この頭痛、いつまで続くんだろう」\nそう思ったらLINEへ。\n\n頭痛を根本から減らすヒントを無料で受け取れます👇\n{url}",
    "頭痛とサヨナラしたい人だけ見てください。\n\n専門家が頭痛改善の情報を\nLINEで毎日シェアしています👇\n{url}",
    "毎朝の頭痛がなくなった理由、知りたくない？\n\n実は頭痛は呼吸・姿勢・睡眠で変わります。\n無料情報をLINEで配信中👇\n{url}",
    "頭痛で仕事の効率が落ちていませんか。\n\n改善できる方法があります。\n専門的な情報をLINEで無料でお伝えしています👇\n{url}",
    "季節の変わり目に頭痛が出やすい人へ。\n\n事前対策があります。\nLINE登録で体の準備の仕方を学べます👇\n{url}",
]


def generate_line_listin_post() -> str:
    """公式LINE（頭痛改善情報）へのリストイン投稿を生成"""
    tmpl = random.choice(LINE_LISTIN_TEMPLATES)
    return fill(tmpl.replace("{url}", LINE_LISTIN_URL))


NAGAOKA_PHRASES = [
    # ルール（全アカウント投稿ルール集より）:
    # ・1文目NG・個人名(まぁ等)NG・未確認実績NG・末尾単独追加NG
    # ・3〜4投稿に1回のみ
    "長岡市で整体院を兄妹で運営しています。",
    "長岡市での施術でも同じことを感じます。",
    "長岡市でよく聞く悩みです。",
    "長岡市の整体院でも同じ相談が多いです。",
]

# フック文の先頭に自然に統合するための修飾語。短い1行フックに使う
NAGAOKA_INTEGRATED_PREFIXES = [
    "長岡市で整体院をして気づいた",
    "長岡市で頭痛・肩こりを診ていて気づいた",
    "長岡市の整体師が見てきた",
    "長岡市で施術をしていて感じた",
    "長岡市で体の不調を診ていて気づいた",
]

# 短い1行フックに長岡市を統合するときの接尾辞パターン
_HOOK_SUFFIXES = ("共通点。", "理由。", "原因。", "違い。", "こと。", "話。", "傾向。")

# ── 軽症者・まだ我慢できる人ターゲット（@truth_nagaoka 専用）──────────
NAGAOKA_PATTERNS = {
    "keisei_target": {
        "desc": "軽症者・まだ我慢できる人へのアプローチ（早期来院促進）",
        "templates": [
            "「{symptom}、まだ我慢できる」\n\n1年後には毎日になります。\n軽症のうちに来てください。",

            "「たまに{symptom}がある」\nそのくらいで来ていいです。\n\n長岡市の整体院、軽症の方大歓迎です。",

            "「{symptom}があるけど仕事には支障ない」\n\nそれ、慢性化の手前です。\n今が来るタイミングです。",

            "まだ軽いうちが\n一番早く根本改善できます。\n\n「{symptom}が気になる」程度で来てください。",

            "「{symptom}、まだ薬を飲むほどじゃない」\n\nそのレベルが一番変わりやすいです。",

            "「大げさかな」って思って来るくらいが\nちょうどいいです。\n\n{symptom}は軽いうちに整えるのが正解。",

            "{symptom}を慢性化させないために\n一番大事なのは「早めに来ること」です。",

            "「整体はもっとひどくなってから」\n\n逆です。\n軽症のうちに来る人が一番早く楽になります。",

            "夕方だけ{symptom}がある。\n\nそのくらいが実は来るベストタイミングです。",

            "「{symptom}、疲れた日だけある」\n\nそれ、体の最初のSOSです。\n無視しないでください。",

            "週に1〜2回の{symptom}。\n\n今が一番変わりやすい段階です。\n放置するほど時間がかかります。",

            "まだ痛くない。\nだから今来てください。\n\n{symptom}は痛くなってからより\n軽いうちの方が3倍早く改善します。",
        ]
    },
    "keisei_risk": {
        "desc": "軽症放置リスク（慢性化の怖さ＋早期介入のすすめ）[COMMENT]形式",
        "templates": [
            "「月に2〜3回{symptom}がある」\n放置すると慢性化します。\n[COMMENT]\n今なら3〜5回で根本改善できます。\n慢性化してからは倍の時間がかかります。\n\n気になり始めた今が来るタイミングです。",

            "「まだ薬を飲むほどじゃない{symptom}」\n放置は禁物です。\n[COMMENT]\n軽い今なら1〜2ヶ月で根本改善できます。\n慢性化すると半年以上かかることも。\n\n早い方が絶対いいです。",

            "「{symptom}、我慢できる範囲」\n\nその感覚が危険です。\n[COMMENT]\n体は痛みに「慣れて」しまいます。\n慣れる＝改善ではありません。\n\n軽症のうちが一番ラクに根本改善できます。",
        ]
    },
    "keisei_kyokan": {
        "desc": "軽症者への共感・来院の背中を押す系",
        "templates": [
            "「{symptom}くらいで整体は大げさ」\n\nそんなことないです。\nそのくらいで来てほしいです。",

            "「忙しくて{symptom}を後回しにしてる」\n\n体は正直です。\n後回しにした分だけケアに時間がかかります。",

            "「{symptom}があるけど病院に行くほどでもない」\n\nそういう方のために整体があります。",

            "軽い{symptom}を後回しにするのは\n一番もったいない選択です。\n\n今が一番変わりやすい時期です。",

            "「整体って、もっとひどくなってから行くところ」\n\n実は逆です。\n軽いうちに来る人が一番早く楽になります。",
        ]
    },
    # ── 口語共感・呼びかけ型（@kisogawa_seitai_hori 参考）──
    # 砕けた口語・"〜だよね"系の共感・呼びかけ・低ハードルな相談誘導。
    # 軽症者ターゲットの核は維持。1文目はフック(共感/呼びかけ)、短文、
    # 個人名NG・未確認年数実績NG・長岡市は1文目に入れない（ルール厳守）。
    "keisei_casual": {
        "desc": "軽症者向け・口語共感／呼びかけ（堀式の砕けたトーン）",
        "templates": [
            "「まだ我慢できる」で何年も様子見してる人、多いよね。\n\nその“まだ”のうちが、一番ラクに変われるタイミングなんです。",

            "たまの{symptom}を「気のせい」で済ませてる人へ。\n\n軽いうちに整えた人ほど、戻りが早い。ほんとに。",

            "「病院に行くほどじゃない{symptom}」を放っておく人が、一番もったいない。\n\n軽症のうちが、一番変わるのに。",

            "{symptom}、我慢が当たり前になってないですか。\n\n慣れただけで、治ったわけじゃないんですよね。",

            "「整体は悪化してから」って思ってる人、逆ですよ。\n\n軽いうちに来た人から、サッと楽になっていきます。",

            "正直に言うと、{symptom}は軽いうちが一番ちょろい。\n\nこじらせるほど、時間もお金もかかります。",

            "「このくらいで相談していいのかな」\n\nいいんです。むしろ、そのくらいがちょうどいい。",

            "“たまに”のうちに動けた人は、ほんと早い。\n\n{symptom}が“いつも”になってからだと、倍かかります。",

            "「{symptom}、まだ我慢できる」って人、コメントで「私も」って教えてください。\n\nその“まだ”が、一番変われる時期だから。",

            "夕方になると{symptom}、ありませんか。\n\nそれ、体の最初のサイン。軽いうちが直しどきです。",
        ]
    },
    # ── nagaoka専用：3つの問い（何のお店?・どうなる?・他と違う?）──
    "nagaoka_store_identity": {
        "desc": "nagaoka店舗アイデンティティ（軽症者向け3つの問い）",
        "templates": [
            # Q1: 何のお店ですか？
            "「整体って、もっとひどくなってから行くところ」\nそう思っていたら、一度読んでください。\n[COMMENT]\n頭痛・肩こりの根本改善が専門の整体院です。\n\n「まだ我慢できる」レベルで来てほしいんです。\n軽症のうちが一番早く、一番ラクに変われるから。\n[COMMENT]\n長岡市で兄妹が運営する整体院です。\nプロフィールからご予約できます👆",

            # Q2: 行くとどうなる？
            "「たまにある{symptom}」が\n「なくて当たり前」に変わった。\n\n来院3〜5回でこういう変化がよく起きます。\n[COMMENT]\n軽症のうちに来てくれた人ほど\n変わるのが早い。\n\n慢性化してから来た場合の\n3分の1くらいの期間で改善できることが多いです。\n[COMMENT]\nプロフィールからご予約できます👆",

            # Q3: 他と何が違う？
            "うちが普通の整体と違うのは\n「まだ軽い人」に来てほしいこと。\n\nほとんどの整体院は「限界の人」を待っています。\n[COMMENT]\nなぜ軽症を大事にするか。\n\n軽いうちが一番変わりやすい。\n慢性化する前なら、根っこから変えられる。\n[COMMENT]\n「もっと早く来ればよかった」\nこの言葉を聞くたびに思います。\n\n長岡市の整体院、軽症の方大歓迎です👆",
        ]
    },
}

def _ensure_nagaoka(text: str, ratio: float = 0.25, prepend: bool = False) -> str:
    """長岡市を投稿に自然に含める。
    ルール: 1文目NG・末尾単独追加NG・3〜4投稿に1回（ratio=0.25）
    """
    if "長岡市" in text:
        return text
    if random.random() > ratio:
        return text
    phrase = random.choice(NAGAOKA_PHRASES)
    # アペンドモード（末尾追加）※prepend引数は後方互換のため残すが使わない
    # 末尾がCTA・URLの場合は追加しない
    _CTA_KW = ("ご相談ください", "はこちら", "ご予約", "一度", "http", "お問い合わせ")
    marker = "\n\n【続き】\n"
    if marker in text:
        main, rest = text.split(marker, 1)
        return main + "\n\n" + phrase + marker + rest
    if "\n\n" in text:
        last_para = text.rsplit("\n\n", 1)[1]
        if any(kw in last_para for kw in _CTA_KW):
            return text
    return text + "\n" + phrase


def _is_valid_first_line(text: str, acct: str = "truth") -> bool:
    """1文目が投稿ルール違反でないか確認（truth/nagaoka共通）
    NG: 「長岡市」「整体師」「施術」「実績」「改善率」で始まる
    """
    if not text:
        return True
    first_line = text.split("\n")[0].strip()
    ng_starts = ["長岡市", "長岡", "整体師", "施術", "実績", "改善率", "1万人", "1万", "100店舗"]
    return not any(first_line.startswith(ng) for ng in ng_starts)


def _enforce_short_body(text: str, max_lines: int = 3) -> str:
    """[COMMENT]/【続き】なし投稿が max_lines 行を超える場合、超過分を [COMMENT] に移動する。
    最初の段落は必ずメイン本文に残す。"""
    if "[COMMENT]" in text or "【続き】" in text:
        return text  # 既に分割済み

    paragraphs = text.split("\n\n")
    if len(paragraphs) <= 1:
        return text  # 段落が1つだけなら分割不要

    main_paras = [paragraphs[0]]
    total_lines = len([l for l in paragraphs[0].split("\n") if l.strip()])
    rest_paras = []

    for para in paragraphs[1:]:
        para_lines = len([l for l in para.split("\n") if l.strip()])
        if not rest_paras and total_lines + para_lines <= max_lines:
            main_paras.append(para)
            total_lines += para_lines
        else:
            rest_paras.append(para)

    if not rest_paras:
        return text

    main = "\n\n".join(main_paras).rstrip()
    rest = "\n\n".join(rest_paras).lstrip()
    return main + "\n[COMMENT]\n" + rest


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

def generate_nagaoka_post(pattern_key: str) -> str:
    """nagaoka専用パターンまたは共通パターンで投稿生成"""
    if pattern_key in NAGAOKA_PATTERNS:
        p = NAGAOKA_PATTERNS[pattern_key]
        return fill(random.choice(p["templates"]))
    elif pattern_key == "nagaoka_store_identity":
        p = NAGAOKA_PATTERNS["nagaoka_store_identity"]
        return fill(random.choice(p["templates"]))
    elif pattern_key == "nanimono_kizuki":
        # インスタハカセ理論：「自分は何者か」×「気づき1つ」
        return fill(random.choice(NANIMONO_KIZUKI_TEMPLATES))
    elif pattern_key == "quote_empathy":
        # nagaoka用: 超短縮版（opener + closer のみ・middleは省略）
        p = PATTERNS["quote_empathy"]
        opener = fill(random.choice(p["openers"]))
        closer = fill(random.choice(p["closers"]))
        return opener + closer
    elif pattern_key == "insight":
        # insight は元々短い（2行）のでそのまま使う
        return fill(random.choice(PATTERNS["insight"]["templates"]))
    elif pattern_key == "hook_one_line":
        # nagaoka用: フックのみ（本文なし・短く）
        p = PATTERNS["hook_one_line"]
        return fill(random.choice(p["templates"]))
    else:
        return generate_post(pattern_key)


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
    # インスタハカセ理論：「自分は何者か」×「気づき1つ」
    if pattern_key == "nanimono_kizuki":
        return fill(random.choice(NANIMONO_KIZUKI_TEMPLATES))

    # 3つの問いシリーズ（何のお店?・どうなる?・他と違う?）
    if pattern_key == "store_identity":
        return fill(random.choice(PATTERNS["store_identity"]["templates"]))

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
                          "hochi_risk", "nayami_kyokan", "selfcare_casual"):
        return fill(random.choice(p["templates"]))

    return ""


def generate_30_posts() -> list[str]:
    w = _load_weights("truth")
    defaults = {
        "nanimono_kizuki": 7,  # 「自分は何者か」×「気づき1つ」
        "store_identity":  3,  # 3つの問い（何のお店?・どうなる?・他と違う?）
        "touka_koukan": 4,
        "prbrep": 4,
        "pasona": 4,
        "hochi_risk": 4,
        "nayami_kyokan": 3,
        "selfcare_casual": 5,  # 親しみ口語×時短セルフケア（うっちゃん参考）
        "hook_one_line": 4,
        "aoi_style": 4,
        "hori_style": 3,
        "gyakusetsu": 4,
        "quote_empathy": 5,
        "insight": 5,
        "education": 5,
        "story": 4,
        "workmom": 3,
        "ranking": 4,
        "question": 3,
    }
    merged = {k: int(w.get(k, v)) for k, v in defaults.items()}
    plan = (
        ["nanimono_kizuki"] * merged["nanimono_kizuki"] +
        ["store_identity"]  * merged["store_identity"] +
        ["touka_koukan"] * merged["touka_koukan"] +
        ["prbrep"] * merged["prbrep"] +
        ["pasona"] * merged["pasona"] +
        ["hochi_risk"] * merged["hochi_risk"] +
        ["nayami_kyokan"] * merged["nayami_kyokan"] +
        ["selfcare_casual"] * merged["selfcare_casual"] +
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

    recent_first_lines = _get_recent_first_lines("truth", days=4)
    posts = []
    seen = set()
    for pk in plan:
        for _ in range(50):
            post = _enforce_short_body(_ensure_nagaoka(generate_post(pk)))
            key = post[:100]
            first_line = post.split("\n")[0].strip()
            # 1文目NG・NGワード・重複チェック
            if (key not in seen and not _is_ng(post) and
                first_line not in recent_first_lines and
                _is_valid_first_line(post, "truth")):
                seen.add(key)
                posts.append(post)
                break

    # feedback の追加テンプレを先頭に差し込む
    for tmpl in _load_extra_templates("truth"):
        try:
            posts.insert(0, _enforce_short_body(_ensure_nagaoka(fill(tmpl))))
        except Exception:
            pass

    # 肩こりCTA 2本・頭痛CTA 2本をランダムな位置に差し込む
    cta_posts = (
        [_enforce_short_body(_ensure_nagaoka(generate_cta_post("katakori"))) for _ in range(2)] +
        [_enforce_short_body(_ensure_nagaoka(generate_cta_post("zutsuu")))   for _ in range(2)]
    )
    for cta in cta_posts:
        pos = random.randint(0, len(posts))
        posts.insert(pos, cta)

    # 公式LINEリストイン投稿を織り交ぜる（頭痛改善情報の配信LINEへ誘導）
    # URLを本文に残すため _ensure_nagaoka / _enforce_short_body は通さない
    # 目標頻度: 全体の5〜7%（feedback.json 2026-06-06ルール）
    # 実行ギャップ対策: 1日の投稿は最大20本程度なので、必ず前半（index 3〜12）に配置
    listin_posts = []
    for _ in range(4):
        for _ in range(20):
            p = generate_line_listin_post()
            if p not in listin_posts:
                listin_posts.append(p)
                break
    for i, lp in enumerate(listin_posts):
        # 前半に均等配置: 4本を index 3, 7, 12, 18 付近に差し込む
        anchor_positions = [3, 7, 12, 18]
        pos = min(anchor_positions[i] if i < len(anchor_positions) else (i * 5 + 3), len(posts))
        posts.insert(pos, lp)

    return posts[:100]


def generate_40_nagaoka_posts() -> list[str]:
    """@truth_nagaoka 専用: 軽症者・まだ我慢できる人ターゲット 40本生成
    方針: 短文（2〜4行）を重視。長岡市は先頭追加（末尾追加しない）。
    """
    w = _load_weights("nagaoka")

    # 超短文優先の配分（7:2:1比率）
    # nagaoka専用パターン（全て2〜4行）: 19本
    # 短い共通パターン（insight=2行・quote_empathy短縮版・hook・ranking）: 17本
    defaults = {
        # ── nagaoka専用・超短文 ──
        "keisei_target":          15,  # 軽症者特化（メイン）
        "keisei_kyokan":           5,  # 軽症者共感
        "keisei_casual":           6,  # 口語共感・呼びかけ（堀式トーン参考）
        "keisei_risk":             5,  # 軽症放置リスク
        "nagaoka_store_identity":  3,  # 3つの問い（何のお店?・どうなる?・他と違う?）
        # ── インスタハカせ理論 ──
        "nanimono_kizuki":         6,  # 「自分は何者か」×「気づき1つ」
        # ── 短い共通パターン ──
        "insight":       6,
        "hook_one_line": 4,
        "quote_empathy": 6,
        "ranking":       4,
        # ── [COMMENT]形式 ──
        "hochi_risk":    3,
        "story":         2,
    }
    merged = {k: int(w.get(k, v)) for k, v in defaults.items()}

    plan = []
    for pk, cnt in merged.items():
        plan.extend([pk] * cnt)
    random.shuffle(plan)

    posts = []
    seen = set()
    # 長岡市を25%程度の投稿に織り込む（3-4投稿に1回）
    # 1文目NG・末尾単独追加NGルール厳守のため、中盤・本文内に挿入
    target_nagaoka_count = max(1, len(plan) // 4)  # 全体の25%程度
    nagaoka_count = 0

    for pk in plan:
        for _ in range(50):
            post = generate_nagaoka_post(pk)
            key = post[:100]
            # 1文目NG・NGワード・重複チェック
            if key not in seen and not _is_ng(post) and _is_valid_first_line(post, "nagaoka"):
                seen.add(key)
                # 目標に達するまで、長岡市を追加（1文目NGルール守る）
                if nagaoka_count < target_nagaoka_count and "長岡" not in post:
                    post = _ensure_nagaoka(post, ratio=1.0, prepend=False)
                    nagaoka_count += 1
                posts.append(post)
                break

    # feedback の追加テンプレを先頭に差し込む
    for tmpl in _load_extra_templates("nagaoka"):
        try:
            posts.insert(0, fill(tmpl))
        except Exception:
            pass

    # CTA: 肩こり2本・頭痛1本（_ensure_nagaoka不使用）
    cta_katakori = [generate_cta_post("katakori") for _ in range(2)]
    cta_zutsuu   = [generate_cta_post("zutsuu")   for _ in range(1)]
    cta_posts = cta_katakori + cta_zutsuu
    nagaoka_cta_idx = -1  # CTAへの長岡市追加なし
    cta_posts = [
        p
        for i, p in enumerate(cta_posts)
    ]
    for cta in cta_posts:
        pos = random.randint(0, len(posts))
        posts.insert(pos, cta)

    # 公式LINEリストイン投稿を織り交ぜる（頭痛改善情報の配信LINEへ誘導）
    # 実行ギャップ対策: 前半（index 4, 10, 18）に固定配置
    listin_posts = []
    for _ in range(3):
        for _ in range(20):
            p = generate_line_listin_post()
            if p not in listin_posts:
                listin_posts.append(p)
                break
    for i, lp in enumerate(listin_posts):
        anchor_positions = [4, 10, 18]
        pos = min(anchor_positions[i] if i < len(anchor_positions) else (i * 7 + 4), len(posts))
        posts.insert(pos, lp)

    return posts[:100]


# ══════════════════════════════════════════════
# @masahide_takahashi_ 投稿生成
# ══════════════════════════════════════════════

LOG_FILE_MASA = BASE / "log_masa.jsonl"

MASA_TOPICS = _masa_mat.get("topics") or [
    "動画集客", "Instagram運用", "Threads運用", "広告費の考え方",
    "プロフィール設計", "コンテンツ設計", "リール動画", "MEO対策",
    "フォロワーより導線", "売上につながる投稿", "エンゲージメント改善",
    "バズる投稿の法則", "コメント活用", "ストーリーズ活用", "教育コンテンツの作り方"
]

MASA_PATTERNS = {
    # ── 最重要：「自分は何者か」×「気づき1つ」（インスタハカセ理論・Threadsの7割はこれ）──
    "nanimono_kizuki": [
        "マーケター12年やってるけど、\nコンサルにお金かけるのに\n広告にお金かけない人、多すぎる。",

        "集客支援で月商0→100万にした話を\n何度もしてきたけど、\n変えたのは「設計」だけです。",

        "フォロワー200人で月商80万出している人がいる。\nフォロワー数の問題じゃないから。",

        "マーケター歴12年、\n1度も売上を落とさずに増収増益してきたけど、\n理由は「当たる訴求を探し続けたから」だけです。",

        "SNS運用を10年以上やってきて気づいたこと。\n\n当たった投稿はコピペでこすっても当たる。\n新しいネタを毎日考えなくていい。",

        "集客支援をしてきて言えること。\n\n伸びない人の共通点は\n「フォロワー数ばかり気にして導線を作ってない」こと。",

        "マーケターとして完全成果報酬でやってきて気づいたこと。\n\nSNSはバズらせる必要がない。\n「決まった人に届けば」それだけで売上が立つ。",

        "Threads運用でLINE登録を月100件以上取ってるけど、\n秘密はない。\n\n「自分が何者か」×「気づき1つ」を毎日投稿してるだけ。",
    ],

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
        # ── 対比フック「AよりB」（小川葵 参考）──
        "フォロワーを増やすより、導線を1つ整えるほうが早い。\n\n忙しい人ほど、ここだけでいい。",
        "毎日投稿より、刺さる1投稿。\n\n数より、誰に届くか。それだけで結果は変わります。",
        "完璧な投稿を月1より、60点を毎日。\n\n小さく続く形のほうが、結果は伸びます。",
        "新規を追うより、来た人を逃さない。\n\nそっちのほうが、ずっとコスパいいです。",
    ],
    # ── 堀式：逆説1行 → 数字実績 → 口語断定 ──
    "hori_style": [
        "フォロワー数より大事なものがある。\n\nインスタ集客を支援してきて気づいたこと。\n\nフォロワー200人 → 月商80万\nフォロワー3000人 → 月商20万\n\nどっちが伸びているかは明らかです。\n\n変えたのは「導線設計」だけ。\n\n正直、設計を知ってるか知らないかの差だけですよ。",
        "{topic}の壁は、センスじゃないです。\n\nサポートしたお客様の変化。\n\n月0件 → 月20件の問い合わせ\n反応ゼロ → フォロワー500人増\n\nみんなコンテンツの質は最初から変わってない。\n変えたのは「仕組み」だけです。\n\n正直、やり方を知ってるか知らないかの話です。",
        "SNSを頑張っても結果が出ないのは、\n才能の話じゃないです。\n\nビフォーアフターを見てください。\n\n投稿しても無反応 → 毎日コメントが来る\n半年フォロワー50人 → 3ヶ月で1000人\n\nやったことは設計を変えただけ。\n\n知識より仕組み。\nこれだけです。",
        # ── パワーワード反転・反復強調・否定→本質（ほり先生 参考）──
        "【フォロワー、いらん】\n\n増やすより先に、売れる導線を作ってください。\nそこが無いと、何人いても同じです。",
        "予約が増えないのは、投稿のせいじゃない。\n\n導線です。何がなんでも、導線なんです。",
        "セルフケア情報を出すのは悪くない。\nけど、それだけじゃ予約は増えません。\n\n足りないのは「次の一歩」の設計です。",
        "【コンサル、まだいらない】\n\nまず{topic}を自分で1つ整えてください。\nそれで動く人は、想像以上に多いです。",
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
    # 保存・コメント・フォロー誘導型（LINEに依存しない多様なCTA）
    "soft_line": [
        "この投稿、保存しておいてください。\n\n{topic}で迷ったとき、\n{point}\n\nいつでも見返せるように保存が便利です。",
        "{topic}について気になった方、\nコメントで「詳しく聞きたい」と教えてください。\n\n{point}",
        "この投稿が参考になったら、\nフォローしておいてください。\n\n{topic}の実践ノウハウを\n毎日発信しています。\n\n{point}",
        "スクショして手元に置いてください。\n\n{topic}を改善するとき、\n{point}\n\nすぐ使える情報です。",
        "「{topic}って結局どうすればいいの？」\n今日から試せることを1つ挙げるなら\n\n{point}\n\nまず1つだけ取り入れてみてください。",
        "この3つ、チェックしてみてください。\n\n□ {point}\n□ プロフィールの導線が明確か\n□ ターゲットに届いているか\n\n1つでも「できていない」があれば、そこが伸びしろです。",
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
        "「{topic}を頑張っているのに\nなぜ結果が出ないんだろう」\nそう悩んでいませんか？\n\n気持ちよく分かります。\n毎日投稿してもフォロワーが増えない。\nいいねはつくのに問い合わせが来ない。\n本当に辛いですよね。\n\n実はその原因は「設計」にあります。\nコンテンツの質より先に\n導線と見せ方を整えることが大事。\n\nこの投稿を保存して、今日から1つ変えてみてください。",

        "「SNSに時間をかけているのに\n集客できない」\nそんな状態が続いていませんか？\n\n{topic}で集客できない本当の理由は\n「誰に」「何を」伝えるかが曖昧なこと。\nそこを整えるだけで、反応は変わります。\n\n悩んでいる今が、変えるタイミングです。\nプロフィールに集客設計の無料資料をまとめています。",

        "集客できないのは、あなたのせいじゃない。\n\n正しいやり方を知らないだけです。\n\n私がサポートした方の変化：\n月0件 → 月20件の問い合わせ\nフォロワー100人 → 6ヶ月で1000人\n\nやったことは{topic}の「設計を変えた」だけ。\n\nまずは一歩、この投稿に「いいね」して\n自分の記録を残しておいてください。",
    ],
    # ── PRBREP法（マーケ版）：結論→理由→根拠→反論→事例→結論 ──
    "prbrep": [
        "{topic}だけやっても集客はできません。\n\nなぜなら{topic}は「手段」であって\n「目的」ではないから。\n[COMMENT]\n「でも他の人はSNSで結果出してるじゃん」\nはい。でもその人たちは{topic}の裏に\n必ず「導線設計」があります。\n[COMMENT]\n{topic}は手段。\n目的から逆算した設計が、結果を作ります。",

        "SNSを頑張るより先に整えるべきことがある。\n\nなぜなら設計なしの発信は\nザルで水を汲むようなもの。\n[COMMENT]\n「でも発信量が足りないんじゃないか」\nいいえ。毎日投稿して0件の人も\n月3本の投稿で月商100万の人もいる。\n\n変えたのは発信の「量」ではなく「設計」。\n[COMMENT]\n正しく設計した発信は\n少ない投稿でも確実に集客につながります。",

        "広告費をかけなくても集客できます。\n\nなぜなら信頼を先に積み上げれば\nお客様は自然に来るから。\n[COMMENT]\n「でも無料で情報を出したら競合に真似される」\nはい。でも真似されても「信頼」は真似できない。\n[COMMENT]\n集客は「信頼の積み上げ」が本質です。",
    ],
    # ── 反社会性（逆張り・常識破壊）：一般通念を否定して興味を引く ──
    "hanshakai": [
        "毎日投稿しなくていい。\n\nむしろ毎日投稿している人ほど\n「ネタ切れ」「疲弊」「軸ブレ」になりやすい。\n\n週3本、設計して届けた方が\n問い合わせはずっと増えます。",

        "フォロワーを増やすのは最後でいい。\n\nフォロワー200人で月商100万を超えた方がいます。\n\n先に整えるのは「導線」と「信頼」。\n数字を追いかける前に、設計を見直してください。",

        "バズっても売上は増えない。\n\n10万インプレ→問い合わせ0件。\nそういう話は珍しくない。\n\n「届く相手」に届けることの方が\n「多くの人に届ける」より10倍大事です。",

        "SNSを頑張るほど、集客できなくなる場合がある。\n\n努力を「量」に向けると\nターゲットも内容もブレていく。\n\n頑張る方向を間違えると\n半年後に消耗するだけです。",

        "プロフィールは自己紹介じゃなくていい。\n\n「何者か」より\n「あなたの何が解決できるか」を書いた方が\n反応率は上がります。\n\n今すぐプロフィールを読み直してください。",

        "「続けることが大事」は半分嘘です。\n\n正しい方向で続けることが大事。\n\n間違った設計を続けても\n消耗するだけで集客にはなりません。\n\n方向を確認してから、継続を語ってください。",

        "いいねを集めようとするな。\n\n信頼を集めてください。\n\nいいねは1秒で消えます。\n信頼は何年も残ります。\n\n投稿の目的は「反応」ではなく「記憶」です。",

        "リールを作る前にやることがある。\n\nどれだけいい動画を作っても\n「誰に届けたいか」が曖昧なままでは\n問い合わせは来ません。\n\nターゲット設計が先です。コンテンツは後。",
    ],
    # ── 自己開示・個人ストーリー型（最高エンゲージメント）──
    "jiko_kaiji": [
        "正直に言います。\n\nSNS集客を始めた頃、\nフォロワー0、問い合わせ0でした。\n\n何が変わったか。\n\n「誰に届けるか」を決めた。\nそれだけです。\n\n難しいことは何もなかった。",

        "こんな時間だから言いますね。\n\n僕も最初は\nいいね0、コメント0が\n何ヶ月も続きました。\n\nでも諦めなかった理由が1つあります。\n\nお客様に変化が出ていたから。\n\nSNSの反応より、目の前のお客様を見てください。",

        "ちょっとだけ正直に話します。\n\n集客がうまくいかない時期、\n「才能がないのかな」と思ってました。\n\n違いました。\n\n知識がなかっただけ。\n\n知識は努力で手に入ります。\n才能は関係ない。",

        "深夜なのでぶっちゃけます。\n\n売上が上がらない人に\n共通することがあります。\n\n「勉強して満足している」\n\n学んだら、次の日に実践してください。\n行動した人だけが変わります。",

        "年末だし、誰も見てないと思うから\n本音を書きますね。\n\nSNSで月商100万を超えた時、\n特別なことは何もしていませんでした。\n\nただ、お客様の悩みを\n毎日1つずつ投稿していただけ。\n\n継続だけが、武器になります。",
    ],

    # ── 深夜・ぶっちゃけ系（特別感・親近感）──
    "yakan_bucchake": [
        "この際だからハッキリ言います。\n\nインスタライブも無料の\nzoomセミナーを見て満足してるだろう？\n\nだから、売上あがらないのよ。\n\nじゃあ、観た内容こと実践した？\nそれを3ヶ月以上継続した？\n\n行動が遅すぎる。\n集客もできる人はその差です。",

        "夜中なので、ぶっちゃけます。\n\n「安定集客」なんて夢見たいな言葉に\n騙されるんじゃねーよ。\n\nそんな簡単に安定したら\nみんな大行列ですよ。\n\nだからこそ、常に勉強なんです。\nあぐらを描いた瞬間に終わりますよ。",

        "正直に言うと、\n\nSNSで流れている無料の情報で\n売上が上がらないってそろそろ\n気づいた方がいいよ。\n\n例えるなら、\n駅前で配っているティッシュと同じ。\n\nま、あなたの人生なので好きにしてください。",

        "もう辞めませんか？\n\n・技術スクールに大金を落とし続ける\n・高いコンサル費を払ってドブに捨てる\n・いろんなところをつまみ食いする\n・無料情報ばかり漁って結果にならない\n\nこんなことばかりするのはもう辞めませんか？",
    ],

    # ── 金言・名言型（高シェア・高保存）──
    "meigen": [
        "「価格」で選んだお客様は、「価格」で去っていく。\n「価値」で選んだお客様は、「あなた」がいる限り、去っていきません。",

        "あなたのサロンの「ファン」第一号は、あなた自身です。\n\nあなたが、自分のサロンを愛せなくて、\n誰が、愛してくれますか？\n\n自分を信じることから、全てが始まります。",

        "お客様は施術が上手いから通うのではありません。\n\n「あなたに会うと元気になるから」\n通うのです。",

        "人のことを幸せにしたいって人が\n売上が上がらないわけがないんですよ。\n\nただ、1番は自分が幸せになることを\n考えなきゃダメって話です。\n\nあなたの幸せは、みんなの幸せよ。",

        "Threadsで集客できる人の、たった1つの特徴。\n\n『続けること』\n\nマジでこれです。\n\n1、2日の投稿を頑張ったくらいで\n集客なんてできるわけないんです。\n\n最低でも1ヶ月は続けないと。",

        "いいね、コメントが欲しいのなら\n自分からしないともらえませんよ。\n\nGIVEの精神なさすぎやねーん。",
    ],

    # ── 警告・危険フック型（損失回避・注目を引く）──
    "keikoku": [
        "【危険】そのままだと、あなたのファンは一人もできません12選\n\n・発信の「目的」が、自分でも分かっていない\n・「誰に」届けたいのか、全くイメージできていない\n・「すごい人」だと思われようと、背伸びしている\n・「コメント」をもらっても、いいねを押すだけ\n・「プロフィール」が、あなたの自慢話で埋まっている\n・「人間味」がなく、AIが書いたような文章\n・「宣伝」ばかりで、お客様への貢献がない\n・「相互フォロー」を、目的にしてしまっている\n・「同業者」の投稿を、見下している\n・誰とも絡まず、自分の投稿だけしている\n\nファンは、あなたの「愛」の深さを見ています。",

        "Threadsで集客するのにダメなこと5選\n\n・お客様の悪口\n・同業者への批判\n・プライベートすぎる投稿\n・予約がなくて暇アピール\n・「ウチに来たら改善します」と言う\n\n自分なら、こんなサロンに\n行きたいと思いませんよね？",

        "【要注意】そのままだと、あなたは「カモ」にされますよ5選\n\n・「少しだけなら...」と、無料でノウハウを教えてしまう\n・「モニターだから」と、ありえない価格で施術してしまう\n・お客様の「時間がない」という言い訳を、真に受けてしまう\n・「あなたのため」という言葉を、無条件に信じてしまう\n・「ありがとう」という言葉に、満足して、お金を請求しない\n\nあなたは、プロです。ボランティアではありません。",

        "集客がうまくいかない時の5つの処方箋\n\n・そもそも知られていない（認知不足）\n・魅力が伝わっていない（訴求力不足）\n・信頼されていない（信頼性不足）\n・行動する理由がない（緊急性・限定性不足）\n・どう行動すればいいかわからない（導線不備）\n\n問題点を正しく特定しましょう。",
    ],

    # ── 価値認識・承認型（フォロワーの自己肯定感を上げる）──
    "kachi_ninshou": [
        "みんな、自分の価値を過小評価しすぎです。\n\nみんな、すごいことしてるのよ！\n\nオバハンを若返らせたり\nニキビで悩む人を救ったり\n腰痛の人を救ったり\n\nめちゃくちゃ凄いことなのよ\nそれ、ちゃんとわかってるか？\n\nあなたは、凄いです。",

        "50万円の売上を\n100万円にするのは\n実は、そんなに難しくありません。\n\n0を1にする方が\nよっぽど大変だったはず。\n\nあなたは、もう\nその大変な時期を\n乗り越えているんです。",

        "【なぜ、あなたは今の仕事を選んだのですか？】\n\n・人を美しくすることに、喜びを感じるから\n・誰かの悩みを、解決してあげたいから\n・自分の手で、人を癒すことができるから\n・お客様の笑顔が、何よりの報酬だから\n・この仕事が、たまらなく好きだから\n\nその「原点」を忘れなければ、\nあなたは、道に迷いません。",

        "お客様は\n施術が上手いから\n通うのではありません。\n\n「あなたに会うと\n元気になるから」\n通うのです。\n\nあなたの「人」が、価値になっています。",
    ],

    # ── SNS本質・哲学系（共感・共鳴を生む）──
    "sns_honshitsu": [
        "SNSってね、\n集客をする場所じゃなくて\n交流をする場所なんですよね。\n\n交流していた結果、集客に繋がった\nってのが正しいんです。\n\n理解できない人は、フォローしておいて\n解説するわ。",

        "いいねもしない、\nコメントもしない、\n引用も、再投稿もせずに\n自分の投稿だけしている人は\n\n果たして、関係を築いているのでしょうか？\n答えはNOです。\n\nもっと色々な人に絡みましょう！",

        "Threadsを見ているお客様の本音はコレ\n\n・恥ずかしいら、いいねしない\n・恥ずかしいからコメントしない\n・いつか行ってみようかな～\n\nだから、いいねやコメントに\n囚われなくて大丈夫ですよ。\n\nちゃんと見られてますよ。",

        "ここ数日で確定したことがあります。\nThreadsの表示回数が2桁の人は\n圧倒的に交流がないってことです。\n\nつまり、他人へコメントや引用\nそしてメンションを全くしていない人。\n\nThreadsで集客したいなら\nやった方がいいよ。",
    ],

    # ── 等価交換型（マーケ版）：情報提供→信頼獲得 ──
    "touka_koukan": [
        "{topic}で結果が出る人の習慣、3つだけ。\n\n① {tip1}\n② {tip2}\n③ {tip3}\n\nどれも今日から実践できることばかり。\nまずは1つだけ取り入れてみてください。",

        "集客できない人がやりがちな\n5つの間違い。\n\n① {bad1}\n② {bad2}\n③ {bad3}\n④ ターゲットを決めずに全員に向けて発信\n⑤ 継続をやめてしまう\n\nこれを避けるだけで\n反応率は大きく変わります。\n\n知識が行動を変えます。",

        "「なぜSNSで集客できないのか」\nの本質、3行でまとめます。\n\n・誰に届けたいか不明確\n・投稿後の導線がない\n・信頼を積み上げる前に売ろうとしている\n\nこの3つを整えると\n発信量が同じでも結果は変わります。\n\n設計を知っているかどうかの差です。",
    ],
    # ── 猿でもわかるアナリティクス ──
    # 数値を極限まで簡単に・自虐ユーモアで親近感
    "saru_analytics": [
        # エンゲージメント率
        "猿でもわかるエンゲージメント率。\n\n1%以下 → やばい\n1〜3% → 普通\n3%以上 → 伸びる\n\n以上です。",

        # 自虐から入るシリーズ
        "何を隠そう、僕が一番数値を見てなかった。\n\n投稿して「なんか伸びないな〜」で終わってた。\n\n数値って見るだけで何が悪いか全部わかるんですよね。\n[COMMENT]\n見るべき数値はこの3つだけです。\n\n① エンゲージメント率（反応率）\n② プロフィールアクセス数（興味を持った人数）\n③ フォロワー転換率（フォローしてくれた割合）\n\nこれだけ見れば、どこを直すべきかわかります。",

        # 保存率
        "猿でもわかる「バズる予兆」。\n\n保存数が急に増えた投稿は\nあとからじわじわ伸びます。\n\nいいねより保存を見てください。",

        # リーチ率
        "リーチ率って何？って人へ。\n\nフォロワー1000人で\n200〜300人に届いてたらOK。\n\nそれ以下なら投稿の質か頻度の問題。\nそれ以上なら拡散してる。\n\n以上。猿でもわかった？",

        # フォロワー転換率
        "何を隠そう、フォロワーが増えない理由。\n\n投稿を見た人がプロフィールに来ても\n「何の人かわからない」とフォローしない。\n\nプロフィールの1行目が全てです。\n[COMMENT]\nフォロワー転換率の目安は0.1〜0.3%。\n\n1000人リーチして1〜3人フォローが普通。\n\nこの数字が低いなら、プロフィールを直してください。\n投稿の問題じゃないことが多い。",

        # 深夜ぶっちゃけ × 数値
        "夜中なので正直に言います。\n\n「インプが少ない」と悩んでる人の\n9割はプロフィールが弱い。\n\n投稿より先にプロフィールを直してください。\n[COMMENT]\n見るべき数値は「プロフィールアクセス率」。\n\nリーチ数に対してプロフィールに来た割合が\n5%未満なら、投稿の1文目が弱い。\n\n5%以上でもフォローされないなら、プロフィール文が弱い。\n\nどちらかです。",

        # 猿レベルの保存率説明
        "猿でもわかる保存率の見方。\n\n保存数 ÷ リーチ数 = 保存率\n\n1%以上 → その投稿は資産になる\n3%以上 → バズる可能性あり\n\n保存される投稿は「後で使いたい」と思わせた証拠。\nいいねは感情、保存は価値。",

        # 猿レベルのストーリー閲覧率
        "猿でもわかるストーリーズ閲覧率。\n\nフォロワーの5〜10%が見てたら合格。\n\n100人フォロワーで5人見てたらOK。\n\nこれより低い人は\nストーリーの最初の1枚を変えてください。\n1枚目で離脱してる。",

        # 自虐スタイルの数値まとめ
        "何を隠そう僕が猿以下です。\n\n数値の見方、全然わかってなかった時期がある。\n\nで、わかってから何が変わったか。\n[COMMENT]\n「どこを直せばいいか」が一瞬でわかった。\n\n・インプ少ない → 1文目を変える\n・プロフィール来ない → 内容を変える\n・フォローされない → プロフィール文を変える\n・問い合わせ来ない → 固定投稿を変える\n\n数値は答えを教えてくれます。",
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
    # インスタハカセ理論 + コンサル伸びた投稿リスト分析を反映（2026-04-20）
    # 最重要追加: nanimono_kizuki（「自分は何者か」×「気づき1つ」= Threadsの7割はこれ）
    defaults = {
        # ── 最重要（インスタハカセ理論）──
        "nanimono_kizuki": 7,  # 「自分は何者か」×「気づき1つ」
        # ── 猿でもわかるアナリティクス ──
        "saru_analytics":  5,  # 数値解説・自虐ユーモア系（新）
        # ── 高エンゲージメント ──
        "jiko_kaiji":    4,
        "meigen":        5,
        "keikoku":       3,
        "yakan_bucchake":3,
        "kachi_ninshou": 3,
        "sns_honshitsu": 3,
        # ── 既存パターン ──
        "touka_koukan":  4,
        "prbrep":        3,
        "pasona":        3,
        "hook_one_line": 3,
        "aoi_style":     3,
        "hori_style":    3,
        "hanshakai":     3,
        "insight":       3,
        "education":     3,
        "story":         3,
        "soft_line":     2,  # LINE言及を10%未満に抑える（非LINE系のみ）
        "cta":           2,  # LINE URL付きは2本まで（フィードバックルール: URLありは月間2本以下）
        "ranking":       4,
        "question":      3,
    }
    merged = {k: int(w.get(k, v)) for k, v in defaults.items()}
    plan = (
        ["nanimono_kizuki"] * merged["nanimono_kizuki"] +
        ["saru_analytics"]  * merged["saru_analytics"] +
        ["jiko_kaiji"]    * merged["jiko_kaiji"] +
        ["meigen"]        * merged["meigen"] +
        ["keikoku"]       * merged["keikoku"] +
        ["yakan_bucchake"]* merged["yakan_bucchake"] +
        ["kachi_ninshou"] * merged["kachi_ninshou"] +
        ["sns_honshitsu"] * merged["sns_honshitsu"] +
        ["touka_koukan"]  * merged["touka_koukan"] +
        ["prbrep"]        * merged["prbrep"] +
        ["pasona"]        * merged["pasona"] +
        ["hook_one_line"] * merged["hook_one_line"] +
        ["aoi_style"]     * merged["aoi_style"] +
        ["hori_style"]    * merged["hori_style"] +
        ["hanshakai"]     * merged["hanshakai"] +
        ["insight"]       * merged["insight"] +
        ["education"]     * merged["education"] +
        ["story"]         * merged["story"] +
        ["soft_line"]     * merged["soft_line"] +
        ["cta"]           * merged["cta"] +
        ["ranking"]       * merged["ranking"] +
        ["question"]      * merged["question"]
    )
    random.shuffle(plan)

    recent_first_lines = _get_recent_first_lines("masa", days=4)
    posts = []
    seen = set()
    for pk in plan:
        added = False
        for _ in range(30):
            post = generate_masa_post(pk)
            key = post[:60]
            first_line = post.split("\n")[0].strip()
            if (key not in seen and not _is_ng(post)
                    and not _is_masa_yokokoku_ng(post)
                    and first_line not in recent_first_lines):
                seen.add(key)
                posts.append(post)
                added = True
                break
        if not added:
            # 重複でもスロットを埋める（予告型NGは最終手段でもスキップ）
            fallback = generate_masa_post(pk)
            if _is_masa_yokokoku_ng(fallback):
                # 予告型テンプレートしかないパターンは別パターンで代替
                for alt_pk in ["nanimono_kizuki", "meigen", "keikoku"]:
                    alt = generate_masa_post(alt_pk)
                    if not _is_masa_yokokoku_ng(alt):
                        posts.append(alt)
                        break
                else:
                    posts.append(fallback)
            else:
                posts.append(fallback)

    # feedback の追加テンプレを先頭に差し込む
    for tmpl in _load_extra_templates("masa"):
        try:
            posts.insert(0, generate_masa_post.__wrapped__(tmpl) if hasattr(generate_masa_post, "__wrapped__") else tmpl)
        except Exception:
            posts.insert(0, tmpl)

    # ── LINE言及率制御（2026-06-13追加） ──
    # ルール: masa は LINE言及を10%未満（39本中4本以下）に制限
    # 直近39本のログと合わせて、超過していないか確認
    line_count = sum(1 for p in posts if "LINE" in p or "lin.ee" in p)
    if line_count > 4:
        # LINE言及を4本以下に削減
        # LINE言及投稿を抽出して、超過分を非LINE投稿で置き換え
        line_posts = [(i, p) for i, p in enumerate(posts) if "LINE" in p or "lin.ee" in p]
        non_line_posts = [p for p in posts if "LINE" not in p and "lin.ee" not in p]

        # 4本だけ残す
        excess = len(line_posts) - 4
        if excess > 0 and len(non_line_posts) > 0:
            # 超過分を非LINE投稿に置き換え
            for i in range(min(excess, len(non_line_posts))):
                idx, _ = line_posts[-(i+1)]  # 後ろから削除
                posts[idx] = non_line_posts[i]

    return posts[:100]


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
        posts = generate_40_nagaoka_posts()   # 軽症者ターゲット40本専用生成
        entry = {"account": "@truth_nagaoka", "theme": "軽症者ターゲット40本生成", "date": TODAY, "posts": posts}
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
