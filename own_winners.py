#!/usr/bin/env python3
"""「自動投稿システム以外から出た本人の投稿」のうち伸びたものを抽出する（2026-10-05）。

目的: truth_body_salon / truth_nagaoka の投稿生成が、自動化の文面だけでなく
本人（または本人が使う外部ツール）が出して実際に伸びた投稿の「型」も参考にできるようにする。
出力: own_winners_<acct>.json（git追跡）。毎朝 generate.yml で insights.py の前に更新し、
      daily-marketing-automation（SKILL.md）がヒーロー執筆時に読む。

判定の肝: 台帳の post_id は欠けが多く、id突合だと「自動化外」を大量に誤判定する
（truthで直近100本中72本が外扱いになった）。判定は【本文の一致】で行う。
  1. 本文先頭80字の一致   2. 1行目40字の一致   3. テンプレ(穴埋め)正規表現
  4. 固定の末尾文         5. 症状語をマスクした1行目の骨格
  6. キューにあるが台帳に無く、窓内で3回以上同文 → 外部ツールの使い回しとみなし「自分」側へ戻す

API失敗時は前回ファイルを維持して exit 0（朝のパイプラインを止めない）。
全HTTPに timeout= を付ける。

使い方: python3 own_winners.py [truth|nagaoka ...]
"""
from __future__ import annotations

import ast
import collections
import datetime as dt
import glob
import json
import os
import random
import re
import statistics
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent
ACCTS = {"truth": ("TRUTH", "past_posts.json"), "nagaoka": ("NAGAOKA", "past_posts_nagaoka.json")}
WINDOW_DAYS = 90
MAX_POSTS = 6000
MAX_WINNERS = 30
WINNER_MULT = 1.5          # アカウント中央値の何倍以上を「伸びた」とするか
INSIGHT_CAP = 160          # アカウントごとのinsights取得上限
REQ_TIMEOUT = 20
TIME_BUDGET = 900          # 全体の時間予算(秒)

JA = re.compile(r"[぀-ヿ一-鿿]")
SYM = re.compile(
    "緊張型頭痛|片頭痛|頭痛|こめかみの痛み|肩甲骨まわりのこり|肩甲骨まわり|肩こり|首こり|首の痛み|"
    "腰痛|腰が痛い|頭の重さ|めまい|寝違え|ストレートネック|眼精疲労|慢性頭痛")
TAILS = [
    "長岡市で整体院を兄妹で運営しています",
    "長岡市での施術でも同じことを感じます",
    "長岡市でよく聞く悩みです",
    "長岡市の整体院でも同じ相談が多いです",
]

try:
    from duplicate_guard import normalize_text as _norm
except Exception:  # duplicate_guardが無くても動く
    def _norm(t: str) -> str:
        return re.sub(r"\n*https?://\S+", "", t or "").strip()


def comp(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def keys(t: str) -> tuple[str, str]:
    """(1行目40字, 本文80字) の空白除去キー"""
    n = _norm(t)
    return comp(n.split("\n")[0])[:40], comp(t)[:80]


def skeleton(t: str) -> str:
    n = _norm(t)
    return SYM.sub("S", comp(n.split("\n")[0]))[:30]


# ---------------------------------------------------------------- 自動化の文面集合
def _jsonl(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            yield json.loads(line)
        except Exception:
            continue


def _walk_strings(o, out: list):
    if isinstance(o, str):
        if len(o) > 20:
            out.append(o)
    elif isinstance(o, dict):
        for v in o.values():
            _walk_strings(v, out)
    elif isinstance(o, list):
        for v in o:
            _walk_strings(v, out)


def build_context(root: Path = BASE) -> dict:
    """自動投稿システムが出しうる文面の索引を作る。"""
    texts: list[str] = []      # キュー・ヒーロー・仮説（出しうる文面）
    posted: list[str] = []     # 実際に投稿した台帳の文面
    for a in ("truth", "nagaoka", "masa"):
        for d in _jsonl(root / f"log_{a}.jsonl"):
            texts += [p for p in d.get("posts", []) if isinstance(p, str)]
        for d in _jsonl(root / f"log_{a}_posted.jsonl"):
            if d.get("text"):
                posted.append(d["text"])
        h = root / f"hero_{a}.json"
        if h.exists():
            try:
                texts += [p for p in json.loads(h.read_text(encoding="utf-8")).get("posts", []) if isinstance(p, str)]
            except Exception:
                pass
    guard_norms = []
    for d in _jsonl(root / "shared_posted_guard.jsonl"):
        if d.get("norm"):
            guard_norms.append(d["norm"])
    hyp = root / "segment_hypotheses.json"
    if hyp.exists():
        try:
            _walk_strings(json.loads(hyp.read_text(encoding="utf-8")), texts)
        except Exception:
            pass

    fl, b80, skel = set(), set(), collections.Counter()
    for t in texts + posted + guard_norms:
        f, b = keys(t)
        fl.add(f)
        b80.add(b)
        skel[skeleton(t)] += 1
    posted_b80 = {keys(t)[1] for t in posted} | {keys(t)[1] for t in guard_norms}
    return {
        "fl": fl, "b80": b80, "skel": skel, "posted_b80": posted_b80,
        "templates": _template_regexes(root),
        "prefixes": _template_prefixes(root),
    }


def _template_regexes(root: Path) -> list:
    """*.py の日本語文字列定数のうち {穴} を含むものを正規表現化（穴=1〜40字）。"""
    pats: dict[str, int] = {}

    def add(parts: list[str]):
        fixed = sum(len(comp(p)) for p in parts)
        if fixed < 10 or len(parts) < 2:
            return
        pats[".{1,40}?".join(re.escape(comp(p)) for p in parts)] = fixed

    for f in glob.glob(str(root / "*.py")):
        if f.endswith("own_winners.py"):
            continue
        try:
            tree = ast.parse(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        for n in ast.walk(tree):
            if (isinstance(n, ast.Constant) and isinstance(n.value, str) and JA.search(n.value)
                    and re.search(r"\{[a-zA-Z_0-9]*\}", n.value)):
                add(re.split(r"\{[a-zA-Z_0-9]*\}", n.value))
            elif isinstance(n, ast.JoinedStr):
                parts = [""]
                for v in n.values:
                    if isinstance(v, ast.Constant):
                        parts[-1] += v.value
                    else:
                        parts.append("")
                if any(JA.search(p) for p in parts):
                    add(parts)
    out = []
    for k in pats:
        try:
            out.append(re.compile(k))
        except re.error:
            pass
    return out


def _template_prefixes(root: Path) -> set:
    """*.py の日本語文字列定数の『{穴』より前（行ごと）が14字以上なら、その先頭を冒頭一致キーにする。"""
    out = set()
    for f in glob.glob(str(root / "*.py")):
        if f.endswith("own_winners.py") or Path(f).name.startswith("test_"):
            continue
        try:
            tree = ast.parse(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        for n in ast.walk(tree):
            strs = []
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and JA.search(n.value):
                strs.append(n.value)
            elif isinstance(n, ast.JoinedStr):
                s = ""
                for v in n.values:
                    if isinstance(v, ast.Constant):
                        s += v.value
                    else:
                        break
                strs.append(s)
            for s in strs:
                for line in s.split("\n"):
                    c = comp(re.split(r"\{", line)[0])
                    if len(c) >= 14:
                        out.add(c[:60])
    return out


def is_automated(text: str, ctx: dict) -> str | None:
    """自動投稿システム由来なら判定根拠のラベル、本人の投稿なら None。"""
    f, b = keys(text)
    if b in ctx["b80"]:
        return "exact_body80"
    if f in ctx["fl"] and len(f) >= 8:
        return "exact_line1"
    c = comp(text)
    for rg in ctx["templates"]:
        if rg.match(c):
            return "template_regex"
    for p in ctx.get("prefixes", ()):
        if c.startswith(p[:min(len(p), 40)]):
            return "template_prefix"
    if any(t in text for t in TAILS):
        return "tail_sig"
    s = skeleton(text)
    if "S" in s and ctx["skel"][s] >= 2 and len(s) >= 8:
        return "skeleton"
    return None


def classify_posts(posts: list[dict], ctx: dict) -> list[dict]:
    """posts: Threads APIの行。各行に auto(ラベル or None)を付けて返す。返信と本文なしは除外。"""
    rows = []
    for x in posts:
        if x.get("is_reply") or not x.get("text"):
            continue
        x = dict(x)
        x["auto"] = is_automated(x["text"], ctx)
        rows.append(x)
    # 窓内で3回以上同文なのに台帳に無い = 外部ツールの使い回し → 「自分」側へ戻す
    cnt = collections.Counter(comp(x["text"]) for x in rows)
    for x in rows:
        if x["auto"] and keys(x["text"])[1] not in ctx["posted_b80"] and cnt[comp(x["text"])] >= 3:
            x["auto_orig"], x["auto"], x["recycled"] = x["auto"], None, True
    return rows


# ---------------------------------------------------------------- 型ラベル
def type_labels(text: str, media: str) -> list[str]:
    lab = []
    if media in ("IMAGE", "VIDEO", "CAROUSEL_ALBUM"):
        lab.append("画像・動画つき")
    lines = [l for l in text.split("\n") if l.strip()]
    if sum(1 for l in lines if "・" in l) >= 2 or sum(1 for l in lines if re.match(r"\s*[①-⑩1-9][.)）]?", l)) >= 2:
        lab.append("リスト型")
    if re.search(r"[？?]\s*$", lines[-1] if lines else ""):
        lab.append("開いた問い")
    if re.search(r"兄妹|夫婦|嫁|妹|兄|うちの|スタッフ", text):
        lab.append("人柄・関係性")
    if re.search(r"見附|津南|柏崎|三条|燕|小千谷|十日町|魚沼|上越|新潟|東京|県|駅|道の駅|[一-鿿]{1,4}市|[一-鿿]{1,4}町", text):
        lab.append("地名・出来事")
    if re.search(r"子連れ|お子様|赤ちゃん|ママ|子ども|子供|育児", text):
        lab.append("ママ・子連れ")
    if re.search(r"信号|通勤|歩く|座っ|寝る前|朝|お風呂|立ち|運転|デスク", text):
        lab.append("日常動作の指示")
    if re.search(r"今日|昨日|ご報告|@\w+|ありがとう|行ってきました", text):
        lab.append("日常・ご報告")
    if len(lines) >= 4:
        lab.append("4行以上")
    elif len(lines) <= 2:
        lab.append("短文")
    return lab


# ---------------------------------------------------------------- API
def load_env() -> dict:
    env = dict(os.environ)
    p = BASE / ".env"
    if p.exists():
        for l in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in l and not l.lstrip().startswith("#"):
                k, v = l.strip().split("=", 1)
                env.setdefault(k, v.strip().strip('"').strip("'"))
    return env


def fetch_posts(uid: str, token: str, days: int, deadline: float) -> list[dict]:
    import requests
    cut = (dt.datetime.utcnow() - dt.timedelta(days=days)).strftime("%Y-%m-%d")
    url = f"https://graph.threads.net/v1.0/{uid}/threads"
    params = {"fields": "id,text,timestamp,is_reply,media_type", "limit": 100, "access_token": token}
    out: list[dict] = []
    while url and len(out) < MAX_POSTS:
        if time.time() > deadline:
            raise RuntimeError("time budget exceeded")
        r = requests.get(url, params=params, timeout=REQ_TIMEOUT)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} {r.text[:120]}")
        j = r.json()
        out += j.get("data", [])
        if out and out[-1]["timestamp"][:10] < cut:
            break
        url, params = j.get("paging", {}).get("next"), None
    return [x for x in out if x["timestamp"][:10] >= cut]


def fetch_views(post_id: str, token: str) -> dict:
    import requests
    j = requests.get(f"https://graph.threads.net/v1.0/{post_id}/insights",
                     params={"metric": "views,likes,replies", "access_token": token}, timeout=10).json()
    out = {}
    for m in j.get("data", []):
        out[m["name"]] = m["values"][0]["value"]
    return out


def account_median(acct: str, rows: list[dict]) -> float:
    pf = BASE / ACCTS[acct][1]
    vals = []
    if pf.exists():
        try:
            vals = [p["views"] for p in json.loads(pf.read_text(encoding="utf-8")) if p.get("views")]
        except Exception:
            pass
    if not vals:
        vals = [x["views"] for x in rows if x.get("views") is not None and x["auto"]]
    return float(statistics.median(vals)) if vals else 0.0


def pick_winners(rows: list[dict], median: float) -> list[dict]:
    """自分の投稿から、本文ユニークで views >= median*1.5 の上位を最大30本。"""
    thr = median * WINNER_MULT
    best: dict[str, dict] = {}
    reps = collections.Counter(comp(x["text"]) for x in rows if not x["auto"])
    for x in rows:
        if x["auto"] or x.get("views") is None or x["views"] < thr:
            continue
        k = comp(x["text"])
        if k not in best or x["views"] > best[k]["views"]:
            best[k] = x
    top = sorted(best.values(), key=lambda x: -x["views"])[:MAX_WINNERS]
    return [{
        "id": x["id"],
        "views": x["views"],
        "date": x["timestamp"][:10],
        "media": x.get("media_type"),
        "first_line": x["text"].split("\n")[0],
        "labels": type_labels(x["text"], x.get("media_type", "")),
        "repeats_in_window": reps[comp(x["text"])],
        "text": x["text"],
    } for x in top]


def run_account(acct: str, env: dict, deadline: float) -> bool:
    suf, ppfile = ACCTS[acct]
    token, uid = env.get("THREADS_ACCESS_TOKEN_" + suf), env.get("THREADS_USER_ID_" + suf)
    if not token or not uid:
        print(f"[{acct}] SKIP: トークン/ユーザーID無し（前回ファイル維持）")
        return False
    posts = fetch_posts(uid, token, WINDOW_DAYS, deadline)
    ctx = build_context()
    rows = classify_posts(posts, ctx)
    known = {}
    pf = BASE / ppfile
    if pf.exists():
        try:
            known = {p["id"]: p for p in json.loads(pf.read_text(encoding="utf-8"))}
        except Exception:
            pass
    for x in rows:
        p = known.get(x["id"])
        x["views"] = p.get("views") if p else None
    own = [x for x in rows if not x["auto"]]
    # insights取得: 本文ごとに最大2本、画像・動画つきを優先
    random.seed(20261005)
    by = collections.defaultdict(list)
    for x in own:
        if x["views"] is None:
            by[x["text"]].append(x)
    sel = []
    for v in by.values():
        sel += random.sample(v, min(2, len(v)))
    sel.sort(key=lambda x: x.get("media_type") == "TEXT_POST")
    n = 0
    for x in sel[:INSIGHT_CAP]:
        if time.time() > deadline:
            break
        try:
            x["views"] = fetch_views(x["id"], token).get("views")
        except Exception:
            pass
        n += 1
    median = account_median(acct, rows)
    winners = pick_winners(rows, median)
    cls = collections.Counter(x["auto"] or "OWN" for x in rows)
    out = {
        "account": acct,
        "generated": dt.date.today().isoformat(),
        "window_days": WINDOW_DAYS,
        "note": "自動投稿システム以外から出た本人の投稿のうち伸びたもの。翻案必須・同一文面の再投稿は禁止（型疲労L8）。構造（フックの型・展開・締め）だけを借りる",
        "fetched_posts": len(rows),
        "own_posts": len(own),
        "classification": dict(cls),
        "account_median_views": median,
        "winner_threshold": median * WINNER_MULT,
        "winners": winners,
    }
    (BASE / f"own_winners_{acct}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{acct}] fetched={len(rows)} own={len(own)} insights={n} median={median} winners={len(winners)}")
    return True


def main(argv: list[str]) -> int:
    accts = [a for a in argv if a in ACCTS] or list(ACCTS)
    try:
        import requests  # noqa: F401
    except Exception:
        print("requests無し: 前回ファイル維持")
        return 0
    env = load_env()
    deadline = time.time() + TIME_BUDGET
    for a in accts:
        try:
            run_account(a, env, deadline)
        except Exception as e:
            print(f"[{a}] 失敗（前回ファイル維持）: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
