#!/usr/bin/env python3
"""アメブロ(整体・まぁ mr-perfect-no41)のジャンル順位を公開ページから記録する「クラウド保険」。
Mac主(launchd com.mt112.ameba-rank 10:05/22:05)が当日成功していれば中央ディスパッチャが起動しない。
Macが止まっていた時だけ動く。記録: ameba_rank/rank_history_cloud.csv（公開ページの順位のみ・個人情報なし）。
同じ(日付,枠)で成功行があれば何もしない。取得失敗は note 付きの行を残して正常終了（次の枠・再実行で成功行を追加できる）。
通知: 月曜午前枠の成功時のみ、クラウド記録の直近7日をDiscordへ1通（Macが落ちていた週の代わり）。LINEは使わない。
環境変数: DRY_RUN=1（記録・通知をしない）, DISCORD_WEBHOOK_URL"""
import csv, json, os, re, sys, urllib.request
from datetime import datetime, timedelta, timezone

BLOG = "mr-perfect-no41"
JST = timezone(timedelta(hours=9))
CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ameba_rank", "rank_history_cloud.csv")
FIELDS = ["date", "slot", "time", "genre", "genre_rank", "genre_rank_before", "total_rank", "note"]


def fetch():
    req = urllib.request.Request(f"https://ameblo.jp/{BLOG}/", headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    g = re.search(r'"blog_genre":\{"daily_rank":(\d+),"daily_rank_before":(\d+),"genre_code":"[^"]*","genre_title":"([^"]+)"', html)
    t = re.search(r'"daily_total_rank":(\d+)', html)
    if not g:
        raise RuntimeError("順位の欄が見つからない")
    return {"genre_rank": int(g.group(1)), "genre_rank_before": int(g.group(2)), "genre": g.group(3),
            "total_rank": int(t.group(1)) if t else ""}


def load():
    if not os.path.exists(CSV):
        return []
    return list(csv.DictReader(open(CSV, encoding="utf-8")))


def weekly(rows):
    by = {r["date"]: r for r in rows if r["genre_rank"]}
    days = sorted(by)[-7:]
    if not days:
        return None
    first, last = by[days[0]], by[days[-1]]
    diff = int(first["genre_rank"]) - int(last["genre_rank"])
    word = f"{diff}位アップ" if diff > 0 else (f"{-diff}位ダウン" if diff < 0 else "変わらず")
    trend = " → ".join(f"{d[5:]} {by[d]['genre_rank']}位" for d in days)
    return (f"📈 アメブロ順位（{last['genre']}）週次（Macが止まっていたためクラウド記録）: いま {last['genre_rank']}位（記録期間で{word}）\n{trend}")


def discord(msg):
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        print("DISCORD_WEBHOOK_URL なし: 通知スキップ")
        return
    req = urllib.request.Request(url, data=json.dumps({"content": msg}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "ameba-rank-cloud"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        print(f"Discord失敗: {type(e).__name__}")


def main():
    dry = os.environ.get("DRY_RUN") == "1"
    now = datetime.now(JST)
    slot = "am" if now.hour < 16 else "pm"
    date = f"{now:%Y-%m-%d}"
    rows = load()
    if any(r["date"] == date and r["slot"] == slot and r["genre_rank"] for r in rows):
        print(f"{date} {slot}: 記録済み → 何もしない")
        return 0
    row = {"date": date, "slot": slot, "time": f"{now:%H:%M}", "genre": "", "genre_rank": "",
           "genre_rank_before": "", "total_rank": "", "note": ""}
    try:
        row.update(fetch())
    except Exception as e:
        row["note"] = f"取得失敗: {type(e).__name__}"
    print(json.dumps(row, ensure_ascii=False))
    if dry:
        print("DRY_RUN: 記録・通知しない")
        return 0 if not row["note"] else 0
    os.makedirs(os.path.dirname(CSV), exist_ok=True)
    new = not os.path.exists(CSV)
    with open(CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    if row["genre_rank"] and now.weekday() == 0 and slot == "am":
        msg = weekly(rows + [row])
        if msg:
            discord(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
