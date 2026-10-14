#!/usr/bin/env python3
"""HPB（ホットペッパービューティー）の空き状況を取得し、告知画像を描く（2026-10-14 masa指示）。

「HPBの空き枠を確認して、2つの整体アカウントで Instagram は週1回ずつ違う曜日、
Threads は週2回ずつ違う曜日に、空き状況を画像にして告知する」ための共通部品。
同じファイルを ig-autopost-cloud と threads-auto-post の両リポジトリに置いている（片方を直したらもう片方へコピーする）。

- 取得: HPBの公開予約ページ（ログイン不要）。クーポン選択ページでセッションcookieを得てから
  日時選択ページを開く。ブラウザ不要＝GitHub Actionsで動く。
- 描画: Pillowで1080x1350の画像（画像の「生成」ではなく文字と図形の合成）。
- 取得に失敗したら例外を投げる（推測の空き状況は絶対に出さない）。

CLI（確認用）:
  python3 hpb_availability.py --out /tmp/hpb.png --account truth
"""
import argparse
import datetime as dt
import http.cookiejar
import json
import os
import re
import urllib.request

STORE_ID = "H000596246"
RESERVE_URL = f"https://beauty.hotpepper.jp/CSP/kr/reserve/?storeId={STORE_ID}"  # 予約導線（クーポン一覧）
BASE_URL = "https://beauty.hotpepper.jp/CSP/kr/reserve/"
# 空き状況を見るクーポン（新規の主力・90分）。月替わりで名前は変わるがIDは同じ。
# 先頭から順に試し、見つからなければ新規クーポン（CT02）の先頭を使う。
PREFERRED_COUPONS = ["CP00000011024165", "CP00000008167524"]
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
TIMEOUT = 30
JST = dt.timezone(dt.timedelta(hours=9))
WEEKDAYS = "月火水木金土日"

ACCOUNTS = {
    "truth": {"handle": "@truth_body_salon", "name": "整体院トゥルース"},
    "nagaoka": {"handle": "@truth_nagaoka", "name": "整体院トゥルース"},
}


# ---------------- 取得 ----------------
def _opener():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", UA), ("Accept-Language", "ja,en;q=0.8")]
    return op


def _get(op, url, referer=None):
    req = urllib.request.Request(url)
    if referer:
        req.add_header("Referer", referer)
    with op.open(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def list_coupons(top_html):
    """クーポン選択ページから [(couponId, 種別CT02/CT03, 名前, 価格)]"""
    out = []
    for block in re.split(r'<table summary="summary"', top_html)[1:]:
        cid = re.search(r"couponId=(CP\d+)&amp;add=0", block)
        name = re.search(r'couponMenuName[^>]*>([^<]+)', block)
        kind = re.search(r"couponLabel(CT\d+)", block)
        price = re.search(r'couponMenuPrice">([^<]+)', block)
        if cid and name:
            out.append((cid.group(1), kind.group(1) if kind else "", name.group(1).strip(),
                        price.group(1).strip() if price else ""))
    return out


def _parse_week(html):
    """日時選択ページ1週分 → {date: [開始可能時刻 'HH:MM', ...]}"""
    head = re.search(r'class="innerTable nowrap jscInnerTable"(.*?)</table>', html, re.S)
    if not head:
        raise RuntimeError("HPB日時選択ページの形式が想定外（ヘッダ表なし）")
    dates = []
    for m in re.finditer(r"\w{3} (\w{3}) (\d{1,2}) 00:00:00 JST (\d{4})", head.group(1)):
        d = dt.datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y").date()
        if not dates or dates[-1] != d:
            dates.append(d)
    if not dates:  # 通常の表示形式: 月セル(colspan=日数)＋日セル「14<br/>(水)」
        months = []
        for m in re.finditer(r'<th[^>]*colspan="(\d+)"[^>]*class="monthCell"[^>]*>\s*(\d{4})年(\d{1,2})月', head.group(1)):
            months += [(int(m.group(2)), int(m.group(3)))] * int(m.group(1))
        days = [int(x) for x in re.findall(r"<th[^>]*>\s*(\d{1,2})<br\s*/?>\s*\(.\)\s*</th>", head.group(1))]
        if not months or len(months) != len(days):
            raise RuntimeError(f"HPB日時選択ページの日付を読めなかった（月{len(months)}・日{len(days)}）")
        dates = [dt.date(y, mo, d) for (y, mo), d in zip(months, days)]
    tabs = re.findall(r'<table[^>]*class="moreInnerTable[^"]*"[^>]*>(.*?)</table>', html, re.S)
    if len(tabs) < len(dates) + 1:
        raise RuntimeError(f"HPB日時選択ページの形式が想定外（列{len(tabs)}・日付{len(dates)}）")
    times = [re.sub(r"<[^>]+>|\s", "", c).replace("：", ":")
             for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tabs[0], re.S)]
    times = [t for t in times if re.fullmatch(r"\d{1,2}:\d{2}", t)]
    result = {}
    for d, tab in zip(dates, tabs[1:1 + len(dates)]):
        cells = [re.sub(r"<[^>]+>|\s", "", c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tab, re.S)]
        free = [t for t, c in zip(times, cells) if c and not re.search(r"[×✕]", c) and c != "-"]
        result[d] = free
    if not result or not times:
        raise RuntimeError("HPB空き状況を1日も読めなかった")
    return result


def fetch_availability(days=7, start=None):
    """今日（JST）以降の days 日分の空き状況。戻り値: (coupon_name, {date: [時刻...]}, 取得時刻)"""
    op = _opener()
    top_url = f"{BASE_URL}?storeId={STORE_ID}"
    top = _get(op, top_url)
    coupons = list_coupons(top)
    ids = {c[0]: c for c in coupons}
    pick = next((ids[c] for c in PREFERRED_COUPONS if c in ids), None) or \
        next((c for c in coupons if c[1] == "CT02" and c[3] not in ("¥0", "")), None)
    if not pick:
        raise RuntimeError("新規クーポンが見つからない（HPBページの形式変更の可能性）")
    html = _get(op, f"{BASE_URL}afterCoupon?storeId={STORE_ID}&couponId={pick[0]}&add=0", referer=top_url)
    if "ご来店日時選択" not in html:
        raise RuntimeError("HPB日時選択ページを開けなかった（予約エラー）")
    avail = _parse_week(html)
    start = start or dt.datetime.now(JST).date()
    end = start + dt.timedelta(days=days - 1)
    week = 1
    while max(avail) < end and week <= 3:
        nxt = _get(op, f"{BASE_URL}schedule?storeId={STORE_ID}&week={week}", referer=top_url)
        avail.update(_parse_week(nxt))
        week += 1
    sel = {d: v for d, v in sorted(avail.items()) if start <= d <= end}
    return pick[2], sel, dt.datetime.now(JST)


# ---------------- 要約 ----------------
def _m(t):
    h, m = map(int, t.split(":"))
    return h * 60 + m


def ranges(times):
    """['11:00','11:10',...] → ['11:00〜11:40', '15:00', ...]（10分刻みの連続をまとめる）"""
    out = []
    for t in times:
        if out and _m(t) - _m(out[-1][1]) <= 10:
            out[-1][1] = t
        else:
            out.append([t, t])
    return [a if a == b else f"{a}〜{b}" for a, b in out]


COURSE_MIN = 90  # 空き状況を見るクーポンの施術時間（分）


def slots_left(times, course=COURSE_MIN):
    """あと何件入れられるか。開始可能時刻の連続区間ごとに、施術時間ぶん間をあけて詰めた件数の合計。
    例: 11:00〜14:10 が開始可能（＝11:00〜15:40が空き）なら 11:00/12:30/14:00 の3件"""
    total, run = 0, []
    for t in times + ["99:99"]:
        if run and (t == "99:99" or _m(t) - _m(run[-1]) > 10):
            total += (_m(run[-1]) - _m(run[0])) // course + 1
            run = []
        if t != "99:99":
            run.append(t)
    return total


def level(left):
    if left == 0:
        return "×", "空きなし"
    if left <= 2:
        return "△", f"残りわずか あと{left}件"
    return "◎", f"あと{left}件"


def summary(avail):
    rows = []
    for d, times in avail.items():
        left = slots_left(times)
        mark, label = level(left)
        rows.append({"date": d, "label": f"{d.month}/{d.day}（{WEEKDAYS[d.weekday()]}）",
                     "mark": mark, "status": label, "ranges": ranges(times), "n": left,
                     "weekend": d.weekday() >= 5})
    return rows


# ---------------- 描画 ----------------
FONT_CANDIDATES = {
    "bold": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
             "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
             "/System/Library/Fonts/Hiragino Sans GB.ttc"],
    "reg": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc"],
}


def _font(kind, size):
    from PIL import ImageFont
    for p in FONT_CANDIDATES[kind]:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise RuntimeError("日本語フォントが見つからない")


C = {"bg": "#FBF6EE", "card": "#FFFFFF", "brown": "#5B3A23", "ink": "#2B2118", "mut": "#8A7A6A",
     "line": "#E9DFD2", "ok": "#2E8B57", "few": "#E08A00", "ng": "#B0A89E", "acc": "#F0A020", "sat": "#2F6DB5", "sun": "#C8403A"}


def render(rows, out_path, account="truth", coupon_name="", fetched_at=None, channel="instagram"):
    """1080x1350の告知画像を書き出す"""
    from PIL import Image, ImageDraw
    W, H = 1080, 1350
    im = Image.new("RGB", (W, H), C["bg"])
    dr = ImageDraw.Draw(im)
    acc = ACCOUNTS.get(account, ACCOUNTS["truth"])
    # ヘッダ帯
    dr.rectangle([0, 0, W, 250], fill=C["brown"])
    dr.text((W // 2, 70), acc["name"], font=_font("reg", 34), fill="#F3E6D3", anchor="mm")
    dr.text((W // 2, 145), "今週の空き状況", font=_font("bold", 76), fill="#FFFFFF", anchor="mm")
    span = f"{rows[0]['label']} 〜 {rows[-1]['label']}" if rows else ""
    dr.text((W // 2, 215), span, font=_font("bold", 34), fill=C["acc"], anchor="mm")
    # 日別カード
    top, gap = 280, 12
    ch = int((1120 - top - gap * (len(rows) - 1)) / max(len(rows), 1))
    for i, r in enumerate(rows):
        y = top + i * (ch + gap)
        dr.rounded_rectangle([48, y, W - 48, y + ch], radius=18, fill=C["card"], outline=C["line"], width=2)
        col = C["sat"] if r["date"].weekday() == 5 else C["sun"] if r["date"].weekday() == 6 else C["ink"]
        dr.text((80, y + ch // 2), r["label"], font=_font("bold", 40), fill=col, anchor="lm")
        mc = {"◎": C["ok"], "△": C["few"], "×": C["ng"]}[r["mark"]]
        dr.text((360, y + ch // 2), r["mark"], font=_font("bold", 56), fill=mc, anchor="mm")
        if r["n"] == 0:
            dr.text((420, y + ch // 2), "空きなし", font=_font("bold", 34), fill=C["ng"], anchor="lm")
        else:
            txt = "　".join(r["ranges"][:3]) + ("　ほか" if len(r["ranges"]) > 3 else "")
            dr.text((420, y + ch // 2 - 18), r["status"], font=_font("bold", 40), fill=mc, anchor="lm")
            dr.text((420, y + ch // 2 + 26), txt, font=_font("reg", 24 if len(txt) <= 30 else 21), fill=C["mut"], anchor="lm")
    # フッター
    dr.text((W // 2, 1170), "あと○件＝90分コースで予約できる人数　小さい数字は開始できる時間",
            font=_font("reg", 26), fill=C["mut"], anchor="mm")
    dr.rounded_rectangle([120, 1205, W - 120, 1285], radius=40, fill=C["acc"])
    cta = "ご予約はプロフィールのリンク（ホットペッパー）から" if channel == "instagram" else "ご予約はホットペッパーから（リンクはコメント欄）"
    dr.text((W // 2, 1245), cta, font=_font("bold", 32), fill="#FFFFFF", anchor="mm")
    when = (fetched_at or dt.datetime.now(JST)).strftime("%-m/%-d %H:%M")
    dr.text((W // 2, 1318), f"※{when}時点のネット予約の空きです。最新の空きは予約ページでご確認ください",
            font=_font("reg", 22), fill=C["mut"], anchor="mm")
    im.save(out_path, "JPEG" if out_path.lower().endswith((".jpg", ".jpeg")) else "PNG", quality=92)
    return out_path


def caption_text(rows, channel="instagram", account="truth"):
    """投稿本文（効果保証の言い回しは使わない）"""
    open_days = [r for r in rows if r["n"] > 0]
    weekend = [r for r in open_days if r["weekend"]]
    lines = ["【今週の空き状況】", ""]
    for r in rows:
        lines.append(f"{r['mark']} {r['label']} " + ("空きなし" if r["n"] == 0 else f"あと{r['n']}件（{'・'.join(r['ranges'][:2])}）"))
    lines.append("")
    if weekend:
        lines.append(f"土日も {'・'.join(r['label'] for r in weekend)} に空きがあります。")
    lines.append("肩こり・頭痛でつらい日が続いている方は、空いているうちにどうぞ。")
    if channel == "instagram":
        lines += ["", "ご予約はプロフィールのリンク（ホットペッパー）から。",
                  "※ネット予約の空き状況です。電話・ご来店の予約で埋まることがあります。",
                  "", "#長岡市整体 #長岡市 #肩こり #頭痛 #整体院トゥルース"]
    else:
        lines += ["", "ご予約はホットペッパーから（リンクはコメント欄）。"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--account", default="truth", choices=list(ACCOUNTS))
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--channel", default="instagram", choices=["instagram", "threads"])
    a = ap.parse_args()
    name, avail, at = fetch_availability(a.days)
    rows = summary(avail)
    render(rows, a.out, a.account, name, at, a.channel)
    print(json.dumps({"coupon": name, "rows": [{k: (str(v) if k == "date" else v) for k, v in r.items()} for r in rows]},
                     ensure_ascii=False, indent=1))
    print(caption_text(rows, a.channel, a.account))


if __name__ == "__main__":
    main()
