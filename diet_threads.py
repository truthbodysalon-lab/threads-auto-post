#!/usr/bin/env python3
"""ダイエットThreads(@truth_nagaoka_daietto) API自動投稿 (自己完結モジュール)

- Grok Bot(ブラウザ)の投稿と並走する「追加」投稿。1日5枠: 9:00 / 11:00 / 13:00 / 15:00 / 20:00 (JST)
- 起動は中央ディスパッチャ(schedule_table.json)が workflow_dispatch で行う。ここにcronは無い
- トークン THREADS_ACCESS_TOKEN_DIET が空なら何もせず exit 0(認可前)
- ルール正本: diet_threads_rules.md / 事実の根拠: diet_threads_facts.md (生成プロンプトに全文を入れる)

使い方:
  python3 diet_threads.py --slot 09:00          # 本番(枠を指定)
  DRY_RUN=1 python3 diet_threads.py --slot 15:00  # Threadsへ一切アクセスせず生成だけ表示
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).parent
ENV_FILE = BASE / ".env"
LOG_FILE = BASE / "log_diet_posted.jsonl"
RULES_FILE = BASE / "diet_threads_rules.md"
FACTS_FILE = BASE / "diet_threads_facts.md"
GROK_RULES_FILE = BASE / "diet_threads_rules_grok.md"   # Grokのrules.md原文の抜き書き(~/.claude/scripts/diet_threads_rules_sync.py が自動更新)
PAUSE_FILE = BASE / "diet_threads_pause.json"           # 警告・制限を検知したら作る。削除するまで全枠スキップ
BASE_URL = "https://graph.threads.net/v1.0"
JST = timezone(timedelta(hours=9))

# 2026-11-02 院長指示「1日合計30投稿以上」→ Grok 10本＋API 21本。間隔30分・6時〜24時（深夜は出さない）。Grokの毎時ちょうど(7,8,10,12,14,16,17,19,21,22時)の間に置く
SLOTS = ["06:30", "07:30", "08:30", "09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30", "16:30", "17:30", "18:00", "18:30", "19:30", "20:00", "20:30", "21:30", "22:30", "23:00", "23:30"]
DAILY_CAP = 10           # API投稿の1日上限（2026-11-11 院長指示「Threadsの投稿は1日10投稿を目標にして」）
DAILY_TOTAL_TARGET = 10  # Grok Bot の分も合わせた1日の目標本数。枠はそのまま21か所で、ペースより先行している枠は見送る
PACE_START_MIN, PACE_END_MIN = 6 * 60, 24 * 60   # この時間帯に目標本数を均等に配る
CTA_DAILY_MAX = 2        # Grok分を含む1日の公式LINE案内の上限
CTA_SLOTS = {"15:30", "20:00"}   # 案内を付けてよい枠(反応が取れる夕方〜夜。Grokの案内枠を食わないよう控えめ)
MIN_GAP_MIN = 30         # 直前の投稿(誰のものでも)からの最低間隔（2026-11-02 院長指示で45→30分）
MAX_WAIT_MIN = 10        # 間隔が足りない時に待つ上限(超えるならこの枠は見送り)
SIM_THRESHOLD = 0.5      # 文字バイグラムJaccardの重複しきい値
MAX_LEN = 500
MAX_ATTEMPTS = 3
MODELS = ["claude-haiku-4-5-20251001", "claude-sonnet-4-5"]  # 先頭から試し、失敗したら次へ

CTA_TEXT = "気になる方は公式LINE（@886oidmv）で『相談』と送ってください。院長が直接お返事します"
CTA_TEXT_RESERVE = "ご予約は公式LINE（@886oidmv）で『予約』と送ってください。ホットペッパーの初回クーポンの予約ページが届きます"   # 2026-11-10 院長指示(HPBクーポン誘導)
CTA_TEXTS = [CTA_TEXT, CTA_TEXT_RESERVE]   # 案内を付ける回は交互に使う
CTA_MARK = "@886oidmv"


def strip_cta(text: str) -> str:
    """案内の固定文(どちらでも)を取り除く"""
    for c in CTA_TEXTS:
        text = text.replace(c, "")
    return text


def cta_in(text: str) -> str | None:
    """本文に含まれる案内の固定文(なければNone)"""
    for c in CTA_TEXTS:
        if c in text:
            return c
    return None


def next_cta_text(entries: list[dict], recent: list[dict] | None = None) -> str:
    """直近の自分のログで前回使った案内と逆の方。ログに案内がなければ相談版(自分のログに無ければ取得した直近投稿も見る)"""
    for src in (list(reversed(entries)), sorted_recent(recent or [])):
        for e in src:
            c = cta_in(e.get("text") or "")
            if c:
                return CTA_TEXT_RESERVE if c == CTA_TEXT else CTA_TEXT
    return CTA_TEXT

# 2026-11-07 院長指示「Threadsの投稿に関しては文章がながい。太りやすい食材や食べ方、痩せやすい方法を端的にまとめて投稿とかツリー投稿を活用して」
TYPES = ["太りやすい食材", "太りやすい食べ方", "やせやすい方法・続けやすい食べ方", "よくある勘違い", "お悩み別の食材"]
FORMATS = {
    "short": "短文(1〜2行・50字以内。要点を1つだけ端的に)",
    "tree": "ツリー(1つ目は40字以内のフック1〜2行。続きのツリーを2〜3個、各60字以内で1つずつ端的に。最後のツリーはまとめか答えやすい問いかけ)",
}
SHORT_MAX = 60          # 短文の本文の上限（案内の固定文は除く）
TREE_HEAD_MAX = 40      # ツリー1つ目の上限
TREE_REPLY_MAX = 60     # ツリーの続き1つの上限（案内の固定文は除く）
TREE_REPLIES = (2, 5)   # ツリーの続きの個数（長い続きを文の切れ目で分けた結果、5つまでは可）
TOPICS_FILE = BASE / "diet_threads_topics.json"   # Instagramと同じ31件の題材（根拠は講座ノート）
TOPIC_COOLDOWN = 12     # 直近この本数で使った題材は選ばない
WEIGHTS_FILE = BASE / "diet_threads_weights.json"   # diet_threads_learn.py が毎朝書く（閲覧データから学んだ形式・テーマの重みと、よく読まれた投稿）
TOPIC_TAG = "ダイエット"   # フォロワー以外にも見つけてもらうためのトピックタグ（2026-11-07）


def load_weights() -> dict:
    try:
        return json.loads(WEIGHTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── ログ・時刻ユーティリティ ─────────────────────────

def log(msg: str) -> None:
    print(f"[diet {datetime.now(JST):%H:%M:%S}] {msg}", flush=True)


def load_env() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def parse_ts(s: str) -> datetime | None:
    """Threadsのtimestamp('2026-11-02T03:00:00+0000')をJSTのawareにする。失敗はNone"""
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(s, fmt).astimezone(JST)
        except ValueError:
            pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(JST)
    except Exception:
        return None


# ── 自分のログ(log_diet_posted.jsonl) ───────────────

def read_log(path: Path = None) -> list[dict]:
    path = path or LOG_FILE
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def append_log(entry: dict, path: Path = None) -> None:
    path = path or LOG_FILE
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def today_entries(entries: list[dict], now: datetime) -> list[dict]:
    d = now.strftime("%Y-%m-%d")
    out = []
    for e in entries:
        ts = parse_ts(e.get("ts", ""))
        if ts and ts.strftime("%Y-%m-%d") == d:
            out.append(e)
    return out


def slot_guard(entries: list[dict], slot: str, now: datetime) -> str | None:
    """見送り理由を返す。None なら投稿してよい"""
    todays = today_entries(entries, now)
    if any(e.get("slot") == slot for e in todays):
        return f"枠 {slot} は本日投稿済み"
    if len(todays) >= DAILY_CAP:
        return f"本日のAPI投稿が上限({DAILY_CAP})に達している"
    return None


def todays_total(recent: list[dict], now: datetime) -> int:
    """今日（JST）にこのアカウントから出た投稿の本数（Grok Bot の分も含む。ツリーの続きは数えない）"""
    day = now.astimezone(JST).date()
    n = 0
    for p in recent:
        t = parse_ts(p.get("timestamp", ""))
        if t and t.astimezone(JST).date() == day:
            n += 1
    return n


def pace_guard(recent: list[dict], now: datetime) -> str | None:
    """1日の合計目標（DAILY_TOTAL_TARGET）に対して、今の時刻までに出ているべき本数より多く出ていれば見送り理由を返す。
    Grok Bot が動いている日は Grok の分でペースが埋まるので API はほとんど出さず、止まっている日は API が埋める。"""
    n = todays_total(recent, now)
    if n >= DAILY_TOTAL_TARGET:
        return f"本日の合計が目標の{DAILY_TOTAL_TARGET}本に達している（{n}本・Grok分を含む）"
    m = now.astimezone(JST).hour * 60 + now.astimezone(JST).minute
    frac = min(1.0, max(0.0, (m - PACE_START_MIN) / (PACE_END_MIN - PACE_START_MIN)))
    expected = max(1, -(-int(DAILY_TOTAL_TARGET * frac * 100) // 100))   # 切り上げ
    if n >= expected:
        return f"ペースより先行（今日{n}本・この時刻までの目安{expected}本／1日{DAILY_TOTAL_TARGET}本）"
    return None


# ── 警告・制限での即停止(Grok規定「警告・制限が出たら即停止して本人に報告」) ──

PAUSE_CODES = {4, 10, 17, 32, 190, 200, 368, 613}
PAUSE_SUBCODES = {2207051}
# 「limit」単独は入れない（文字数の上限エラーで止まってしまうため）。利用制限を表す言い方だけ
PAUSE_WORDS = ("rate limit", "limit reached", "request limit", "posting limit", "spam", "restricted",
               "temporarily blocked", "policy",
               "oauthexception", "制限", "一時的")


def is_pause_error(msg: str) -> bool:
    """APIエラー文が警告・制限・規約・認証系なら True。5xx・通信エラーは False(一時的な失敗として従来どおり見送り)"""
    m = str(msg or "")
    low = m.lower()
    mt = re.match(r"HTTP (\d{3})", m)
    if mt and mt.group(1) == "429":
        return True
    for c in re.findall(r"\"(?:error_)?(?:sub)?code\"\s*:\s*(\d+)", m):
        if int(c) in PAUSE_CODES or int(c) in PAUSE_SUBCODES:
            return True
    if any(w in low for w in PAUSE_WORDS):
        return True
    return False


def _summarize_error(msg: str) -> str:
    """停止理由用の要約。トークン・URLを含めない"""
    t = re.sub(r"https?://\S+", "[URL]", str(msg or ""))
    t = re.sub(r"(access_token|token)[=:]\s*\S+", r"\1=[省略]", t, flags=re.I)
    t = re.sub(r"\bTHAA\w{10,}|\bEAA\w{10,}", "[省略]", t)
    return t.replace("\n", " ")[:200]


def write_pause(reason: str, slot: str, now: datetime, path: Path | None = None) -> None:
    path = path or PAUSE_FILE
    path.write_text(json.dumps({"since": now.isoformat(timespec="seconds"), "reason": _summarize_error(reason),
                                "slot": slot}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pause_and_notify(reason: str, slot: str, now: datetime, path: Path | None = None) -> None:
    """停止ファイルを作り、Discordへ1回だけ知らせる(停止中は次回以降run()冒頭でスキップするので通知は繰り返されない)"""
    write_pause(reason, slot, now, path)
    log(f"警告・制限を検知したため停止: {_summarize_error(reason)}")
    notify("🛑 ダイエットThreads API投稿を停止しました\n"
           f"枠: {slot} / 理由: {_summarize_error(reason)[:150]}\n"
           "再開するには diet_threads_pause.json を削除してpushするか、Claude Codeに「ダイエットThreadsを再開して」と伝えてください。")


# ── Threads API ───────────────────────────────────

def _http(url: str, data: dict | None = None, method: str = "GET") -> dict:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        # URLにトークンが入るので、エラー本文だけを出す(URLは出さない)
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"通信エラー: {getattr(e, 'reason', 'unknown')}") from None


def fetch_recent(token: str, limit: int = 50) -> list[dict]:
    """GET /me/threads。自分(=Grokの投稿も含む)の直近投稿 [{id,text,timestamp}]"""
    q = urllib.parse.urlencode({"fields": "id,text,timestamp", "limit": limit, "access_token": token})
    d = _http(f"{BASE_URL}/me/threads?{q}")
    return d.get("data") or []


def publish_text(token: str, user_id: str, text: str, reply_to: str = "", topic_tag: str = "") -> str:
    """2段階投稿(コンテナ作成→公開)。公開後の投稿IDを返す。再試行しない(二重投稿防止)。reply_to があればその投稿へのツリー"""
    uid = user_id or "me"
    data = {"media_type": "TEXT", "text": text, "access_token": token}
    if reply_to:
        data["reply_to_id"] = reply_to
    elif topic_tag:
        data["topic_tag"] = topic_tag
    try:
        c = _http(f"{BASE_URL}/{uid}/threads", data, "POST")
    except Exception as e:
        # トピックタグが原因でコンテナ作成だけ失敗した時は、タグなしでもう一度だけ作る（公開前なので二重投稿にはならない）
        if "topic_tag" not in data or is_pause_error(e):
            raise
        data.pop("topic_tag")
        c = _http(f"{BASE_URL}/{uid}/threads", data, "POST")
    cid = c["id"]
    time.sleep(2)
    p = _http(f"{BASE_URL}/{uid}/threads_publish", {"creation_id": cid, "access_token": token}, "POST")
    return p["id"]


def reply_exists(token: str, post_id: str) -> bool:
    """投稿後の確認: ツリーの続きが実際に見えるか（2026-11 Grokのブラウザ投稿でツリーの2つ目が消えた件の見張り）"""
    time.sleep(5)
    q = urllib.parse.urlencode({"fields": "id,text", "access_token": token})
    try:
        with urllib.request.urlopen(f"{BASE_URL}/{post_id}?{q}", timeout=30) as r:
            return bool(json.loads(r.read()).get("id"))
    except Exception:
        return False


# ── 重複・CTA判定 ──────────────────────────────────

def bigrams(s: str) -> set:
    s = re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else ({s} if s else set())


def jaccard(a: str, b: str) -> float:
    A, B = bigrams(a), bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def max_similarity(text: str, others: list[str]) -> tuple[float, str]:
    best, best_t = 0.0, ""
    for o in others:
        j = jaccard(text, o)
        if j > best:
            best, best_t = j, o
    return best, best_t


def sorted_recent(recent: list[dict]) -> list[dict]:
    """新しい順。timestampが読めないものは末尾"""
    def key(p):
        t = parse_ts(p.get("timestamp", ""))
        return t or datetime.min.replace(tzinfo=JST)
    return sorted(recent, key=key, reverse=True)


def cta_status(recent: list[dict], now: datetime) -> tuple[bool, str]:
    """(案内を入れてよいか, 理由)。本日のJST日付で @886oidmv を含む投稿が2本以上、または直近の投稿に入っていれば不可"""
    rs = sorted_recent(recent)
    today = now.strftime("%Y-%m-%d")
    n = 0
    for p in rs:
        t = parse_ts(p.get("timestamp", ""))
        if t and t.strftime("%Y-%m-%d") == today and CTA_MARK in (p.get("text") or ""):
            n += 1
    if n >= CTA_DAILY_MAX:
        return False, f"本日すでに案内つき{n}本(上限{CTA_DAILY_MAX})"
    if rs and CTA_MARK in (rs[0].get("text") or ""):
        return False, "直前の投稿が案内つき(連続禁止)"
    return True, f"本日の案内つき{n}本"


def gap_minutes(recent: list[dict], now: datetime) -> float | None:
    """最新投稿からの経過分。投稿が読めなければNone"""
    for p in sorted_recent(recent):
        t = parse_ts(p.get("timestamp", ""))
        if t:
            return (now - t).total_seconds() / 60
    return None


def classify_format(text: str) -> str:
    return "short" if len(text or "") <= 60 else "tree"


def choose_format_and_type(recent: list[dict], slot: str, rng: random.Random) -> tuple[str, str]:
    """短文とツリーを半々・同形式3連続禁止（2026-11-07 院長指示で長文・中くらいはやめた）"""
    w = dict(load_weights().get("fmt") or {"short": 0.5, "tree": 0.5})   # 閲覧データで毎朝更新（無ければ半々）
    w = {k: float(w.get(k, 0.5)) for k in FORMATS}
    last = [classify_format(p.get("text") or "") for p in sorted_recent(recent)[:2]]
    if len(last) == 2 and last[0] == last[1]:
        w[last[0]] = 0.0
    fmts, ws = zip(*w.items())
    fmt = rng.choices(fmts, weights=ws)[0] if sum(ws) > 0 else rng.choice(fmts)
    tw = load_weights().get("type") or {}
    typ = rng.choices(TYPES, weights=[float(tw.get(t, 1.0)) for t in TYPES])[0]
    return fmt, typ


# ── 検品 ──────────────────────────────────────────

_NG_WORDS = [
    "栄養講座", "講座", "講義", "ケイ先生", "学んだ", "学びました", "習いました", "習った", "教わ", "勉強して",
    "今すぐ", "限定", "先着", "今だけ", "無料", "特典", "キャンペーン", "クーポン", "割引", "初回", "料金", "価格", "コース", "回数券",
    "お客様の声", "お客さまの声", "口コミ", "ビフォー", "アフター", "患者", "お客様が", "お客さまが", "来院",
    "治る", "治す", "治療", "完治", "脂肪燃焼", "断食", "ファスティング", "絶食",
    "ダイエット専門", "ボディコーディネート", "見てきた", "診てきた", "経験上", "これまでの経験", "実際に変わった",
]
_NG_REGEX = [
    (r"第\s*\d+\s*回", "第◯回"),
    (r"[#＃]", "ハッシュタグ"),
    (r"https?://|www\.|\.com|\.jp|lin\.ee|line\.me", "URL"),
    (r"\d+(?:\.\d+)?\s*(?:kg|ｋｇ|㎏|キロ|cm|㎝|%|％|パーセント)", "体重・サイズ・割合の数値"),
    (r"\d+\s*(?:か月|ヶ月|カ月|ケ月|週間|日)\s*で", "期間の約束"),
    (r"[一二三四五六七八九十]+\s*(?:か月|ヶ月|週間|日)\s*で", "期間の約束"),
    (r"\d[\d,]*\s*円", "金額"),
    (r"(?:必ず|絶対|確実に|間違いなく)", "断定"),
    (r"(?:脂肪が|脂肪を)(?:落ち|燃え|燃や)", "脂肪が落ちる系の断定"),
    (r"代謝が(?:上が|アップ|あが)", "代謝が上がる断定"),
    (r"(?:やせ|痩せ)(?:ます|られます|るはず)", "やせる断定"),
    (r"(?:私|わたし|あたし|俺)(?!たち)", "一人称が「僕」でない"),
    (r"1日\s*\d*\s*[LlＬℓ]", "水分量の指導"),
    (r"(?:こまめに|たくさん|多め(?:に)?)(?:水|お水)|水分を(?:とっ|摂|こまめ|多め|控え|意識)|水を(?:飲|こまめ)", "水分のとり方の指導"),
    # 2026-11-02 院長確認「3点直して」①: 根拠ファイルにない体の仕組みの話（内臓・骨のずれ等）は書かない
    (r"内臓|骨盤が(?:開|ずれ|歪|ゆが|閉)|骨(?:の|が)(?:ずれ|ゆがみ|歪み)|ゆがみ(?:が|の)原因|歪み(?:が|の)原因|リンパ|老廃物|むくみが(?:取れ|消え|なくな)|血流が(?:良くな|よくな|改善)|体質が変わ", "根拠のない体の仕組みの話"),
    # ③: 専門用語は読者に伝わらないので使わない（言い換える）
    (r"エネルギー密度|カロリー密度|インスリン|PFC|微量栄養素|黄体期|卵胞期|糖新生|熱産生|DIT|ミトコンドリア|ホルモンバランス|ホメオスタシス|飢餓適応|セレン|有酸素運動", "専門用語"),
]

# ②: 決まり文句の繰り返しを避ける（Grokの投稿も含めた直近の投稿と比べる）
STOCK_PHRASES = ["本当に多いです", "意外と多いです", "多いんです", "多いです", "という方", "そんな方", "思っていませんか",
                 "ありませんか", "大丈夫です", "一緒に整理", "一緒に見て", "いろんな角度"]


def stock_phrase_errors(text: str, recent_texts: list[str]) -> list[str]:
    """直近3本のどれかに出ている決まり文句、または直近10本で2回以上出ている決まり文句を使っていたらNG"""
    errs = []
    last3, last10 = recent_texts[:3], recent_texts[:10]
    for p in STOCK_PHRASES:
        if p in text and (any(p in t for t in last3) or sum(p in t for t in last10) >= 2):
            errs.append(f"決まり文句「{p}」が直近の投稿と重なる")
    return errs
_NG_NFKC = [(re.compile(p), name) for p, name in _NG_REGEX]


def inspect_text(text: str, has_cta: bool) -> list[str]:
    """NGの一覧(空なら合格)"""
    errs: list[str] = []
    if not text or not text.strip():
        return ["本文が空"]
    t = unicodedata.normalize("NFKC", text)
    if len(text) > MAX_LEN:
        errs.append(f"{MAX_LEN}字超過({len(text)}字)")
    if len(text.strip()) < 10:
        errs.append("短すぎる")
    # 公式LINE案内は固定文のみ許可(NFKCでは全角@が崩れるので原文で判定)
    body = strip_cta(text) if has_cta else text
    nb = unicodedata.normalize("NFKC", body)
    for w in _NG_WORDS:
        if w in nb:
            errs.append(f"NGワード「{w}」")
    for rx, name in _NG_NFKC:
        if rx.search(nb):
            errs.append(f"NG表現: {name}")
    if has_cta:
        if cta_in(text) is None:
            errs.append("案内ありなのに固定文が一字一句一致していない")
        elif not any(text.rstrip().endswith(c) for c in CTA_TEXTS):
            errs.append("案内文が最後の一文になっていない")
    else:
        if "886oidmv" in t or "公式LINE" in t:
            errs.append("案内フラグなしなのにLINE案内が入っている")
    return errs


# ── 題材 ──────────────────────────────────────────

def load_topics(path: Path | None = None) -> list[dict]:
    try:
        return [t for t in json.loads((path or TOPICS_FILE).read_text(encoding="utf-8")).get("topics", []) if t.get("id")]
    except Exception:
        return []


def pick_topic(entries: list[dict], rng: random.Random, topics: list[dict] | None = None) -> dict | None:
    """直近 TOPIC_COOLDOWN 本で使っていない題材を1つ（無ければ None＝題材なしで書く）"""
    topics = load_topics() if topics is None else topics
    used = {e.get("topic_id") for e in entries[-TOPIC_COOLDOWN:] if e.get("topic_id")}
    cand = [t for t in topics if t["id"] not in used] or topics
    return rng.choice(cand) if cand else None


def examples_block() -> str:
    """閲覧データでよく読まれた自分の投稿（書き方の参考。内容・言い回しはコピーしない）"""
    ex = [e for e in (load_weights().get("examples") or []) if e.get("text")][:3]
    if not ex:
        return ""
    return ("【最近よく読まれた投稿（書き出し・テンポ・具体性の参考。内容や言い回しはコピーしない。古いルールで書かれた語は使わない）】\n"
            + "\n".join(f"- {e['views']}回: {e['text'][:120].replace(chr(10), ' ')}" for e in ex) + "\n")


def topic_block(topic: dict | None) -> str:
    if not topic:
        return ""
    pts = topic.get("pts") or {}
    lines = [f"今回の題材: {topic.get('theme')}（{topic.get('axis')}）", f"よくある思い込み・悩み: {topic.get('hook')}",
             f"根拠の説明: {topic.get('cause')}", f"できること: {topic.get('solution')}"]
    if topic.get("foods"):
        lines.append("関係する食材: " + "、".join(topic["foods"]))
    for k, lab in (("pain", "悩みの要点"), ("cause", "理由の要点"), ("solution", "できることの要点")):
        if pts.get(k):
            lines.append(f"{lab}: " + " / ".join(pts[k]))
    return ("\n【今回の題材（この中身だけを使う。ここにない事実・数字・食材は足さない）】\n" + "\n".join(lines) + "\n")


# ── 生成 ──────────────────────────────────────────

def build_prompts(fmt: str, typ: str, slot: str, allow_cta: bool, recent_texts: list[str], feedback: str = "",
                  topic: dict | None = None, cta_text: str | None = None) -> tuple[str, str]:
    rules = RULES_FILE.read_text(encoding="utf-8") if RULES_FILE.exists() else ""
    facts = FACTS_FILE.read_text(encoding="utf-8") if FACTS_FILE.exists() else ""
    grok = GROK_RULES_FILE.read_text(encoding="utf-8") if GROK_RULES_FILE.exists() else ""
    grok_block = (
        "\n\n【Grokのrules.md 原文抜き書き】(上の【運用ルール】の要約と食い違う場合は、こちらの新しい日付の記述が優先。"
        "ただし【運用ルール】のAPI投稿向け追加規則(短文とツリー・案内の数え方など)は有効)\n" + grok
    ) if grok.strip() else ""
    system = (
        "あなたは長岡市の整体院トゥルースの院長として、Threadsのダイエットアカウントの投稿を1本書く。"
        "以下の【運用ルール】と【栄養の事実ファイル】だけに従う。"
        "栄養・ダイエットの主張は【栄養の事実ファイル】にあることだけを使い、ここにない事実・数字・具体的な食品名や手順・体験談(「見てきた」「経験上」など)は書かない。"
        "長さは依頼文の【形式】の指定が最優先（運用ルールやGrokの原文に長文・中くらいの記述があっても使わない。2026-11-07 院長指示「文章がながい」）。"
        "出力はJSONオブジェクト1つだけ。\n\n【運用ルール】\n" + rules + grok_block + "\n\n【栄養の事実ファイル】\n" + facts
    )
    avoid = "\n".join(f"- {t.strip()[:80].replace(chr(10), ' ')}" for t in recent_texts[:20]) or "(なし)"
    cta_line = (
        f"公式LINE案内は付けても付けなくてもよい。付ける場合は最後の一文(ツリーなら最後のツリーの最後)を次の固定文にし、has_ctaをtrueにする(固定文は一字一句変えない。他の案内文は使わない): {cta_text or CTA_TEXT}"
        if allow_cta else "公式LINE案内は今回は入れない(has_ctaはfalse。@886oidmv や「公式LINE」の語を本文に書かない)。"
    )
    user = (
        f"投稿枠: {slot} (JST)\nテーマ: {typ}\n形式: {FORMATS[fmt]}\n{cta_line}\n" + topic_block(topic) + "\n"
        "直近の投稿(Grok Botの投稿を含む)。次のテーマ・書き出しと被らないこと:\n" + avoid + "\n\n"
        "1文目は「問いかけ(〇〇、1日に何個食べていますか？ など)」か「身近な食べ物の意外な事実」で始める。「」で始めない(閲覧データで「」始まりは平均の半分以下)。"
        "具体的な食べ物の名前を必ず1つ以上出す。最後は答えやすい問いかけで終えると反応が増えやすい。ハッシュタグ・URL・絵文字の乱用はしない。\n"
        + examples_block() +
        "文章は短く端的に。前置き・言い訳・まわりくどい言い方をしない。1つのツリーには要点を1つだけ書く。"
        + ("短文は2文まで・全体で30〜50字。書いたら字数を数え、超えていたら削ってから出す。\n" if fmt == "short" else
           "1つ目は15〜35字、続きは各25〜50字（1文か2文）。書いたら字数を数え、超えていたら削ってから出す。\n")
        + "体の仕組み(内臓の位置・骨のずれ・ゆがみ・リンパ・血流など)を原因や効果として書かない。姿勢・骨盤の話は「姿勢や体の使い方のクセが関わることもある」程度にとどめる。\n"
        "専門用語(エネルギー密度・インスリン・PFC・黄体期など)は使わず、中学生にも伝わる言葉に言い換える(例: エネルギー密度の低い野菜→量のわりに軽い野菜、黄体期→生理前)。\n"
        "直近の投稿で使われた決まり文句(「本当に多いです」「という方」「ありませんか」など)は使い回さず、言い方を変える。\n"
        + (f"\n前回の出力は次の理由で不合格だった。直すこと: {feedback}\n" if feedback else "")
        + ('\n出力形式(JSONのみ): {"text": "1つ目(フック)", "replies": ["続き1", "続き2", "続き3は任意"], "type": "テーマ", "has_cta": true または false}'
           if fmt == "tree" else '\n出力形式(JSONのみ): {"text": "本文", "replies": [], "type": "テーマ", "has_cta": true または false}')
    )
    return system, user


def call_llm(system: str, user: str, temperature: float = 0.8) -> str | None:
    """既存方式(agents/generator.py)と同じくAnthropic Messages APIをurllibで叩く。モデルは順にフォールバック"""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    for model in MODELS:
        payload = json.dumps({
            "model": model, "max_tokens": 1500, "temperature": temperature,
            "system": system, "messages": [{"role": "user", "content": user}],
        }).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())["content"][0]["text"]
        except Exception as e:
            log(f"LLM失敗({model}): {type(e).__name__}")
    return None


def parse_generated(raw: str | None) -> dict | None:
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    text = (d.get("text") or "").strip()
    if not text:
        return None
    replies = [str(r).strip() for r in (d.get("replies") or []) if str(r).strip()]
    return {"text": text, "replies": replies, "type": str(d.get("type") or ""), "has_cta": bool(d.get("has_cta"))}


def full_text(post: dict) -> str:
    """類似判定・決まり文句・ログ用に、ツリー全体を1本の文にする"""
    return "\n".join([post["text"]] + list(post.get("replies") or []))


def _sentences(t: str) -> list[str]:
    return [x for x in re.findall(r"[^。！？!?]+[。！？!?]?", t) if x.strip()]


def fit_tree(post: dict) -> dict:
    """ツリーの長すぎる部分を、文の切れ目で次のツリーに分ける（言葉は変えない）。案内の固定文がある最後の部分は分けない"""
    parts = [post["text"]] + list(post["replies"])
    out = []
    for i, part in enumerate(parts):
        lim = TREE_HEAD_MAX if i == 0 else TREE_REPLY_MAX
        has_cta = cta_in(part) is not None
        if has_cta or len(part) <= lim:
            out.append(part); continue
        cur = ""
        for sen in _sentences(part):
            if cur and len(cur) + len(sen) > (TREE_HEAD_MAX if not out and not cur.startswith("\0") and i == 0 else TREE_REPLY_MAX):
                out.append(cur); cur = sen
            else:
                cur += sen
        if cur:
            out.append(cur)
    return dict(post, text=out[0], replies=out[1:])


def length_errors(post: dict, fmt: str) -> list[str]:
    """形式ごとの長さ・個数の検品（案内の固定文は字数に数えない）"""
    body = lambda t: len(strip_cta(t).strip())  # noqa: E731
    errs = []
    if fmt == "short":
        if post["replies"]:
            errs.append("短文の指定なのにツリーがある")
        if body(post["text"]) > SHORT_MAX:
            errs.append(f"短文の指定なのに{body(post['text'])}字(30〜50字目安・{SHORT_MAX}字以内)")
    else:
        if not (TREE_REPLIES[0] <= len(post["replies"]) <= TREE_REPLIES[1]):
            errs.append(f"ツリーの続きが{len(post['replies'])}個({TREE_REPLIES[0]}〜{TREE_REPLIES[1]}個)")
        if body(post["text"]) > TREE_HEAD_MAX:
            errs.append(f"ツリー1つ目が{body(post['text'])}字(15〜35字目安・{TREE_HEAD_MAX}字以内)")
        for i, r in enumerate(post["replies"], 2):
            if body(r) > TREE_REPLY_MAX:
                errs.append(f"ツリー{i}つ目が{body(r)}字(25〜50字目安・{TREE_REPLY_MAX}字以内)")
    return errs


def cta_on_own_line(post: dict) -> dict:
    """案内の固定文が前の文と同じ行に続いていたら、改行を1つ入れて読みやすくする（文言は変えない）"""
    parts = [post["text"]] + list(post.get("replies") or [])
    last = parts[-1]
    for c in CTA_TEXTS:
        if last.endswith(c) and len(last) > len(c) and not last[: -len(c)].endswith("\n"):
            parts[-1] = last[: -len(c)].rstrip() + "\n" + c
            break
    return dict(post, text=parts[0], replies=parts[1:])


def generate_post(slot: str, fmt: str, typ: str, allow_cta: bool, recent_texts: list[str], sim_pool: list[str],
                  generator=None, topic: dict | None = None, cta_text: str | None = None) -> tuple[dict | None, str]:
    """検品と類似チェックを通った {text,type,has_cta} と、失敗時の理由。generator(system,user)->raw は差し替え可"""
    generator = generator or call_llm
    feedback = ""
    sim_fail = 0
    last_reason = "生成失敗"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        system, user = build_prompts(fmt, typ, slot, allow_cta, recent_texts, feedback, topic, cta_text)
        post = parse_generated(generator(system, user))
        if not post:
            last_reason = "LLM出力を読めない/空"
            feedback = "JSONオブジェクトのみを出力すること"
            continue
        if post["has_cta"] and not allow_cta:
            last_reason = "案内不可なのに案内つき"
            feedback = "公式LINE案内は入れない"
            continue
        if fmt == "tree":
            post = fit_tree(post)
        errs = length_errors(post, fmt)
        parts = [post["text"]] + post["replies"]
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            errs += [f"{i + 1}つ目: {e}" if len(parts) > 1 else e for e in inspect_text(part, post["has_cta"] and last)]
        errs += stock_phrase_errors(full_text(post), recent_texts)
        if errs:
            last_reason = "検品NG: " + " / ".join(errs)
            feedback = "; ".join(errs)
            log(f"試行{attempt}: {last_reason}")
            if fmt == "short" and attempt >= 2 and all("短文の指定なのに" in e for e in errs):
                fmt = "tree"   # 短文に収まらない中身はツリーに分ける（2026-11-07）
                feedback = "短文に収まらなかったので、ツリー（1つ目は短いフック・続きを2〜3個）に分けて書くこと"
                log("短文に収まらないため、次はツリーで書く")
            continue
        sim, other = max_similarity(full_text(post), sim_pool)
        if sim >= SIM_THRESHOLD:
            sim_fail += 1
            last_reason = f"直近投稿と類似(Jaccard {sim:.2f})"
            log(f"試行{attempt}: {last_reason}")
            if sim_fail >= 2:   # 類似は『1回だけ作り直し』。2回目も類似ならこの枠は見送り
                return None, last_reason
            feedback = f"直近の投稿「{other[:40]}」と似すぎ。テーマと書き出しを変えること"
            continue
        post["fmt"] = fmt
        post = cta_on_own_line(post)
        return post, ""
    return None, last_reason


# ── 通知(Discordのみ) ──────────────────────────────

def notify(msg: str) -> None:
    try:
        from ops_deadman import discord_send
        discord_send("[ダイエットThreads] " + msg)
    except Exception as e:
        log(f"Discord通知失敗: {type(e).__name__}")


# ── メイン ────────────────────────────────────────

def pick_slot(arg: str | None, now: datetime) -> str:
    if arg:
        return arg
    # 指定が無ければ、現在時刻以前で最も近い枠
    cur = now.strftime("%H:%M")
    prev = [s for s in SLOTS if s <= cur]
    return prev[-1] if prev else SLOTS[0]


# ── BA事例（2026-11-06 院長指示）──────────────────────
# 数字は diet_threads_ba.json の固定文だけ（AIに書かせない）。決まった枠で every_days 日に1本。
# 固定文にも検品をかけ、数字・期間の2項目だけは許す（それ以外のNGがあれば出さない）。

BA_FILE = BASE / "diet_threads_ba.json"
BA_ALLOWED_NG = {"NG表現: 体重・サイズ・割合の数値", "NG表現: 期間の約束"}


def load_ba(path: Path | None = None) -> dict:
    try:
        return json.loads((path or BA_FILE).read_text(encoding="utf-8"))
    except Exception:
        return {}


def pick_ba(entries: list[dict], slot: str, now: datetime, cfg: dict | None = None) -> dict | None:
    """今日のこの枠をBA事例にするなら投稿(dict: key,text)、しないなら None"""
    cfg = load_ba() if cfg is None else cfg
    posts = [p for p in cfg.get("posts", []) if p.get("key") and p.get("text")]
    if not cfg.get("enabled") or not posts or slot != cfg.get("slot"):
        return None
    last = {}
    for e in entries:
        if e.get("type") == "ba":
            t = parse_ts(e.get("ts", ""))
            if t:
                last[e.get("ba_key", "")] = max(last.get(e.get("ba_key", ""), t), t)
    if last and now - max(last.values()) < timedelta(days=float(cfg.get("every_days", 3))):
        return None
    old = datetime(2000, 1, 1, tzinfo=JST)
    return min(posts, key=lambda p: (last.get(p["key"], old), posts.index(p)))


def post_ba(ba: dict, slot: str, now: datetime, dry_run: bool, token: str, uid: str, publisher,
            log_path: Path | None, pause_path: Path) -> int:
    errs = [e for e in inspect_text(ba["text"], False) if e not in BA_ALLOWED_NG]
    if errs:
        log(f"BA事例 {ba['key']} を見送り: {errs}")
        notify(f"{slot}枠のBA事例({ba['key']})を見送り: 固定文の検品NG {errs[:3]}。diet_threads_ba.json を直してください")
        return 0
    if dry_run:
        print("----- DRY_RUN BA事例 -----")
        print(f"[枠 {slot} / {ba['key']}]")
        print(ba["text"])
        print("--------------------------")
        return 0
    try:
        pid = publisher(token, uid, ba["text"])
    except Exception as e:
        log(f"投稿失敗(BA事例): {e}")
        if is_pause_error(e):
            pause_and_notify(str(e), slot, now, pause_path)
            return 0
        notify(f"{slot}枠のBA事例の投稿に失敗: {str(e)[:150]}")
        return 0
    append_log({
        "ts": now.isoformat(timespec="seconds"), "slot": slot, "id": pid, "type": "ba", "ba_key": ba["key"],
        "has_cta": False, "text_hash": hashlib.sha256(ba["text"].encode("utf-8")).hexdigest()[:16], "text": ba["text"],
    }, log_path)
    log(f"投稿完了 id={pid} BA事例={ba['key']}")
    return 0


def run(slot: str | None = None, now: datetime | None = None, dry_run: bool = False, generator=None,
        fetch=None, publisher=None, sleeper=time.sleep, rng: random.Random | None = None,
        log_path: Path | None = None, pause_path: Path | None = None, verify=None) -> int:
    """戻り値は常に0(見送りも正常終了)。失敗はDiscord通知で知らせる"""
    load_env()
    now = now or datetime.now(JST)
    token = os.environ.get("THREADS_ACCESS_TOKEN_DIET", "").strip()
    uid = os.environ.get("THREADS_USER_ID_DIET", "").strip()
    slot = pick_slot(slot, now)
    rng = rng or random.Random()
    fetch = fetch or fetch_recent
    if publisher is None:
        publisher = publish_text
        verify = verify or reply_exists
    pause_path = pause_path or PAUSE_FILE

    if pause_path.exists():
        log("停止中のためスキップ（diet_threads_pause.json）")
        return 0

    if not dry_run and not token:
        log("トークン未設定のためスキップ")
        return 0

    entries = read_log(log_path)
    why = slot_guard(entries, slot, now)
    if why:
        log(f"見送り: {why}")
        return 0

    # 直近投稿(Grok分を含む)。DRY_RUNはThreadsへ一切アクセスしないので、自分のログだけを使う
    if dry_run:
        recent = [{"id": "", "text": e.get("text", ""), "timestamp": e.get("ts", "")} for e in entries[-50:] if e.get("text")]
    else:
        try:
            recent = fetch(token, 50)
        except Exception as e:
            if is_pause_error(e):
                pause_and_notify(str(e), slot, now, pause_path)
                return 0
            log(f"見送り: 直近投稿の取得に失敗({e})")
            notify(f"{slot}枠を見送り: 直近投稿を取得できませんでした({str(e)[:120]})。トークン失効の可能性があります。")
            return 0

    # 30分間隔(誰の投稿でも)
    gap = gap_minutes(recent, now)
    if gap is not None and gap < MIN_GAP_MIN:
        need = MIN_GAP_MIN - gap
        if need <= MAX_WAIT_MIN and not dry_run:
            log(f"直前の投稿から{gap:.0f}分。{need:.1f}分待つ")
            sleeper(need * 60 + 5)
            now = now + timedelta(minutes=need, seconds=5)
        else:
            log(f"見送り: 直前の投稿から{gap:.0f}分(30分未満)")
            return 0

    why_pace = pace_guard(recent, now)
    ba = pick_ba(entries, slot, now)
    if why_pace and not (ba and not why_pace.startswith("本日の合計")):
        log(f"見送り: {why_pace}")
        return 0
    if ba:
        return post_ba(ba, slot, now, dry_run, token, uid, publisher, log_path, pause_path)

    cta_ok, cta_reason = cta_status(recent, now)
    allow_cta = cta_ok and slot in CTA_SLOTS
    log(f"枠{slot} / 案内: {'可' if allow_cta else '不可'}({cta_reason}{'' if slot in CTA_SLOTS else '・この枠は案内なし'})")

    fmt, typ = choose_format_and_type(recent, slot, rng)
    topic = pick_topic(entries, rng)
    rs = sorted_recent(recent)
    recent_texts = [p.get("text") or "" for p in rs if p.get("text")]
    recent_texts += [e.get("text", "") for e in reversed(entries[-20:]) if e.get("text")]   # 自分のツリーの続きも重複判定に入れる
    cta_text = next_cta_text(entries, recent)
    post, reason = generate_post(slot, fmt, typ, allow_cta, recent_texts[:40], recent_texts[:70], generator, topic, cta_text)
    if not post:
        log(f"見送り: {reason}")
        if "類似" not in reason:
            notify(f"{slot}枠を見送り: 3回生成しても合格しませんでした({reason[:150]})")
        return 0

    if dry_run:
        print("----- DRY_RUN 生成結果 -----")
        fmt = post.get("fmt", fmt)
        print(f"[枠 {slot} / テーマ {post['type'] or typ} / 形式 {fmt} / 題材 {(topic or {}).get('id')} / 案内 {post['has_cta']}]")
        print(post["text"])
        for r in post["replies"]:
            print("  └ " + r)
        print("----------------------------")
        return 0

    try:
        pid = publisher(token, uid, post["text"], "", TOPIC_TAG) if publisher is publish_text else publisher(token, uid, post["text"])
    except Exception as e:
        log(f"投稿失敗: {e}")
        if is_pause_error(e):   # 再試行しない。停止して本人に報告
            pause_and_notify(str(e), slot, now, pause_path)
            return 0
        notify(f"{slot}枠の投稿に失敗: {str(e)[:150]}")
        return 0
    fmt = post.get("fmt", fmt)
    reply_ids, reply_err = [], ""
    prev = pid
    for r in post["replies"]:   # ツリー: 直前の投稿への返信として順につなぐ。再試行しない（二重投稿防止）
        try:
            sleeper(3)
            prev = publisher(token, uid, r, prev)
            reply_ids.append(prev)
        except Exception as e:
            reply_err = str(e)
            log(f"ツリーの続きの投稿に失敗({len(reply_ids) + 2}つ目): {e}")
            if is_pause_error(e):
                pause_and_notify(str(e), slot, now, pause_path)
            else:
                notify(f"{slot}枠のツリー {len(reply_ids) + 2}つ目の投稿に失敗（1つ目は投稿済み・再投稿しない）: {str(e)[:120]}")
            break
    whole = full_text(post)
    append_log({
        "ts": now.isoformat(timespec="seconds"), "slot": slot, "id": pid, "fmt": fmt, "reply_ids": reply_ids,
        "topic_id": (topic or {}).get("id"), "type": post["type"] or typ, "has_cta": post["has_cta"],
        "text_hash": hashlib.sha256(whole.encode("utf-8")).hexdigest()[:16],
        "text": whole, **({"reply_error": reply_err[:200]} if reply_err else {}),
    }, log_path)
    log(f"投稿完了 id={pid} 形式={fmt} ツリー={len(reply_ids)}/{len(post['replies'])} 題材={(topic or {}).get('id')} 案内={post['has_cta']}")
    if reply_ids and not dry_run and verify:
        try:
            missing = [rid for rid in reply_ids if not verify(token, rid)]
            if missing:
                notify(f"{slot}枠のツリーの続き {len(missing)}個 が投稿後に見つかりません（{missing}）。Threadsのアプリで表示を確認してください")
        except Exception as e:
            log(f"ツリーの確認に失敗（投稿は済み）: {e}")
    return 0


def main(argv: list[str]) -> int:
    slot = None
    if "--slot" in argv:
        slot = argv[argv.index("--slot") + 1]
    elif os.environ.get("DIET_SLOT"):
        slot = os.environ["DIET_SLOT"].strip() or None
    if slot and slot not in SLOTS:
        log(f"不明な枠 {slot}。{SLOTS} のいずれかを指定してください")
        return 0
    dry = os.environ.get("DRY_RUN", "") in ("1", "true", "True") or "--dry-run" in argv
    return run(slot=slot, dry_run=dry)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
