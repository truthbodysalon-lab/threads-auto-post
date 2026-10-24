#!/usr/bin/env python3
"""masa公式LINE『今週のプレゼント』友だち全員配布（クラウド版・2026-10-24）。

なぜ: Mac版(line-harness/present_broadcast.py)はMacが寝ている・止まっていると送れないため、
      無料枠の制限がない公開repoのGitHub Actionsへ移した（本人指示「Macがスリープしていても動くように」）。

- present_calendar.json の今の期のプレゼント1つを、1つの期につき1回だけ broadcast する。
- 台帳 present_broadcast_ledger.json（個人情報なし・期のキーと状態だけ）で二重送信を防ぐ。
  送る前に pending を書いて push し、送った後に sent/failed へ更新する（workflow側でpush）。
- pending/failed が残っている期は自動では再送しない（再送は本人判断: workflow_dispatch の retry_failed）。
使い方: python present_broadcast_cloud.py [--date YYYY-MM-DD --now HH:MM] [--send] [--retry-failed] [--stage plan|send]
  --stage plan: 送る必要があれば台帳に pending を書いて exit 0（送る必要なしは exit 3）
  --stage send: pending の期を実際に送り、sent/failed を書く
"""
import argparse, json, os, sys, urllib.error, urllib.request, uuid
from datetime import datetime, timedelta, date, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAL = HERE / "present_calendar.json"
LEDGER = HERE / "present_broadcast_ledger.json"
BROADCAST = "https://api.line.me/v2/bot/message/broadcast"
JST = timezone(timedelta(hours=9))
SEND_AFTER_HOUR = 12


def log(msg):
    print(f"{datetime.now(JST):%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def notify(text):
    hook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not hook:
        log("DISCORD_WEBHOOK_URL 未設定のため通知できません")
        return
    try:
        req = urllib.request.Request(hook, data=json.dumps({"content": text[:1900]}).encode("utf-8"),
                                     method="POST", headers={"Content-Type": "application/json",
                                                             "User-Agent": "present-broadcast/1.0"})
        urllib.request.urlopen(req, timeout=20).close()
    except Exception as e:
        log(f"Discord通知失敗: {e}")


def current_present(cal, today):
    anchor = date.fromisoformat(cal["anchor"])
    days = int(cal.get("days", 7))
    delta = (today - anchor).days
    if delta < 0:
        return None
    n = delta // days
    ps = cal["presents"]
    p = dict(ps[n % len(ps)])
    p["period_start"] = (anchor + timedelta(days=n * days)).isoformat()
    return p


def build_message(p):
    # Mac版と一字一句同じ文面
    return (f"今週のプレゼントのお知らせです。\n\n"
            f"今週は「{p['name']}」です。\n{p['hook']}\n\n"
            f"受け取る方は、このLINEに「{p['keyword']}」と送ってください。すぐにお届けします。\n\n"
            f"─ 髙橋 雅英")


def load_ledger():
    if LEDGER.exists():
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    return {"_doc": "present_broadcast_cloud.py の送信台帳。キー=プレゼント@期の開始日。個人情報は入れない", "periods": {}}


def save_ledger(led):
    LEDGER.write_text(json.dumps(led, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def broadcast(token, text):
    # X-Line-Retry-Key はハイフン付きUUID必須（無いと400。10/24 Mac版で実際に失敗）
    req = urllib.request.Request(BROADCAST, data=json.dumps({"messages": [{"type": "text", "text": text}]}).encode(),
                                 method="POST", headers={"Authorization": f"Bearer {token}",
                                                         "Content-Type": "application/json",
                                                         "X-Line-Retry-Key": str(uuid.uuid4())})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:800]
        except Exception:
            detail = ""
        return e.code, detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date"); ap.add_argument("--now")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--stage", choices=["plan", "send"], required=True)
    a = ap.parse_args()
    now = datetime.now(JST)
    if a.date:
        now = datetime.combine(date.fromisoformat(a.date),
                               datetime.strptime(a.now or "12:05", "%H:%M").time()).replace(tzinfo=JST)
    cal = json.loads(CAL.read_text(encoding="utf-8"))
    p = current_present(cal, now.date())
    if not p:
        log(f"期間外（開始 {cal['anchor']} 前）: 何もしません")
        return 3
    pkey = f"{p['key']}@{p['period_start']}"
    led = load_ledger()
    rec = led["periods"].get(pkey)

    if a.stage == "plan":
        if now.hour < SEND_AFTER_HOUR:
            log(f"{now:%H:%M} は {SEND_AFTER_HOUR}:00 より前: 何もしません")
            return 3
        if rec and rec.get("status") == "sent":
            log(f"{pkey} は送信済み（{rec.get('at')}）: 何もしません")
            return 3
        if rec and not a.retry_failed:
            log(f"{pkey} は状態={rec.get('status')}（{rec.get('at')}）。自動では再送しません")
            if not rec.get("alerted"):
                rec["alerted"] = True
                save_ledger(led)
                notify(f"🙋公式LINEのプレゼント配布（{p['name']}）が「{rec.get('status')}」のまま止まっています。"
                       f"届いているかLINE公式アカウントの画面で確認し、未配信なら GitHub の present_broadcast を "
                       f"retry_failed=true で手動実行してください")
                return 0  # 台帳(alerted)をpushさせる。sendステップは pending ではないので送らない
            return 3
        log(f"[計画] 期={p['period_start']}〜 プレゼント={p['key']}({p['name']}) キーワード={p['keyword']}")
        log("本文:\n" + build_message(p))
        if not a.send:
            log("DRY_RUN: 台帳は書きません")
            return 3
        led["periods"][pkey] = {"status": "pending", "at": now.strftime("%Y-%m-%d %H:%M:%S")}
        save_ledger(led)
        return 0

    # stage == send
    if not rec or rec.get("status") != "pending":
        log(f"{pkey} は送る状態ではありません（{rec and rec.get('status')}）")
        return 0
    token = os.environ.get("LINE_MASA_CHANNEL_ACCESS_TOKEN", "").strip()
    if not token:
        code, detail = 0, "LINE_MASA_CHANNEL_ACCESS_TOKEN 未設定"
    else:
        code, detail = broadcast(token, build_message(p))
    ok = 200 <= code < 300
    led["periods"][pkey] = {"status": "sent" if ok else "failed",
                            "at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
                            **({} if ok else {"error": f"HTTP {code} {detail}"[:300]})}
    save_ledger(led)
    log(f"完了: 全員宛 {'成功' if ok else '失敗'} {'' if ok else detail}")
    notify(f"公式LINEでプレゼント配布({p['name']}・友だち全員・クラウド): "
           + ("送信しました" if ok else f"失敗しました（HTTP {code}）。GitHub Actions の present_broadcast のログを確認してください"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
