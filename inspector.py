#!/usr/bin/env python3
"""
inspector.py — Threads投稿の検品ゲート（完全スタンドアロン・標準ライブラリのみ）

Brain記事「AIでThreadsを事故ゼロ運用する方法」(aoi_ai, 2026-07-20)の知見を反映。
生成(generate_remix.py)と投稿直前(auto_post.py)の2箇所から呼ばれる、
生成/投稿パイプラインとは独立した最終チェック層。

設計原則（2026-07-21）:
  - 誤検知を恐れて保守的に。既存キューの正常投稿を巻き込まない。
  - 全停止しない。NGは「消費済みマーク＋補充」で処理する（呼び出し側の責務）。
  - この関数自体は判定のみ行い、キュー操作や投稿は一切行わない（副作用は
    inspection_log.jsonl への記録のみ）。

使い方:
    from inspector import inspect_post
    ok, reasons = inspect_post(text, "truth", pattern_name="hook_one_line")
    if not ok:
        ...  # 呼び出し側で再生成 or 消費済みマークして次へ
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
FEEDBACK_FILE = BASE / "feedback.json"
INSPECTION_LOG = BASE / "inspection_log.jsonl"


# ── feedback.json 読み込み（ng_words / dead_openings）─────────────────

def _load_feedback() -> dict:
    if not FEEDBACK_FILE.exists():
        return {}
    try:
        return json.loads(FEEDBACK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_ng_words() -> list[str]:
    fb = _load_feedback()
    return fb.get("ng_words", []) or []


def _load_dead_openings() -> dict:
    fb = _load_feedback()
    d = fb.get("dead_openings", {}) or {}
    return d


def _load_dead_openings_auto(account: str) -> list[str]:
    """analyze_and_tune.py が負け投稿から自動追記した書き出しNGパターン
    （feedback.json dead_openings_auto[account]）。要素は
    {"regex": ..., "motif": ..., "added": ...} 形式のdictまたは素のregex文字列の
    両方を受け付ける（将来の形式変更に耐えるため）。"""
    fb = _load_feedback()
    entries = (fb.get("dead_openings_auto", {}) or {}).get(account, []) or []
    out = []
    for e in entries:
        if isinstance(e, dict):
            r = e.get("regex", "")
        else:
            r = str(e)
        if r:
            out.append(r)
    return out


# ── 誇大・規制表現（既存ng_wordsに無い追加分。account="masa"は追加語も見る）──
_HYPE_WORDS_ALL = ["必ず治る", "絶対治る", "誰でも必ず"]
_HYPE_WORDS_MASA = ["稼げる", "儲かる"]

# 規制表現の「型」検出（2026-07-21・Brain記事有料本文第9回）:
# 断定保証型（必ず/絶対に/誰でも + 成果語）・比較優位型（業界No.1等）。
# 語の完全一致でなく型で検出することで言い換え（「絶対に治ります」等）も捕捉する。
_HYPE_TYPE_PATTERNS = [
    re.compile(r"(必ず|絶対に?|誰でも)(稼げ|儲か|治る|治り|痩せ|伸び|成功)"),
    re.compile(r"業界No\.?1|ナンバー ?ワン"),
]

# 個人情報（検品7項目④・2026-07-21）: 個人名はtruth/nagaokaで投稿禁止
# （feedback.json 2026-05-13ルール）。誤検知回避のため「さん」付き完全一致のみ。
_PERSONAL_NAMES_TRUTH_NAGAOKA = ["まぁさん", "ゆうさん"]


# ── 本文分割ヘルパー ──────────────────────────────────────────

def _main_body(text: str) -> str:
    """[COMMENT] より前（=メイン投稿として表示される本文）を返す。"""
    return (text or "").split("[COMMENT]")[0]


def _first_line(text: str) -> str:
    """メイン本文の1行目（[COMMENT]以降は対象外＝本文2投稿目扱い）。"""
    return _main_body(text).split("\n")[0].strip()


# ── ① dead_openings（死んだ書き出し）─────────────────────────────

def _check_dead_openings(text: str, account: str, pattern_name: str | None) -> list[str]:
    d = _load_dead_openings()
    exempt = set(d.get("exempt_patterns", []) or [])
    if pattern_name and pattern_name in exempt:
        return []

    first = _first_line(text)
    if not first:
        return []

    reasons = []
    pools = (d.get("all", []) or []) + (d.get(account, []) or []) \
        + _load_dead_openings_auto(account)
    for pat in pools:
        try:
            if re.search(pat, first):
                reasons.append(f"死にパターンの書き出し（{pat}）")
                break  # 1件検出できれば十分（多重報告しない）
        except re.error:
            continue
    return reasons


# ── ② 助詞・文法破綻（テンプレ穴埋み事故）─────────────────────────

# 実例（log_masa_posted.jsonl 2026-06-26/07-02/07-05）:
#   「良いコンテンツを作れば売れると思っているを続ける限り」
#   「良いコンテンツを作れば売れると思っているをやっていること」
# 述語終止形（思っている/している/できる/やっている）に直接「を」が続くのは
# 常に文法的に破綻している（名詞を挟まず目的格助詞が付いてしまうテンプレ穴埋み事故）。
# 「を(やめる|変える|直す)」等に限定せず、直後に「を」が来る時点で検出する
# （実例はいずれも「を続ける」「をやっている」「を同時にやっている」で、
#   「やめる/変える/直す」に限定すると実例を取りこぼすため広めに取る）。
_PREDICATE_WO_BREAK = re.compile(r"(?:思っている|している|できる|やっている)を")

_PARTICLE_DUP = re.compile(r"をを|がが|はは|にに")
_PUNCT_BREAK = re.compile(r"。。|ですです|ますです")

# 追加破綻型（2026-07-21第3弾・実投稿ログで検出された取りこぼし）:
#   「〜と思っているです。」= 述語終止形＋です（「〜ているんです」は正しい日本語で、
#   ん が挟まるためこのregexにはマッチしない＝誤検知しないことをテストで確認済み）
#   「〜がないではなく」= 節＋ではなく（「〜のではなく」は正しいためマッチさせない）
# 全postedログ8,545件を走査して誤検知0件・真陽性のみを確認済み（2026-07-21）。
_PREDICATE_DESU_BREAK = re.compile(r"(?:ている|ていた)です")
_NAI_DEWANAKU_BREAK = re.compile(r"ないではなく")


def _check_grammar(text: str) -> list[str]:
    reasons = []
    if _PREDICATE_WO_BREAK.search(text or ""):
        reasons.append("述語＋「を」の助詞破綻（テンプレ穴埋み事故）")
    if _PREDICATE_DESU_BREAK.search(text or ""):
        reasons.append("述語＋「です」の破綻（「〜ているです」テンプレ穴埋み事故）")
    if _NAI_DEWANAKU_BREAK.search(text or ""):
        reasons.append("「〜ないではなく」の破綻（テンプレ穴埋み事故）")
    if _PARTICLE_DUP.search(text or ""):
        reasons.append("助詞の重複（をを/がが/はは/にに）")
    if _PUNCT_BREAK.search(text or ""):
        reasons.append("句読点・語尾の重複（。。/ですです/ますです）")
    return reasons


# ── ③ 実績数字の許可リスト（捏造疑い検出）───────────────────────

# 「実績主張」パターンに限定する。目安提案の数字（来院3回・1日3分・月10回等）や
# 事例中の例示数字（フォロワー1000人等のマーケ教育コンテンツの仮の数字）は対象外。
# 対象外にするため、②④は「文脈キーワードが近傍にあるときのみ」実績主張とみなす。
_ACHIEVE_IMPROVE = re.compile(r"改善率\d+(?:\.\d+)?%?")
_ACHIEVE_STORE = re.compile(r"\d+店舗")
_ACHIEVE_CONTRACT = re.compile(r"成約率\d+(?:\.\d+)?%?")
_ACHIEVE_PEOPLE = re.compile(r"\d{1,3}(?:,\d{3})+(?:人|名)|\d{4,}(?:人|名)|\d+万人")
# 「◯年」は症状の悩み期間（「肩こり歴5年」「3年以上抱えている」等）と紛らわしいため、
# 施術・経験・実績の語が近傍にある「店の年数実績」主張に限定して検出する。
_ACHIEVE_YEARS = re.compile(r"\d+年(?:以上)?の?(?:施術|経験|実績)")
_ACHIEVE_COUNSEL = re.compile(r"\d+件のカウンセリング(?:録音)?(?:分析)?")

_PEOPLE_CONTEXT_WORDS = ("施術", "診て", "診た", "診断実績", "患者", "来院", "治療実績", "のべ", "実績")

_ALLOWED = {
    "改善率": {"改善率93.7%", "改善率93.7"},
    "成約率": {"成約率97%", "成約率47%", "成約率50%", "成約率20%"},
    "人数": {"1万人", "13,300名", "13,300人"},
    "カウンセリング": {"72件のカウンセリング分析", "72件のカウンセリング録音分析", "72件"},
}


def _match_allowed(matched: str, category: str) -> bool:
    allowed = _ALLOWED.get(category, set())
    norm = matched.replace(",", "")
    for a in allowed:
        if a.replace(",", "") in norm or norm in a.replace(",", ""):
            return True
    return False


def _check_achievement_numbers(text: str) -> list[str]:
    t = text or ""
    reasons = []

    for m in _ACHIEVE_IMPROVE.finditer(t):
        if not _match_allowed(m.group(), "改善率"):
            reasons.append(f"許可リスト外の改善率主張: {m.group()}")

    for m in _ACHIEVE_STORE.finditer(t):
        reasons.append(f"許可リスト外の店舗数主張: {m.group()}")

    for m in _ACHIEVE_CONTRACT.finditer(t):
        if not _match_allowed(m.group(), "成約率"):
            reasons.append(f"許可リスト外の成約率主張: {m.group()}")

    for m in _ACHIEVE_PEOPLE.finditer(t):
        window = t[max(0, m.start() - 12): m.end() + 12]
        if any(k in window for k in _PEOPLE_CONTEXT_WORDS):
            if not _match_allowed(m.group(), "人数"):
                reasons.append(f"許可リスト外の実績人数主張: {m.group()}")

    for m in _ACHIEVE_YEARS.finditer(t):
        reasons.append(f"許可リスト外の年数実績主張: {m.group()}")

    for m in _ACHIEVE_COUNSEL.finditer(t):
        if not _match_allowed(m.group(), "カウンセリング"):
            reasons.append(f"許可リスト外のカウンセリング件数主張: {m.group()}")

    return reasons


# ── ④ 既存ng_words＋誇大・規制表現 ─────────────────────────────

def _check_hype_and_ng(text: str, account: str) -> list[str]:
    t = text or ""
    reasons = []

    ng_words = _load_ng_words()
    for w in ng_words:
        if w and w in t:
            reasons.append(f"NGワード: {w}")

    for w in _HYPE_WORDS_ALL:
        if w in t:
            reasons.append(f"誇大・規制表現: {w}")

    for pat in _HYPE_TYPE_PATTERNS:
        m = pat.search(t)
        if m:
            reasons.append(f"規制表現の型（断定保証/比較優位）: {m.group()}")

    if account == "masa":
        for w in _HYPE_WORDS_MASA:
            if w in t:
                reasons.append(f"誇大・規制表現(masa): {w}")

    if account in ("truth", "nagaoka"):
        for nm in _PERSONAL_NAMES_TRUTH_NAGAOKA:
            if nm in t:
                reasons.append(f"個人名の混入: {nm}")

    return reasons


# ── ⑤ URL本数（本文のみ・[COMMENT]は除く）───────────────────────

_URL_RE = re.compile(r"https?://\S+")


def _check_url_count(text: str) -> list[str]:
    body = _main_body(text)
    urls = _URL_RE.findall(body)
    if len(urls) >= 2:
        return [f"本文URL過多（{len(urls)}個）"]
    return []


# ── ⑥ 1行目の長さ（60字超でNG。40字以内推奨は運用上の目安でありNGにはしない）──

_FIRST_LINE_MAX = 60


def _check_first_line_length(text: str) -> list[str]:
    first = _first_line(text)
    if len(first) > _FIRST_LINE_MAX:
        return [f"1行目が{_FIRST_LINE_MAX}字超（{len(first)}字）"]
    return []


# ── ログ記録 ──────────────────────────────────────────────

def _log_ng(account: str, reasons: list[str], text: str):
    try:
        entry = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "account": account,
            "reason": "; ".join(reasons)[:200],
            "text_head": (text or "")[:50],
        }
        with open(INSPECTION_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # ログ失敗で検品自体を止めない（全停止しない原則）


# ── 公開API ──────────────────────────────────────────────

def inspect_post(text: str, account: str = "truth", pattern_name: str | None = None,
                  log: bool = True) -> tuple[bool, list[str]]:
    """投稿本文を検品する。

    Args:
        text: 検品対象の本文全文（[COMMENT]で分割された複数投稿を含む・切り詰め禁止）
        account: "truth" / "nagaoka" / "masa"
        pattern_name: 生成元のパターン名（分かる場合）。dead_openingsのexempt_patternsに
                      該当すれば①はスキップする。
        log: True の場合、NG時に inspection_log.jsonl へ記録する。生成ループ内の
             再抽選試行など「まだ何も確定していない」段階の呼び出しでは log=False を
             推奨（ノイズ防止・verify_system.pyの検証実行で誤ってWARNが積み上がるのを防ぐ）。

    Returns:
        (ok, reasons): ok=Trueなら合格。ok=Falseならreasonsに不合格理由（複数可）。
    """
    if not text or not text.strip():
        return True, []

    reasons: list[str] = []
    reasons += _check_dead_openings(text, account, pattern_name)
    reasons += _check_grammar(text)
    reasons += _check_achievement_numbers(text)
    reasons += _check_hype_and_ng(text, account)
    reasons += _check_url_count(text)
    reasons += _check_first_line_length(text)

    ok = len(reasons) == 0
    if not ok and log:
        _log_ng(account, reasons, text)
    return ok, reasons


if __name__ == "__main__":
    import sys
    sample = sys.argv[1] if len(sys.argv) > 1 else "知っていましたか？肩こりの原因"
    acct = sys.argv[2] if len(sys.argv) > 2 else "truth"
    print(inspect_post(sample, acct))
