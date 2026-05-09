#!/usr/bin/env python3
"""
GeneratorAgent — Claude APIでmyfiles・過去実績・フィードバックを参照して投稿生成
テンプレートベースにフォールバックする二段構え
"""
from __future__ import annotations

import json
import os
import random
import sys
import urllib.request
from datetime import date
from pathlib import Path

BASE   = Path(__file__).parent.parent
TODAY  = date.today().strftime("%Y-%m-%d")
MYFILES = Path("/Users/mt112/Desktop/my files/myfiles")

ACCOUNT_PROFILES = {
    "truth": {
        "name": "@truth_body_salon",
        "persona": "長岡市の整体師・まぁ先生",
        "target": "35〜55歳の慢性肩こり・頭痛に悩むワーママ",
        "tone": "優しく寄り添う。1文1行。超短文。「 」で患者のセリフを引用。共感→原因→解決の3ステップ。押しつけがましくない。",
        "topics": ["肩こり", "頭痛", "姿勢", "水分不足", "食いしばり", "スマホ首", "根本改善", "ワーママの体", "セルフケア"],
        "cta_katakori": "https://beauty.hotpepper.jp/CSP/kr/reserve/?storeId=H000596246&couponId=CP00000008152494&add=0",
        "cta_zutsuu": "https://beauty.hotpepper.jp/CSP/kr/reserve/?storeId=H000596246&couponId=CP00000008707618&add=0",
        "profile_file": "SNS・Threads/truth_body_salon/まとめ（プロフィール・分析）.md",
        # テーマローテーション（放置リスク・悩み共感→改善実績・メカニズム解説）
        "themes": [
            "放置リスク（肩こり→自律神経の乱れ・うつ・睡眠障害）",
            "放置リスク（頭痛→慢性化・薬が効かない体質・集中力低下）",
            "放置リスク（猫背→内臓圧迫・呼吸が浅い・老け顔）",
            "放置リスク（眼精疲労→めまい・吐き気スパイラル）",
            "放置リスク（睡眠不足→ホルモンバランス崩壊・免疫低下）",
            "悩み共感→来院→改善実績→CTAシリーズ",
            "症状メカニズム解説（巻き肩・猫背・自律神経など）",
        ],
    },
    "nagaoka": {
        "name": "@truth_nagaoka",
        "persona": "長岡市の整体師兄妹（薬が効かない頭痛・肩こりを改善する整体院）",
        "target": "長岡市在住・肩こり・頭痛の軽症者〜まだ我慢できるレベルの方（症状はあるが「大したことない」と放置している35〜55歳）",
        "tone": "優しく寄り添う。1文1行。超短文。「 」で患者のセリフを引用。軽症者の「まだ大丈夫かな」に共感してから「今が来るタイミング」と背中を押す。長岡の地域感を盛り込む。",
        "topics": ["たまある肩こり", "週数回の頭痛", "我慢してる体の不調", "軽いうちに根本改善", "予防整体", "慢性化させない体づくり", "長岡の季節と体"],
        "cta_katakori": "https://beauty.hotpepper.jp/CSP/kr/reserve/?storeId=H000596246&couponId=CP00000008152494&add=0",
        "cta_zutsuu": "https://beauty.hotpepper.jp/CSP/kr/reserve/?storeId=H000596246&couponId=CP00000008707618&add=0",
        "profile_file": "SNS・Threads/truth_body_salon/まとめ（プロフィール・分析）.md",
        "themes": [
            "軽症者へのアプローチ（まだ我慢できる人に早期来院を促す）",
            "軽症放置リスク（月数回の症状が慢性化するプロセス）",
            "長岡の季節と体の不調（雪・寒暖差・豪雪地帯特有の体への影響）",
            "我慢している人の声→来院→改善実績（Before/After）",
            "症状メカニズム解説（なぜ軽症のうちに来ると根本改善が早いか）",
            "長岡市の生活スタイルと体の不調（車社会・デスクワーク・冬の運動不足）",
            "放置リスク（軽い症状→慢性化→薬依存のスパイラル）",
        ],
    },
    "masa": {
        "name": "@masahide_takahashi_",
        "persona": "インスタ集客の先生・髙橋雅英",
        "target": "集客・売上に悩む店舗オーナー・個人事業主",
        "tone": "断言調。口語。数字実績を使う。1行フック→箇条書き→断言の小川式か、逆説1行→数字実績→口語断定の堀式。LINEへ誘導（URL付きは30本中3本のみ）。",
        "topics": ["Instagram集客", "リール動画", "LINE集客", "導線設計", "フォロワーより売上", "プロフィール改善"],
        "line_url": "https://lin.ee/8PsIHHC",
        "profile_file": "SNS・Threads/masahide_takahashi_/まとめ（プロフィール・インプ）.md",
        # 1日5本構成
        "daily_structure": [
            "1本目：数値の読み方（エンゲージメント率・リーチ率などの基準値を提示）",
            "2本目：よくある勘違い・NG行動（具体的な失敗パターン）",
            "3本目：改善アクション（今日からできること・具体的な手順）",
            "4本目：事例・Before→After（数字で変化を示す）",
            "5本目：自己診断チェック または CTA（LINE登録誘導）",
        ],
        # 数値テーマローテーション
        "metrics_themes": [
            "エンゲージメント率（基準値：1〜3%以上が目安・良し悪しの判断）",
            "リーチ数・リーチ率（フォロワー数対比20〜30%が目安）",
            "保存数・保存率（バズの予兆指標・エンゲの中で最重要）",
            "プロフィールアクセス数・遷移率（投稿→プロフィールへの誘導率）",
            "フォロワー転換率（リーチ→フォロワー 0.1〜0.3%が目安）",
            "ストーリーズ閲覧率（フォロワーの5〜10%が目安）",
            "インプレッション vs リーチの違いと活用法",
            "投稿時間帯とリーチの相関（朝7〜9時・夜21〜23時が高い）",
            "週ごとの数値トレンドの読み方と改善サイクル",
        ],
    },
}

# 参照スタイル（小川式・堀式）
STYLE_EXAMPLES = """
【小川式（@aoi_ogawa_sns）】
新規が来る店はここが違う。（1本目 — 謎かけ1行）
来店理由がハッキリしている。
・なぜこの店なのか
・何が違うのか
この説明があるだけで選ばれやすさは大きく変わります。
選ばれる理由。これを作ることが大事。（2本目 — 答え+箇条書き+断言）

【堀式（@yusuke_hori_）】
月商100万の壁って、技術じゃないです。（逆説1行フック）
技術の勉強ばかりしてても売上は頭打ちになります
変えたのは「考え方」だけです
正直、仕組みを知ってるか知らないかの差だけですよ。（数字実績+口語断定）
"""


def _read_myfile(rel_path: str) -> str:
    path = MYFILES / rel_path
    try:
        return path.read_text(encoding="utf-8")[:3000]
    except Exception:
        return ""


def _load_top_posts(acct: str, n: int = 5) -> str:
    """インサイトからトップ投稿を取得"""
    ifile = BASE / f"insights_{acct}.json"
    if not ifile.exists():
        return ""
    try:
        data = json.loads(ifile.read_text())
        top = data.get("top10_by_likes", [])[:n]
        if not top:
            return ""
        lines = ["【過去の高パフォーマンス投稿（参考）】"]
        for p in top:
            lines.append(f"いいね{p['likes']} 閲覧{p['views']}: {p['text'][:80]}")
        return "\n".join(lines)
    except Exception:
        return ""


def _load_feedback() -> str:
    fb_file = BASE / "feedback.json"
    if not fb_file.exists():
        return ""
    try:
        fb = json.loads(fb_file.read_text())
        parts = []
        ng = fb.get("ng_words", [])
        if ng:
            parts.append(f"NGワード（使わないこと）: {', '.join(ng)}")
        notes = [n["text"] for n in fb.get("notes", [])[-3:]]
        if notes:
            parts.append("フィードバックメモ:\n" + "\n".join(f"・{n}" for n in notes))
        return "\n".join(parts)
    except Exception:
        return ""


def _call_claude(prompt: str, system: str) -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 4096,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
            return resp["content"][0]["text"]
    except Exception as e:
        print(f"  [GeneratorAgent] Claude API エラー: {e}", file=sys.stderr)
        return None


def generate(acct: str, count: int = 30) -> list[str]:
    """メイン生成関数 — Claude API → テンプレートフォールバック"""
    # nagaoka は truth と同じプロファイル構造を持つ（独自プロファイルあり）
    if acct not in ACCOUNT_PROFILES:
        acct = "truth"  # フォールバック
    prof = ACCOUNT_PROFILES[acct]
    profile_text = _read_myfile(prof["profile_file"])
    top_posts    = _load_top_posts(acct)
    feedback     = _load_feedback()

    system = f"""あなたはSNS投稿の専門家です。
以下のアカウント情報と小川教材フレームワークに基づき、Threads投稿を{count}本生成してください。

アカウント: {prof['name']}（{prof['persona']}）
ターゲット: {prof['target']}
文体ルール: {prof['tone']}

━━━━━━━━━━━━━━━━━━━━━━━
【小川教材コアフレームワーク】
━━━━━━━━━━━━━━━━━━━━━━━

■ 投稿比率 7:2:1（厳守）
- 情報提供型 70%: ターゲットの悩みを解決する有益な情報。煽りなし。
- 日常/共感型 20%: ストーリー・共感・問いかけ。信頼を積み上げる。
- 宣伝型 10%: CTAあり。強くても1日1本まで。

■ 1日の投稿リズム
- 朝（共感型）: 「分かる、辛い、自分もそう」から入る
- 夜（逆説型）: 「実は〜」「知ってた？」と意外性で刺す

■ PASONA法則（宣伝・CTA型に使う）
Problem（悩みを言い当てる）→ Affinity（共感）→ Solution（解決策）→ Offer（提案）→ Narrowing down（限定感）→ Action（行動喚起）

■ PRBREP法（教育・情報型に使う）
Point（結論）→ Reason（理由）→ Basis（科学的根拠・数字）→ Rebuttal（反論への反論）→ Example（具体的事例）→ Point（結論を繰り返す）

■ 等価交換の法則（情報提供型の基本）
煽り・押し売りなし。先に価値ある情報を与えることで信頼を獲得。
「情報を受け取った人は次の行動を考え始める」

■ 三位一体脳モデル
- 爬虫類脳（本能）: 痛み・恐怖・生存本能に訴える
- 哺乳類脳（感情）: 共感・ストーリー・感情移入
- 人間脳（理性）: 数字・根拠・論理

■ 2Qテクニック（ストーリー系で活用）
1Q: 「なぜ〜なのですか？」（事実の深掘り）
2Q: 「それはどんな気持ちでしたか？」（感情の深掘り）
→ ビフォーアフターのギャップを最大化

■ マイクロコピー（行動喚起フレーズ）
「まずは1つだけ試してみてください」
「気づいた今がチャンスです」
「今すぐ行動に変えてください」

■ インスタハカセ理論（最重要）
- Threadsの7割は「自分は何者か」×「気づき1つ」の構文で良い
- 例: 「整体師として1万人施術してきたけど、肩こりを年齢のせいにしてる人が一番遅い」
- 例: 「マーケター12年やってるけど、広告にお金かけない人多すぎる」
- 投稿の長さは3〜4文がベスト（長文不要、Threadsはエンタメ）
- 権威性・信頼性（実績・数字）を「毎投稿」に自然に盛り込む
- 常識破壊（みんなが当然と思っていることを覆す）+具体的な数字が「有益さ」の核心
- 当たった投稿はコピペ（マイナーチェンジ）してこすり続ける

━━━━━━━━━━━━━━━━━━━━━━━

参考スタイル:
{STYLE_EXAMPLES}

アカウントプロフィール情報:
{profile_text}

{top_posts}

{feedback}

【出力フォーマット】
各投稿を --- で区切って出力してください。余計な説明は不要です。
例:
---
投稿本文1
---
投稿本文2
---
"""

    # CTAルール
    if acct in ("truth", "nagaoka"):
        import datetime as _dt
        day_of_year = _dt.date.today().timetuple().tm_yday
        themes = prof["themes"]
        today_theme = themes[day_of_year % len(themes)]
        acct_label = "truth_body_salon" if acct == "truth" else "truth_nagaoka"

        # nagaoka 専用ルール（軽症者ターゲット）
        nagaoka_extra = """
■ 【truth_nagaoka 特別ルール: 軽症者・まだ我慢できる人ターゲット】
- ターゲット像: 「たまに肩こりや頭痛がある」「まだ薬を飲むほどじゃない」「我慢できるレベル」の人
- メッセージの核心: 「軽いうちが一番早く根本改善できる」「今が来るタイミング」
- 軽症者の心理に共感してから背中を押す（「大げさかな」→「そんなことない」）
- 症状レベルの表現: 毎日ではない・週1〜2回・夕方だけ・疲れた日だけ
- 「慢性化する前に」「まだ軽いうちに」というフレーズを積極活用
- 来院ハードルを下げる表現: 「まだ軽いけど気になる」でも大丈夫
- 「長岡市」は投稿の冒頭1行目に自然に入れる（末尾追加は絶対NG）
- 1行目に「長岡市の整体師です。」などを入れてから本文に入る構成が理想
""" if acct == "nagaoka" else ""

        cta_rule = f"""
【{acct_label} 投稿ルール（必須）】

■ 今日のメインテーマ（ローテーション）: {today_theme}
{nagaoka_extra}
■ 絶対ルール
- 本文は2〜3行以内に収める（超短文・スクロールを止める1行フックから入る）
- 「長岡市」は投稿の冒頭に自然に入れる（末尾に追加・アペンドするのは絶対NG）
- 長い解説は [COMMENT] で区切ってコメント1・2に分割する（3分割まで）
- 最後の [COMMENT] には必ずCTA（プロフィール誘導・予約）を入れる
- 同じ内容・構成の投稿を重複して作らない

■ 出力フォーマット（[COMMENT]マーカーで分割）
【本文: 3〜4行】
[COMMENT]
【コメント1: 続き・メカニズム解説】
[COMMENT]
【コメント2: 改善策・対策】
[COMMENT]
【コメント3: CTA（プロフィール誘導・予約）】

■ 放置リスクネタ（積極活用）
- 肩こり放置 → 自律神経の乱れ・うつ症状・睡眠障害
- 頭痛放置 → 慢性化・薬が効かない体質・集中力低下
- 猫背放置 → 内臓圧迫・呼吸が浅い・老け顔
- 眼精疲労放置 → めまい・吐き気スパイラル
- 睡眠不足放置 → ホルモンバランス崩壊・免疫力低下

■ CTA配置ルール（小川教材7:2:1比率）
- 宣伝型（PASONA法則・放置リスク→CTA）: {count//5}本
- 情報提供型（PRBREP・等価交換・メカニズム解説）: {count//5*3}本
- 日常/共感型（ストーリー・問いかけ・ワーママ共感）: {count//5}本
- 肩こり予約URL（{count//15}本のみ）: {prof['cta_katakori']}
- 頭痛予約URL（{count//15}本のみ）: {prof['cta_zutsuu']}
"""
    else:
        import datetime as _dt
        day_of_year = _dt.date.today().timetuple().tm_yday
        metrics = prof["metrics_themes"]
        today_metric = metrics[day_of_year % len(metrics)]
        daily_struct = "\n".join(f"  {s}" for s in prof["daily_structure"])
        cta_rule = f"""
【masahide_takahashi_ 投稿ルール（必須）】

■ 今日の数値テーマ: {today_metric}

■ 1日5本の構成（この順番で生成）
{daily_struct}

■ 絶対ルール
- PASONA・PREP構成・具体的な数字データを積極活用する
- 数字は具体的に（例: エンゲージメント率1.2%→問題あり、3%以上→良好）
- 同じ内容・構成の投稿を重複して作らない

■ CTA配置ルール（小川教材7:2:1比率）
- 情報提供型（数値解説・改善方法・等価交換）: {count//5*3}本
- 日常/共感型（ストーリー・問いかけ）: {count//5}本
- 宣伝型（PASONA+LINE誘導）: {count//5}本
- URLあり: {count//10}本のみ → LINE URL: {prof['line_url']}
- さりげないLINE誘導（URLなし）: {count//5}本
"""

    prompt = f"""Threads投稿を{count}本生成してください。

{cta_rule}

【投稿の多様性（小川教材フレームワーク活用）】
- 等価交換型（情報提供→信頼・煽りなし）: {count//10}本
- PRBREP型（結論→根拠→反論→事例→結論）: {count//10}本
- PASONA型（問題→共感→解決→提案→行動）: {count//10}本
- 放置リスク型（問題提起→末路→改善→CTA）: {count//8}本（truthのみ）
- 小川式（謎かけ→箇条書き→断言）: {count//6}本
- 堀式（逆説→数字実績→口語断定）: {count//7}本
- 共感・引用系: {count//6}本
- 教育・知識系: {count//6}本
- 日常ストーリー・2Qテクニック: {count//8}本
- 残りはフック・逆説・問いかけ等

各投稿は150〜400文字程度（[COMMENT]分割を含む場合は全体で500文字まで可）。
改行を活かしてテンポよく。
"""

    raw = _call_claude(prompt, system)
    if raw:
        posts = [p.strip() for p in raw.split("---") if p.strip()]
        if len(posts) >= count // 2:
            print(f"  [GeneratorAgent] Claude API で{len(posts)}本生成", file=sys.stderr)
            return posts[:count]

    # フォールバック: テンプレートベース
    print("  [GeneratorAgent] テンプレートにフォールバック", file=sys.stderr)
    return _fallback_generate(acct, count)


def _fallback_generate(acct: str, count: int) -> list[str]:
    """generate_remix.py を呼ぶフォールバック"""
    import subprocess
    result = subprocess.run(
        [sys.executable, str(BASE / "generate_remix.py"), acct, "--count", str(count)],
        capture_output=True, text=True, cwd=str(BASE)
    )
    # ログファイルから読み込む
    log_file = BASE / f"log_{acct}.jsonl"
    if log_file.exists():
        entries = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
        today_entries = [e for e in entries if e.get("date") == TODAY]
        if today_entries:
            return today_entries[-1].get("posts", [])
    return []


if __name__ == "__main__":
    acct = sys.argv[1] if len(sys.argv) > 1 else "truth"
    posts = generate(acct)
    for i, p in enumerate(posts, 1):
        print(f"\n--- {i} ---\n{p}")
