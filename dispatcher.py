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
    # 2026-10-18: discord_push はこのリポに無く警告が Discord に届いていなかった。
    # 見張り(ops_deadman.py)の discord_send を使う（LINEは使わない。失敗してもループは止めない）
    # 同じ内容の警告は6時間に1回だけ（10分ごとのループで同じ失敗を連投しない）
    try:
        import hashlib
        import re
        import json as _json
        import time as _time
        key = hashlib.sha1(re.sub(r"\d+", "#", msg)[:120].encode()).hexdigest()[:12]
        path = "/tmp/dispatcher_warn_sent.json"
        try:
            sent = _json.load(open(path))
        except Exception:
            sent = {}
        now = _time.time()
        if now - sent.get(key, 0) < 6 * 3600:
            return
        from ops_deadman import discord_send
        if discord_send(f"dispatcher: {msg}"):
            sent[key] = now
            _json.dump(sent, open(path, "w"))
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


def fetch_json(repo, path):
    """ig-autopost-cloud 内のJSONファイルを取得。取得できなければ（404・権限不足・不正JSON含む）None
    ＝呼び出し側は判定せず従来どおり起動する（フェイルオープン）。"""
    import base64
    try:
        _, d = api("GET", f"/repos/{repo}/contents/{path}")
        return json.loads(base64.b64decode(d["content"]).decode("utf-8"))
    except Exception:
        return None


def pause_file_active(e, repo, today):
    """entry の "pause_file"（例: state/feed_pause.json）の until(JST日付) が今日以降なら True（=起動しない）。
    ig_daily 側の feed_pause_active() と同じ判定（today <= until）。2026-10-18: 固定の skip_until だと
    停止を早めに解除しても再開しないため、停止の正本（feed_pause.json）にそのまま従うようにした。
    ファイル欠落・取得失敗・until無しは False（=起動する。run側が自分で一時停止を判定するので安全）。"""
    pf = e.get("pause_file")
    if not pf:
        return False
    d = fetch_json(repo, pf)
    if not isinstance(d, dict):
        return False
    until = str(d.get("until", "")).strip()
    return bool(until) and today <= until


def queue_empty_today(e, repo, today):
    """entry の "queue_file"（例: state/reels_queue.json）に今日(JST)の item が無ければ True（=起動しない）。
    リール連載が終わった後も毎日『投稿する物なし』の空振りrunが1分課金されるのを防ぐ（2026-10-18）。
    取得失敗・形式が違う・想定外は全て False（=起動する）。連載を延ばす時は queue に日付を足すだけでよい。"""
    qf = e.get("queue_file")
    if not qf:
        return False
    d = fetch_json(repo, qf)
    if not isinstance(d, dict) or not isinstance(d.get("items"), list):
        return False
    return not any(isinstance(i, dict) and i.get("date") == today for i in d["items"])


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


STEPMAIL_FILES = ["stepmail.mjs", "step_messages.json", "state/enrollments.json", "state/sent.json"]
STEPMAIL_SKEW_MS = 120000     # 判定〜本番run開始の数十秒の差で取りこぼさないための時刻先読み(DRY_RUN時のみ効く)
STEPMAIL_STREAK_ALERT = 3     # 『送るものあり』の判定が3枠連続(約6時間)で続いたら本人へDiscordで知らせる
STEPMAIL_GATE_STATE = HERE / "stepmail_gate_state.json"
_SENT_RE = None


def fetch_raw(repo, path):
    """repo の default ブランチのファイルをそのままのバイト列で取得(Contents API・raw)。失敗は例外。"""
    req = urllib.request.Request(f"{API}/repos/{repo}/contents/{path}", headers={
        "Authorization": f"Bearer {token()}", "Accept": "application/vnd.github.raw",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "central-dispatcher"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def stepmail_pending(repo):
    """ステップ配信の起動前ゲート(2026-10-18 Actions無料枠対策・施策C)。
    line-stepmail-cloud の stepmail.mjs / step_messages.json / state を一時ディレクトリへ取得し、**本物の stepmail.mjs を
    DRY_RUN=1 で実行**して『この枠で送るものがあるか』(末尾の 送信=N件) を返す。判定ロジックを複製しないので本番とずれない。
    取得失敗・node無し・異常終了・書式不一致・例外は全て None(=判定不能。呼び出し側は従来どおり起動する=フェイルオープン)。
    顧客情報の保護: stepmail.mjs の DRY_RUN 出力には userId と本文が含まれるため、stdout/stderr は取り込んで**ログに一切出さず捨てる**。
    子プロセスの環境変数は PATH/DRY_RUN/先読みだけ(GH_PAT・Webhook等は渡さない)。一時ファイルは終了時に削除。"""
    import re, shutil, subprocess, tempfile
    global _SENT_RE
    if _SENT_RE is None:
        _SENT_RE = re.compile(r"stepmail\.mjs 完了: 送信=(\d+)件")
    node = shutil.which("node")
    if not node:
        return None
    tmp = tempfile.mkdtemp(prefix="stepgate_")
    try:
        for rel in STEPMAIL_FILES:
            dest = Path(tmp) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(fetch_raw(repo, rel))
        env = {"PATH": os.environ.get("PATH", ""), "DRY_RUN": "1", "STEPMAIL_NOW_SKEW_MS": str(STEPMAIL_SKEW_MS)}
        r = subprocess.run([node, "stepmail.mjs"], cwd=tmp, env=env, capture_output=True, timeout=30)
        if r.returncode != 0:
            return None
        m = _SENT_RE.findall(r.stdout.decode("utf-8", "replace"))
        return int(m[-1]) if m else None
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _stepmail_streak(update):
    """update: 'run'(送信対象ありで起動) / 'skip' / None(読むだけ)。連続回数と通知済みフラグを返す。"""
    try:
        st = json.loads(STEPMAIL_GATE_STATE.read_text())
    except Exception:
        st = {}
    streak, alerted = int(st.get("streak", 0)), bool(st.get("alerted", False))
    if update == "run":
        streak += 1
    elif update == "skip":
        streak, alerted = 0, False
    return streak, alerted


def _stepmail_streak_save(streak, alerted):
    try:
        STEPMAIL_GATE_STATE.write_text(json.dumps({"streak": streak, "alerted": alerted}))
    except Exception as ex:
        log(f"stepmail_gate_state書込失敗 {type(ex).__name__}")


def stepmail_gate(e, repo):
    """True=この枠は送るものが無いので起動しない / False=起動する。判定は枠ごとに1回(main側でSKIPにも当日マーカーを付ける)。
    ログに出すのは件数だけ(userId・本文・enrollmentsの内容は出さない)。"""
    n = stepmail_pending(repo)
    if n is None:
        log(f"{e['id']}: gate(stepmail) 判定不能 → 従来どおり起動")
        return False
    if n == 0:
        log(f"{e['id']}: gate(stepmail) SKIP 送信対象0件")
        _stepmail_streak_save(*_stepmail_streak("skip"))
        return True
    log(f"{e['id']}: gate(stepmail) RUN 送信対象{n}件")
    streak, alerted = _stepmail_streak("run")
    if streak >= STEPMAIL_STREAK_ALERT and not alerted:
        try:
            import ops_deadman
            ops_deadman._load_dotenv()
            if ops_deadman.discord_send(f"⚠️ LINEステップ配信が滞留している可能性があります（送信対象ありの判定が{streak}枠連続・約{streak * 2}時間）。"
                                        "LINEトークン失効・LINE障害・line-stepmail-cloud のActions失敗を確認してください（お客様への配信が出ていない恐れ）"):
                alerted = True
        except Exception as ex:
            log(f"滞留通知失敗 {type(ex).__name__}")
    _stepmail_streak_save(streak, alerted)
    return False


def gate_skip(e, repo, now):
    """起動前ゲート（M10・2026-10-18）。entry の "gate": pool|qa|offer|features が付いたものだけ、
    「確実に何も起きない」時に起動を省略する。判定ロジックは story_gate.py（ig-autopost-cloud 本体の複製）。
    取得失敗・想定外・importエラーは全て False（=従来どおり起動）。entry から "gate" を消せば元の挙動。"""
    g = e.get("gate")
    if not g:
        return False
    if g == "stepmail":
        return stepmail_gate(e, repo)
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
            if pause_file_active(e, repo, today):
                log(f"{e['id']}: {e['pause_file']} により一時停止中 → 起動しない（今日は再判定しない）")
                state[e["id"]] = today; changed = True
                continue
            if queue_empty_today(e, repo, today):
                log(f"{e['id']}: {e['queue_file']} に本日({today})分なし → 起動しない（今日は再判定しない）")
                state[e["id"]] = today; changed = True
                continue
            if gate_skip(e, repo, now):
                if e.get("gate") == "stepmail":
                    # ステップ配信は『枠ごとに1回だけ』判定する(従来も枠の最初のサイクルのrunがその時点の期日分を送った)。
                    # 10分ごとに再判定すると、枠内で後から期日が来た分が従来より早く出てしまい配信時刻が変わる
                    log(f"{e['id']}: 今日のこの枠は判定済み → 再判定しない")
                    state[e["id"]] = today; changed = True
                continue   # 完了済み等で確実に空振り → 起動しない（story系はマーカーを付けず、次ループで再判定）
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


def run_ops_deadman():
    """Mac死活監視(cloud-daily-tasks / line-stepmail-cloud の2本)を公開repoのこのループで行う(2026-10-18 施策A)。
    非公開repoのActionsで3時間・2時間おきに起動していた見張りは99%が空振りの1分課金だったため、ここへ移した。
    別プロセス+timeoutで隔離し、失敗してもディスパッチャ本体には影響させない。auto_post.yml のループ起動済みの
    旧ステップでも毎サイクル git pull で本ファイルが更新されるので、workflow の再起動を待たずに有効になる。"""
    try:
        import subprocess
        subprocess.run([sys.executable, str(HERE / "ops_deadman.py")], timeout=45, check=False)
    except Exception as ex:
        print(f"::warning::ops_deadman 起動失敗: {type(ex).__name__}", flush=True)


if __name__ == "__main__":
    run_ops_deadman()   # 見張りは先に実行(数秒)。ゲート判定などで時間を使っても監視は必ず走る
    try:
        main()
    except BaseException as e:
        print(f"::warning::dispatcher fatal: {e}", flush=True)
    sys.exit(0)
