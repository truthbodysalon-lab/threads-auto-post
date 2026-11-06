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
DAILY_CAP = 21           # API投稿の1日上限（Grokの10本と合わせて31本）
CTA_DAILY_MAX = 2        # Grok分を含む1日の公式LINE案内の上限
CTA_SLOTS = {"15:30", "20:00"}   # 案内を付けてよい枠(反応が取れる夕方〜夜。Grokの案内枠を食わないよう控えめ)
MIN_GAP_MIN = 30         # 直前の投稿(誰のものでも)からの最低間隔（2026-11-02 院長指示で45→30分）
MAX_WAIT_MIN = 10        # 間隔が足りない時に待つ上限(超えるならこの枠は見送り)
SIM_THRESHOLD = 0.5      # 文字バイグラムJaccardの重複しきい値
MAX_LEN = 500
MAX_ATTEMPTS = 3
MODELS = ["claude-haiku-4-5-20251001", "claude-sonnet-4-5"]  # 先頭から試し、失敗したら次へ

CTA_TEXT = "気になる方は公式LINE（@886oidmv）で『相談』と送ってください。院長が直接お返事します"
CTA_MARK = "@886oidmv"

TYPES = ["悩みの言い当て", "よくある勘違いをほどく", "今日からできる小さなコツ", "相談の入口", "人柄が出るつぶやき"]
FORMATS = {
    "short": "短文(1〜3行・60字以内)",
    "medium": "中くらい(100〜200字前後)",
    "long": "長文(250〜450字。悩み→勘違い→小さなコツ→持病・膝腰への配慮の一言)",
}


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


def publish_text(token: str, user_id: str, text: str) -> str:
    """2段階投稿(コンテナ作成→公開)。公開後の投稿IDを返す。再試行しない(二重投稿防止)"""
    uid = user_id or "me"
    c = _http(f"{BASE_URL}/{uid}/threads", {"media_type": "TEXT", "text": text, "access_token": token}, "POST")
    cid = c["id"]
    time.sleep(2)
    p = _http(f"{BASE_URL}/{uid}/threads_publish", {"creation_id": cid, "access_token": token}, "POST")
    return p["id"]


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
    n = len(text or "")
    return "short" if n <= 60 else ("long" if n >= 250 else "medium")


def choose_format_and_type(recent: list[dict], slot: str, rng: random.Random) -> tuple[str, str]:
    """短文約4割・長文3割以上・同形式3連続禁止。朝は長文寄り、夕夜は短文寄り(Grokの振り返りより)"""
    w = {"short": 0.40, "medium": 0.25, "long": 0.35}
    if slot in ("09:00", "11:00"):
        w["long"] += 0.10; w["short"] -= 0.10
    elif slot in ("15:00", "20:00"):
        w["short"] += 0.10; w["long"] -= 0.10
    last = [classify_format(p.get("text") or "") for p in sorted_recent(recent)[:2]]
    if len(last) == 2 and last[0] == last[1]:
        w[last[0]] = 0.0
    fmts, ws = zip(*w.items())
    fmt = rng.choices(fmts, weights=ws)[0]
    typ = rng.choice(TYPES)
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
    body = text.replace(CTA_TEXT, "") if has_cta else text
    nb = unicodedata.normalize("NFKC", body)
    for w in _NG_WORDS:
        if w in nb:
            errs.append(f"NGワード「{w}」")
    for rx, name in _NG_NFKC:
        if rx.search(nb):
            errs.append(f"NG表現: {name}")
    if has_cta:
        if CTA_TEXT not in text:
            errs.append("案内ありなのに固定文が一字一句一致していない")
        elif not text.rstrip().endswith(CTA_TEXT):
            errs.append("案内文が最後の一文になっていない")
    else:
        if "886oidmv" in t or "公式LINE" in t:
            errs.append("案内フラグなしなのにLINE案内が入っている")
    return errs


# ── 生成 ──────────────────────────────────────────

def build_prompts(fmt: str, typ: str, slot: str, allow_cta: bool, recent_texts: list[str], feedback: str = "") -> tuple[str, str]:
    rules = RULES_FILE.read_text(encoding="utf-8") if RULES_FILE.exists() else ""
    facts = FACTS_FILE.read_text(encoding="utf-8") if FACTS_FILE.exists() else ""
    grok = GROK_RULES_FILE.read_text(encoding="utf-8") if GROK_RULES_FILE.exists() else ""
    grok_block = (
        "\n\n【Grokのrules.md 原文抜き書き】(上の【運用ルール】の要約と食い違う場合は、こちらの新しい日付の記述が優先。"
        "ただし【運用ルール】のAPI投稿向け追加規則(ツリー禁止・案内の数え方など)は有効)\n" + grok
    ) if grok.strip() else ""
    system = (
        "あなたは長岡市の整体院トゥルースの院長として、Threadsのダイエットアカウントの投稿を1本書く。"
        "以下の【運用ルール】と【栄養の事実ファイル】だけに従う。"
        "栄養・ダイエットの主張は【栄養の事実ファイル】にあることだけを使い、ここにない事実・数字・具体的な食品名や手順・体験談(「見てきた」「経験上」など)は書かない。"
        "出力はJSONオブジェクト1つだけ。\n\n【運用ルール】\n" + rules + grok_block + "\n\n【栄養の事実ファイル】\n" + facts
    )
    avoid = "\n".join(f"- {t.strip()[:80].replace(chr(10), ' ')}" for t in recent_texts[:20]) or "(なし)"
    cta_line = (
        f"公式LINE案内は付けても付けなくてもよい。付ける場合は本文の最後の一文を次の固定文にし、has_ctaをtrueにする: {CTA_TEXT}"
        if allow_cta else "公式LINE案内は今回は入れない(has_ctaはfalse。@886oidmv や「公式LINE」の語を本文に書かない)。"
    )
    user = (
        f"投稿枠: {slot} (JST)\n型: {typ}\n長さ: {FORMATS[fmt]}\n{cta_line}\n\n"
        "直近の投稿(Grok Botの投稿を含む)。次のテーマ・書き出しと被らないこと:\n" + avoid + "\n\n"
        "1文目は「読者のセリフ(「〇〇なのにやせない」など)」か「身近な食べ物名」で始める。ハッシュタグ・URL・絵文字の乱用はしない。\n"
        "体の仕組み(内臓の位置・骨のずれ・ゆがみ・リンパ・血流など)を原因や効果として書かない。姿勢・骨盤の話は「姿勢や体の使い方のクセが関わることもある」程度にとどめる。\n"
        "専門用語(エネルギー密度・インスリン・PFC・黄体期など)は使わず、中学生にも伝わる言葉に言い換える(例: エネルギー密度の低い野菜→量のわりに軽い野菜、黄体期→生理前)。\n"
        "直近の投稿で使われた決まり文句(「本当に多いです」「という方」「ありませんか」など)は使い回さず、言い方を変える。\n"
        + (f"\n前回の出力は次の理由で不合格だった。直すこと: {feedback}\n" if feedback else "")
        + '\n出力形式(JSONのみ): {"text": "本文", "type": "型", "has_cta": true または false}'
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
    return {"text": text, "type": str(d.get("type") or ""), "has_cta": bool(d.get("has_cta"))}


def generate_post(slot: str, fmt: str, typ: str, allow_cta: bool, recent_texts: list[str], sim_pool: list[str],
                  generator=None) -> tuple[dict | None, str]:
    """検品と類似チェックを通った {text,type,has_cta} と、失敗時の理由。generator(system,user)->raw は差し替え可"""
    generator = generator or call_llm
    feedback = ""
    sim_fail = 0
    last_reason = "生成失敗"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        system, user = build_prompts(fmt, typ, slot, allow_cta, recent_texts, feedback)
        post = parse_generated(generator(system, user))
        if not post:
            last_reason = "LLM出力を読めない/空"
            feedback = "JSONオブジェクトのみを出力すること"
            continue
        if post["has_cta"] and not allow_cta:
            last_reason = "案内不可なのに案内つき"
            feedback = "公式LINE案内は入れない"
            continue
        errs = inspect_text(post["text"], post["has_cta"]) + stock_phrase_errors(post["text"], recent_texts)
        n_body = len(post["text"].replace(CTA_TEXT, "").strip())
        if fmt == "short" and n_body > 90:
            errs.append(f"短文の指定なのに{n_body}字(60字目安・90字以内)")
        elif fmt == "long" and n_body < 200:
            errs.append(f"長文の指定なのに{n_body}字(250〜450字)")
        if errs:
            last_reason = "検品NG: " + " / ".join(errs)
            feedback = "; ".join(errs)
            log(f"試行{attempt}: {last_reason}")
            continue
        sim, other = max_similarity(post["text"], sim_pool)
        if sim >= SIM_THRESHOLD:
            sim_fail += 1
            last_reason = f"直近投稿と類似(Jaccard {sim:.2f})"
            log(f"試行{attempt}: {last_reason}")
            if sim_fail >= 2:   # 類似は『1回だけ作り直し』。2回目も類似ならこの枠は見送り
                return None, last_reason
            feedback = f"直近の投稿「{other[:40]}」と似すぎ。テーマと書き出しを変えること"
            continue
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
        log_path: Path | None = None, pause_path: Path | None = None) -> int:
    """戻り値は常に0(見送りも正常終了)。失敗はDiscord通知で知らせる"""
    load_env()
    now = now or datetime.now(JST)
    token = os.environ.get("THREADS_ACCESS_TOKEN_DIET", "").strip()
    uid = os.environ.get("THREADS_USER_ID_DIET", "").strip()
    slot = pick_slot(slot, now)
    rng = rng or random.Random()
    fetch = fetch or fetch_recent
    publisher = publisher or publish_text
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

    ba = pick_ba(entries, slot, now)
    if ba:
        return post_ba(ba, slot, now, dry_run, token, uid, publisher, log_path, pause_path)

    cta_ok, cta_reason = cta_status(recent, now)
    allow_cta = cta_ok and slot in CTA_SLOTS
    log(f"枠{slot} / 案内: {'可' if allow_cta else '不可'}({cta_reason}{'' if slot in CTA_SLOTS else '・この枠は案内なし'})")

    fmt, typ = choose_format_and_type(recent, slot, rng)
    rs = sorted_recent(recent)
    recent_texts = [p.get("text") or "" for p in rs if p.get("text")]
    post, reason = generate_post(slot, fmt, typ, allow_cta, recent_texts[:20], recent_texts[:50], generator)
    if not post:
        log(f"見送り: {reason}")
        if "類似" not in reason:
            notify(f"{slot}枠を見送り: 3回生成しても合格しませんでした({reason[:150]})")
        return 0

    if dry_run:
        print("----- DRY_RUN 生成結果 -----")
        print(f"[枠 {slot} / 型 {post['type'] or typ} / 形式 {fmt} / 案内 {post['has_cta']}]")
        print(post["text"])
        print("----------------------------")
        return 0

    try:
        pid = publisher(token, uid, post["text"])
    except Exception as e:
        log(f"投稿失敗: {e}")
        if is_pause_error(e):   # 再試行しない。停止して本人に報告
            pause_and_notify(str(e), slot, now, pause_path)
            return 0
        notify(f"{slot}枠の投稿に失敗: {str(e)[:150]}")
        return 0
    append_log({
        "ts": now.isoformat(timespec="seconds"), "slot": slot, "id": pid,
        "type": post["type"] or typ, "has_cta": post["has_cta"],
        "text_hash": hashlib.sha256(post["text"].encode("utf-8")).hexdigest()[:16],
        "text": post["text"],
    }, log_path)
    log(f"投稿完了 id={pid} 型={post['type'] or typ} 案内={post['has_cta']}")
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
