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

def load_truth_materials() -> dict:
    """myfilesを参照しつつ固定素材でフォールバック"""
    extra_symptoms = _load_from_file_symptoms()
    extra_scenes = _load_extra_life_scenes()

    symptoms = list(dict.fromkeys(_SYMPTOMS_BASE + extra_symptoms))
    life_scenes_noun = list(dict.fromkeys(_LIFE_SCENES_NOUN_BASE + extra_scenes))

    return {
        "symptoms": symptoms[:18],
        "causes": _CAUSES_BASE,
        "habits": _HABITS_BASE,
        "emotions": _EMOTIONS_BASE,
        "life_scenes_noun": life_scenes_noun[:15],
        "life_scenes_verb": _LIFE_SCENES_VERB_BASE,
    }


# ══════════════════════════════════════════════
# @masahide_takahashi_ 用素材
# ══════════════════════════════════════════════

_MASA_TOPICS_BASE = [
    "Instagram集客", "動画広告", "LINE集客", "リール動画",
    "プロフィール設計", "コンテンツ設計", "MEO対策",
    "フォロワー獲得", "導線設計", "売上につながる投稿",
    "広告費の使い方", "店舗集客", "SNS運用"
]
_MASA_INSIGHTS_BASE = [
    "ターゲットを絞ることが最優先",
    "まず「誰に」「何を」伝えるかを決めること",
    "フォロワー数より問い合わせ導線が100倍大事",
    "最初の3秒で興味を引けるかがすべて",
    "完璧を目指さずまず公開することが大事",
    "お客様の声・実績を積極的に発信する",
    "継続こそが最大の差別化になる",
    "行動喚起（CTA）を必ず入れること",
    "広告は「コスト」でなく「投資」として考える",
    "良い動画より届く動画を作る",
    "集客は感情で動く。ロジックより想いを伝える",
    "結果が出ない人は発信の目的が曖昧",
]
_MASA_TIPS_BASE = [
    "ターゲットを明確にする",
    "一貫したメッセージを発信する",
    "行動喚起（CTA）を入れる",
    "お客様の実績・声を見せる",
    "継続して発信する",
    "プロフィールを最適化する",
    "感情に訴えるストーリーを使う",
    "数値で結果を示す",
    "投稿の目的を一つに絞る",
]

def _load_masa_extra_insights() -> list[str]:
    """マーケティング書籍から洞察を追加抽出"""
    results = []
    mkt_path = MYFILES_PATH / "マーケティング"
    for f in mkt_path.glob("*.md"):
        text = _read(f)
        for line in text.splitlines():
            c = _clean_line(line)
            # 「〜が大切」「〜が重要」等のアドバイス行
            if re.search(r'(大切|重要|必要|ポイント|本質|差|違い|まず)', c):
                if 12 <= len(c) <= 40 and not c.startswith("http"):
                    results.append(c)
    return list(dict.fromkeys(results))[:10]

def load_masa_materials() -> dict:
    """myfilesを参照しつつ固定素材でフォールバック"""
    extra_insights = _load_masa_extra_insights()
    insights = list(dict.fromkeys(_MASA_INSIGHTS_BASE + extra_insights))

    # アカウントプロフィールからトピック追加
    profile_text = _read(MYFILES_PATH / "SNS・Threads" / "masahide_takahashi_" / "まとめ（プロフィール・インプ）.md")
    extra_topics = []
    for line in profile_text.splitlines():
        c = _clean_line(line)
        if any(kw in c for kw in ["集客", "Instagram", "動画", "LINE", "リール"]):
            if 5 <= len(c) <= 18:
                extra_topics.append(c)

    topics = list(dict.fromkeys(_MASA_TOPICS_BASE + extra_topics[:8]))

    return {
        "topics": topics[:20],
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
