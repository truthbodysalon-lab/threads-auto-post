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
    },
    "masa": {
        "name": "@masahide_takahashi_",
        "persona": "インスタ集客の先生・髙橋雅英",
        "target": "集客・売上に悩む店舗オーナー・個人事業主",
        "tone": "断言調。口語。数字実績を使う。1行フック→箇条書き→断言の小川式か、逆説1行→数字実績→口語断定の堀式。LINEへ誘導（URL付きは30本中3本のみ）。",
        "topics": ["Instagram集客", "リール動画", "LINE集客", "導線設計", "フォロワーより売上", "プロフィール改善"],
        "line_url": "https://lin.ee/8PsIHHC",
        "profile_file": "SNS・Threads/masahide_takahashi_/まとめ（プロフィール・インプ）.md",
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
    prof = ACCOUNT_PROFILES[acct]
    profile_text = _read_myfile(prof["profile_file"])
    top_posts    = _load_top_posts(acct)
    feedback     = _load_feedback()

    system = f"""あなたはSNS投稿の専門家です。
以下のアカウント情報に基づき、Threads投稿を{count}本生成してください。

アカウント: {prof['name']}（{prof['persona']}）
ターゲット: {prof['target']}
文体ルール: {prof['tone']}

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
    if acct == "truth":
        cta_rule = f"""
【CTA配置ルール】
- 30本中2本のみ肩こり予約URL: {prof['cta_katakori']}
- 30本中2本のみ頭痛予約URL: {prof['cta_zutsuu']}
- CTAは「▶ 予約はこちら\\n[URL]」の形式で本文末に追加
- 残り26本はURLなし
"""
    else:
        cta_rule = f"""
【CTA配置ルール】
- 30本中3本のみLINE URL: {prof['line_url']}
- 5本はさりげないLINE誘導（URLなし、「LINEで配信しています」程度）
- 残り22本はURLなし
"""

    prompt = f"""Threads投稿を{count}本生成してください。

{cta_rule}

【投稿の多様性】
- 小川式（謎かけ→箇条書き→断言）: {count//6}本
- 堀式（逆説→数字実績→口語断定）: {count//6}本
- 共感・引用系: {count//5}本
- 教育・知識系: {count//5}本
- 日常ストーリー系: {count//7}本
- 残りはその他（1行フック、逆説等）

各投稿は150〜350文字程度。改行を活かしてテンポよく。
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
