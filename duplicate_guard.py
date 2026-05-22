"""
duplicate_guard.py — Threads投稿重複防止ガード（全システム共通）

使い方:
    from duplicate_guard import is_duplicate, mark_pending, mark_posted, normalize_text

    norm = normalize_text(raw_text)
    if is_duplicate(norm, "truth"):
        return  # スキップ

    mark_pending(norm, "truth")   # API前に必ず呼ぶ
    try:
        post_id = api_post(norm)
        mark_posted(norm, "truth", post_id)
    except Exception:
        pass  # PENDINGのまま残す → 次回もスキップされる
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

BASE        = Path(__file__).parent
SHARED_LOG  = BASE / "shared_posted_guard.jsonl"

# 各システムの個別投稿ログ（後方互換チェック用）
_INDIVIDUAL_LOGS: dict[str, Path] = {
    "truth":   BASE / "log_truth_posted.jsonl",
    "nagaoka": BASE / "log_nagaoka_posted.jsonl",
    "masa":    BASE / "log_masa_posted.jsonl",
}


# ── テキスト正規化 ────────────────────────────────────────

def normalize_text(text: str) -> str:
    """全システム共通の正規化。mark_posted と is_duplicate で必ず同じ結果になる。

    処理順:
      1. URL除去
      2. [COMMENT] より前の本文のみ取得
      3. 【続き】より前の本文のみ取得
      4. 最初の段落のみ（\n\n で分割）
      5. 両端の空白除去
    """
    t = re.sub(r"\n*https?://\S+", "", text).strip()

    if "[COMMENT]" in t:
        t = re.split(r"\[COMMENT\]", t)[0].strip()
    elif "\n\n【続き】\n" in t:
        t = t.split("\n\n【続き】\n")[0].strip()
    elif "\n\n" in t:
        first, rest = t.split("\n\n", 1)
        if rest.strip():
            t = first.rstrip()

    return t


def _first_line(norm: str) -> str:
    return norm.split("\n")[0].strip()


# ── 共有ログ読み書き ──────────────────────────────────────

def _read_shared() -> list[dict]:
    if not SHARED_LOG.exists():
        return []
    out = []
    for line in SHARED_LOG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _write_shared(entry: dict):
    with open(SHARED_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── 個別ログのテキストを正規化して取得（後方互換）─────────────────

def _individual_norms(acct: str) -> set[str]:
    pfile = _INDIVIDUAL_LOGS.get(acct)
    if not pfile or not pfile.exists():
        return set()
    norms: set[str] = set()
    for line in pfile.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            t = json.loads(line).get("text", "")
            if t:
                norms.add(normalize_text(t))
        except Exception:
            pass
    return norms


# ── 公開 API ──────────────────────────────────────────────

def is_duplicate(norm: str, acct: str, days: int = 7) -> bool:
    """正規化済みテキストが過去 days 日以内に投稿済みか確認する。

    チェック対象:
      - shared_posted_guard.jsonl（全システム共通）
      - log_{acct}_posted.jsonl（個別システム・後方互換、直近days日のみ）
    """
    cutoff = (datetime.now() - timedelta(days=days)).date()
    fl = _first_line(norm)

    # ① 共有ログチェック
    for e in _read_shared():
        if e.get("acct") != acct:
            continue
        try:
            entry_date = datetime.fromisoformat(e["ts"]).date()
        except Exception:
            entry_date = date.today()
        if entry_date < cutoff:
            continue
        if e.get("norm") == norm or e.get("first_line") == fl:
            return True

    # ② 個別ログチェック（後方互換・直近days日のみ）
    pfile = _INDIVIDUAL_LOGS.get(acct)
    if pfile and pfile.exists():
        for line in pfile.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                d = entry.get("date", "")
                t = entry.get("text", "")
                if not t:
                    continue
                try:
                    entry_date = datetime.strptime(d, "%Y-%m-%d").date()
                except Exception:
                    entry_date = date.today()
                if entry_date < cutoff:
                    continue
                saved_norm = normalize_text(t)
                if saved_norm == norm or _first_line(saved_norm) == fl:
                    return True
            except Exception:
                pass

    return False


def mark_pending(norm: str, acct: str):
    """API呼び出し前に必ず呼ぶ。タイムアウト後でも重複しなくなる。"""
    _write_shared({
        "ts":         datetime.now().isoformat(),
        "date":       date.today().strftime("%Y-%m-%d"),
        "acct":       acct,
        "post_id":    "PENDING",
        "norm":       norm,
        "first_line": _first_line(norm),
    })


def mark_posted(norm: str, acct: str, post_id: str):
    """投稿成功後に呼ぶ。PENDING に加えて本IDも記録する。"""
    _write_shared({
        "ts":         datetime.now().isoformat(),
        "date":       date.today().strftime("%Y-%m-%d"),
        "acct":       acct,
        "post_id":    post_id,
        "norm":       norm,
        "first_line": _first_line(norm),
    })
