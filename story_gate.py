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
  python3 story_gate.py --mode pool|qa|offer|features|daily [--repo OWNER/REPO]
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
FRIDAY_WEEKDAY = 4                      # nagaoka の特徴シリーズ曜日（後方互換。正は FEATURES_WEEKDAY）
FEATURES_WEEKDAY = {"nagaoka": 4, "headache": 2, "diet": 5}   # 2026-10-21: アカウント別（nagaoka=金 / headache=水 / diet=土）
STORY_PLAN_MAX_AGE_HOURS = 36
# 2026-10-21: diet（ダイエット特化）・headache（頭痛特化）を追加。曜日は既存と同じ種類が重ならないようにずらした
QA_WEEKDAY = {"nagaoka": 5, "truth": 6, "headache": 4, "diet": 1}
OFFER_WEEKDAY = {"nagaoka": 2, "truth": 1, "headache": 0, "diet": 3}

# モードごとの対象アカウントと plan.kind（ig-story-*.yml のステップ構成と同じ）
MODES = {
    "pool": {"accounts": ["nagaoka", "truth", "headache", "diet"], "kind": None},
    "qa": {"accounts": ["nagaoka", "truth", "headache", "diet"], "kind": "qa_video"},
    "offer": {"accounts": ["nagaoka", "truth", "headache", "diet"], "kind": "offer"},
    "features": {"accounts": ["nagaoka", "headache", "diet"], "kind": "friday_features"},   # ig-story-friday.yml
    # 2026-10-21: @masahide_takahashi_ の日次ストーリー（ig-story.yml → ig_daily.py --slot story）。plan は state/story_plan.json
    #   （アカウント別ディレクトリではない）。"masa" は plan_path() で解決する擬似アカウント名。
    "daily": {"accounts": ["masa"], "kind": None},
}
KNOWN_KINDS = ("friday_features", "offer", "qa_video")

# 2026-10-21 追加アカウント（頭痛特化=headache / ダイエット特化=diet）。トークン登録前（＝一度も動いていない）は story_plan.json が
# リポジトリに無い（取得は None）。その状態で毎回「plan不明→起動」と判定すると、起動前ゲートが効かなくなり無料枠を無駄にするため、
# planが無い間は判定に含めない（専用日でも起動しない）。初回は nagaoka/truth の通常ストーリーのrun（同じworkflow内に新アカウントの
# ステップがある）で plan ができ、一度でもplanができれば、以後は nagaoka / truth と同じ扱い（専用日の起動もここから）。
# 2026-10-23: diet はトークン登録済み（@truth_nagaoka_daietto・稼働中）なので外した。残していると plan が無い間ずっと
#   「判定に含めない」になり、ストーリーの起動自体がスキップされて一度も始まらなかった。headache はトークン未登録のため残す。
NEW_ACCOUNTS = ("headache",)

PLAN_PATH = "state/{acct}/story_plan.json"
PLAN_PATH_OVERRIDE = {"masa": "state/story_plan.json"}

# --- ig_daily.run_story() の時間帯ガードの写し（tests/test_story_gate.py が一致を検査）---
DAILY_PARTS = 4
DAILY_BOUNDS_HM = (1200, 1730, 2100)    # 〜11:59=起(0) / 12:00〜17:29=承(1) / 17:30〜20:59=転(2) / 21:00〜=結(3)
DAILY_LOOKAHEAD_MIN = 10                # run は枠の少し後に始まる。10分後でも何も起きない時だけスキップ


def plan_path(acct):
    return PLAN_PATH_OVERRIDE.get(acct) or PLAN_PATH.format(acct=acct)


def daily_target(hm):
    """ig_daily.run_story の target（その時刻に投稿してよい最後のパート番号）。"""
    b0, b1, b2 = DAILY_BOUNDS_HM
    return 0 if hm < b0 else 1 if hm < b1 else 2 if hm < b2 else 3


def _daily_nothing_happens(plan, now):
    """ig_daily.run_story() が「何もせず終了する」条件だけを写す。
    今日(JST)のplanがあり、posted が 4 以上、または posted > target（この時間帯のパートは投稿済み）。
    planなし・日付違い・形式不正は run が新規plan生成/投稿に進みうるので必ず起動する。"""
    if not isinstance(plan, dict) or not plan:
        return False, "masa: planなし/不明(新規生成の可能性)"
    if plan.get("date") != now.strftime("%Y-%m-%d"):
        return False, f"masa: planの日付が今日ではない({plan.get('date')})"
    posted = plan.get("posted")
    if not isinstance(posted, int) or isinstance(posted, bool):
        return False, "masa: posted不正"
    if posted >= DAILY_PARTS:
        return True, f"masa: 本日の連作{DAILY_PARTS}枚は投稿済み(posted={posted})"
    later = now + timedelta(minutes=DAILY_LOOKAHEAD_MIN)
    if later.strftime("%Y-%m-%d") != now.strftime("%Y-%m-%d"):
        return False, "masa: 日付またぎ付近(判定しない)"
    target = daily_target(int(later.strftime("%H%M")))
    if posted > target:
        return True, f"masa: この時間帯のパートは投稿済み(posted={posted} > target={target})"
    return False, f"masa: 未投稿のパートあり(posted={posted} <= target={target})"


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
        "friday": wd == FEATURES_WEEKDAY.get(acct),
    }


def nothing_happens(mode, acct, plan, now):
    """このアカウント・このモードの実行が「必ず何もせず終了する」なら (True, 理由)、
    投稿・plan生成の可能性が少しでもあれば (False, 理由)。
    plan: story_plan.json の dict / 無ければ {} / 取得失敗は None（呼び出し側で扱う）。"""
    if mode == "daily":
        return _daily_nothing_happens(plan, now)
    wd = now.weekday()
    fl = _acct_weekday_flags(acct, wd)

    # --- 曜日だけで決まる「何もしない」（pool=通常ストーリーのみ） ---
    if mode == "pool":
        if fl["offer_day"]:
            return True, f"{acct}: オファー専用日(通常ストーリー休止)"
        if fl["qa_day"]:
            return True, f"{acct}: Q&A専用日(通常ストーリー休止)"
        if acct in FEATURES_WEEKDAY and fl["friday"]:
            return True, f"{acct}: 特徴シリーズ専用日(通常ストーリー休止)"

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
        if acct in NEW_ACCOUNTS and plans.get(acct) is None:
            # 専用日でも起動しない（トークン登録前に専用日ごとに空振りrunが出て無料枠を食うため）。トークン登録後は、
            # nagaoka/truth の通常ストーリーのrunで同じworkflow内の新アカウントのステップが動いて story_plan.json ができ、以後は通常判定になる
            reasons.append(f"{acct}: 未稼働(planなし)=判定に含めない")
            continue
        ok, why = nothing_happens(mode, acct, plans.get(acct), now)
        reasons.append(why)
        if not ok:
            return False, "; ".join(reasons)
    return True, "; ".join(reasons)


# --- 取得（Mac: gh CLI） -------------------------------------------------------------
def fetch_plan_gh(gh, repo, acct):
    """gh api でmainのplanを取得。取得できなければ（404含む）None＝ゲートは判定せず必ず起動する。
    （404を「planなし」と扱うと、権限不足のトークンでも404になり得るため誤ってスキップする恐れがある）"""
    path = plan_path(acct)
    try:
        out = subprocess.run([gh, "api", f"repos/{repo}/contents/{path}"],
                             capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    if out.returncode != 0:
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
