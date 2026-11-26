"""無料のGemini（無料枠）で文章を作る共通部品。

2026-11-26 masa指示「課金はなし 無料枠の範囲内で完結させて」で、頭痛Threads(zutsu_threads.py)と
ダイエットThreads(diet_threads.py)の生成を Anthropic API（従量課金）からここへ切り替えた。

- キーは GEMINI_API_KEY（GitHub Secrets／Macでは ~/.claude/secrets/gemini.env）。このキーのプロジェクトは無料枠で、
  上限を超えると 429 で断られるだけ（課金されない）。有料の API はこのリポジトリから呼ばない。
- 429(枠切れ)・503(混雑)・404(廃止)・400(設定を受け付けない) は待たずに次のモデルへ。全部だめなら None
  （呼び出し側はその枠の投稿を見送る＝課金で埋め合わせない）。
- 429 を返したモデルは、その実行の間は二度と試さない（無駄打ちしない）。
- thinkingBudget:0 は必須（思考トークンが出力枠を食って本文が途中で切れるため）。finishReason が STOP 以外は使わない。
- モデルの無料枠はモデルごとに別なので、ほかのリポジトリが主に使うモデル（3.8=ブログ・3.5=IG・3.7=cloud-daily-tasks・
  2.5=最後の砦）を後ろに回し、あまり使われていない 3.6 / 3-flash-preview を先にする。2026-11-26 に実機で応答を確認した順。
  環境変数 GEMINI_MODELS（カンマ区切り）で差し替えられる。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODELS = [
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
]
_exhausted: set[str] = set()   # この実行の間に 429（無料枠切れ）を返したモデル


def load_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    for path in ("~/.claude/secrets/gemini.env",):
        try:
            with open(os.path.expanduser(path), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("export "):
                        line = line[len("export "):]
                    if line.startswith("GEMINI_API_KEY"):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def models(first: str | None = None) -> list[str]:
    env = [m.strip() for m in os.environ.get("GEMINI_MODELS", "").split(",") if m.strip()]
    ms = env or list(DEFAULT_MODELS)
    if first and first in ms:
        ms = [first] + [m for m in ms if m != first]
    return ms


def generate(system: str, user: str, temperature: float = 0.8, max_tokens: int = 2048,
             first: str | None = None, log=print, opener=urllib.request.urlopen) -> str | None:
    """本文を返す。無料枠のモデルを順に試し、全部だめなら None。"""
    key = load_key()
    if not key:
        log("GEMINI_API_KEY が無いため生成しない（有料APIには切り替えない）")
        return None
    payload = json.dumps({
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }, ensure_ascii=False).encode()
    for model in models(first):
        if model in _exhausted:
            continue
        req = urllib.request.Request(f"{BASE}/{model}:generateContent?key={key}", data=payload,
                                     headers={"content-type": "application/json"}, method="POST")
        try:
            with opener(req, timeout=60) as r:
                d = json.loads(r.read())
            cand = (d.get("candidates") or [{}])[0]
            text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", [])).strip()
            if text and cand.get("finishReason") == "STOP":
                return text
            log(f"LLM不採用({model}): finishReason={cand.get('finishReason')}")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _exhausted.add(model)
            log(f"LLM失敗({model}): HTTP {e.code}")
        except Exception as e:
            log(f"LLM失敗({model}): {type(e).__name__}")
    return None
