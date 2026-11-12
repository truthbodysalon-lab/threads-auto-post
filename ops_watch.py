#!/usr/bin/env python3
"""GitHub Actions 失敗run監視・決定論的1回リトライ（公開repo版・Macが寝ていても動く）

Mac の ~/.claude/scripts/gh_failure_watch.sh（中身はPython）と同じ判定をクラウドへ移したもの（2026-11-12 masa指示「Macが稼働していなくてもできるように・無料の枠内で」）。
公開repoのActionsは無料・無制限。起動は中央ディスパッチャ(schedule_table.json の ops-watch@* 枠・毎時)。

【役割分担（二重リトライ防止）】
  失敗runの再実行(retry)は「このクラウド版だけ」が行う。Mac の gh_failure_watch は検知とログだけ（GH_FAILURE_WATCH_RERUN=1 の時だけ従来どおり再実行する戻し口あり）。

【ルール（Mac版と同じ・厳守）】
  - 再実行は同一run IDにつき生涯1回だけ（run_attempt==1 かつ状態ファイルに無い run だけ）
  - 投稿・送信系ワークフロー(二重投稿/二重送信の恐れ)は NO_RERUN_WORKFLOWS に列挙。名前に Post/Story/Feed/Reels/Step Mail/Broadcast/Ekiten/Blog を含むものも念のため再実行しない
  - 再実行で直ったら通知しない。通知は「再実行しても直らなかった」を1tickに最大1通。通知文の末尾は必ず [auto-triage]
  - 課金上限で「開始すらされなかった」失敗は再実行しない（記録のみ。使用量の通知は quota_watch.py が担当）
  - 6時間より古い失敗runは再実行しない（停止明けに過去分を一斉に再実行しない）
  - 初回は「起点を置くだけ」（既存の失敗runは全て baseline＝何もしない）
  - 再実行禁止の失敗のうち未回復のものは、Mac が止まっている時だけDiscordへ1通まとめて知らせる（Mac稼働中は Mac 側の引き継ぎ→discord-error-autofix が拾う）
  - LINE送信なし・通知はDiscordのみ。トークン・Webhook URL・API本文はログに出さない（公開ログ）
  - すべてのHTTPに timeout を付ける

環境変数: GH_TOKEN|GH_PAT（必須・非公開repoの actions:read/write）/ DISCORD_WEBHOOK_URL / OPS_WATCH_DRY=1（再実行・通知・状態書込をしない）/ OPS_WATCH_NOW=<epoch>（テスト用）
終了コード: 常に0（監視の失敗でworkflowを赤くしない）。ただし検査不能は標準出力に INSPECT_FAILED と出す
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_PATH = HERE / "ops_watch_state.json"
API = "https://api.github.com"
OWNER = "truthbodysalon-lab"
JST = timezone(timedelta(hours=9))

MONITORED_REPOS = [
    f"{OWNER}/blog-auto-post",
    f"{OWNER}/threads-auto-post",
    f"{OWNER}/ig-autopost-cloud",
    f"{OWNER}/note-auto",
    f"{OWNER}/line-stepmail-cloud",
    f"{OWNER}/cloud-daily-tasks",
]

# 再実行禁止（workflow名の完全一致）。Mac版 NO_RERUN_WORKFLOWS と同じ（外部へ実投稿・実送信するもの／自己修復で投稿を伴うもの）
NO_RERUN_WORKFLOWS = {
    "Blog Auto Post", "Blog Auto Post Recovery", "Ekiten Auto Post",
    "Threads Auto Post", "Threads HPB空き状況告知（truth=月木・nagaoka=水土）",
    "Threads Watchdog (Mac非依存の外形監視・自己修復)",
    "IG Feed (daily carousel)", "IG Feed - truth (weekly carousel)", "IG Feed - nagaoka (weekly carousel)",
    "IG Feed - nagaoka (thursday education carousel)", "IG Reels (daily lecture clip)", "IG Story (4x daily)",
    "IG Story Friday Features (nagaoka only, weekly)", "IG Story Pool (nagaoka + truth, every 3 days)",
    "IG Verify (post-slot check & self-heal)", "IG Verify Pool (weekly feed self-heal for nagaoka/truth)",
    "IG Verify Pool (weekly feed self-heal + daily video check)",
    "IG Story - HPB空き状況告知（truth=火・nagaoka=金）", "IG Story Offer (nagaoka + truth, every Wednesday)",
    "IG Story QA Video (nagaoka=Sat / truth=Sun, weekly)",
    "LINE Step Mail (Cloud)",
    "Ops Watch",   # 自分自身は再実行しない
}
# 名前に含まれていたら（上のリストに無くても）再実行しない保険（新しい投稿系workflowの追加漏れ対策）
NO_RERUN_KEYWORDS = ("Post", "Story", "Feed", "Reels", "Step Mail", "Broadcast", "Ekiten", "Blog", "Verify", "Watchdog")

BILLING_PATTERNS = ("spending limit", "payments have failed", "was not started because")
MAX_RERUN_AGE_S = 6 * 3600
STATE_KEEP_S = 30 * 86400
MAC_STALE_S = 3 * 3600
TERMINAL = {"baseline", "skipped_no_rerun_policy", "skipped_already_recovered", "recovered",
            "notified_failed_after_rerun", "skipped_billing_limit", "skipped_old", "notified_no_rerun"}


def log(msg):
    print(f"[ops_watch {datetime.now(JST):%H:%M:%S}] {msg}", flush=True)


def now_epoch():
    v = os.environ.get("OPS_WATCH_NOW")
    return int(v) if v else int(time.time())


def dry():
    return os.environ.get("OPS_WATCH_DRY", "") not in ("", "0", "false")


def token():
    return os.environ.get("GH_TOKEN") or os.environ.get("GH_PAT") or ""


def _load_dotenv():
    try:
        for line in (HERE / ".env").read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


def api(path, method="GET", body=None):
    """(status, json|None)。通信例外は (0, None)。トークンはログに出さない。"""
    for attempt in range(2):
        try:
            data = json.dumps(body).encode("utf-8") if body is not None else None
            req = urllib.request.Request(API + path, data=data, method=method, headers={
                "Authorization": f"Bearer {token()}", "Accept": "application/vnd.github+json",
                "User-Agent": "ops-watch/1.0", "X-GitHub-Api-Version": "2022-11-28"})
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


def parse_iso(s):
    d = datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def is_no_rerun(workflow):
    return workflow in NO_RERUN_WORKFLOWS or any(k in workflow for k in NO_RERUN_KEYWORDS)


def load_state():
    try:
        d = json.loads(STATE_PATH.read_text())
        d.setdefault("runs", {})
        return d
    except Exception:
        return {"initialized": False, "runs": {}}


def save_state(state):
    if dry():
        return
    cut = now_epoch() - STATE_KEEP_S
    state["runs"] = {k: v for k, v in state["runs"].items() if v.get("t", 0) >= cut}
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True))
    os.replace(tmp, STATE_PATH)


def failed_runs(repo):
    st, d = api(f"/repos/{repo}/actions/runs?status=failure&per_page=20")
    if st != 200 or not isinstance(d, dict):
        return None
    return d.get("workflow_runs", [])


def has_newer_success(repo, run):
    """同じworkflowで、この失敗runより新しい成功runがあるか。判定不能は None。Recovery系は skipped も正常終了。"""
    wid = run.get("workflow_id")
    st, d = api(f"/repos/{repo}/actions/workflows/{wid}/runs?per_page=10&status=completed")
    if st != 200 or not isinstance(d, dict):
        return None
    ok = ("success", "skipped") if "Recovery" in (run.get("name") or "") else ("success",)
    for r in d.get("workflow_runs", []):
        if r["id"] == run["id"]:
            continue
        if r.get("conclusion") in ok and r.get("created_at", "") > run.get("created_at", ""):
            return True
    return False


def is_billing_block(repo, run_id):
    """True=課金上限でジョブ未開始 / False=通常の失敗 / None=判定不能（steps無しの失敗ジョブの注釈だけ読む）"""
    st, d = api(f"/repos/{repo}/actions/runs/{run_id}/jobs?per_page=30")
    if st != 200 or not isinstance(d, dict):
        return None
    for j in d.get("jobs", []):
        if j.get("conclusion") != "failure" or j.get("steps"):
            continue
        st2, ann = api(f"/repos/{repo}/check-runs/{j.get('id')}/annotations")
        if st2 != 200 or not isinstance(ann, list):
            return None
        for a in ann:
            if any(p in (a.get("message") or "").lower() for p in BILLING_PATTERNS):
                return True
    return False


def discord_send(text):
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if dry() or not url:
        log("(通知を送らない: " + ("dry" if dry() else "Webhook未設定") + ")")
        return False
    try:
        req = urllib.request.Request(url, data=json.dumps({"content": text[:1900]}).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "ops-watch/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except Exception as e:
        log(f"Discord送信失敗: {type(e).__name__}")
        return False


def mac_stale():
    """Mac(cloud-daily-tasks の heartbeat)が MAC_STALE_S より古ければ True。読めなければ True（取りこぼしより重複通知を選ぶ）。"""
    try:
        import ops_deadman
        ts = ops_deadman.read_cloud_heartbeat_epoch()
        return ts == 0 or (now_epoch() - ts) > MAC_STALE_S
    except Exception:
        return True


def rerun(repo, run_id):
    if dry():
        return True, "dry"
    st, _ = api(f"/repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs", method="POST", body={})
    return (st in (201, 204)), f"HTTP{st}"


def process(state, runs_by_repo, now):
    """1tick分の判定。戻り値: 結果dict（テスト用）。state を更新する。"""
    res = {"reran": 0, "notified": False, "inspect_failed": False, "verdicts": []}
    pending_no_rerun = []     # (repo, name, url) 未回復の再実行禁止失敗
    for repo, runs in runs_by_repo.items():
        if runs is None:
            res["inspect_failed"] = True
            log(f"{repo.split('/')[-1]}: 失敗run取得に失敗")
            continue
        for r in runs:
            rid = str(r["id"])
            name = r.get("name") or ""
            url = r.get("html_url") or f"https://github.com/{repo}/actions/runs/{rid}"
            created = int(parse_iso(r.get("created_at")).timestamp()) if r.get("created_at") else 0
            ex = state["runs"].get(rid)
            if not state.get("initialized"):
                state["runs"][rid] = {"s": "baseline", "t": now}
                res["verdicts"].append((rid, "baseline"))
                continue
            if ex and ex.get("s") in TERMINAL:
                continue

            # 再実行済みの run を再確認
            if ex and ex.get("s") == "rerun_requested":
                st, v = api(f"/repos/{repo}/actions/runs/{rid}")
                if st != 200 or not isinstance(v, dict):
                    res["inspect_failed"] = True
                    continue
                if v.get("status") != "completed":
                    continue
                if v.get("conclusion") == "success":
                    ex.update(s="recovered", t=now)
                    log(f"{repo.split('/')[-1]} run#{rid}: 再実行で回復（通知なし）")
                    res["verdicts"].append((rid, "recovered"))
                    continue
                if int(v.get("run_attempt") or 1) < 2:
                    continue   # まだ再実行が反映されていない。次tickで
                if res["notified"]:
                    continue   # 1tick1通。次tickへ持ち越し（状態は rerun_requested のまま）
                text = ("GitHub Actions 失敗run: 再実行しても直りませんでした（クラウド監視 ops-watch）\n"
                        f"repo: {repo}\nworkflow: {name}\nrun: {url}\n"
                        "→ 1回再実行しても再び失敗したため、コード起因の可能性が高いです。discord-error-autofix側での調査をお願いします。\n[auto-triage]")
                if discord_send(text):
                    ex.update(s="notified_failed_after_rerun", t=now)
                    res["notified"] = True
                    res["verdicts"].append((rid, "failed_after_rerun"))
                continue

            # --- 新規の失敗run ---
            if now - created > MAX_RERUN_AGE_S:
                state["runs"][rid] = {"s": "skipped_old", "t": now}
                continue
            newer = has_newer_success(repo, r)
            if newer:
                state["runs"][rid] = {"s": "skipped_already_recovered", "t": now}
                res["verdicts"].append((rid, "skipped_already_recovered"))
                continue
            billing = is_billing_block(repo, rid)
            if billing:
                state["runs"][rid] = {"s": "skipped_billing_limit", "t": now}
                log(f"{repo.split('/')[-1]} run#{rid}: 課金上限で未開始（再実行しない・記録のみ。使用量はquota_watchが通知）")
                res["verdicts"].append((rid, "skipped_billing_limit"))
                continue
            if is_no_rerun(name):
                if newer is None:
                    res["inspect_failed"] = True
                pending_no_rerun.append((rid, repo, name, url))
                continue
            if newer is None:
                res["inspect_failed"] = True
                continue
            if int(r.get("run_attempt") or 1) > 1:
                state["runs"][rid] = {"s": "skipped_already_retried", "t": now}   # すでに再実行済み（Mac版や手動）。生涯1回の保証
                continue
            ok, why = rerun(repo, rid)
            if not ok:
                res["inspect_failed"] = True
                log(f"{repo.split('/')[-1]} run#{rid}: 再実行の呼び出し失敗 {why}（PATに actions:write が無い可能性）")
                continue
            state["runs"][rid] = {"s": "rerun_requested", "t": now}
            res["reran"] += 1
            res["verdicts"].append((rid, "reran"))
            log(f"{repo.split('/')[-1]} run#{rid}: 一時障害と判断し --failed を1回だけ再実行")

    # 再実行禁止の未回復失敗: Macが止まっている時だけ1通にまとめて知らせる（Mac稼働中は Mac側の引き継ぎが拾う）
    if pending_no_rerun and state.get("initialized"):
        if mac_stale():
            if not res["notified"]:
                lines = [f"・{repo.split('/')[-1]} / {name}\n  {url}" for _rid, repo, name, url in pending_no_rerun[:6]]
                text = ("GitHub Actions 失敗run（投稿・送信系のため自動再実行していません・Macが止まっているため直接お知らせ）\n"
                        + "\n".join(lines) + "\n→ 原因確認をお願いします。\n[auto-triage]")
                if discord_send(text):
                    for rid, *_ in pending_no_rerun:
                        state["runs"][rid] = {"s": "notified_no_rerun", "t": now}
                    res["notified"] = True
        else:
            for rid, *_ in pending_no_rerun:
                state["runs"][rid] = {"s": "skipped_no_rerun_policy", "t": now}   # Mac稼働中: Mac版が handoff する
    elif pending_no_rerun:
        for rid, *_ in pending_no_rerun:
            state["runs"][rid] = {"s": "baseline", "t": now}
    return res


def main():
    _load_dotenv()
    if not token():
        print("INSPECT_FAILED: トークンがありません（GH_PAT）")
        return
    now = now_epoch()
    state = load_state()
    runs_by_repo = {repo: failed_runs(repo) for repo in MONITORED_REPOS}
    first = not state.get("initialized")
    res = process(state, runs_by_repo, now)
    if first:
        # 全repoの取得に失敗していたら起点を置かない（次回やり直す）
        if all(v is None for v in runs_by_repo.values()):
            print("INSPECT_FAILED: 初回起点を置けませんでした")
            return
        state["initialized"] = True
    save_state(state)
    n_fail = sum(len(v) for v in runs_by_repo.values() if v)
    print(f"ops_watch: 失敗run={n_fail}件 / 再実行={res['reran']}件 / 通知={'あり' if res['notified'] else 'なし'}"
          + (" / baseline初期化" if first else "") + (" / INSPECT_FAILED" if res["inspect_failed"] else "") + (" / DRY" if dry() else ""))


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:  # noqa: BLE001
        print(f"INSPECT_FAILED: {type(e).__name__}")
    sys.exit(0)
