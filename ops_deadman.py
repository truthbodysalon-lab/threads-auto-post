#!/usr/bin/env python3
"""Mac死活監視（見張り2本）の公開repo版（2026-10-18 Actions無料枠対策・施策A）

背景:
  非公開repoのActionsは月2,000分の無料枠をアカウント全体で共有する。見張り2本
  (cloud-daily-tasks/deadman.yml と line-stepmail-cloud/deadman.yml) は 99%/92% が「正常・何もしない」
  なのに、1回ごとに1分へ切り上げ課金され月867分を消費していた。
  公開repo(threads-auto-post)のActionsは無料・無制限なので、既に10分ごとに動いている常駐ループ
  (auto_post.yml → dispatcher.py)に同居させる。検知は旧方式より早くなる(旧: 最大8〜9時間/6〜7時間 → 新: 約6時間10分/3時間10分)。

見るもの(GH_PATで読む。新しいSecretは不要):
  cloud : cloud-daily-tasks の heartbeat/heartbeat.txt の最終コミット時刻(committer date)  しきい値 6時間 / 再通知抑制 12時間
  line  : line-stepmail-cloud の heartbeat ブランチ heartbeat.json の {"ts": ISO}            しきい値 3時間 / 再通知抑制 3時間
通知: Discordのみ(本人宛。LINEは使わない)。文面は旧deadmanと同一。

公開ログに出すのは「経過秒数」と OK/ALERT の語だけ。API応答の本文・Webhook URL・トークンは出さない。
読み取り失敗(PAT失効・権限不足・通信障害)は1回では警告せず、連続6回(約1時間)で「見張りが読めていない」を1回だけ通知する。

環境変数(テスト用):
  OPS_DEADMAN_TEST=1        "[試験]"付きで両方の警告文をDiscordへ送る(状態は書かない)。実データの経過秒数は使わず擬似的に古い値を使う
  OPS_DEADMAN_DRY=1         送信せず内容をログに出すだけ
  OPS_DEADMAN_NOW=<epoch>   現在時刻の差し替え(単体テスト用)
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "deadman_state.json"
API = "https://api.github.com"
OWNER = "truthbodysalon-lab"
CLOUD_REPO = f"{OWNER}/cloud-daily-tasks"
LINE_REPO = f"{OWNER}/line-stepmail-cloud"
TARGET_CONFIG_FILE = HERE / "ops_targets.json"
TARGET_CONFIG = json.loads(TARGET_CONFIG_FILE.read_text()) if TARGET_CONFIG_FILE.exists() else {}
LINE_REPO = TARGET_CONFIG.get("line_repo", LINE_REPO)

CLOUD_THRESHOLD = 21600       # 6時間(旧 deadman.yml と同じ)
CLOUD_SUPPRESS = 43200        # 再通知抑制12時間(旧と同じ)
LINE_THRESHOLD = 10800        # 3時間(旧 deadman.mjs と同じ)
LINE_SUPPRESS = 10800         # 旧は抑制なし(3時間おきの実行=3時間に1回)。新も3時間に1回までで頻度は増えない
READ_FAIL_LIMIT = 6           # 連続6回(約1時間)読めなければ「見張りが読めていない」を1回通知

JST = timezone(timedelta(hours=9))


class ReadFailure(Exception):
    """heartbeat を『読めなかった』(権限・通信・5xx)。『heartbeat が無い』とは区別する。"""


def log(msg):
    print(f"[ops_deadman {datetime.now(JST):%H:%M:%S}] {msg}", flush=True)


def now_epoch():
    v = os.environ.get("OPS_DEADMAN_NOW")
    return int(v) if v else int(time.time())


def _load_dotenv():
    """auto_post.yml の常駐ループ(起動済みの旧ステップ)は env に DISCORD_WEBHOOK_URL を渡していないため .env も見る。"""
    try:
        for line in (HERE / ".env").read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


def token():
    return os.environ.get("GH_TOKEN") or os.environ.get("GH_PAT") or ""


def api(path):
    """GET。成功=(status, json)。HTTPエラーは (code, None)。通信例外は ReadFailure。"""
    req = urllib.request.Request(API + path, headers={
        "Authorization": f"Bearer {token()}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ops-deadman"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        raise ReadFailure(type(e).__name__)


def parse_iso(s):
    s = s.replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def read_cloud_heartbeat_epoch():
    """cloud-daily-tasks の heartbeat/heartbeat.txt の最終コミット時刻(epoch)。
    旧: git log -1 --format=%ct -- heartbeat/heartbeat.txt (committer date)。コミットが無ければ 0(=旧と同じ『記録なし』扱い)。"""
    st, d = api(f"/repos/{CLOUD_REPO}/commits?path=heartbeat/heartbeat.txt&per_page=1")
    if st != 200 or not isinstance(d, list):
        raise ReadFailure(f"HTTP{st}")
    if not d:
        return 0
    try:
        return int(parse_iso(d[0]["commit"]["committer"]["date"]).timestamp())
    except Exception:
        raise ReadFailure("parse")


def read_line_heartbeat_ts():
    """line-stepmail-cloud の heartbeat ブランチの heartbeat.json の ts(ISO文字列)。
    ファイルが無い/壊れている→None(旧 deadman.mjs と同じ『記録なし』)。読めなかった→ReadFailure。"""
    st, d = api(f"/repos/{LINE_REPO}/contents/heartbeat.json?ref=heartbeat")
    if st == 404:
        # 権限が無い非公開repoも404になるので、repo自体が読めるかで区別する
        rs, _ = api(f"/repos/{LINE_REPO}")
        if rs != 200:
            raise ReadFailure(f"HTTP{rs}")
        return None
    if st != 200 or not isinstance(d, dict):
        raise ReadFailure(f"HTTP{st}")
    try:
        obj = json.loads(base64.b64decode(d["content"]).decode("utf-8"))
        ts = obj.get("ts")
        return ts if ts else None
    except Exception:
        return None


def format_jst(iso):
    """deadman.mjs formatJst と同じ書式: YYYY-MM-DD HH:MM JST"""
    if not iso:
        return "（不明）"
    try:
        d = parse_iso(iso)
    except Exception:
        return "（不明な日時）"
    return d.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


def cloud_message(age_sec):
    hours = age_sec // 3600
    return (f"🚨 Mac停止検知: ハートビートが{hours}時間途絶。"
            "クラウドのGmail通知/医学学習は継続稼働中。マーケ学習のCodex生成はMac復旧が必要")


def line_message(ts_iso):
    """ts_iso が None/不正なら『記録なし』。"""
    label = "記録なし"
    if ts_iso:
        try:
            parse_iso(ts_iso)
            label = format_jst(ts_iso)
        except Exception:
            label = "記録なし"
    return f"⚠️ Macのライン配信同期が止まっています（最終同期: {label}）。クラウド側のステップ配信は継続中です"


def discord_send(text):
    hook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if os.environ.get("OPS_DEADMAN_DRY") == "1":
        log("[DRY] 送信予定の文面: " + text)
        return True
    if not hook:
        log("DISCORD_WEBHOOK_URL 未設定のため送信できません")
        return False
    for attempt in range(2):
        try:
            req = urllib.request.Request(hook, data=json.dumps({"content": text[:1900]}).encode("utf-8"),
                                         method="POST",
                                         headers={"Content-Type": "application/json", "User-Agent": "ops-deadman/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status in (200, 204):
                    return True
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                try:
                    wait = float(json.loads(e.read().decode()).get("retry_after", 2))
                except Exception:
                    wait = 2.0
                time.sleep(min(wait, 10) + 0.5)
                continue
            log(f"Discord送信失敗 HTTP{e.code}")
            return False
        except Exception as e:
            log(f"Discord送信失敗 {type(e).__name__}")
            return False
    return False


def load_state():
    try:
        d = json.loads(STATE.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_state(st):
    try:
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1, sort_keys=True))
    except Exception as e:
        log(f"state書込失敗 {type(e).__name__}")


def check_one(name, st, now, reader, threshold, suppress, make_message, label):
    """1系統を検査し、st[name] を更新する。戻り値: 状態の語 OK / ALERT / SUPPRESSED / READFAIL。"""
    s = st.setdefault(name, {})
    try:
        value = reader()
    except ReadFailure as e:
        s["read_fail"] = int(s.get("read_fail", 0)) + 1
        log(f"{label}: heartbeat を読めません({e}) 連続{s['read_fail']}回")
        if s["read_fail"] >= READ_FAIL_LIMIT and not s.get("read_warned"):
            msg = (f"⚠️ 見張り（{label}）がハートビートを読めていません（連続{s['read_fail']}回・約1時間）。"
                   "GH_PATの権限または失効を確認してください。この間、Mac停止の検知ができません")
            if discord_send(msg):
                s["read_warned"] = True
        return "READFAIL"
    s["read_fail"] = 0
    s["read_warned"] = False

    if name == "cloud":
        age = now - value if value else now      # 旧: HB_TS=0 → AGE=NOW
        stale = age > threshold
        shown_age = age
    else:
        try:
            last_ms = parse_iso(value).timestamp() if value else None
        except Exception:
            last_ms = None
        stale = last_ms is None or (now - last_ms) > threshold
        shown_age = int(now - last_ms) if last_ms is not None else -1

    if not stale:
        log(f"{label}: heartbeat age={shown_age}s threshold={threshold}s -> OK")
        s["alerted_at"] = 0     # 回復したら抑制を解除(次の停止は即通知)
        return "OK"

    since = now - int(s.get("alerted_at", 0) or 0)
    if since < suppress:
        log(f"{label}: heartbeat age={shown_age}s threshold={threshold}s -> ALERT(抑制中 前回通知から{since}s)")
        return "SUPPRESSED"
    msg = make_message(value if name == "line" else shown_age)
    if discord_send(msg):
        s["alerted_at"] = now
        log(f"{label}: heartbeat age={shown_age}s threshold={threshold}s -> ALERT 通知済み")
        return "ALERT"
    log(f"{label}: heartbeat age={shown_age}s threshold={threshold}s -> ALERT(通知失敗・次回再試行)")
    return "ALERT"


def run_test():
    """[試験] 両方の警告文を実際にDiscordへ送る(実データは見ない・状態は書かない)。"""
    now = now_epoch()
    fake_cloud_age = 25 * 3600 + 120
    fake_line_ts = datetime.fromtimestamp(now - 4 * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    ok1 = discord_send("[試験] 見張り移設テスト(1/2) " + cloud_message(fake_cloud_age))
    ok2 = discord_send("[試験] 見張り移設テスト(2/2) " + line_message(fake_line_ts))
    log(f"試験通知 cloud={'送信' if ok1 else '失敗'} line={'送信' if ok2 else '失敗'}")
    return 0 if (ok1 and ok2) else 1


def main():
    _load_dotenv()
    if os.environ.get("OPS_DEADMAN_TEST") == "1":
        return run_test()
    now = now_epoch()
    st = load_state()
    before = json.dumps(st, sort_keys=True)
    if TARGET_CONFIG.get("cloud", True):
        r1 = check_one("cloud", st, now, read_cloud_heartbeat_epoch, CLOUD_THRESHOLD, CLOUD_SUPPRESS,
                   cloud_message, "cloud")
    if TARGET_CONFIG.get("line", True):
        r2 = check_one("line", st, now, read_line_heartbeat_ts, LINE_THRESHOLD, LINE_SUPPRESS,
                   line_message, "line")
    if json.dumps(st, sort_keys=True) != before:
        save_state(st)
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as e:   # 例外はループへ出さない(型名のみログ)
        print(f"::warning::ops_deadman fatal: {type(e).__name__}", flush=True)
        rc = 0
    sys.exit(rc)
