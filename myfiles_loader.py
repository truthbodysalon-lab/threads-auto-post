#!/usr/bin/env python3
"""
Obsidian myfiles から投稿素材を読み込む
"""
import re
from pathlib import Path

MYFILES_PATH = Path("/Users/mt112/Desktop/my files/myfiles")

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""

def _clean_line(line: str) -> str:
    """Markdownの装飾を除去してプレーンテキストに"""
    line = re.sub(r"\[\[.*?\]\]", "", line)   # Obsidianリンク
    line = re.sub(r"\[.*?\]\(.*?\)", "", line) # Markdownリンク
    line = re.sub(r"[#\|>*_`]", "", line)      # 記号
    line = re.sub(r"^[\s\d\.\-\*①-⑩◎○▶]+", "", line)  # 行頭記号
    line = re.sub(r"tags:.*|category:.*|updated:.*|total:.*", "", line)  # フロントマター
    return line.strip().strip("。、.")


# ══════════════════════════════════════════════
# @truth_body_salon 用素材（固定値ベース＋ファイル補完）
# ══════════════════════════════════════════════

# 固定ベース素材（精度保証）
_SYMPTOMS_BASE = [
    "肩こり", "頭痛", "首こり", "慢性的な肩の重さ", "頭の重さ",
    "体のだるさ", "朝の疲れ", "肩と首の張り", "呼吸の浅さ", "眠りの浅さ"
]
_CAUSES_BASE = [
    "水分不足", "口呼吸", "食いしばり", "姿勢の歪み", "デスクワーク",
    "スマホの見すぎ", "運動不足", "睡眠不足", "猫背", "巻き肩", "呼吸の浅さ"
]
_LIFE_SCENES_NOUN_BASE = [
    "子どもとの時間", "休日の過ごし方", "仕事への集中力",
    "朝の時間の使い方", "家族との時間", "趣味の時間",
    "日常の家事", "子どもの行事", "週末のお出かけ"
]
_LIFE_SCENES_VERB_BASE = [
    "子どもと思いっきり遊び", "休日を元気に過ごし", "仕事に集中し",
    "朝すっきり起き", "家族と笑って過ごし", "趣味を楽しみ",
    "料理や家事をこなし", "子どもの行事に参加し"
]
_HABITS_BASE = [
    "口呼吸になっている", "食いしばりがある", "水分が足りていない",
    "スマホを長時間見ている", "猫背で座っている", "運動習慣がない",
    "睡眠が浅い", "呼吸が浅くなっている", "肩が内側に入っている"
]
_EMOTIONS_BASE = [
    "もう仕方ない", "年齢のせいだと思っていた",
    "お金をかけて失敗したくない", "自分を後回しにしてきた",
    "忙しくて自分のことは後回し", "根本から変えたい",
    "また薬を飲んだ", "我慢が限界"
]

def _load_from_file_symptoms() -> list[str]:
    """患者リサーチファイルから実際の症状を抽出"""
    text = _read(MYFILES_PATH / "整体" / "患者リサーチ" / "お悩みまとめ.md")
    results = []
    # 「〜が常にあり重だるい」のような文体から症状部分だけ取る
    for line in text.splitlines():
        c = _clean_line(line)
        # 数字+症状の行（1. 慢性的な首・肩のこり... の形式）
        if re.match(r'^\d+', line.strip()) and 8 <= len(c) <= 25:
            # 症状の核心部分だけ
            short = re.split(r'[やため、がで]', c)[0]
            if 4 <= len(short) <= 18:
                results.append(short)
    return results[:10]

def _load_extra_life_scenes() -> list[str]:
    """プロフィール分析から生活シーンを追加抽出"""
    text = _read(MYFILES_PATH / "SNS・Threads" / "truth_body_salon" / "まとめ（プロフィール・分析）.md")
    results = []
    for line in text.splitlines():
        c = _clean_line(line)
        if any(kw in c for kw in ["育児", "子育て", "仕事", "家事", "休日", "朝"]):
            if 6 <= len(c) <= 20:
                results.append(c)
    return results[:8]

# ── お客様の感想（顧客の声）：ルール準拠で素材抽出 ─────────────
# 個人名・未確認の年数実績は除外する（投稿ルールを絶対にブラさない）
_NAME_HINT = re.compile(r'.(様|さん|氏|くん|ちゃん)([\s:：）)、。]|$)')
_VOICE_NG = ("まぁ", "ゆう", "10年", "20年", "30年", "何年も通", "年通って")


def _voice_safe(s: str) -> bool:
    if not s or any(ng in s for ng in _VOICE_NG):
        return False
    if _NAME_HINT.search(s):
        return False
    return True


def load_customer_voices() -> dict:
    """お客様の感想・患者リサーチから、投稿ルール準拠の素材を抽出する。
    感想を丸ごと投稿に使うのではなく、症状の言葉・感情・変化の切り口として
    既存テンプレートに反映する。個人名・未確認実績は除外。
    """
    files = [
        MYFILES_PATH / "整体" / "患者リサーチ" / "お客様の感想.md",
        MYFILES_PATH / "整体" / "患者リサーチ" / "Part1_表層.md",
        MYFILES_PATH / "整体" / "患者リサーチ" / "Part3_契約トリガー.md",
    ]
    symptoms, emotions, results = [], [], []
    for f in files:
        for raw in _read(f).splitlines():
            s = raw.strip()
            # 実データ行のみ採用: 番号付きリスト or "- "箇条書きの本文だけ
            if not (re.match(r'^\d+[\.\．、]', s) or s.startswith("- ")):
                continue
            c = _clean_line(raw)
            # 見出し・件数・コメント・絵文字・記号残りは除外
            if (not c or "例" in c or "件" in c or "トリガー" in c
                    or any(e in c for e in "🔴🟡🔵📋⚠️🟢→←")):
                continue
            if not _voice_safe(c):
                continue
            if any(k in c for k in ["楽になった", "減った", "眠れる", "改善", "軽くなった", "通える", "変わった", "起きられる"]):
                if 6 <= len(c) <= 26:
                    results.append(c)
            elif any(k in c for k in ["諦め", "不安", "我慢", "後回し", "悩んで", "怖", "仕方ない"]):
                if 6 <= len(c) <= 24:
                    emotions.append(c)
            elif any(k in c for k in ["痛", "こり", "だるさ", "重だる", "疲れ", "眠れない", "つらい", "張り"]):
                # {symptom} にinline挿入されるので短い名詞句のみ
                short = re.split(r'[やためがでをにはやと、。]', c)[0]
                if 4 <= len(short) <= 14:
                    symptoms.append(short)
    dd = lambda xs: list(dict.fromkeys(xs))
    return {"symptoms": dd(symptoms)[:10], "emotions": dd(emotions)[:8], "results": dd(results)[:12]}


def load_truth_materials() -> dict:
    """myfilesを参照しつつ固定素材でフォールバック（毎回最新を読み込む）"""
    extra_symptoms = _load_from_file_symptoms()
    extra_scenes = _load_extra_life_scenes()
    voices = load_customer_voices()  # お客様の感想を反映

    symptoms = list(dict.fromkeys(_SYMPTOMS_BASE + extra_symptoms + voices["symptoms"]))
    life_scenes_noun = list(dict.fromkeys(_LIFE_SCENES_NOUN_BASE + extra_scenes))
    emotions = list(dict.fromkeys(_EMOTIONS_BASE + voices["emotions"]))

    return {
        "symptoms": symptoms[:20],
        "causes": _CAUSES_BASE,
        "habits": _HABITS_BASE,
        "emotions": emotions[:18],
        "life_scenes_noun": life_scenes_noun[:15],
        "life_scenes_verb": _LIFE_SCENES_VERB_BASE,
        "customer_results": voices["results"],  # 変化・結果の声（任意利用）
    }


# ══════════════════════════════════════════════
# @masahide_takahashi_ 用素材
# ══════════════════════════════════════════════

_MASA_TOPICS_BASE = [
    # Instagram / リール
    "Instagram集客", "リール動画", "プロフィール設計", "ストーリーズ活用",
    "インスタのハッシュタグ戦略", "フォロワー獲得",
    # Threads
    "Threads運用", "Threadsの伸ばし方", "テキスト投稿の書き方",
    # LINE
    "LINE集客", "LINE公式アカウント", "LINE配信設計",
    # 動画・ショート
    "ショート動画制作", "動画広告", "TikTok集客", "YouTube活用",
    # コンテンツ全般
    "コンテンツ設計", "コンテンツカレンダー", "投稿ネタの見つけ方",
    "売上につながる投稿", "教育コンテンツの作り方", "バズる投稿の法則",
    # 導線・集客設計
    "導線設計", "SNS×LINE導線", "問い合わせを増やす仕組み",
    "新規集客の自動化", "ファン化戦略",
    # マーケティング全般
    "口コミ・紹介の作り方", "ブランディング", "ターゲット設定",
    "MEO対策", "Googleビジネスプロフィール活用",
    "広告費の使い方", "店舗集客", "SNS運用",
    "コメント活用", "お客様の声の集め方",
]
_MASA_INSIGHTS_BASE = [
    # ターゲット・設計
    "ターゲットを絞ることが最優先",
    "まず「誰に」「何を」伝えるかを決めること",
    "結果が出ない人は発信の目的が曖昧",
    "SNSは「場所取り」より「信頼貯金」",
    # フォロワー・エンゲージメント
    "フォロワー数より問い合わせ導線が100倍大事",
    "フォロワーより「温まったフォロワー」が大事",
    "エンゲージメントが上がると自然に露出が増える",
    "コメント返しをするだけでリーチが変わる",
    "「保存される投稿」を意識するだけで結果が変わる",
    # コンテンツ・発信
    "最初の3秒で興味を引けるかがすべて",
    "完璧を目指さずまず公開することが大事",
    "良い動画より届く動画を作る",
    "バズより「刺さる」を狙う",
    "コンテンツは一度作ったら何度でも使い回せる",
    "ターゲットの悩みを言語化できると投稿が刺さる",
    # 信頼・感情
    "集客は感情で動く。ロジックより想いを伝える",
    "お客様の声・実績を積極的に発信する",
    "SNSは教育→信頼→行動の順番で設計する",
    "信頼が先、販売は後。この順番を絶対に崩さない",
    # 継続・習慣
    "継続こそが最大の差別化になる",
    "行動喚起（CTA）を必ず入れること",
    "広告は「コスト」でなく「投資」として考える",
    "1つのSNSを深掘りしてから複数展開が正解",
    "毎日発信することで「認知」が「信頼」に変わる",
]
_MASA_TIPS_BASE = [
    # 投稿設計
    "ターゲットを明確にする",
    "一貫したメッセージを発信する",
    "投稿の目的を一つに絞る",
    "保存される投稿を意識する",
    "フック（冒頭1行）で続きを読ませる",
    "感情に訴えるストーリーを使う",
    "数値で結果を示す",
    "行動喚起（CTA）を入れる",
    # エンゲージメント
    "コメントに必ず返信する",
    "問いかけで読者の参加を促す",
    "他アカウントとのコラボを活用する",
    # 導線・集客
    "お客様の実績・声を見せる",
    "プロフィールを最適化する",
    "SNSからLINEへの導線を明確にする",
    "お客様の声を定期的に投稿する",
    # 継続・運用
    "継続して発信する",
    "週1本は濃い教育コンテンツを出す",
    "投稿のベストタイムを分析する",
    "季節・時事ネタを絡める",
    "複数SNSで同じコンテンツを展開する",
]

def _load_masa_extra_insights() -> list[str]:
    """マーケティング書籍から自然な洞察フレーズを抽出"""
    results = []
    mkt_path = MYFILES_PATH / "マーケティング"
    for f in mkt_path.glob("*.md"):
        text = _read(f)
        for line in text.splitlines():
            c = _clean_line(line)
            # 記号・英数字・特殊表記が多い行を除外
            if re.search(r'[←→「」『』（）【】]', c):
                continue
            if re.search(r'[A-Za-z]{3,}', c):
                continue
            # テーブル行・複数スペース行を除外
            if re.search(r'\s{2,}', c):
                continue
            # 自然な日本語アドバイス行のみ
            if re.search(r'(大切|重要|ポイント|本質|まず|一番|最優先)', c):
                if 14 <= len(c) <= 35:
                    results.append(c)
    return list(dict.fromkeys(results))[:8]

def load_masa_materials() -> dict:
    """myfilesを参照しつつ固定素材でフォールバック"""
    extra_insights = _load_masa_extra_insights()
    insights = list(dict.fromkeys(_MASA_INSIGHTS_BASE + extra_insights))

    # トピックは固定リストのみ使用（プロフィール行は混入しやすいため除外）
    return {
        "topics": _MASA_TOPICS_BASE,
        "insights": insights[:20],
        "tips": _MASA_TIPS_BASE,
    }


if __name__ == "__main__":
    print("=== truth素材 ===")
    t = load_truth_materials()
    for k, v in t.items():
        print(f"\n[{k}] ({len(v)}件): {v[:4]}")

    print("\n=== masa素材 ===")
    m = load_masa_materials()
    for k, v in m.items():
        print(f"\n[{k}] ({len(v)}件): {v[:4]}")
