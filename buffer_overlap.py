#!/usr/bin/env python3
"""masa: Buffer（別系統・1日10枠）の前後で、同じテーマ・同じ型を出さないための判定（2026-10-23 masa承認）。

仕組み（投稿本数は減らさない＝候補の「並べ替え」だけ）:
  1. buffer_slots_masa.json の枠時刻 ±window_minutes 内にいる時だけ有効（枠外は一切何もしない）
  2. Buffer側の直近テーマを集める
       - Threads API: 同アカウントの直近投稿のうち、こちらの台帳(log_masa_posted.jsonl /
         shared_posted_guard.jsonl)に無い本体投稿＝Buffer投稿とみなす（返信・URL付き・短文は除外）
       - threads2-post-log.md（置いてあれば。無ければ影響なし）: 投稿済みの本文・型、最新の「次の型」
  3. 候補ごとに重なりを採点: テーマ重複=2（テーマ語の共通 or 文字bigram類似）、型の一致=1、なし=0
  4. get_next_post は点数の低い候補から選ぶ。全候補が重なる時だけ従来どおり先頭を出す（本数を削らない）
失敗・ファイル欠落・API失敗は全て「影響なし」（従来挙動）で続行する。HTTPは全て timeout 付き。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).parent
CONFIG_FILE = BASE / "buffer_slots_masa.json"
JST = timezone(timedelta(hours=9))
HTTP_TIMEOUT = 15          # 1リクエストの上限秒
API_TOTAL_DEADLINE = 40    # API取得全体の上限秒
CACHE_TTL = 300            # 同一プロセス内でAPI結果を使い回す秒数

# ── テーマ語（店舗・院の集客で、同じ話題と判断する語のまとまり） ─────────
THEME_TAGS = {
    "口コミ": r"口コミ|クチコミ|レビュー|★|星[1-5１-５]",
    "Googleマップ": r"Google|グーグル|マップ|MEO|ビジネスプロフィール",
    "インスタ・SNS": r"インスタ|Instagram|リール|ストーリーズ|SNS|フォロワー|ハッシュタグ|TikTok|Threads|スレッズ",
    "LINE": r"LINE|ライン|公式アカウント|友だち追加",
    "リピート": r"リピート|再来店|2回目|二回目|また来|常連|戻ってく|いつもどおり|いつも通り|再来",
    "新規": r"新規|初めてのお客|初来店|初回",
    "メニュー・看板": r"メニュー|看板|POP|ポップ|一品|おしながき",
    "写真": r"写真|撮る|撮影|撮って|画像",
    "予約・電話": r"予約|キャンセル|無断|電話",
    "価格・単価": r"値上げ|価格|値段|客単価|単価|料金|割引|安売り|値引",
    "スタッフ": r"スタッフ|新人|教育|採用|アルバイト|バイト|人手|従業員|店長",
    "AI": r"AI|ＡＩ|ChatGPT|チャットGPT|生成AI|Gemini|Claude|人工知能",
    "締め・事務": r"締め|レジ|発注|仕込み|日報|事務作業|売上のメモ|帳簿|経理",
    "売上・数字": r"売上|月商|年商|利益|数字",
    "チラシ・紙": r"チラシ|ポスティング|ハガキ|はがき|ショップカード|名刺",
    "接客・声かけ": r"接客|声かけ|声掛け|笑顔|あいさつ|挨拶|お見送り",
    "お礼・メッセージ": r"お礼|お知らせ|定型文|メッセージ|返信",
    "プロフィール": r"プロフィール|自己紹介|名前欄",
}
_THEME_RE = {k: re.compile(v, re.IGNORECASE) for k, v in THEME_TAGS.items()}

_TYPE_RULES = [
    ("速報", re.compile(r"【速報】|速報|提供開始|正式(に)?(スタート|リリース|提供)|発表しました|リリースしました")),
    ("相談の入口", re.compile(r"コメントで|コメント欄で|教えてください|教えてもらえ|聞かせて|どれですか|ありますか[？?]|どうしていますか|していますか[？?]")),
    ("実践Tips", re.compile(r"【\d+ステップ】|①|おすすめです|コツは|やり方|この順で")),
]
_KNOWN_TYPES = ("速報", "相談の入口", "実践Tips", "意見", "つぶやき", "実体験")


def load_config(path: Path = CONFIG_FILE) -> dict:
    """設定を読む。無い・壊れている → enabled=False（従来挙動）。"""
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            return {"enabled": False}
        slots = []
        for s in cfg.get("slots", []):
            hh, mm = str(s).split(":")
            slots.append((int(hh), int(mm)))
        cfg["_slots"] = slots
        cfg.setdefault("window_minutes", 30)
        cfg.setdefault("lookback_hours", 4)
        cfg.setdefault("max_recent", 3)
        cfg.setdefault("similarity_threshold", 0.3)
        cfg.setdefault("max_scan", 60)
        cfg.setdefault("use_api", True)
        cfg.setdefault("log_paths", [])
        cfg.setdefault("account", "masa")
        return cfg
    except Exception:
        return {"enabled": False}


def active_slot(now: datetime, cfg: dict):
    """now(JST) が枠 ±window 内なら "HH:MM" を返す。枠外は None。"""
    if not cfg.get("enabled"):
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=JST)
    now = now.astimezone(JST)
    win = timedelta(minutes=int(cfg.get("window_minutes", 30)))
    for hh, mm in cfg.get("_slots", []):
        for d in (-1, 0, 1):   # 日付またぎも安全側に扱う
            st = now.replace(hour=hh, minute=mm, second=0, microsecond=0) + timedelta(days=d)
            if abs(now - st) <= win:
                return f"{hh:02d}:{mm:02d}"
    return None


# ── テキスト判定（純粋関数） ─────────────────────────────
def theme_tags(text: str) -> set:
    t = (text or "")[:400]
    return {k for k, r in _THEME_RE.items() if r.search(t)}


def classify_type(text: str):
    t = (text or "")[:400]
    for name, r in _TYPE_RULES:
        if r.search(t):
            return name
    return None


def _bigrams(text: str) -> set:
    t = re.sub(r"[\s、。！？!?「」『』（）()・…\-ー〜~,.:：;；\[\]【】①-⑳→👇]+", "", (text or "")[:160])
    return {t[i:i + 2] for i in range(len(t) - 1)}


def similarity(a: str, b: str) -> float:
    x, y = _bigrams(a), _bigrams(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


def overlap_score(text: str, buffer_items: list, sim_thr: float = 0.3):
    """(点数, 理由)。2=テーマ重複 / 1=型の一致 / 0=重なりなし。"""
    best, why = 0, ""
    ct = theme_tags(text)
    ctype = classify_type(text)
    for it in buffer_items:
        btext = it.get("text") or ""
        if btext:
            common = ct & (it.get("tags") or theme_tags(btext))
            if common:
                return 2, f"テーマ重複({'/'.join(sorted(common))})"
            sim = similarity(text, btext)
            if sim >= sim_thr:
                return 2, f"本文類似({sim:.2f})"
        btype = it.get("type")
        if btype and ctype and btype == ctype and best < 1:
            best, why = 1, f"型一致({btype})"
    return best, why


def reorder(eligible: list, buffer_items: list, sim_thr: float = 0.3):
    """eligible: [(text, index)] を重なりの少ない順に並べ替える（同点は元の順＝ヒーロー優先等を保つ）。
    戻り値: (並べ替え後の候補, 最小点数, 先頭候補の理由)。"""
    scored = []
    for pos, (text, idx) in enumerate(eligible):
        sc, why = overlap_score(text, buffer_items, sim_thr)
        scored.append((sc, pos, text, idx, why))
    scored.sort(key=lambda r: (r[0], r[1]))
    if not scored:
        return [], 0, ""
    min_sc = scored[0][0]
    return [(r[2], r[3]) for r in scored], min_sc, scored[0][4]


def best_group(eligible: list, buffer_items: list, sim_thr: float = 0.3) -> list:
    """最小点数の候補だけを元の順で返す（URL連続回避と組み合わせる時用）。"""
    if not eligible:
        return []
    sc = [overlap_score(t, buffer_items, sim_thr)[0] for t, _ in eligible]
    m = min(sc)
    return [c for c, s in zip(eligible, sc) if s == m]


# ── threads2-post-log.md（任意・防御的に読む） ───────────────
_HEAD_RE = re.compile(r"^##\s*(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})\s*JST(.*)$")


def _norm_type(s: str):
    if not s:
        return None
    m = re.search(r"型[:：]\s*([^）)｜|／/]+)", s)
    if m:
        v = m.group(1).strip()
        for k in _KNOWN_TYPES:
            if k in v:
                return k
    if "速報" in s and "AI速報なし" not in s:
        return "速報"
    return None


def parse_post_log(text: str):
    """戻り値: (投稿済みエントリー[{ts,text,type,source}], 最新の「次の型」or None)。壊れた行は飛ばす。"""
    items, next_type, cur = [], None, None
    for line in (text or "").splitlines():
        m = _HEAD_RE.match(line.strip())
        if m:
            if cur and cur.get("posted"):
                items.append(cur)
            try:
                d, hh, mm, rest = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
                ts = datetime.strptime(f"{d} {hh:02d}:{mm:02d}", "%Y-%m-%d %H:%M").replace(tzinfo=JST)
                cur = {"ts": ts, "type": _norm_type(rest), "text": "", "posted": "投稿済み" in rest,
                       "source": "threads2-post-log"}
            except Exception:
                cur = None
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith(">"):
            body = s.lstrip(">").strip()
            if body:
                cur["text"] += body + "\n"
        elif s.startswith("- 次の型"):
            nt = _norm_type("型: " + s.split(":", 1)[-1].split("：", 1)[-1])
            if nt:
                next_type = nt
    if cur and cur.get("posted"):
        items.append(cur)
    for it in items:
        it.pop("posted", None)
    return items, next_type


def _read_log_files(cfg: dict):
    items, next_type = [], None
    for p in cfg.get("log_paths", []):
        try:
            path = Path(os.path.expanduser(str(p)))
            if not path.is_absolute():
                path = BASE / path
            if not path.is_file():
                continue
            its, nt = parse_post_log(path.read_text(encoding="utf-8", errors="replace"))
            items.extend(its)
            next_type = nt or next_type
        except Exception:
            continue
    return items, next_type


# ── Threads API（同アカウントの直近投稿からBuffer分を抜き出す） ─────────
def _fetch_recent_api(token: str, uid: str, since: datetime) -> list:
    """直近の本体投稿 [{ts,text,is_reply}]。失敗時は例外（呼び出し側で握る）。全HTTPに timeout。"""
    deadline = time.time() + API_TOTAL_DEADLINE
    q = urllib.parse.urlencode({"fields": "id,text,timestamp,is_reply", "limit": 100, "access_token": token})
    url = f"https://graph.threads.net/v1.0/{uid}/threads?{q}"
    has_reply_field = True
    out = []
    for page in range(4):
        if time.time() > deadline:
            break
        try:
            with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
                d = json.loads(r.read())
        except urllib.error.HTTPError:
            if page == 0 and has_reply_field:   # is_reply の権限が無いトークン → 項目を外して再試行
                has_reply_field = False
                url = url.replace("id%2Ctext%2Ctimestamp%2Cis_reply", "id%2Ctext%2Ctimestamp")
                continue
            raise
        oldest = None
        for p in d.get("data", []):
            try:
                ts = datetime.strptime(p.get("timestamp", "").replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
            except Exception:
                continue
            oldest = ts if oldest is None or ts < oldest else oldest
            if ts < since:
                continue
            out.append({"ts": ts.astimezone(JST), "text": p.get("text") or "",
                        "is_reply": p.get("is_reply") if has_reply_field else None})
        nxt = (d.get("paging") or {}).get("next")
        if not nxt or (oldest is not None and oldest < since):
            break
        url = nxt
    return out


def _own_firstlines(posted_file: Path, guard_file: Path, acct: str, keyfn, since: datetime) -> set:
    """こちら（auto_post系統）が出した投稿の1文目キー（直近3日）。"""
    keys = set()
    day_min = (since - timedelta(days=2)).strftime("%Y-%m-%d")
    for f, is_guard in ((posted_file, False), (guard_file, True)):
        try:
            if not f or not Path(f).exists():
                continue
            for line in Path(f).read_text(encoding="utf-8", errors="replace").splitlines()[-3000:]:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if (e.get("date") or "")[:10] < day_min:
                    continue
                if is_guard:
                    if e.get("acct") != acct:
                        continue
                    for k in (e.get("first_line"), e.get("norm")):
                        if k:
                            keys.add(keyfn(k))
                else:
                    keys.add(keyfn(e.get("text", "")))
        except Exception:
            continue
    keys.discard("")
    return keys


def _looks_like_buffer_body(text: str, is_reply) -> bool:
    if is_reply:
        return False
    t = (text or "").strip()
    if len(t) < 60:                    # ブリッジ・CTA等の短文コメント
        return False
    if re.search(r"https?://|lin\.ee", t):
        return False
    if t.startswith(("①", "▶", "受け取り", "詳しくは")):   # 自系統の[COMMENT]分割部分
        return False
    return True


_CACHE: dict = {}


def buffer_items(acct: str, now: datetime, cfg: dict, *, token: str = "", uid: str = "",
                 posted_file: Path = None, guard_file: Path = None, keyfn=None,
                 fetch=None, log=None) -> list:
    """Buffer側の直近テーマ一覧。どの取得経路が失敗しても、取れた分だけ返す（空でも可）。"""
    since = now - timedelta(hours=float(cfg.get("lookback_hours", 4)))
    keyfn = keyfn or (lambda s: (s or "").split("\n")[0].strip()[:40])
    items = []
    # 1) threads2-post-log.md（あれば）
    file_items, next_type = _read_log_files(cfg)
    items.extend(i for i in file_items if since <= i["ts"] <= now + timedelta(minutes=5))
    # 2) Threads API
    if cfg.get("use_api", True) and token and uid:
        ck = (acct, int(now.timestamp() // CACHE_TTL))
        api = _CACHE.get(ck)
        if api is None:
            try:
                api = (fetch or _fetch_recent_api)(token, uid, since)
            except Exception as e:
                api = []
                if log:
                    log(f"[Buffer回避] API取得失敗（ファイル分のみで判定）: {type(e).__name__}: {e}")
            _CACHE.clear()
            _CACHE[ck] = api
        own = _own_firstlines(posted_file, guard_file, acct, keyfn, now)
        file_keys = {keyfn(i["text"]) for i in items}
        for p in api:
            if not (since <= p["ts"] <= now + timedelta(minutes=5)):
                continue
            if not _looks_like_buffer_body(p["text"], p.get("is_reply")):
                continue
            k = keyfn(p["text"])
            if not k or k in own or k in file_keys:
                continue
            items.append({"ts": p["ts"], "text": p["text"], "type": classify_type(p["text"]),
                          "source": "threads-api"})
    items.sort(key=lambda i: i["ts"], reverse=True)
    items = items[: int(cfg.get("max_recent", 3))]
    for i in items:
        i["tags"] = theme_tags(i["text"])
    # 3) 次のBuffer枠の型（ログの最新「次の型」。本文はまだ無いので型だけ）
    if next_type:
        items.append({"ts": now, "text": "", "type": next_type, "tags": set(), "source": "next_type"})
    return items


def describe(items: list) -> str:
    out = []
    for i in items:
        if i.get("text"):
            out.append(f"{i['ts'].strftime('%H:%M')} {'/'.join(sorted(i.get('tags') or [])) or '-'}"
                       f"[{i.get('type') or '-'}] {i['text'][:18].replace(chr(10), ' ')}")
        else:
            out.append(f"次枠の型[{i.get('type')}]")
    return " | ".join(out)
