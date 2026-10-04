#!/usr/bin/env python3
"""
X（旧Twitter）整体アカウント自動投稿（2026-10-04 新設）

auto_post.yml の常駐ループから10分ごとに呼ばれ、決まった時刻枠に1本ずつ投稿する。
本文は truth の Threads 投稿済みログ（生成時＋投稿直前の二段検品を通過済み）から再利用する。

- 枠: X_SLOTS（JST時・既定 8,12,15,18,21）。枠を過ぎても未投稿なら1本ずつ追いつく（間隔は最低60分）
- URL入り投稿は使わない（X APIはリンク付き投稿が約13倍の単価のため。2026-04〜）
- 文字数はXの重み付き計算で280以内（日本語は1文字=2）
- X_* の4キーが未設定なら何もせず終了（exit 0）
- DRY_RUN=1 なら送信せず候補を表示

なぜ: Threadsで検品済みの本文をそのまま流用すれば、生成コストと検品の二重実装が要らない。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).parent
SOURCE_LOG = BASE / "log_truth_posted.jsonl"
X_LOG = BASE / "log_x_truth_posted.jsonl"
JST = timezone(timedelta(hours=9))
API_URL = "https://api.x.com/2/tweets"
LOOKBACK_DAYS = 7
MIN_GAP_MIN = 60
KEYS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")

try:
    import inspector as _inspector
except Exception:
    _inspector = None


def load_env() -> None:
    env = BASE / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def weighted_len(text: str) -> int:
    """twitter-text の重み付き文字数（URLは23換算・ラテン系は1、それ以外は2）。"""
    text = re.sub(r"https?://\S+", "x" * 23, text)
    n = 0
    for ch in text:
        c = ord(ch)
        if (c <= 0x10FF or 0x2000 <= c <= 0x200D or 0x2010 <= c <= 0x201F
                or 0x2032 <= c <= 0x2037):
            n += 1
        else:
            n += 2
    return n


def slots() -> list[int]:
    raw = os.environ.get("X_SLOTS", "8,12,15,18,21")
    return sorted(int(s) for s in raw.split(",") if s.strip().isdigit())


def pick_candidate(x_posted: list[dict]) -> str | None:
    used = {normalize(r.get("text", "")) for r in x_posted}
    since = (datetime.now(JST) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    for row in reversed(read_jsonl(SOURCE_LOG)):
        text = (row.get("text") or "").strip()
        if not text or row.get("date", "") < since:
            continue
        if normalize(text) in used or "http" in text or "[COMMENT]" in text:
            continue
        if weighted_len(text) > 280:
            continue
        if _inspector is not None:
            try:
                ok, _ = _inspector.inspect_post(text, "truth", log=False)
                if not ok:
                    continue
            except Exception:
                pass
        return text
    return None


def oauth_header(method: str, url: str) -> str:
    """OAuth 1.0a（ユーザーコンテキスト）。JSONボディは署名対象に含めない。"""
    params = {
        "oauth_consumer_key": os.environ["X_API_KEY"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": os.environ["X_ACCESS_TOKEN"],
        "oauth_version": "1.0",
    }
    q = lambda s: urllib.parse.quote(s, safe="")
    param_str = "&".join(f"{q(k)}={q(v)}" for k, v in sorted(params.items()))
    base = "&".join([method, q(url), q(param_str)])
    key = f"{q(os.environ['X_API_SECRET'])}&{q(os.environ['X_ACCESS_TOKEN_SECRET'])}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{q(k)}="{q(v)}"' for k, v in sorted(params.items()))


def post_tweet(text: str) -> tuple[bool, str]:
    req = urllib.request.Request(
        API_URL, data=json.dumps({"text": text}).encode(), method="POST",
        headers={"Authorization": oauth_header("POST", API_URL),
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return True, json.loads(r.read())["data"]["id"]
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        return False, f"HTTP {e.code}: {body}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def notify(text: str) -> None:
    try:
        import stall_check
        if stall_check.discord_send(text):
            return
    except Exception:
        pass
    print(f"[通知未送信] {text}")


def main() -> int:
    load_env()
    dry = os.environ.get("DRY_RUN") == "1"
    if not dry and not all(os.environ.get(k) for k in KEYS):
        print("X: キー未設定のためスキップ")
        return 0

    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")
    x_posted = read_jsonl(X_LOG)
    today_rows = [r for r in x_posted if r.get("date") == today]
    due = sum(1 for h in slots() if h <= now.hour)
    if not dry and len(today_rows) >= due:
        print(f"X: 本日 {len(today_rows)}/{due} 本 投稿済み → 待機")
        return 0
    if not dry and today_rows:
        last = datetime.fromisoformat(today_rows[-1]["at"])
        if now - last < timedelta(minutes=MIN_GAP_MIN):
            print("X: 前回から60分未満 → 待機")
            return 0

    text = pick_candidate(x_posted)
    if text is None:
        print("X: 使える候補なし（直近7日の未使用・URLなし・280以内）")
        return 0
    if dry:
        print(f"[DRY_RUN] 重み{weighted_len(text)}/280\n{text}")
        return 0

    ok, info = post_tweet(text)
    if not ok:
        print(f"X投稿失敗: {info}")
        if "duplicate" in info.lower():
            # 同文はXが弾く。使用済みに記録して次回は別の本文を選ばせる（日次本数には数えない）
            with X_LOG.open("a") as f:
                f.write(json.dumps({"date": "", "at": now.isoformat(timespec="seconds"),
                                    "tweet_id": None, "text": text}, ensure_ascii=False) + "\n")
            return 0
        # 401=キー失効 / 402・403=クレジット切れや権限不足。自動では直らないので知らせる
        if info.startswith(("HTTP 401", "HTTP 402", "HTTP 403")):
            notify(f"🚨X整体アカウント投稿失敗: {info}")
        return 0
    with X_LOG.open("a") as f:
        f.write(json.dumps({"date": today, "at": now.isoformat(timespec="seconds"),
                            "tweet_id": info, "text": text}, ensure_ascii=False) + "\n")
    print(f"X投稿OK: {info}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
