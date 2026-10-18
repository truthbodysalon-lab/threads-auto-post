#!/usr/bin/env python3
"""ストーリー系ワークフローの「起動前ゲート」（Actions無料枠対策 M10 / 2026-10-18）

目的:
  GitHub Actions は 1ジョブごとに1分へ切り上げて課金される。ig-story-pool / qa / offer / friday は
  Mac定刻起動(launchd)やクラウド中央ディスパッチャが枠ごとに起動するが、
  「本日分は完了済み」「今日はそのアカウントの投稿日ではない」ときは起動しても必ず空振り
  （約30秒でも1分課金）。このモジュールは、起動する前に **確実に何も起きない** と判定できる時だけ
  「起動を省略してよい」と返す。

設計原則（誤ってスキップすると投稿が止まるため）:
  - 判定は ig_story_pool.py の run() / run_*_cycle() の「何もせず sys.exit(0) する条件」だけを写したもの。
  - 少しでも想定外（取得失敗・JSON不正・dry_runのplan・未知のkind・日付欠落）なら必ず「起動する」(フェイルオープン)。
  - 投稿ロジック本体には触れない。ゲートが誤判定した疑いがあるときは、環境変数 STORY_GATE=off、
    またはファイル ~/.claude/ig_story_gate_off（Mac側）、schedule_table.json の gate キー削除（クラウド側）で
    全て元の挙動（毎枠起動）に戻る。

使い方（Macの定刻起動スクリプトから）:
  python3 story_gate.py --mode pool|qa|offer|features [--repo OWNER/REPO]
  終了コード: 10=起動を省略してよい（理由を標準出力） / 0=起動する / それ以外=起動する（フェイルオープン）

このファイルの複製が threads-auto-post/story_gate.py にある（クラウド中央ディスパッチャ用）。
必ず同一内容に保つこと（tests/test_story_gate.py が定数の同期を検査する）。
"""
import argparse
import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

# --- ig_story_pool.py / story_offer.py の定数の写し（tests/test_story_gate.py が一致を検査） ---
STORY_GATE_DAYS = 2
FRIDAY_WEEKDAY = 4
STORY_PLAN_MAX_AGE_HOURS = 36
QA_WEEKDAY = {"nagaoka": 5, "truth": 6}
OFFER_WEEKDAY = {"nagaoka": 2, "truth": 1}

# モードごとの対象アカウントと plan.kind（ig-story-*.yml のステップ構成と同じ）
MODES = {
    "pool": {"accounts": ["nagaoka", "truth"], "kind": None},
    "qa": {"accounts": ["nagaoka", "truth"], "kind": "qa_video"},
    "offer": {"accounts": ["nagaoka", "truth"], "kind": "offer"},
    "features": {"accounts": ["nagaoka"], "kind": "friday_features"},   # ig-story-friday.yml
}
KNOWN_KINDS = ("friday_features", "offer", "qa_video")

PLAN_PATH = "state/{acct}/story_plan.json"


def _created_at(plan):
    """ig_story_pool._plan_created_at と同じ（created_at → 無ければ date の 00:00 JST）。"""
    raw = plan.get("created_at")
    if raw:
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            return dt
        except Exception:
            pass
    ds = plan.get("date")
    if ds:
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
            return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=JST)
        except Exception:
            pass
    return None


def _acct_weekday_flags(acct, wd):
    return {
        "qa_day": wd == QA_WEEKDAY.get(acct),
        "offer_day": wd == OFFER_WEEKDAY.get(acct),
        "friday": wd == FRIDAY_WEEKDAY,
    }


def nothing_happens(mode, acct, plan, now):
    """このアカウント・このモードの実行が「必ず何もせず終了する」なら (True, 理由)、
    投稿・plan生成の可能性が少しでもあれば (False, 理由)。
    plan: story_plan.json の dict / 無ければ {} / 取得失敗は None（呼び出し側で扱う）。"""
    wd = now.weekday()
    fl = _acct_weekday_flags(acct, wd)

    # --- 曜日だけで決まる「何もしない」（pool=通常ストーリーのみ） ---
    if mode == "pool":
        if fl["offer_day"]:
            return True, f"{acct}: オファー専用日(通常ストーリー休止)"
        if fl["qa_day"]:
            return True, f"{acct}: Q&A専用日(通常ストーリー休止)"
        if acct == "nagaoka" and fl["friday"]:
            return True, f"{acct}: 金曜は特徴シリーズ専用(通常ストーリー休止)"

    if not isinstance(plan, dict):
        return False, f"{acct}: plan不明"
    if plan.get("dry_run"):
        return False, f"{acct}: dry_runのplan(判定対象外)"

    created = _created_at(plan) if plan else None
    valid = created is not None and (now - created).total_seconds() / 3600 <= STORY_PLAN_MAX_AGE_HOURS
    kind = plan.get("kind") if plan else None
    frames = plan.get("frames")
    posted = plan.get("posted", 0)
    complete = isinstance(frames, list) and isinstance(posted, int) and posted >= len(frames)

    if mode == "pool":
        if valid:
            if kind in KNOWN_KINDS:
                return True, f"{acct}: 継続中の{kind} planがあり通常フローは触らない"
            if kind is None:
                if complete:
                    return True, f"{acct}: 通常ストーリー本日分完了(posted={posted}/{len(frames)})"
                return False, f"{acct}: 通常ストーリー未完了(posted={posted})"
            return False, f"{acct}: 未知のkind={kind!r}"
        # planなし/期限切れ → 2日ゲート。plan.date は「最後の非dry planの作成日」以下なので、
        # plan.date からの経過日数が2日未満なら、実際の最終日(>=plan.date)からも2日未満＝必ずゲートで止まる。
        ds = plan.get("date") if plan else None
        if ds:
            try:
                last = datetime.strptime(ds, "%Y-%m-%d").date()
            except Exception:
                return False, f"{acct}: plan.date不正"
            elapsed = (now.date() - last).days
            if 0 <= elapsed < STORY_GATE_DAYS:
                return True, f"{acct}: 2日ゲート中(前回plan {ds}から{elapsed}日)"
            return False, f"{acct}: 2日ゲート明け(前回plan {ds}から{elapsed}日)"
        return False, f"{acct}: planなし(新規生成の可能性)"

    # --- qa / offer / features（専用モード）---
    want_kind = MODES[mode]["kind"]
    allow_create = {"qa": fl["qa_day"], "offer": fl["offer_day"], "features": fl["friday"]}[mode]
    if valid and kind == want_kind:
        if complete:
            return True, f"{acct}: {want_kind}本日分完了(posted={posted}/{len(frames)})"
        return False, f"{acct}: {want_kind}未完了(posted={posted})"
    if valid and kind not in (None,) + KNOWN_KINDS:
        return False, f"{acct}: 未知のkind={kind!r}"
    # 同種の有効planが無い: 新規作成できる曜日(allow_create)か、他種planの上書き(専用日)なら動く。そうでなければ何もしない
    if allow_create:
        return False, f"{acct}: 専用日で新規plan作成の可能性"
    return True, f"{acct}: 専用日ではなく継続対象の{want_kind} planも無い"


def decide(mode, plans, now=None):
    """plans: {acct: dict|{}|None}。戻り値 (skip: bool, reason: str)。"""
    if now is None:
        now = datetime.now(JST)
        fake = os.environ.get("STORY_GATE_NOW", "").strip()   # テスト用 "YYYY-MM-DD HH:MM"（本番では未設定）
        if fake:
            now = datetime.strptime(fake, "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    if mode not in MODES:
        return False, f"未知のmode={mode}"
    if os.environ.get("STORY_GATE", "").strip().lower() == "off":
        return False, "STORY_GATE=off"
    reasons = []
    for acct in MODES[mode]["accounts"]:
        ok, why = nothing_happens(mode, acct, plans.get(acct), now)
        reasons.append(why)
        if not ok:
            return False, "; ".join(reasons)
    return True, "; ".join(reasons)


# --- 取得（Mac: gh CLI） -------------------------------------------------------------
def fetch_plan_gh(gh, repo, acct):
    """gh api でmainのplanを取得。ファイル無し(404)は {}、その他の失敗は None。"""
    path = PLAN_PATH.format(acct=acct)
    try:
        out = subprocess.run([gh, "api", f"repos/{repo}/contents/{path}"],
                             capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    if out.returncode != 0:
        if "404" in (out.stderr + out.stdout) or "Not Found" in (out.stderr + out.stdout):
            return {}
        return None
    try:
        meta = json.loads(out.stdout)
        return json.loads(base64.b64decode(meta["content"]).decode("utf-8"))
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", required=True)
    p.add_argument("--repo", default="truthbodysalon-lab/ig-autopost-cloud")
    p.add_argument("--gh", default=os.environ.get("GH_BIN", "gh"))
    a = p.parse_args()
    try:
        if a.mode not in MODES:
            print(f"RUN 未知のmode={a.mode}")
            return 0
        plans = {acct: fetch_plan_gh(a.gh, a.repo, acct) for acct in MODES[a.mode]["accounts"]}
        skip, why = decide(a.mode, plans)
    except Exception as e:  # noqa: BLE001  何があっても起動側へ倒す
        print(f"RUN gate例外のためフェイルオープン: {type(e).__name__}: {e}")
        return 0
    print(("SKIP " if skip else "RUN ") + why)
    return 10 if skip else 0


if __name__ == "__main__":
    sys.exit(main())
