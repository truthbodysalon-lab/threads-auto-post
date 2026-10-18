#!/usr/bin/env python3
"""中央ディスパッチャ: schedule_table.json に従い、各repoのcronが遅延/欠落した時だけ workflow_dispatch で補完起動する。
mode=dry_run はログのみ。例外は外へ出さない（auto_post.yml のループを止めない）。"""
import json, os, sys, time
import urllib.request, urllib.error, urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    JST = ZoneInfo("Asia/Tokyo")
except Exception:
    JST = timezone(timedelta(hours=9))

HERE = Path(__file__).resolve().parent
TABLE = HERE / "schedule_table.json"
STATE = HERE / "dispatch_state.json"
API = "https://api.github.com"
_branch_cache = {}


def log(msg):
    print(f"[dispatcher {datetime.now(JST):%H:%M:%S}] {msg}", flush=True)


def warn(msg):
    print(f"::warning::dispatcher: {msg}", flush=True)
    try:
        import discord_push
        discord_push.send(f"dispatcher: {msg}")
    except Exception:
        pass


def token():
    return os.environ.get("GH_TOKEN") or os.environ.get("GH_PAT") or ""


def api(method, path, body=None):
    req = urllib.request.Request(API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token()}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "central-dispatcher"})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if raw else {})


def default_branch(repo):
    if repo not in _branch_cache:
        _branch_cache[repo] = api("GET", f"/repos/{repo}")[1]["default_branch"]
    return _branch_cache[repo]


def runs_since(repo, wf, since_utc):
    q = urllib.parse.quote(">=" + since_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), safe="")
    _, d = api("GET", f"/repos/{repo}/actions/workflows/{wf}/runs?created={q}&per_page=1")
    return d.get("total_count", 0)


def fetch_plan(repo, acct):
    """ig-autopost-cloud の state/<acct>/story_plan.json を取得。取得できなければ（404・権限不足含む）None
    ＝ゲートは判定せず従来どおり起動する（フェイルオープン）。"""
    import base64
    try:
        _, d = api("GET", f"/repos/{repo}/contents/state/{acct}/story_plan.json")
        return json.loads(base64.b64decode(d["content"]).decode("utf-8"))
    except Exception:
        return None


_sync_cache = {}


def gate_in_sync(repo):
    """story_gate.py の複製（このrepo）が ig-autopost-cloud 本体と同一かを確認する（2026-10-18）。
    2か所にあるコードが片方だけ変わると、古い判定で『必要な起動』を省く恐れがあるため、
    食い違い・取得失敗のときは False（=ゲートを使わず従来どおり起動する）。結果は1時間キャッシュ。"""
    import base64
    c = _sync_cache.get(repo)
    if c and time.time() - c[0] < 3600:
        return c[1]
    ok = False
    try:
        _, d = api("GET", f"/repos/{repo}/contents/story_gate.py")
        remote = base64.b64decode(d["content"]).decode("utf-8")
        local = (HERE / "story_gate.py").read_text(encoding="utf-8")
        ok = remote.replace("\r\n", "\n") == local.replace("\r\n", "\n")
        if not ok:
            log("story_gate.py が本体(ig-autopost-cloud)と食い違い → ゲートを使わず従来どおり起動する。複製を同期すること")
    except Exception as ex:
        log(f"story_gate.py 同期確認失敗（{type(ex).__name__}）→ ゲートを使わず従来どおり起動する")
    _sync_cache[repo] = (time.time(), ok)
    return ok


def gate_skip(e, repo, now):
    """起動前ゲート（M10・2026-10-18）。entry の "gate": pool|qa|offer|features が付いたものだけ、
    「確実に何も起きない」時に起動を省略する。判定ロジックは story_gate.py（ig-autopost-cloud 本体の複製）。
    取得失敗・想定外・importエラーは全て False（=従来どおり起動）。entry から "gate" を消せば元の挙動。"""
    g = e.get("gate")
    if not g:
        return False
    try:
        if not gate_in_sync(repo):
            return False
        import story_gate
        plans = {a: fetch_plan(repo, a) for a in story_gate.MODES[g]["accounts"]}
        skip, why = story_gate.decide(g, plans, now)
        log(f"{e['id']}: gate({g}) {'SKIP' if skip else 'RUN'} {why}")
        return bool(skip)
    except Exception as ex:
        log(f"{e['id']}: gate例外のため起動側へ: {type(ex).__name__}: {ex}")
        return False


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def main():
    try:
        cfg = json.loads(TABLE.read_text())
    except Exception as e:
        warn(f"schedule_table.json 読込失敗: {e}")
        return
    mode = cfg.get("mode", "dry_run")
    now = datetime.now(JST)
    if os.environ.get("DISPATCH_NOW"):  # テスト用 "YYYY-MM-DD HH:MM"
        now = datetime.strptime(os.environ["DISPATCH_NOW"], "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    today = now.strftime("%Y-%m-%d")
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    state = load_state()
    state = {k: v for k, v in state.items() if v == today}  # 古いマーカーは捨てる
    changed = False
    due = 0
    for e in cfg.get("entries", []):
        try:
            if not e.get("enabled", True):
                continue
            if e.get("weekdays") is not None and now.weekday() not in e["weekdays"]:
                continue
            if e.get("skip_until") and today < e["skip_until"]:  # 一時停止中は起動しない（YYYY-MM-DD未満の日）
                continue
            hh, mm = map(int, e["jst_time"].split(":"))
            slot = day0.replace(hour=hh, minute=mm)
            if not (slot <= now <= slot + timedelta(minutes=int(e.get("window_min", 90)))):
                continue
            if state.get(e["id"]) == today:
                continue
            due += 1
            repo, wf = e["repo"], e["workflow"]
            since = day0 if e.get("since", "day") == "day" else slot - timedelta(minutes=12)
            n = runs_since(repo, wf, since.astimezone(timezone.utc))
            if n > 0:
                log(f"{e['id']}: 既存run {n}件あり → 起動不要" + ("" if mode == "live" else " (dry_run: マーカー無し)"))
                if mode == "live":
                    state[e["id"]] = today; changed = True
                continue
            if mode != "live":
                log(f"{e['id']}: 起動予定 {repo} {wf} (dry_run)")
                continue
            if gate_skip(e, repo, now):
                continue   # 完了済み等で確実に空振り → 起動しない（マーカーは付けず、次ループで再判定）
            ref = default_branch(repo)
            body = {"ref": ref}
            if e.get("inputs"):  # workflow_dispatch の入力（例: hpb の account）
                body["inputs"] = e["inputs"]
            st, _ = api("POST", f"/repos/{repo}/actions/workflows/{wf}/dispatches", body)
            log(f"{e['id']}: dispatch {repo} {wf} ref={ref} -> HTTP {st}")
            state[e["id"]] = today; changed = True
        except Exception as ex:
            warn(f"{e.get('id')} 失敗: {type(ex).__name__}: {ex}")
    if due == 0:
        log("対象枠なし")
    if changed:
        try:
            STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        except Exception as ex:
            warn(f"state書込失敗: {ex}")


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:
        print(f"::warning::dispatcher fatal: {e}", flush=True)
    sys.exit(0)
