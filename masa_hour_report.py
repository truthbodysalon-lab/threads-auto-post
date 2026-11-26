"""@masahide_takahashi_ のよく見られる時間帯を、実際の閲覧数から毎回数え直す（2026-11-26 masa指示）。

なぜ: 本人指示「（夜22〜23時台がよく見られ、次が朝8〜10時台です）→投稿の反応パターンを見ながら随時更新して」。
従来は手順書に時間帯を書き込んだままで、実測と食い違っても誰も直さなかった（11/26時点の直近200本では
22時台が平均51回で1位・10時台が約41回で2位、8〜9時台は14回以下）。

入力: past_posts_masa.json（insights.py が Threads API から取った直近の投稿＝timestamp と views）
出力: masa_best_hours.json（時間帯ごとの本数・平均・中央値と、よく見られる時間帯の上位）
使い方: insights.py が masa の更新のたびに呼ぶ。手で確かめる: python3 masa_hour_report.py
ヒーローの執筆・Grok Bot の Buffer 枠・手順書は、時間帯の数字をこのファイルから読む（数字を手で書き写さない）。
外部への通信はしない。
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).parent
SRC = BASE / "past_posts_masa.json"
OUT = BASE / "masa_best_hours.json"
JST = timezone(timedelta(hours=9))
MIN_POSTS = 5   # 本数が少ない時間帯は順位に入れない（1本だけ伸びた時間帯を1位にしない）
TOP_N = 3


def build(posts: list) -> dict:
    by_hour: dict[int, list[int]] = {}
    for p in posts:
        ts = p.get("timestamp") or ""
        text = p.get("text") or ""
        if not ts or not text or "テスト投稿" in text:
            continue
        try:
            t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").astimezone(JST)
        except ValueError:
            continue
        by_hour.setdefault(t.hour, []).append(int(p.get("views") or 0))
    hours = {}
    for h in sorted(by_hour):
        v = by_hour[h]
        hours[f"{h:02d}"] = {"posts": len(v), "avg_views": round(sum(v) / len(v), 1),
                             "median_views": statistics.median(v)}
    ranked = sorted((h for h in hours if hours[h]["posts"] >= MIN_POSTS),
                    key=lambda h: (hours[h]["avg_views"], hours[h]["median_views"]), reverse=True)
    n = sum(len(v) for v in by_hour.values())
    return {
        "account": "masa",
        "updated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
        "source": "past_posts_masa.json（Threads APIの直近の投稿）",
        "posts_counted": n,
        "min_posts_per_hour": MIN_POSTS,
        "best_hours": [f"{h}時台" for h in ranked[:TOP_N]],
        "weak_hours": [f"{h}時台" for h in ranked[-TOP_N:]] if len(ranked) > TOP_N * 2 else [],
        "hours": hours,
        "note": "平均閲覧数の多い順（本数5本以上の時間帯だけ）。masa本人の指示で、時間帯のルールはこのファイルの実測に合わせて随時更新する。",
    }


def run() -> dict:
    try:
        posts = json.loads(SRC.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[masa_hour_report] 読めないため更新しない: {type(e).__name__}")
        return {}
    if not isinstance(posts, list) or not posts:
        print("[masa_hour_report] 投稿が0件のため更新しない（前回の結果を残す）")
        return {}
    result = build(posts)
    if result["posts_counted"] < 30:
        print(f"[masa_hour_report] 数えられた投稿が{result['posts_counted']}本と少ないため更新しない")
        return {}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[masa_hour_report] よく見られる時間帯: {'・'.join(result['best_hours'])}（{result['posts_counted']}本）→ {OUT.name}")
    return result


if __name__ == "__main__":
    run()
