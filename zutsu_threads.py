#!/usr/bin/env python3
"""頭痛Threads(@truth_nagaoka_zutsuu) API自動投稿 (自己完結モジュール・diet_threads.py を元に2026-11-05作成)

- このアカウントは API 投稿だけ(Grok Botのブラウザ投稿は無い)。1日20枠（2026-11-26 masa指示「投稿は20投稿にして」・正本は下の SLOTS）・図解は 9:15 / 14:30 / 20:15
- 起動は中央ディスパッチャ(schedule_table.json)が workflow_dispatch で行う。ここにcronは無い
- トークン THREADS_ACCESS_TOKEN_ZUTSU が空なら何もせず exit 0(認可前)
- ルール正本: zutsu_threads_rules.md / 事実の根拠: zutsu_threads_facts.md (生成プロンプトに全文を入れる)

使い方:
  python3 zutsu_threads.py --slot 07:30          # 本番(枠を指定)
  DRY_RUN=1 python3 zutsu_threads.py --slot 12:15  # Threadsへ一切アクセスせず生成だけ表示
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
LOG_FILE = BASE / "log_zutsu_posted.jsonl"
RULES_FILE = BASE / "zutsu_threads_rules.md"
FACTS_FILE = BASE / "zutsu_threads_facts.md"
PAUSE_FILE = BASE / "zutsu_threads_pause.json"          # 警告・制限を検知したら作る。削除するまで全枠スキップ
BASE_URL = "https://graph.threads.net/v1.0"
JST = timezone(timedelta(hours=9))

# 2026-11-11 masa指示「Thredsの投稿を1日10投稿を目標にして その際に画像の解説も取り入れて投稿するようにして」:
# 1日5本→10本。うち3枠(IMAGE_SLOTS)は図解カード(zutsu_figure.py・AI生成ではない)＋その解説文で投稿する
# 2026-11-26 masa指示「投稿は20投稿にして」で10→20枠。既存10枠はそのまま、Grok側daietto(毎時ちょうど・前20分〜後30分)・masa Buffer(毎時10分±15分)を避けて10枠を足した（間隔30分以上）
SLOTS = ["06:00", "06:30", "07:35", "08:30", "09:15", "09:50", "10:30", "11:15", "12:30", "13:15",
         "14:30", "15:15", "16:35", "17:30", "18:15", "19:35", "20:15", "21:35", "22:30", "23:15"]
_OLD_SLOTS_20261114 = ["06:30", "08:30", "09:15", "10:30", "12:30", "14:30", "17:30", "18:15", "20:15", "22:30"]   # 2026-11-14 masa指示「各アカウント合計1日10本にして」で10枠。同日01:10 Grok側のdaietto(毎時ちょうど+数〜15分)・masa Buffer(毎時10分±15分)の時刻 7/8/10/12/14/16/17/19/21/22時 の前20分〜後30分を避けて再配置（間隔45分以上・truth毎時40〜50分/nagaoka 50〜60分の帯もなるべく避ける）
IMAGE_SLOTS = {"09:15", "14:30", "20:15"}   # 2026-11-14 再配置: 9:00/13:45/19:00→9:15/14:30/20:15
DAILY_CAP = 20           # 2026-11-26 masa指示「投稿は20投稿にして」で10→20。旧: API投稿の1日上限（2026-11-11 10→20／11-12 20→30／2026-11-14 masa指示「各アカウント合計1日10本にして」で10に戻す。手動投稿も含めて10本）
IMAGE_BODY_MAX = 160     # 画像の解説文の本文上限
RAW_IMG = "https://raw.githubusercontent.com/truthbodysalon-lab/threads-auto-post/main/images/zutsu"
CTA_DAILY_MAX = 2        # 1日の公式LINE案内の上限（手動投稿分も含めて数える）
CTA_SLOTS = {"12:30", "17:30", "20:15", "21:35"}   # 公式LINE案内を付けてよい枠（1日2本まで・2本続けない。2026-11-26 20枠化で4枠に）
# 2026-11-26 masa指示「（締めの固定文）→他のCTAで試して」: LINEの『診断』案内は言い方を4通りにし、フォロー・保存の誘いも試す。
# どれを付けたかは log の cta_id に残し、zutsu_cta_report.py が閲覧数で比べる。公式LINEから誘うのは『診断』だけ（自由文の相談には誘わない）
OTHER_CTA_SLOTS = {"08:30", "11:15", "15:15", "19:35", "22:30"}   # フォロー・保存の誘いを付けてよい枠
OTHER_CTA_DAILY_MAX = 3
MIN_GAP_MIN = 30         # 直前の投稿(手動投稿も含む)からの最低間隔
MAX_WAIT_MIN = 10        # 間隔が足りない時に待つ上限(超えるならこの枠は見送り)
SIM_THRESHOLD = 0.5      # 文字バイグラムJaccardの重複しきい値
MAX_LEN = 260   # 1投稿（ツリーは1つずつ）の上限。 2026-11-10「文章長すぎ」で500→260（案内の固定文を付けても収まる長さ）
MAX_ATTEMPTS = 5          # 2026-11-10 ありきたりな言い回しの検品を厳しくしたため3→5
# 2026-11-26 masa指示「課金はなし 無料枠の範囲内で完結させて」で Anthropic API（従量課金）→ Gemini 無料枠（free_llm.py）へ切替。
# 旧: MODELS = ["claude-haiku-4-5-20251001", "claude-sonnet-4-5"]。最初に試すモデルだけアカウントごとに変えて、無料枠を分け合う
LLM_FIRST_MODEL = "gemini-3.6-flash"

# 頭痛LINE(@561yryuk)の「診断」は自動応答(id 4 頭痛タイプ診断)が返す案内済みの言葉。自由文の相談には誘導しない(自由文は手動返信のため)
CTA_TEXT = "自分の頭痛のタイプが気になる方は、公式LINE（@561yryuk）で『診断』と送ってください"
LINE_CTA_VARIANTS = {   # 公式LINEの『診断』案内の言い方（2026-11-26〜 試し分け。どれも『診断』の自動応答＝頭痛タイプ診断へ）
    "L1": CTA_TEXT,
    "L2": "頭痛のタイプを知りたい方は、公式LINE（@561yryuk）に『診断』とだけ送ってください",
    "L3": "公式LINE（@561yryuk）で『診断』と送ると、頭痛のタイプ診断が返ってきます",
    "L4": "『診断』の2文字を、公式LINE（@561yryuk）に送ってください",
}
OTHER_CTA_VARIANTS = {   # LINE以外の締め（2026-11-26〜 試し分け）
    "F1": "長岡の天気と頭痛の話を、毎日書いています。フォローしておくと流れてきます",
    "F2": "頭が重くなりやすい季節の話も続けます。気になる方はフォローを",
    "S1": "頭が重い日に見返せるように、保存しておいてください",
}
CTA_MARK = "@561yryuk"
# 頭痛を扱う投稿(中くらい・長文)に必ず入れる1行（ig-autopost-cloud guides/headache_facts.md #23）
SAFETY_LINE = "※突然の激しい頭痛や手足のしびれがある場合は、先に医療機関へ。"
# 2026-11-10 masa指示「※で同じ文章を毎回上げすぎ」: ※の1行は毎回ではなく、頭痛のタイプを扱う投稿に1日1本まで。
# 言い方は事実16の範囲で3通りを順に替える（直前と同じ言い方にしない）
SAFETY_LINES = [
    SAFETY_LINE,
    "※急な激しい痛み、手足のしびれ、ろれつが回らないときは、まず医療機関へ。",
    "※発熱と首の硬さ、見え方の異常をともなう頭痛は、整体より先に受診を。",
]

# 2026-11-26 masa指示「頭痛の種類や対処法などの情報も追加で伝えて」で情報の型を2つ足した（ツリーの投稿はこの2つを多めに使う）
INFO_TYPES = ["頭痛の種類と見分け方の情報", "頭痛が出たときの対処法の情報"]
TYPES = ["悩みの言い当て", "よくある勘違いをほどく", "今日からできる小さなコツ", "自分の頭痛のタイプを知る手がかり", "院での見方の紹介"] + INFO_TYPES
# テーマは事実ファイル(zutsu_threads_facts.md)の項目に対応させ、同じ日に同じテーマを2回使わない。
# 2026-11-05 試し生成で5枠すべてが「雨の前に頭が重い」になったため追加
THEMES = [
    "緊張型の頭痛（締めつけ感・デスクワーク・食いしばり。事実10）",
    "片頭痛（ズキズキ・温めず暗く静かに休む。事実11）",
    "首からくる頭痛（スマホ首・枕・ボキボキ鳴らさない。事実12）",
    "痛み止めを飲む日が増えてきたとき（医師・薬剤師に相談。事実13）",
    "天気・気圧と頭痛（睡眠と起床時刻・天気アプリで先回り。事実14）",
    "目の疲れと画面の位置（20分ごとに20秒遠くを見る・画面は目線のやや下。事実15）",
    "枕の高さとホットタオル（事実15）",
    "受診したほうがよい頭痛の目安（事実16）",
    "自分の頭痛のタイプの手がかり（痛み方・場所・きっかけ・薬の回数。事実17）",
    "整体院トゥルースでの見方（姿勢・動きの検査・痛い場所の手前まで見る。事実3〜8）",
]
# 2026-11-10 masa指示「長岡市で頭痛に悩んでいる人のためになる情報も発信するようにして」。
# 1日1本（LOCAL_SLOT）は長岡の暮らし・地域の情報のテーマにする。急な頭痛の相談先は週1回まで
EMERGENCY_THEME = "長岡で夜や休日に急な頭痛が出て受診を迷ったとき（#7119・急患診療所。事実21〜22）"
LOCAL_THEMES = [
    "長岡の冬の雪かきと首肩・頭の重さ（事実18）",
    "雪道の運転で肩に力が入るときの頭の重さ（事実18）",
    "長岡の冬の寒暖差（暖房の部屋と外）と頭痛（事実19）",
    "冬に水分をとる量が減りやすいことと頭の重さ（事実20）",
    EMERGENCY_THEME,
    # 2026-11-26 masa指示「長岡市の方達や周辺地域の方達に認知して最終的に来店してもらう。だからそれにまつわる情報も発信して」
    "長岡駅から車で5分の整体院トゥルースの通いやすさ（夜22時まで・土日祝も営業・個室・完全予約制。事実2・23・24）",
    "仕事帰りや休日に寄れる時間（夜22時まで・土日祝も営業。事実2・24）",
    "初めて来たときの流れ（カウンセリング→検査→原因の説明→施術→セルフケア。事実3〜8）",
    "見附・小千谷・柏崎・三条から長岡へ車で通う人の、運転中の首肩と頭の重さ（事実18・25）",
    "長岡と周りの市（見附・小千谷・柏崎・三条）で冬を過ごす人の寒暖差と水分（事実19・20・25）",
]
THEMES = THEMES + LOCAL_THEMES
STORE_THEMES = LOCAL_THEMES[5:]   # 2026-11-26 追加の店・周りの市のテーマは地域の枠(LOCAL_SLOTS)だけで使う（ほかの枠が店の話ばかりにならないように）
# ※の1行を付けるのは頭痛のタイプそのものを扱うテーマだけ（受診の目安・相談先のテーマは本文がその話なので付けない）
SAFETY_THEMES = {THEMES[0], THEMES[1], THEMES[2], THEMES[4], THEMES[8]}
LOCAL_SLOT = "12:30"   # 2026-11-14 再配置: 12:15→12:30
LOCAL_SLOTS = {LOCAL_SLOT, "16:35", "19:35"}   # 2026-11-26 20枠化＋「長岡と周りの市の人に知ってもらい来店へ」で1日1本→3本
EMERGENCY_EVERY_DAYS = 7
# 2026-11-10 masa指示「文章長すぎ」: 長文(250〜450字)をやめ、短文と中くらい(本文140字以内)だけにする
FORMATS = {
    # 2026-11-26 masa指示「投稿内容についてはツリー投稿も入れて」「（長さ）長すぎ。長文になる場合はツリー投稿で」
    "tree": "ツリー(親投稿は1〜2行・60字以内で、続きが読みたくなる言い切りか場面。続きは返信1〜2本に分け、1本110字以内。頭痛の種類や対処法の情報は返信に書く)",
    "short": "短文(1〜3行・60字以内)",
    "medium": "中くらい(本文60〜110字・3行まで。※の注意書きを指示されたときはそれを別に足す。言いたいことを1つにして言い切る)",
    "image": "画像つき(図解カード1枚＋その解説文。解説文は80〜150字・4行まで)",
}
MEDIUM_BODY_MAX = 120
TREE_PARENT_MAX = 65     # ツリーの親投稿（目安60字）
SHORT_BODY_MAX = 70      # 短文（目安60字）。2026-11-26 masa指示「長すぎ」で、短文の上限を中くらいと同じ120字→70字に
TREE_REPLY_MAX = 115     # ツリーの返信1本（目安110字）
TREE_REPLIES_MAX = 2


# ── ログ・時刻ユーティリティ ─────────────────────────

def log(msg: str) -> None:
    print(f"[zutsu {datetime.now(JST):%H:%M:%S}] {msg}", flush=True)


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


# ── 自分のログ(log_zutsu_posted.jsonl) ───────────────

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


# ── 警告・制限での即停止(ダイエットThreadsと同じ「警告・制限が出たら即停止して本人に報告」) ──

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
    notify("🛑 頭痛Threads API投稿を停止しました\n"
           f"枠: {slot} / 理由: {_summarize_error(reason)[:150]}\n"
           "再開するには zutsu_threads_pause.json を削除してpushするか、Claude Codeに「頭痛Threadsを再開して」と伝えてください。")


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
    """GET /me/threads。自分(手動投稿も含む)の直近投稿 [{id,text,timestamp}]"""
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

def publish_image(token: str, user_id: str, text: str, image_url: str, sleeper=time.sleep) -> str:
    """画像つき投稿。raw URLの反映待ちのため、コンテナ作成だけ最大3回やり直す(公開は1回だけ＝二重投稿しない)"""
    uid = user_id or "me"
    last = None
    for i in range(3):
        try:
            c = _http(f"{BASE_URL}/{uid}/threads",
                      {"media_type": "IMAGE", "image_url": image_url, "text": text, "access_token": token}, "POST")
            break
        except Exception as e:
            last = e
            if is_pause_error(e):
                raise
            sleeper(30)
    else:
        raise last
    sleeper(10)   # 画像の処理待ち
    p = _http(f"{BASE_URL}/{uid}/threads_publish", {"creation_id": c["id"], "access_token": token}, "POST")
    return p["id"]


def publish_reply(token: str, user_id: str, text: str, reply_to: str, sleeper=time.sleep) -> str:
    """ツリーの返信（reply_to_id つき）。2段階投稿。再試行しない(二重投稿防止)"""
    uid = user_id or "me"
    sleeper(3)
    c = _http(f"{BASE_URL}/{uid}/threads",
              {"media_type": "TEXT", "text": text, "reply_to_id": reply_to, "access_token": token}, "POST")
    sleeper(2)
    p = _http(f"{BASE_URL}/{uid}/threads_publish", {"creation_id": c["id"], "access_token": token}, "POST")
    return p["id"]


def push_figure(path: Path) -> bool:
    """図解カードをリポジトリへcommit/pushして raw URL で読めるようにする(Actionsのcheckoutは書き込み可)"""
    import subprocess
    try:
        subprocess.run(["git", "-C", str(BASE), "add", str(path)], capture_output=True, timeout=60, check=True)
        subprocess.run(["git", "-C", str(BASE), "-c", "user.name=github-actions[bot]",
                        "-c", "user.email=github-actions[bot]@users.noreply.github.com",
                        "commit", "-q", "-m", f"chore: zutsu threads figure {path.name} [skip ci]"],
                       capture_output=True, timeout=60, check=True)
        for _ in range(3):
            if subprocess.run(["git", "-C", str(BASE), "push", "-q"], capture_output=True, timeout=120).returncode == 0:
                return True
            subprocess.run(["git", "-C", str(BASE), "pull", "-q", "--rebase", "--autostash", "origin", "main"],
                           capture_output=True, timeout=120)
        return False
    except Exception as e:
        log(f"図の push 失敗: {type(e).__name__}")
        return False


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
    """(案内を入れてよいか, 理由)。本日のJST日付で @561yryuk を含む投稿が2本以上、または直近の投稿に入っていれば不可"""
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


def safety_line_for(fmt: str, theme: str, recent: list[dict], now: datetime) -> str:
    """今回の投稿に付ける※の1行。付けないなら空文字。
    中くらい×頭痛のタイプのテーマだけ・その日(JST)にまだ※つきの投稿が無いときだけ・直前に使った言い方は避ける"""
    if fmt not in ("medium", "image") or theme not in SAFETY_THEMES:
        return ""
    d = now.strftime("%Y-%m-%d")
    last_used = None
    for p in sorted_recent(recent):
        t = p.get("text") or ""
        if "※" not in t:
            continue
        ts = parse_ts(p.get("timestamp", ""))
        if ts and ts.astimezone(JST).strftime("%Y-%m-%d") == d:
            return ""
        if last_used is None:
            last_used = next((x for x in SAFETY_LINES if x in t), "")
    i = (SAFETY_LINES.index(last_used) + 1) if last_used in SAFETY_LINES else 0
    return SAFETY_LINES[i % len(SAFETY_LINES)]


def strip_ctas(text: str) -> str:
    """締めの決まった文（LINEの『診断』案内・フォロー・保存の誘い）を外した本文"""
    t = text or ""
    for c in list(LINE_CTA_VARIANTS.values()) + list(OTHER_CTA_VARIANTS.values()):
        t = t.replace(c, "")
    return t.strip()


def _cta_counts(entries: list[dict], days: int, now: datetime) -> dict:
    since = now - timedelta(days=days)
    out: dict = {}
    for e in entries:
        ts = parse_ts(e.get("ts") or "")
        if ts and ts >= since and e.get("cta_id"):
            out[e["cta_id"]] = out.get(e["cta_id"], 0) + 1
    return out


def choose_cta_variant(kind: str, entries: list[dict], now: datetime, rng: random.Random) -> str:
    """試し分け: 直近14日で使った回数が一番少ない言い方を選ぶ（同数ならランダム）"""
    pool = LINE_CTA_VARIANTS if kind == "line" else OTHER_CTA_VARIANTS
    used = _cta_counts(entries, 14, now)
    low = min(used.get(k, 0) for k in pool)
    return rng.choice(sorted(k for k in pool if used.get(k, 0) == low))


def other_cta_ok(entries: list[dict], recent: list[dict], slot: str, now: datetime) -> bool:
    """フォロー・保存の誘いを付けてよいか（その枠・1日3本まで・直前の投稿が締めつきでない）"""
    if slot not in OTHER_CTA_SLOTS:
        return False
    n = sum(1 for e in today_entries(entries, now) if str(e.get("cta_id") or "")[:1] in ("F", "S"))
    if n >= OTHER_CTA_DAILY_MAX:
        return False
    rs = sorted_recent(recent)
    last = (rs[0].get("text") or "") if rs else ""
    return not (CTA_MARK in last or any(c in last for c in OTHER_CTA_VARIANTS.values()))


def first_sentence(text: str) -> str:
    return re.split(r"[。！？!?\n]", (text or "").strip(), maxsplit=1)[0]


def classify_format(text: str) -> str:  # ツリーの親は短文として数える
    # ※の注意書きつき、または90字を超えるものを中くらいとして数える（短文の指定でも少し長めに書かれることがあるため）
    t = text or ""
    return "short" if len(t) <= 90 and "※" not in t else "medium"


def choose_format_and_type(recent: list[dict], slot: str, rng: random.Random) -> tuple[str, str]:
    """短文と中くらいを半々・同形式3連続禁止。地域の情報の枠は中身を入れるため中くらい寄り、夜は短文寄り"""
    if slot in IMAGE_SLOTS:
        return "image", rng.choice(TYPES)
    # 2026-11-26: 短文4割・中くらい3割・ツリー3割（長くなる話はツリーへ）
    w = {"short": 0.40, "medium": 0.30, "tree": 0.30}
    if slot in LOCAL_SLOTS:
        w = {"short": 0.10, "medium": 0.50, "tree": 0.40}
    elif slot in ("21:35", "22:30", "23:15"):   # 夜の枠は短文寄り
        w = {"short": 0.55, "medium": 0.25, "tree": 0.20}
    last = [classify_format(p.get("text") or "") for p in sorted_recent(recent)[:2]]
    if len(last) == 2 and last[0] == last[1]:
        w[last[0]] = 0.0
        if not any(w.values()):
            w = {"short": 0.5, "medium": 0.5}
    fmts, ws = zip(*w.items())
    fmt = rng.choices(fmts, weights=ws)[0]
    typ = rng.choice(INFO_TYPES) if (fmt == "tree" and rng.random() < 0.7) else rng.choice(TYPES)
    return fmt, typ


# ── 検品 ──────────────────────────────────────────

_NG_WORDS = [
    "講座", "講義", "学んだ", "学びました", "習いました", "習った", "教わ", "勉強して",
    "今すぐ", "限定", "先着", "今だけ", "無料", "特典", "キャンペーン", "クーポン", "割引", "初回", "料金", "価格", "コース", "回数券",
    "お客様の声", "お客さまの声", "口コミ", "ビフォー", "アフター", "患者", "お客様が", "お客さまが", "来院",
    # 頭痛は医療に近いので、治る・効く系と診断の言い切りは書かない（guides/headache_facts.md「書かない」）
    "治る", "治す", "治り", "治療", "完治", "効く", "効き", "効果", "有効", "に強くな", "保証", "改善率", "No.1", "唯一",
    "薬物乱用頭痛", "薬に頼らない", "頭痛薬に頼", "群発頭痛", "副鼻腔炎",
    "見てきた", "診てきた", "経験上", "これまでの経験", "実際に変わった",
]
_NG_REGEX = [
    (r"第\s*\d+\s*回", "第◯回"),
    (r"[#＃](?!7119|8000)", "ハッシュタグ"),   # 新潟県の電話相談 #7119・#8000 は番号なので可
    (r"https?://|www\.|\.com|\.jp|lin\.ee|line\.me", "URL"),
    (r"\d+(?:\.\d+)?\s*(?:%|％|パーセント)", "割合の数値"),
    (r"\d+\s*(?:か月|ヶ月|カ月|ケ月|週間|日|回)\s*で", "期間・回数の約束"),
    (r"[一二三四五六七八九十]+\s*(?:か月|ヶ月|週間|日|回)\s*で", "期間・回数の約束"),
    (r"\d[\d,]*\s*円", "金額"),
    (r"\d[\d,]*\s*(?:人|名|件)以上", "実績の数字"),
    (r"(?:必ず|絶対|確実に|間違いなく)", "断定"),
    (r"(?:私|わたし|あたし|俺)(?!たち)", "一人称が「僕」でない"),
    (r"(?:頭痛|痛み)が(?:消え|なくな|ゼロ)", "痛みが消える断定"),
    (r"あなたは.{0,8}(?:頭痛|片頭痛)です", "読者の頭痛の決めつけ"),
    # 薬の減量・中止・銘柄・用量の指示はしない（薬の話は「医師・薬剤師に相談」まで）
    (r"(?:薬|くすり|痛み止め|鎮痛)(?:剤)?を(?:やめ|止め|減ら|飲まな|控え|手放)", "薬の減量・中止の指示"),
    (r"ロキソニン|(?<![ァ-ヶー])イブ(?:クイック|プロフェン)?(?![ァ-ヶー])|バファリン|カロナール|ナロン|セデス|トリプタン", "薬の銘柄"),
    # 2026-11-05 試し生成ですり抜けた言い回し
    (r"(?:人|方)(?:も|が)(?:多い|たくさん|少なくない)|だけ(?:でも|で)[、,]?\s*(?:\S{0,8}(?:が|も))?(?:違|変わ|ラク|楽)|楽になる人|ラクになる人|違ってく|違うかも|変わってく|変わるかも|変わることも|違ってくることも|重さを変え|頭の重さが変わ|ほぐれ|負担(?:も|が)?減(?:り|る)|和らぎ|和らげ|スッキリ|すっきり", "効果・割合の示唆"),
    (r"僕(?:も|自身)", "院長自身の体験・体質の話"),
    # 2026-11-11 図の要点で「水分不足が緊張型の頭痛を招く」と言い切られた（事実は「きっかけになることがある」）
    (r"招く|招き|引き起こ|が原因(?:です|だ|になる)|原因になります", "原因の言い切り"),
    (r"何か(?:が|を)?(?:起き|隠れ|潜)|ただの頭痛(?:では|じゃ)|いつもの頭痛と違|放っておく|放置すると|見逃", "不安をあおる言い方"),
    (r"(?<![師医]に)(?:相談してみ|相談(?:を|して)(?:ください|みて))|ご相談|気軽に相談", "自由文の相談への誘導"),   # 医師・薬剤師・かかりつけ医への相談は可
    (r"朝日|日光|日差し|サプリ|カフェイン|コーヒー|お酒|アルコール|ストレッチ|マッサージ|ツボ|レンジ|\d+\s*秒(?:〜|～|-)", "事実ファイルにない対処"),   # 20秒(20-20-20)は事実にあるので範囲指定だけ弾く
    # 根拠ファイルにない体の仕組みの話は書かない
    (r"内臓|骨盤|骨(?:の|が)(?:ずれ|ゆがみ|歪み)|ゆがみ(?:が|の)原因|歪み(?:が|の)原因|リンパ|老廃物|血流が(?:良くな|よくな|改善)|体質が変わ", "根拠のない体の仕組みの話"),
    # 2026-11-26 ツリーの試し生成で「血管が急に縮みやすくなる」「意識しておくと違います」が出た（事実ファイルに無い仕組み・効果の示唆）
    (r"血管(?:が|は)?(?:急に)?(?:縮|収縮|広が|拡張|ひら)|自律神経(?:が|を)(?:乱|整)", "根拠のない体の仕組みの話"),
    (r"(?:と|ば|たら|ておくと|ていると)、?(?:全然|かなり|ぐっと|だいぶ)?(?:違います|違ってきます|変わってきます|変わります)|重さが(?:変わ|軽く|減)|変わりやすい|軽くなりやすい|戻りにく|多くの場合|ほとんどの(?:頭痛|人|方)", "効果・割合の示唆"),
    (r"胃に負担|水分を失|汗をかかなくても|体が(?:温度|気温)(?:変化)?に対応|渇き|持ち越し|引きずらな|(?:運転|移動)中(?:は|も)?(?:特に)?水分", "事実ファイルにない体の仕組み・効果の話"),
    # 専門用語は読者に伝わらないので使わない（言い換える）
    (r"CGRP|セロトニン|三叉神経|閃輝暗点|後頭下筋|胸鎖乳突筋|僧帽筋|ミトコンドリア|ホルモンバランス", "専門用語"),
]

# 2026-11-10 masa指示「ありきたりな文章使いすぎ」: 11/6〜11/10の投稿で繰り返されていた言い回しは常に使わない
# 2026-11-26 masa指示「（使わない言い回し）→言い回しとして使わないという選択肢を持ってもいいけど話し言葉で回してみて」:
# 本人が挙げた12個（SOFT_PHRASES）は常時禁止をやめ、直近10本に出ていなければ使ってよい（同じ言い回しを続けない）。
# 型の名前・効果寄りの言い回し（下の CLICHES）は従来どおり使わない。
SOFT_PHRASES = ["でも実は", "そんなときって", "原因とは限りません", "痛い場所を揉", "が一番です", "が大切です",
                "見直してみる", "院でもここから始まります", "根っこ", "意識するだけ", "小さな工夫", "見えてきます"]
SOFT_RECENT_N = 10
CLICHES = ["よくある勘違い", "今日からできる", "小さなコツ",
           "ことが大切", "が大事です", "大事な視点", "価値があります",
           "ここから始まります", "一度見直", "近道です", "積み重ね",
           "抜け出すきっかけ", "疑ってみて",
           "が大事", "次のステップ", "工夫が", "プロの", "しましょう"]
# 決まり文句の繰り返しを避ける（手動投稿も含めた直近の投稿と比べる）
STOCK_PHRASES = ["本当に多いです", "意外と多いです", "多いんです", "多いです", "という方", "そんな方", "思っていませんか",
                 "ありませんか", "大丈夫です", "一緒に整理", "一緒に見て", "いろんな角度",
                 "気になりませんか", "気になることがあれば", "そういう時", "そういうとき", "向き合",
                 "そんなとき", "そんな日", "てみてください", "していませんか", "しまいませんか", "なりませんか",
                 "見直して", "目安です", "避けたほうが"]


def stock_phrase_errors(text: str, recent_texts: list[str]) -> list[str]:
    """ありきたりな言い回し(CLICHES)は常にNG。決まり文句(STOCK_PHRASES)は直近5本のどれかに出ているか、
    直近15本で2回以上出ていたらNG。書き出しの1文が直近15本の書き出しと似ていてもNG"""
    text = strip_ctas(text)
    errs = [f"ありきたりな言い回し「{c}」" for c in CLICHES if c in text and not (c == "ここから始まります" and "院でもここから始まります" in text)]
    errs += [f"言い回し「{c}」が直近{SOFT_RECENT_N}本と重なる" for c in SOFT_PHRASES
             if c in text and any(c in t for t in recent_texts[:SOFT_RECENT_N])]
    last5, last15 = recent_texts[:5], recent_texts[:15]
    for p in STOCK_PHRASES:
        if p in text and (any(p in t for t in last5) or sum(p in t for t in last15) >= 2):
            errs.append(f"決まり文句「{p}」が直近の投稿と重なる")
    head = first_sentence(text)
    if len(head) >= 6:
        for t in last15:
            if jaccard(head, first_sentence(t)) >= 0.45:
                errs.append(f"書き出しが直近の投稿「{first_sentence(t)[:20]}」と似ている")
                break
    return errs
_NG_NFKC = [(re.compile(p), name) for p, name in _NG_REGEX]


def blocked_phrases(recent_texts: list[str]) -> list[str]:
    """今回の生成で使えない言い回し（プロンプトに先に渡して、検品で落ちる回数を減らす）"""
    last5, last15 = recent_texts[:5], recent_texts[:15]
    stock = [p for p in STOCK_PHRASES if any(p in t for t in last5) or sum(p in t for t in last15) >= 2]
    soft = [p for p in SOFT_PHRASES if any(p in t for t in recent_texts[:SOFT_RECENT_N])]
    return CLICHES + soft + stock


def inspect_text(text: str, has_cta: bool) -> list[str]:
    """NGの一覧(空なら合格)"""
    errs: list[str] = []
    if not text or not text.strip():
        return ["本文が空"]
    t = unicodedata.normalize("NFKC", text)
    # #7119 は新潟県の窓口（長岡市のものではない。2026-11-10 試し生成で「長岡市の#7119」と書かれた）
    if "7119" in t and ("新潟県" not in t or re.search(r"長岡市?の\s*#?7119", t)):
        errs.append("#7119は「新潟県の救急医療電話相談」と書く（長岡市の窓口ではない）")
    if len(text) > MAX_LEN:
        errs.append(f"{MAX_LEN}字超過({len(text)}字)")
    if len(text.strip()) < 10:
        errs.append("短すぎる")
    # 公式LINE案内は決めた言い方(LINE_CTA_VARIANTS)のみ許可(NFKCでは全角@が崩れるので原文で判定)
    body = strip_ctas(text)
    nb = unicodedata.normalize("NFKC", body)
    for w in _NG_WORDS:
        if w in nb:
            errs.append(f"NGワード「{w}」")
    for rx, name in _NG_NFKC:
        if rx.search(nb):
            errs.append(f"NG表現: {name}")
    if has_cta:
        used = [c for c in LINE_CTA_VARIANTS.values() if c in text]
        if not used:
            errs.append("案内ありなのに固定文が一字一句一致していない")
        elif not any(text.rstrip().endswith(c) for c in used):
            errs.append("案内文が最後の一文になっていない")
    else:
        if "561yryuk" in t or "公式LINE" in t:
            errs.append("案内フラグなしなのにLINE案内が入っている")
    return errs


# ── 生成 ──────────────────────────────────────────

def choose_theme(entries: list[dict], now: datetime, rng: random.Random, slot: str | None = None) -> str:
    """今日まだ使っていないテーマを選ぶ(自分のログの theme で判定)。全部使っていたら全体から選ぶ。
    地域の情報の枠(LOCAL_SLOT)は長岡のテーマから選ぶ。急な頭痛の相談先は直近7日に出していれば選ばない"""
    used = {e.get("theme") for e in today_entries(entries, now)}
    # 2026-11-10: 枕・首の話が3日続いたため、直近2日に使ったテーマもなるべく避ける
    since2 = now - timedelta(days=2)
    used2 = used | {e.get("theme") for e in entries if (parse_ts(e.get("ts") or "") or since2) > since2}
    since = now - timedelta(days=EMERGENCY_EVERY_DAYS)
    recent_emerg = any(e.get("theme") == EMERGENCY_THEME and (parse_ts(e.get("ts") or "") or now) >= since
                       for e in entries)
    pool = LOCAL_THEMES if slot in LOCAL_SLOTS else [t for t in THEMES if t not in STORE_THEMES]
    if recent_emerg:
        pool = [t for t in pool if t != EMERGENCY_THEME]
    rest2 = [t for t in pool if t not in used2]
    rest = [t for t in pool if t not in used]
    return rng.choice(rest2 or rest or pool)


def seitai_reference(n: int = 8) -> str:
    """整体アカウント（@truth_body_salon・@truth_nagaoka）の頭痛の種類・対処法の投稿（zutsuu_templates.py）から数本。
    2026-11-26 masa指示「（根拠は頭痛の事実ファイルだけ）→整体のアカウントの発信内容を参考にして」"""
    try:
        from zutsuu_templates import ZUTSUU_TRUTH, ZUTSUU_NAGAOKA
        pool = list(ZUTSUU_TRUTH) + list(ZUTSUU_NAGAOKA)
    except Exception:
        return "(読み込めず)"
    rng = random.Random(datetime.now(JST).strftime("%Y%m%d%H"))
    pick = rng.sample(pool, min(n, len(pool)))
    return "\n".join("- " + t.replace("\n[COMMENT]\n", " ／続き: ").replace("\n", " ")[:220] for t in pick)


def build_prompts(fmt: str, typ: str, slot: str, allow_cta: bool, recent_texts: list[str], feedback: str = "",
                  theme: str = "", safety: str = "") -> tuple[str, str]:
    rules = RULES_FILE.read_text(encoding="utf-8") if RULES_FILE.exists() else ""
    facts = FACTS_FILE.read_text(encoding="utf-8") if FACTS_FILE.exists() else ""
    system = (
        "あなたは長岡市の整体院トゥルースの院長として、Threadsの頭痛専用アカウント(@truth_nagaoka_zutsuu)の投稿を1本書く。"
        "以下の【運用ルール】と【頭痛の事実ファイル】に従い、【整体アカウントの頭痛の発信】を中身と言い方の参考にする。"
        "頭痛についての主張は【頭痛の事実ファイル】と【整体アカウントの頭痛の発信】にあることだけを使い、どちらにもない事実・数字・病名・薬の話・体験談(「見てきた」「経験上」など)は書かない。"
        "整体アカウントの発信を参考にするときも、このアカウントの禁止事項（効果の約束・薬の量や回数の話・食べ物の名前・病名の決めつけ・断定）は守る。"
        "整体は医療の代替ではない。診断・治療・効果の約束はしない。"
        "文章は話し言葉で、読者に話しかけるように書く。"
        "出力はJSONオブジェクト1つだけ。\n\n【運用ルール】\n" + rules + "\n\n【頭痛の事実ファイル】\n" + facts
        + "\n\n【整体アカウントの頭痛の発信（@truth_body_salon・@truth_nagaoka の実際の投稿。参考）】\n" + seitai_reference()
    )
    avoid = "\n".join(f"- {t.strip()[:80].replace(chr(10), ' ')}" for t in recent_texts[:20]) or "(なし)"
    cta_line = (
        f"公式LINE案内は付けても付けなくてもよい。付ける場合は本文の最後の一文を次の固定文にし、has_ctaをtrueにする（言い方はプログラムが差し替える）: {CTA_TEXT}"
        if allow_cta else "公式LINE案内は今回は入れない(has_ctaはfalse。@561yryuk や「公式LINE」の語を本文に書かない)。"
    ) + "フォローや保存のお願いは書かない（必要ならプログラムが最後に付ける）。"
    user = (
        f"投稿枠: {slot} (JST)\n型: {typ}\n" + (f"テーマ: {theme}（このテーマの事実だけを使う）\n" if theme else "")
        + f"長さ: {FORMATS[fmt]}\n{cta_line}\n\n"
        "直近の投稿(手動投稿を含む)。次のテーマ・書き出しと被らないこと:\n" + avoid + "\n\n"
        "1文目は「読者のセリフ(「夕方になると頭が重い」など)」か「身近な場面(デスクワーク・スマホ・雨の前の日など)」で始める。ハッシュタグ・URL・絵文字の乱用はしない。\n"
        "短く書く。言いたいことは1つだけ。前置き・言い換え・まとめの繰り返しはしない。1文は40字以内。\n"
        "最後は「やり方」か「目安」で終える。「〜が変わります」「〜だけで違います」「頭の重さが変わることも」のような効果の言い方で締めない。\n"
        + ("このテーマは長岡と周りの市（見附・小千谷・柏崎・三条）で暮らす人向けの地域の情報。読者に整体院トゥルースを知ってもらい、来てみようと思ってもらうための投稿。"
           "「長岡」「雪」「冬」や市の名前など読者の暮らしの言葉を入れ、地域と店の事実(事実2〜8・18〜25)は書かれているとおりに使う。"
           "電話番号・時間を書くときは事実の文字どおりにし、変わることがあるので確かめてから、と添える。店の住所・電話番号・料金は書かない。\n"
           if theme in LOCAL_THEMES else "")
        + (f"本文の最後(公式LINE案内を付けるならその直前)に次の1行をそのまま入れる: {safety}\n" if safety
           else "今回は「※」で始まる注意書きの1行を入れない。\n")
        + "型の名前(「よくある勘違い」「今日からできる小さなコツ」など)を本文に書かない。"
        "「でも実は」「そんなときは」「〜が一番です」「〜が大切です」「見直してみてください」のような、どの投稿にも書けるありきたりな言い回しを使わない。"
        "その場面ならではの具体的な言葉で書く(ここに挙げた言葉の使い回しではなく、自分で場面を選ぶ)。体験談・お客様の話は作らない。\n"
        "書き出しは、直近の投稿の書き出しと違う場面・違う言葉にする。直近の書き出し: "
        + " / ".join(first_sentence(t)[:25] for t in recent_texts[:8] if t) + "\n"
        + ("短文は60字以内・2文まで。説明を足さない。\n" if fmt == "short" else "")
        + ("ツリー: 親投稿(text)は60字以内・2文までで、続きを読みたくなる場面か言い切りにする（答えは返信に置く）。"
           "返信(replies)は1〜2本、1本110字以内・3行まで。頭痛の種類・見分け方・対処法などの情報は返信に書く。"
           "「※」の1行を指示されたときは最後の返信の最後に入れる。\n" if fmt == "tree" else "")
        + "頭痛のタイプの話は「〜と言われています」「〜のことがあります」の言い方にし、読者の頭痛を決めつけない。\n"
        + "事実ファイルと整体アカウントの発信に無い理由づけ（体の中で何が起きているか・水分が減る理由など）は、「〜と言われています」を付けても足さない。\n"
        "薬の話は「飲む日が増えてきたら医師・薬剤師に相談」までにとどめ、やめる・減らす・銘柄・量の話はしない。\n"
        "専門用語(CGRP・セロトニン・三叉神経・筋肉の名前など)は使わず、中学生にも伝わる言葉に言い換える。\n"
        "次の言い回しは今回使えない(含めると不合格): " + "／".join(f"「{x}」" for x in blocked_phrases(recent_texts)) + "\n"
        "僕自身の体験・体質の話(「僕も気圧に弱い」など)は書かない。「楽になる人が多い」「〜するだけで違う」のような効果の言い方、"
        "「いつもの頭痛と違う何か」「放っておくと」のような不安をあおる言い方はしない。\n"
        "「相談してみませんか」など自由文の相談に誘う言い方はしない(案内は固定文だけ)。読者に問いかけるなら、答えが投稿の中にあるか、読者が自分で確かめられる問いにする。\n"
        + (IMAGE_PROMPT if fmt == "image" else "")
        + (f"\n前回の出力は次の理由で不合格だった。直すこと: {feedback}\n" if feedback else "")
        + ('\n出力形式(JSONのみ): {"text": "画像の解説文", "type": "型", "has_cta": true または false, '
           '"figure": {"title": "図の見出し", "points": ["要点1", "要点2", "要点3"], "foot": "補足(任意)"}}'
           if fmt == "image" else
           '\n出力形式(JSONのみ): {"text": "親投稿", "replies": ["返信1", "返信2(任意)"], "type": "型", "has_cta": true または false}'
           if fmt == "tree" else
           '\n出力形式(JSONのみ): {"text": "本文", "type": "型", "has_cta": true または false}')
    )
    return system, user


IMAGE_PROMPT = (
    "\n今回は画像つきの投稿。図解カード(文字だけの図)の中身(figure)と、その図を解説する本文(text)を書く。\n"
    "- figure.title: 図の見出し。16字以内。\n"
    "- figure.points: 図に並べる要点を3つ(2〜4個)。1つ18字以内の言い切り。事実ファイルにある見分け方・目安・やり方だけを書く。\n"
    "- figure.foot: 図の下に小さく添える一言。20字以内。無くてもよい。\n"
    "- text: 図の解説。1文目は読者の場面から入り、図の要点を順に短く説明する。図の文字をそのまま3つ並べ直さない。80〜150字・4行まで。\n"
    "- 図にも本文にも、効果の約束・数字の実績・料金・病名の決めつけは書かない。\n"
)


def call_llm(system: str, user: str, temperature: float = 0.8) -> str | None:
    """Gemini の無料枠で生成する（free_llm.py）。有料の API は呼ばない。全モデルだめなら None＝この枠は見送り"""
    import free_llm
    return free_llm.generate(system, user, temperature=temperature, first=LLM_FIRST_MODEL, log=log)


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
    out = {"text": text, "type": str(d.get("type") or ""), "has_cta": bool(d.get("has_cta"))}
    if isinstance(d.get("replies"), list):
        out["replies"] = [str(r).strip() for r in d["replies"] if str(r).strip()][:TREE_REPLIES_MAX]
    if isinstance(d.get("figure"), dict):
        out["figure"] = d["figure"]
    return out


_CTA_MARKERS = ("公式LINE", "561yryuk", "『診断』と送", "「診断」と送")


def attach_fixed_cta(text: str, cta: str = CTA_TEXT) -> str:
    """案内らしき文（公式LINE・@561yryuk・「診断」と送って を含む文）を本文から外し、決めた案内文 cta を最後の行に付ける"""
    out_lines = []
    for line in strip_ctas(text).splitlines():
        if any(m in line for m in _CTA_MARKERS):
            # 行の中の案内の文だけを外す（。！？で区切った文単位）
            parts = re.split(r"(?<=[。！？!?])", line)
            line = "".join(p for p in parts if not any(m in p for m in _CTA_MARKERS))
        out_lines.append(line.rstrip())
    body = "\n".join(out_lines).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    return f"{body}\n\n{cta}" if body else cta


def generate_post(slot: str, fmt: str, typ: str, allow_cta: bool, recent_texts: list[str], sim_pool: list[str],
                  generator=None, theme: str = "", safety: str = "", line_cta: str = CTA_TEXT) -> tuple[dict | None, str]:
    """検品と類似チェックを通った {text,type,has_cta} と、失敗時の理由。generator(system,user)->raw は差し替え可"""
    generator = generator or call_llm
    feedback = ""
    sim_fail = 0
    last_reason = "生成失敗"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        system, user = build_prompts(fmt, typ, slot, allow_cta, recent_texts, feedback, theme, safety)
        post = parse_generated(generator(system, user))
        if not post:
            last_reason = "LLM出力を読めない/空"
            feedback = "JSONオブジェクトのみを出力すること"
            continue
        if post["has_cta"] and not allow_cta:
            last_reason = "案内不可なのに案内つき"
            feedback = "公式LINE案内は入れない"
            continue
        replies = post.get("replies") or []
        eff = fmt
        if fmt == "tree" and not replies:
            eff = "medium"   # 返信が無ければ1投稿として扱う（長さは中くらいの基準で検品）
        if fmt != "tree":
            replies = []
            post.pop("replies", None)
        if post["has_cta"]:
            # 2026-11-17: LLMが固定文を一字一句写せず10回とも検品NG→枠見送り（20:15枠）。
            # 案内文はLLMに任せず、案内らしき文を外してから決めた言い方をプログラムで最後に付ける（ツリーは最後の返信）
            if replies:
                post["text"] = strip_ctas(post["text"])
                replies[-1] = attach_fixed_cta(replies[-1], line_cta)
            else:
                post["text"] = attach_fixed_cta(post["text"], line_cta)
        if replies:
            post["replies"] = replies
        last_part = replies[-1] if replies else post["text"]
        errs = inspect_text(post["text"], post["has_cta"] and not replies) + stock_phrase_errors(post["text"], recent_texts)
        for k, r in enumerate(replies):
            errs += [f"返信{k + 1}: {e}" for e in inspect_text(r, post["has_cta"] and k == len(replies) - 1) if "短すぎる" not in e]
            errs += [f"返信{k + 1}: {e}" for e in stock_phrase_errors(r, recent_texts) if "書き出し" not in e]
            if len(strip_ctas(r).replace(safety or SAFETY_LINE, "").strip()) > TREE_REPLY_MAX:
                errs.append(f"返信{k + 1}が長すぎる。110字以内に削る")
        if fmt == "image":
            from zutsu_figure import validate_figure, figure_text
            fig = post.get("figure")
            errs += validate_figure(fig)
            if not validate_figure(fig):
                errs += [f"図の文字: {e}" for e in inspect_text(figure_text(fig), False) if "短すぎる" not in e]
                errs += [f"図の文字: {e}" for e in stock_phrase_errors(figure_text(fig), []) if "書き出し" not in e]
        all_text = "\n".join([post["text"]] + replies)
        if safety and safety not in last_part:
            errs.append(f"受診の目安の1行「{safety}」が{'最後の返信に' if replies else ''}入っていない")
        elif not safety and "※" in all_text:
            errs.append("今回は※の注意書きを入れない（1日1本まで・頭痛のタイプの投稿だけ）")
        n_body = len(strip_ctas(post["text"]).replace(safety or SAFETY_LINE, "").strip())
        if eff == "tree" and n_body > TREE_PARENT_MAX:
            errs.append(f"親投稿が長すぎる({n_body}字)。60字以内にして続きは返信へ")
        elif eff == "short" and n_body > SHORT_BODY_MAX:
            errs.append(f"長すぎる({n_body}字)。短文は60字目安。言いたいことを1つにする")
        elif fmt == "image" and n_body > IMAGE_BODY_MAX:
            errs.append(f"長すぎる({n_body}字)。画像の解説文は150字以内に削る")
        elif eff == "medium" and n_body > (MEDIUM_BODY_MAX + 40 if theme == EMERGENCY_THEME else MEDIUM_BODY_MAX):
            errs.append(f"長すぎる({n_body}字)。本文は110字以内に削る（言いたいことを1つにする）")
        if errs:
            last_reason = "検品NG: " + " / ".join(errs)
            feedback = "; ".join(errs)
            log(f"試行{attempt}: {last_reason}")
            continue
        sim, other = max_similarity(all_text if replies else post["text"], sim_pool)
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
        discord_send("[頭痛Threads] " + msg)
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


def run(slot: str | None = None, now: datetime | None = None, dry_run: bool = False, generator=None,
        fetch=None, publisher=None, sleeper=time.sleep, rng: random.Random | None = None, image_publisher=None,
        log_path: Path | None = None, pause_path: Path | None = None, reply_publisher=None) -> int:
    """戻り値は常に0(見送りも正常終了)。失敗はDiscord通知で知らせる"""
    load_env()
    now = now or datetime.now(JST)
    token = os.environ.get("THREADS_ACCESS_TOKEN_ZUTSU", "").strip()
    uid = os.environ.get("THREADS_USER_ID_ZUTSU", "").strip()
    slot = pick_slot(slot, now)
    rng = rng or random.Random()
    fetch = fetch or fetch_recent
    publisher = publisher or publish_text
    image_publisher = image_publisher or publish_image
    reply_publisher = reply_publisher or publish_reply
    pause_path = pause_path or PAUSE_FILE

    if pause_path.exists():
        log("停止中のためスキップ（zutsu_threads_pause.json）")
        return 0

    if not dry_run and not token:
        log("トークン未設定のためスキップ")
        return 0

    entries = read_log(log_path)
    why = slot_guard(entries, slot, now)
    if why:
        log(f"見送り: {why}")
        return 0

    # 直近投稿(手動投稿を含む)。DRY_RUNはThreadsへ一切アクセスしないので、自分のログだけを使う
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

    cta_ok, cta_reason = cta_status(recent, now)
    allow_cta = cta_ok and slot in CTA_SLOTS
    log(f"枠{slot} / 案内: {'可' if allow_cta else '不可'}({cta_reason}{'' if slot in CTA_SLOTS else '・この枠は案内なし'})")

    fmt, typ = choose_format_and_type(recent, slot, rng)
    theme = choose_theme(entries, now, rng, slot)
    rs = sorted_recent(recent)
    recent_texts = [p.get("text") or "" for p in rs if p.get("text")]
    safety = safety_line_for(fmt if fmt != "tree" else "medium", theme, recent, now)
    line_cta_id = choose_cta_variant("line", entries, now, rng)
    line_cta = LINE_CTA_VARIANTS[line_cta_id]
    post, reason = generate_post(slot, fmt, typ, allow_cta, recent_texts[:20], recent_texts[:50], generator, theme, safety, line_cta)
    if not post:
        # 2026-11-10: 同じ書き出しになりやすいテーマで見送りが増えたので、テーマを替えて1回だけやり直す
        pool = LOCAL_THEMES if slot in LOCAL_SLOTS else [t for t in THEMES if t not in STORE_THEMES]
        others = [t for t in pool if t != theme and t != EMERGENCY_THEME]
        if others:
            log(f"テーマ「{theme[:20]}」で合格せず({reason[:80]})。テーマを替えてやり直す")
            theme = rng.choice(others)
            safety = safety_line_for(fmt if fmt != "tree" else "medium", theme, recent, now)
            post, reason = generate_post(slot, fmt, typ, allow_cta, recent_texts[:20], recent_texts[:50], generator, theme, safety, line_cta)
    if not post and fmt == "image":
        # 画像の枠で合格しなかったら、文字だけ(中くらい)で1回作る。1日10本の目標を落とさないため
        log(f"画像の枠で合格せず({reason[:80]})。文字だけで作り直す")
        fmt = "medium"
        safety = safety_line_for(fmt, theme, recent, now)
        post, reason = generate_post(slot, fmt, typ, allow_cta, recent_texts[:20], recent_texts[:50], generator, theme, safety, line_cta)
    if not post:
        log(f"見送り: {reason}")
        if "類似" not in reason:
            notify(f"{slot}枠を見送り: テーマを替えても合格しませんでした({reason[:150]})")
        return 0

    # 締めの言い方の記録（試し分け）。LINEの案内が無い枠では、フォロー・保存の誘いを付けることがある
    cta_id = line_cta_id if post["has_cta"] else ""
    if not post["has_cta"] and theme != EMERGENCY_THEME and other_cta_ok(entries, recent, slot, now):
        oid = choose_cta_variant("other", entries, now, rng)
        cand = (post.get("replies") or [post["text"]])[-1].rstrip() + "\n\n" + OTHER_CTA_VARIANTS[oid]
        if len(cand) <= MAX_LEN and not inspect_text(cand, False):
            if post.get("replies"):
                post["replies"][-1] = cand
            else:
                post["text"] = cand
            cta_id = oid

    fig_path = None
    if fmt == "image" and post.get("figure"):
        try:
            from zutsu_figure import render
            fig_path = render(post["figure"], BASE / "images" / "zutsu" / f"{now.strftime('%Y%m%d_%H%M')}.png")
        except Exception as e:
            log(f"図の描画に失敗({type(e).__name__}: {e})。文字だけで投稿する")
            fig_path = None

    if dry_run:
        print("----- DRY_RUN 生成結果 -----")
        print(f"[枠 {slot} / 型 {post['type'] or typ} / テーマ {theme} / 形式 {fmt} / 案内 {post['has_cta']} / 締め {cta_id or 'なし'}]")
        if post.get("figure"):
            print(f"[図] {json.dumps(post['figure'], ensure_ascii=False)} -> {fig_path}")
        print(post["text"])
        for k, r in enumerate(post.get("replies") or []):
            print(f"  └ 返信{k + 1}: " + r.replace("\n", "\n    "))
        print("----------------------------")
        return 0

    try:
        if fig_path and push_figure(fig_path):
            try:
                pid = image_publisher(token, uid, post["text"], f"{RAW_IMG}/{fig_path.name}")
            except Exception as e:
                if is_pause_error(e):
                    raise
                # 画像で出せなくても、その枠は文字だけで出す(成果物を落とさない)。コンテナ作成で失敗したので二重投稿にはならない
                log(f"画像投稿に失敗({str(e)[:120]})。文字だけで投稿する")
                notify(f"{slot}枠: 画像つき投稿に失敗したため文字だけで投稿します({str(e)[:100]})")
                fig_path = None
                pid = publisher(token, uid, post["text"])
        else:
            if fmt == "image":
                log("図を用意できなかったため文字だけで投稿する")
                fig_path = None
            pid = publisher(token, uid, post["text"])
    except Exception as e:
        log(f"投稿失敗: {e}")
        if is_pause_error(e):   # 再試行しない。停止して本人に報告
            pause_and_notify(str(e), slot, now, pause_path)
            return 0
        notify(f"{slot}枠の投稿に失敗: {str(e)[:150]}")
        return 0
    # ツリーの返信（親を出した後に続ける。返信が失敗しても親の記録は残す＝同じ枠を出し直さない）
    reply_ids = []
    parent = pid
    for k, r in enumerate(post.get("replies") or []):
        try:
            parent = reply_publisher(token, uid, r, parent)
            reply_ids.append(parent)
        except Exception as e:
            log(f"返信{k + 1}の投稿に失敗: {str(e)[:120]}")
            if is_pause_error(e):
                pause_and_notify(str(e), slot, now, pause_path)
            else:
                notify(f"{slot}枠: ツリーの返信{k + 1}を出せませんでした（親投稿は公開済み）: {str(e)[:100]}")
            break
    append_log({
        "ts": now.isoformat(timespec="seconds"), "slot": slot, "id": pid,
        "type": post["type"] or typ, "theme": theme, "has_cta": post["has_cta"],
        "text_hash": hashlib.sha256(post["text"].encode("utf-8")).hexdigest()[:16],
        "text": post["text"], "fmt": fmt, "cta_id": cta_id,
        **({"replies": post.get("replies"), "reply_ids": reply_ids} if post.get("replies") else {}),
        **({"image": fig_path.name, "figure": post.get("figure")} if fig_path else {}),
    }, log_path)
    log(f"投稿完了 id={pid} 型={post['type'] or typ} 案内={post['has_cta']}")
    return 0


def main(argv: list[str]) -> int:
    slot = None
    if "--slot" in argv:
        slot = argv[argv.index("--slot") + 1]
    elif os.environ.get("ZUTSU_SLOT"):
        slot = os.environ["ZUTSU_SLOT"].strip() or None
    if slot and slot not in SLOTS:
        log(f"不明な枠 {slot}。{SLOTS} のいずれかを指定してください")
        return 0
    dry = os.environ.get("DRY_RUN", "") in ("1", "true", "True") or "--dry-run" in argv
    return run(slot=slot, dry_run=dry)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
