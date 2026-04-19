#!/usr/bin/env python3
"""
Threads API OAuth認証スクリプト
実行するとブラウザが開き、認証後にアクセストークンを .env に保存します。

使い方:
  python auth.py                     # truth_body_salonアカウントで認証
  python auth.py masahide            # masahide_takahashi_アカウントで認証
"""

import os
import sys
import json
import urllib.parse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import urllib.request
from pyngrok import ngrok

# ── 設定 ──────────────────────────────────────────
PORT = 8080
AUTH_URL = "https://threads.net/oauth/authorize"
TOKEN_URL = "https://graph.threads.net/oauth/access_token"
LONG_LIVED_TOKEN_URL = "https://graph.threads.net/access_token"
SCOPE = "threads_basic,threads_content_publish"

ENV_FILE = Path(__file__).parent / ".env"

# ─────────────────────────────────────────────────


def load_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def save_env(env: dict):
    lines = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text().splitlines()

    result = []
    written_keys = set()

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in env:
                result.append(f"{k}={env[k]}")
                written_keys.add(k)
            else:
                result.append(line)
        else:
            result.append(line)

    for k, v in env.items():
        if k not in written_keys:
            result.append(f"{k}={v}")

    ENV_FILE.write_text("\n".join(result) + "\n")
    print(f"  → .env に保存しました")


# ── OAuthコールバック受信サーバー ──────────────────
received_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global received_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            received_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<h2>認証完了！このタブを閉じてください。</h2>".encode("utf-8")
            )
        else:
            error = params.get("error", ["不明"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<h2>エラー: {error}</h2>".encode("utf-8"))

    def log_message(self, format, *args):
        pass  # アクセスログを非表示


# ── アクセストークン取得 ─────────────────────────────


def exchange_code_for_token(code: str, app_id: str, app_secret: str, callback_url: str) -> dict:
    data = urllib.parse.urlencode(
        {
            "client_id": app_id,
            "client_secret": app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": callback_url,
            "code": code,
        }
    ).encode()

    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get_long_lived_token(short_token: str, app_secret: str) -> dict:
    params = urllib.parse.urlencode(
        {
            "grant_type": "th_exchange_token",
            "client_secret": app_secret,
            "access_token": short_token,
        }
    )
    url = f"{LONG_LIVED_TOKEN_URL}?{params}"
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


def get_user_id(access_token: str) -> str:
    url = f"https://graph.threads.net/v1.0/me?fields=id,username&access_token={access_token}"
    with urllib.request.urlopen(url) as resp:
        data = json.loads(resp.read())
        return data["id"], data.get("username", "")


# ── メイン ────────────────────────────────────────


ACCOUNT_CONFIG = {
    "truth":   {"token_key": "THREADS_ACCESS_TOKEN_TRUTH",   "uid_key": "THREADS_USER_ID_TRUTH",   "name": "@truth_body_salon"},
    "masa":    {"token_key": "THREADS_ACCESS_TOKEN_MASA",    "uid_key": "THREADS_USER_ID_MASA",    "name": "@masahide_takahashi_"},
    "nagaoka": {"token_key": "THREADS_ACCESS_TOKEN_NAGAOKA", "uid_key": "THREADS_USER_ID_NAGAOKA", "name": "@truth_nagaoka"},
}


def push_to_github_secrets(env: dict, token_key: str, uid_key: str):
    """取得したトークン・UIDをGitHub Secretsに自動登録"""
    try:
        from nacl.public import PublicKey, SealedBox
        from base64 import b64decode, b64encode
    except ImportError:
        print("  PyNaCl未インストール → GitHub Secrets自動登録スキップ")
        return

    pat  = env.get("GH_PAT", "")
    repo = env.get("GH_REPO", "")
    if not pat or not repo:
        print("  GH_PAT/GH_REPO未設定 → GitHub Secrets自動登録スキップ")
        return

    import urllib.request as _req
    def _api(path, method="GET", body=None):
        url = f"https://api.github.com{path}"
        data = json.dumps(body).encode() if body else None
        r = _req.Request(url, data=data, method=method, headers={
            "Authorization": f"token {pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        })
        try:
            with _req.urlopen(r) as resp:
                return json.loads(resp.read()) if resp.status != 204 else {}
        except Exception as e:
            print(f"  GitHub API エラー: {e}")
            return None

    pub_data = _api(f"/repos/{repo}/actions/secrets/public-key")
    if not pub_data:
        return

    key_id  = pub_data["key_id"]
    pub_key = pub_data["key"]

    def _put_secret(name, value):
        pub = PublicKey(b64decode(pub_key))
        encrypted = b64encode(SealedBox(pub).encrypt(value.encode())).decode()
        ok = _api(f"/repos/{repo}/actions/secrets/{name}", "PUT",
                  {"encrypted_value": encrypted, "key_id": key_id})
        print(f"  GitHub Secret [{name}]: {'✓ 登録完了' if ok is not None else '失敗'}")

    _put_secret(token_key, env[token_key])
    _put_secret(uid_key,   env[uid_key])


def main():
    account = sys.argv[1] if len(sys.argv) > 1 else "truth"
    acct_key = "masa" if "masa" in account.lower() else "nagaoka" if "nagaoka" in account.lower() else "truth"
    cfg = ACCOUNT_CONFIG[acct_key]

    env = load_env()

    app_id = env.get("THREADS_APP_ID", "")
    app_secret = env.get("THREADS_APP_SECRET", "")

    if not app_id or app_id == "your_app_id_here":
        app_id = input("Threads App ID を入力してください: ").strip()
    if not app_secret or app_secret == "your_app_secret_here":
        app_secret = input("Threads App Secret を入力してください: ").strip()

    # ngrokでHTTPS URLを生成
    print("\nHTTPS Callback URLを生成中...")
    tunnel = ngrok.connect(PORT, "http")
    callback_url = tunnel.public_url + "/callback"
    print(f"\n{'='*50}")
    print(f"Callback URL（Meta Developerに登録してください）:")
    print(f"\n  {callback_url}\n")
    print(f"{'='*50}")
    print("↑ このURLを Meta Developer Console の")
    print("  「コールバックURLをリダイレクト」に追加して保存してください。")
    input("\n保存したら Enter を押してください...")

    # 認証URL生成
    params = urllib.parse.urlencode({
        "client_id": app_id,
        "redirect_uri": callback_url,
        "scope": SCOPE,
        "response_type": "code",
    })
    auth_url = f"{AUTH_URL}?{params}"

    print(f"\n--- Threads OAuth 認証: {cfg['name']} ---")
    print(f"ブラウザで認証ページを開きます...")
    webbrowser.open(auth_url)

    # コールバック待機
    print(f"コールバックを待機中...")
    server = HTTPServer(("localhost", PORT), CallbackHandler)
    server.handle_request()
    ngrok.disconnect(tunnel.public_url)

    if not received_code:
        print("認証コードを受信できませんでした")
        sys.exit(1)

    print(f"認証コード受信 ✓")

    # 短期トークン取得
    print("アクセストークンを取得中...")
    token_data = exchange_code_for_token(received_code, app_id, app_secret, callback_url)
    short_token = token_data["access_token"]

    # 長期トークンに交換（60日間有効）
    print("長期トークンに交換中...")
    long_data = get_long_lived_token(short_token, app_secret)
    long_token = long_data["access_token"]
    expires_in = long_data.get("expires_in", 0)
    print(f"  有効期限: {expires_in // 86400} 日間")

    # ユーザーID取得
    user_id, username = get_user_id(long_token)
    print(f"  ユーザー: @{username} (ID: {user_id})")

    # .env に保存
    env["THREADS_APP_ID"] = app_id
    env["THREADS_APP_SECRET"] = app_secret
    env[cfg["token_key"]] = long_token
    env[cfg["uid_key"]] = user_id
    save_env(env)
    print(f"\n✓ {cfg['name']} のトークンを.envに保存しました")

    # GitHub Secrets に自動登録
    print("\nGitHub Secretsに自動登録中...")
    push_to_github_secrets(env, cfg["token_key"], cfg["uid_key"])

    print(f"\n✓ 認証完了！{cfg['name']} の自動投稿が有効になりました。")


if __name__ == "__main__":
    main()
