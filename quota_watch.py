#!/usr/bin/env python3
"""GitHub Actions 無料枠の使用量を 1日1回 見張り、Discord に知らせる（公開repo版・Macが寝ていても検知するための保険）

背景（2026-10-20 院長方針「あくまで無料の枠で利用」＝予算を上げない・有料にしない）:
  非公開repoの Actions は月2,000分の無料枠をアカウント全体で共有する。使い切ると定期処理が全て止まる。
  Mac の quota_guard が 1日2回 使用量を数え、1,750分で自動で Mac 代行へ切り替える（gh workflow disable・定刻起動の停止などは Mac 側が行う）。
  このスクリプトは公開repoの常駐ループ(dispatcher.py)から呼ばれ、Mac が寝ていても『近づいている』『切替が必要』を Discord に出す。

  ★このスクリプトは非公開repoの設定を一切変えない（読み取りのみ）。切替は必ず Mac 側。
  ★公開ログ・公開ファイルには 使用量の数字と フラグだけを出す（秘密・詳細は書かない）。

動き（1日1回・JST 12:20 以降の最初のサイクル）:
  1. Mac の状態(quota_state_mac.json: 月・最終確認時刻・予告済み/切替済み)と自分の状態(quota_watch_state.json)を読む
  2. Mac が今月すでに切替済みなら、使用量は数えず終了
  3. 非公開repoの当月の使用量を推定（全ジョブの所要時間を1分に切り上げ。Mac 側と同じ数え方）
  4. 1,500分以上で未予告 → 予告を1回だけ ／ 1,750分以上で Mac が未切替 → 「切替が必要」を 20時間に1回
環境変数（テスト用）: QUOTA_WATCH_DRY=1 送信・書き込みをしない / QUOTA_WATCH_FAKE_USAGE=<分> / QUOTA_WATCH_NOW=<epoch> /
                     QUOTA_WATCH_FORCE=1 時刻・1日1回の制限を無視 / QUOTA_WATCH_MAC_STATE=<json> / QUOTA_WATCH_CLOUD_STATE=<json>
"""
import base64
import concurrent.futures as cf
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))
API = "https://api.github.com"
OWNER = "truthbodysalon-lab"
PUBLIC_REPO = f"{OWNER}/threads-auto-post"
FALLBACK_PRIVATE = [f"{OWNER}/ig-autopost-cloud", f"{OWNER}/cloud-daily-tasks", f"{OWNER}/line-stepmail-cloud"]
CLOUD_STATE_PATH = "quota_watch_state.json"
MAC_STATE_PATH = "quota_state_mac.json"

FREE_MIN = 2000
WARN_MIN = int(os.environ.get("QUOTA_WARN") or 1500)
SWITCH_MIN = int(os.environ.get("QUOTA_SWITCH") or 1750)
CHECK_AFTER = (12, 20)            # JST。Mac の 07:05 / 19:05 の確認の後に見る
NEED_NOTICE_EVERY_S = 20 * 3600
MAC_STALE_S = 13 * 3600
DEADLINE_S = 400
MAX_WORKERS = 8


def log(msg):
    print(f"[quota_watch {datetime.now(JST):%H:%M:%S}] {msg}", flush=True)


def now_epoch():
    v = os.environ.get("QUOTA_WATCH_NOW")
    return int(v) if v else int(time.time())


def token():
    return os.environ.get("GH_TOKEN") or os.environ.get("GH_PAT") or ""


def api(path, method="GET", body=None):
    """(status, json|None)。通信例外は (0, None)。トークンはログに出さない。"""
    for attempt in range(2):
        try:
            data = json.dumps(body).encode("utf-8") if body is not None else None
            req = urllib.request.Request(API + path, data=data, method=method, headers={
                "Authorization": f"Bearer {token()}", "Accept": "application/vnd.github+json",
                "User-Agent": "quota-watch/1.0", "X-GitHub-Api-Version": "2022-11-28"})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8")
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt == 0:
                time.sleep(2)
                continue
            return e.code, None
        except Exception:
            if attempt == 0:
                time.sleep(2)
                continue
            return 0, None
    return 0, None


# ---------------- 使用量の推定（Mac 側 quota_guard.py と同じ数え方） ----------------
def _parse(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def job_minutes(j, now_utc):
    if j.get("conclusion") == "skipped" or not j.get("started_at"):
        return 0
    labels = [str(x).lower() for x in (j.get("labels") or [])]
    if "self-hosted" in labels:
        return 0
    st = _parse(j["started_at"])
    en = _parse(j["completed_at"]) if j.get("completed_at") else now_utc
    secs = (en - st).total_seconds()
    if secs <= 0:
        return 0
    m = max(1, int(math.ceil(secs / 60.0)))
    mult = 10 if any("macos" in x for x in labels) else 2 if any("windows" in x for x in labels) else 1
    return m * mult


def month_start(now_utc):
    return datetime(now_utc.year, now_utc.month, 1, tzinfo=timezone.utc)


def private_repos():
    st, d = api("/user/repos?visibility=private&affiliation=owner&per_page=100")
    names = []
    if st == 200 and isinstance(d, list):
        names = [x["full_name"] for x in d if x.get("full_name", "").startswith(OWNER + "/") and not x.get("archived")]
    return sorted(set(names or FALLBACK_PRIVATE))


def estimate_usage(now_utc, repos, deadline_s=DEADLINE_S):
    """(合計分, 不完全か, エラー数)。読み取りのみ。"""
    t_end = time.time() + deadline_s
    start = month_start(now_utc)
    days = []
    d = start
    while d <= now_utc:
        days.append(d)
        d += timedelta(days=1)
    errs = [0]

    def list_runs(args):
        repo, day = args
        out, page = [], 1
        a, b = day.strftime("%Y-%m-%dT00:00:00Z"), day.strftime("%Y-%m-%dT23:59:59Z")
        while time.time() < t_end:
            st, data = api(f"/repos/{repo}/actions/runs?created={a}..{b}&per_page=100&page={page}")
            if st != 200 or not isinstance(data, dict):
                errs[0] += 1
                return repo, out
            runs = data.get("workflow_runs", [])
            out += [(repo, r["id"]) for r in runs]
            if len(runs) < 100:
                return repo, out
            page += 1
        errs[0] += 1
        return repo, out

    runs = []
    with cf.ThreadPoolExecutor(MAX_WORKERS) as ex:
        for _, rs in ex.map(list_runs, [(r, dd) for r in repos for dd in days]):
            runs += rs

    def run_min(r):
        repo, rid = r
        if time.time() >= t_end:
            errs[0] += 1
            return 0
        st, data = api(f"/repos/{repo}/actions/runs/{rid}/jobs?filter=all&per_page=100")
        if st != 200 or not isinstance(data, dict):
            errs[0] += 1
            return 0
        return sum(job_minutes(j, now_utc) for j in data.get("jobs", []))

    with cf.ThreadPoolExecutor(MAX_WORKERS) as ex:
        total = sum(ex.map(run_min, runs))
    return total, errs[0] > 0, errs[0]


# ---------------- 判断（純関数・テスト対象） ----------------
def decide(usage, month, mac, cloud, now):
    """戻り値: アクション名のリスト（'warn' / 'need_switch'）。
    mac: Mac が公開repoに書いた状態（無ければ {}）。cloud: 自分の前回状態。"""
    mac = mac if isinstance(mac, dict) else {}
    cloud = cloud if isinstance(cloud, dict) else {}
    mac_ok = mac.get("month") == month
    mac_switched = mac_ok and bool(mac.get("switched"))
    mac_warned = mac_ok and bool(mac.get("warned"))
    cloud_warned = cloud.get("warned_month") == month
    acts = []
    if usage >= SWITCH_MIN:
        if not mac_switched:
            last = cloud.get("need_notice_at") if cloud.get("need_notice_month") == month else None
            if last is None or now - float(last) >= NEED_NOTICE_EVERY_S:
                acts.append("need_switch")
    elif usage >= WARN_MIN and not (mac_warned or cloud_warned):
        acts.append("warn")
    return acts


def mac_age_text(mac, now):
    try:
        ts = datetime.fromisoformat(mac["last_check"]).timestamp()
        h = (now - ts) / 3600.0
        return f"{h:.0f}時間前" if h < 48 else f"{h / 24:.0f}日前"
    except Exception:
        return "不明"


def messages(act, usage, mac, now):
    pct = int(round(100.0 * usage / FREE_MIN))
    if act == "warn":
        return (f"📈 GitHub Actions無料枠の予告（クラウド見張り）: 今月の非公開リポの使用量が約{usage:,}分（{pct}%・無料枠{FREE_MIN:,}分）になりました。"
                f"{SWITCH_MIN:,}分に達するとMac側が自動でMac代行に切り替えます（無料枠のみで運用・予算は上げません）。今すぐの操作は不要です。")
    age = mac_age_text(mac, now)
    stale = ""
    try:
        stale = "（Macが寝ているかオフの可能性があります）" if now - datetime.fromisoformat(mac["last_check"]).timestamp() > MAC_STALE_S else ""
    except Exception:
        stale = "（Macからの確認記録が見つかりません）"
    return (f"🙋切替が必要です（クラウド見張り）: GitHub Actions無料枠の使用量が約{usage:,}分（{pct}%）で、{SWITCH_MIN:,}分の閾値を超えました。"
            f"まだMac側の自動切替が実行されていません。Macの最終確認: {age}{stale}。\n"
            f"Macを起動すると次の確認で自動的にMac代行へ切り替わります。急ぐ場合はMac側の quota_guard を手動で実行してください。"
            f"（クラウド側からは非公開リポの設定を変えられない設計です。無料枠を使い切ると定期処理が止まります）")


# ---------------- 状態の読み書き（公開repoのContents API） ----------------
def get_json_file(path):
    st, d = api(f"/repos/{PUBLIC_REPO}/contents/{path}")
    if st == 404:
        return {}, None
    if st != 200 or not isinstance(d, dict):
        return None, None
    try:
        return json.loads(base64.b64decode(d["content"]).decode("utf-8")), d.get("sha")
    except Exception:
        return None, d.get("sha")


def put_json_file(path, obj, sha, msg):
    body = {"message": msg, "content": base64.b64encode((json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8")).decode("ascii")}
    if sha:
        body["sha"] = sha
    st, _ = api(f"/repos/{PUBLIC_REPO}/contents/{path}", method="PUT", body=body)
    return st in (200, 201)


def send(text):
    if os.environ.get("QUOTA_WATCH_DRY") == "1":
        log("[DRY] 送信予定: " + text.replace("\n", " / "))
        return True
    try:
        sys.path.insert(0, str(HERE))
        import ops_deadman
        ops_deadman._load_dotenv()
        return ops_deadman.discord_send(text)
    except Exception as e:
        log(f"Discord送信失敗 {type(e).__name__}")
        return False


def main():
    dry = os.environ.get("QUOTA_WATCH_DRY") == "1"
    force = os.environ.get("QUOTA_WATCH_FORCE") == "1"
    now = now_epoch()
    jst = datetime.fromtimestamp(now, JST)
    today = jst.strftime("%Y-%m-%d")
    marker = Path(os.environ.get("QUOTA_WATCH_TMP", "/tmp")) / f"quota_watch_{today}.done"
    if not force:
        if (jst.hour, jst.minute) < CHECK_AFTER or marker.exists():
            return 0
        if not token():
            return 0

    # 状態を読む（読めなければ今日は何もしない。次のサイクルで再試行）
    env_mac, env_cloud = os.environ.get("QUOTA_WATCH_MAC_STATE"), os.environ.get("QUOTA_WATCH_CLOUD_STATE")
    if env_mac is not None or env_cloud is not None:
        mac, cloud, cloud_sha = json.loads(env_mac or "{}"), json.loads(env_cloud or "{}"), None
    else:
        cloud, cloud_sha = get_json_file(CLOUD_STATE_PATH)
        mac, _ = get_json_file(MAC_STATE_PATH)
        if cloud is None or mac is None:
            log("状態ファイルを読めませんでした（次のサイクルで再試行）")
            return 0
    if cloud.get("last_check_date") == today and not force:
        try:
            marker.touch()
        except Exception:
            pass
        return 0

    now_utc = datetime.fromtimestamp(now, timezone.utc)
    month = f"{now_utc:%Y-%m}"
    if mac.get("month") == month and mac.get("switched"):
        log(f"{month}: Macが切替済み（代行中）のため使用量は数えません")
        usage = None
    else:
        fake = os.environ.get("QUOTA_WATCH_FAKE_USAGE")
        if fake not in (None, ""):
            usage, partial, ne = int(float(fake)), False, 0
        else:
            usage, partial, ne = estimate_usage(now_utc, private_repos())
        log(f"{month}: 使用量の推定 {usage}分（警告{WARN_MIN}/切替{SWITCH_MIN}）" + (f" [不完全 エラー{ne}]" if partial else ""))
        if partial and usage < WARN_MIN:
            # 数えきれなかった。今日の確認は『済み』にせず、連続失敗だけ数える（3日続いたら知らせる）
            cloud["count_fail_days"] = int(cloud.get("count_fail_days", 0)) + (1 if cloud.get("count_fail_last") != today else 0)
            cloud["count_fail_last"] = today
            if cloud["count_fail_days"] >= 3 and not dry:
                send("⚠️ GitHub Actions無料枠の使用量をクラウド見張りが数えられていません（3日連続）。GH_PAT の権限や有効期限を確認してください。")
                cloud["count_fail_days"] = 0
            usage = None
    acts = decide(usage, month, mac, cloud, now) if usage is not None else []
    for a in acts:
        ok = send(messages(a, usage, mac, now))
        if ok:
            if a == "warn":
                cloud["warned_month"] = month
            elif a == "need_switch":
                cloud["need_notice_month"] = month
                cloud["need_notice_at"] = now
    if usage is not None or not cloud.get("count_fail_last") == today:
        cloud["last_check_date"] = today
    cloud["month"] = month
    if usage is not None:
        cloud["last_usage"] = usage
    if not dry and env_mac is None and env_cloud is None:
        if not put_json_file(CLOUD_STATE_PATH, cloud, cloud_sha, "quota_watch: state [skip ci]"):
            log("状態の保存に失敗（今日の判定をやり直す可能性があります）")
    if cloud.get("last_check_date") == today:
        try:
            marker.touch()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as ex:
        print(f"[quota_watch] 異常終了 {type(ex).__name__}", flush=True)
        sys.exit(0)        # 見張りの失敗でループを止めない
