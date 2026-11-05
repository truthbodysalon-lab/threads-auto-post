#!/usr/bin/env python3
"""@truth_nagaoka_zutsuu（頭痛）の Threads トークンを貼り付けて登録する（Mac で本人が実行）。2026-11-05（set_diet_token.py を元に作成）

Meta for Developers の「自動投稿のやつ（整体）」→ Threads API → 設定 → ユーザートークン生成ツール で
truth_nagaoka_zutsuu の「アクセストークンを生成」を押し、出てきたトークンを貼り付ける（入力は表示されない）。

  python3 set_zutsu_token.py

1) トークンで /me を呼び、username が truth_nagaoka_zutsuu か確かめる（違えば保存しない）
2) .env に THREADS_ACCESS_TOKEN_ZUTSU / THREADS_USER_ID_ZUTSU を保存
3) gh secret set で GitHub Secrets にも登録（値は画面に出さない）
"""
import getpass
import json
import subprocess
import sys
import urllib.parse
import urllib.request

from auth import ACCOUNT_CONFIG, load_env, save_env

EXPECTED = "truth_nagaoka_zutsuu"
REPO = "truthbodysalon-lab/threads-auto-post"


def main():
    cfg = ACCOUNT_CONFIG["zutsu"]
    token = getpass.getpass("Threadsのアクセストークンを貼り付けて Enter（表示されません）: ").strip()
    if not token:
        print("トークンが空です。中止します")
        return 1
    q = urllib.parse.urlencode({"fields": "id,username", "access_token": token})
    try:
        with urllib.request.urlopen(f"https://graph.threads.net/v1.0/me?{q}", timeout=30) as r:
            me = json.load(r)
    except Exception as e:  # noqa: BLE001
        print(f"トークンの確認に失敗しました: {e}")
        return 1
    if me.get("username") != EXPECTED:
        print(f"別のアカウントのトークンです（username={me.get('username')}）。保存しません")
        return 1
    env = load_env()
    env[cfg["token_key"]] = token
    env[cfg["uid_key"]] = str(me["id"])
    save_env(env)
    for name, value in ((cfg["token_key"], token), (cfg["uid_key"], str(me["id"]))):
        r = subprocess.run(["gh", "secret", "set", name, "-R", REPO], input=value, text=True,
                           capture_output=True, timeout=60)
        print(f"GitHub Secret {name}: {'登録OK' if r.returncode == 0 else '登録失敗 ' + r.stderr.strip()[:200]}")
    print(f"本人確認OK: username={me['username']}・.env に保存しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
