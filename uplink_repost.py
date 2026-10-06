#!/usr/bin/env python3
"""UP LINK(uplink0117)の閲覧200以上の投稿を、本文そのままmasaへ「追加枠」で出すためのプール管理（2026-10-06）。

- プール: uplink_repost_pool.json（git追跡）。status = queued / posted / skipped
- 1投稿はmasaへ1回だけ（同じ文面の再投稿はしない）。本文は無加工（翻案・URL移動・ブリッジコメントなし）
- 通常の50本/日枠・DAILY_CAPには数えない別カウンタ（台帳 log_masa_posted.jsonl の kind="uplink_repost" で識別）
- 判定ルールは「出せない投稿をプールに入れない」ために refresh 側と投稿直前の両方で同じ judge() を使う
"""
from __future__ import annotations

import difflib
import json
import os
import re
from datetime import date
from pathlib import Path

BASE = Path(__file__).parent
POOL = BASE / "uplink_repost_pool.json"
LEDGER = BASE / "log_masa_posted.jsonl"
KIND = "uplink_repost"

DAILY_N = int(os.environ.get("UPLINK_REPOST_DAILY", "6"))   # 1日の追加本数（通常50本とは別枠）
START_HOUR = int(os.environ.get("UPLINK_REPOST_START", "7"))
FULL_HOUR = int(os.environ.get("UPLINK_REPOST_FULL", "21"))  # この時刻までに当日分を出し切るペース
MIN_VIEWS = 200
MAX_FAIL = 3

# 出せない投稿の判定（2026-10-06分析で確定したルール。masa既存ルールを保守的に包含）
_RULES = [
    ("URL/外部リンク", re.compile(r"https?://|lin\.ee|line\.me|note\.com|square\.link|Square|公式LINE|LINE|ライン", re.I)),
    ("面談/相談/決済/価格", re.compile(r"面談|相談会|個別相談|無料相談|決済|申し?込|お支払|円|割引|価格|料金|税込|モニター|キャンペーン|定員|枠|満席|募集")),
    ("導線/固有名", re.compile(r"uplink|UP ?LINK|アップリンク|プロフ(ィール)?(から|の|へ|に)|リンク|DM|コメント欄|無料(プレゼント|配布|診断)|配布|受け取|サロン経営者向け|フォロー(して|を)|保存(して|を)|@\w+")),
    ("店舗名/所在地", re.compile(r"トゥルース|truth|長岡|新潟|整体院|住所|アクセス|〒")),
    ("日付/時事/季節", re.compile(r"\d{1,2}月|\d{1,2}/\d{1,2}|明日|明後日|昨日|今週|来週|今月|来月|今年|来年|昨年|去年|今日(は|の|だけ)|本日|\d{4}年|年末|年始|正月|GW|ゴールデン|お盆|夏休み|クリスマス|バレンタイン|猛暑|梅雨|コロナ|[0-9０-９]+日目|只今|いま(だけ|から)|今(だけ|から)")),
    ("予告型", re.compile(r"お伝えします|について解説します|についてお話しします|をご紹介します|明日(は|から|も)?.*(します|投稿|発信|お話)|次回")),
]


def _norm_first(text: str) -> str:
    from duplicate_guard import normalize_text
    return normalize_text(text).split("\n")[0].strip()


def judge(text: str) -> list:
    """出せない理由のリスト（空なら出せる）。generate_remix/inspector のmasaルールも通す。"""
    t = (text or "").strip()
    r = []
    if not t:
        return ["空"]
    if "[COMMENT]" in t:
        r.append("COMMENT区切り")
    if len(t) > 500:
        r.append("500字超")
    for name, rx in _RULES:
        if rx.search(t):
            r.append(name)
    try:
        import generate_remix as g
        if g._is_masa_sales_ng(t):
            r.append("masa_sales_ng")
        if g._is_masa_yokokoku_ng(t):
            r.append("masa_yokokoku_ng")
        if g._is_ng(t):
            r.append("_is_ng")
    except Exception as e:  # ルール判定不能は出さない側に倒す
        r.append(f"generate_remix読込不可:{type(e).__name__}")
    try:
        import inspector
        ok, rs = inspector.inspect_post(t, "masa", log=False)
        if not ok:
            r.append("inspector:" + "|".join(str(x)[:30] for x in rs))
    except Exception as e:
        r.append(f"inspector読込不可:{type(e).__name__}")
    return r


def masa_posted_firstlines() -> set:
    """masaが過去に出した1行目の集合（台帳・past_posts_masa・共有ガード）"""
    out = set()
    for f in (LEDGER, BASE / "shared_posted_guard.jsonl"):
        if not f.exists():
            continue
        for l in f.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(l)
            except Exception:
                continue
            if f.name.startswith("shared") and "masa" not in str(e.get("account", e.get("acct", "masa"))):
                continue
            if e.get("text"):
                out.add(_norm_first(e["text"]))
    pp = BASE / "past_posts_masa.json"
    if pp.exists():
        try:
            for p in json.loads(pp.read_text(encoding="utf-8")):
                if p.get("text"):
                    out.add(_norm_first(p["text"]))
        except Exception:
            pass
    return out


# ── プール入出力（1件1行で保存＝CI(状態更新)とMac(追加)の同時pushをgitが行単位でマージできる）──
def load_pool() -> list:
    if not POOL.exists():
        return []
    return json.loads(POOL.read_text(encoding="utf-8"))


def save_pool(items: list):
    body = ",\n".join(json.dumps(i, ensure_ascii=False) for i in items)
    tmp = POOL.with_suffix(".tmp")
    tmp.write_text("[\n" + body + "\n]\n", encoding="utf-8")
    tmp.replace(POOL)


def queued_count(items: list | None = None) -> int:
    return sum(1 for i in (items if items is not None else load_pool()) if i.get("status") == "queued")


def next_item(items: list):
    """views上位のqueued。失敗回数が少ないものを優先（同じものを選び続けない）。"""
    q = [i for i in items if i.get("status") == "queued"]
    if not q:
        return None
    return sorted(q, key=lambda i: (i.get("fail", 0), -i.get("views", 0)))[0]


# ── カウント・ペース ─────────────────────────────────
def count_on(day: str) -> int:
    """台帳の kind=uplink_repost の指定日(YYYY-MM-DD)件数"""
    if not LEDGER.exists():
        return 0
    n = 0
    for l in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(l)
        except Exception:
            continue
        if e.get("kind") == KIND and e.get("date") == day:
            n += 1
    return n


def count_today() -> int:
    return count_on(date.today().strftime("%Y-%m-%d"))


def want_cumulative(hour: int) -> int:
    """今あるべき追加投稿の累計（START_HOURから均等・FULL_HOURで満額。先読みなし）"""
    if hour < START_HOUR:
        return 0
    frac = min(1.0, (hour - START_HOUR + 1) / max(1, FULL_HOUR - START_HOUR + 1))
    return int(DAILY_N * frac + 0.5) if frac < 1 else DAILY_N


def near_duplicate(first: str, others: list, thr: float = 0.8) -> bool:
    return any(difflib.SequenceMatcher(None, first, o).ratio() >= thr for o in others)
