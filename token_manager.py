#!/usr/bin/env python3
"""
トークン管理スクリプト
- Threadsアクセストークンの有効期限を確認・リフレッシュ
- リフレッシュ後にGitHub Secretsを自動更新（Mac不要でも継続動作）
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from base64 import b64decode, b64encode
from pathlib import Path

BASE = Path(__file__).parent
ENV_FILE = BASE / ".env"
BASE_URL = "https://graph.threads.net/v1.0"

ACCOUNTS = {
    "truth": {
        "token_key": "THREADS_ACCESS_TOKEN_TRUTH",
        "uid_key":   "THREADS_USER_ID_TRUTH",
    },
    "masa": {
        "token_key": "THREADS_ACCESS_TOKEN_MASA",
        "uid_key":   "THREADS_USER_ID_MASA",
    },
}


# ── .env 読み書き ─────────────────────────────────

def load_env():
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

def save_env_key(key: str, value: str):
    if not ENV_FILE.exists():
        return
    lines = ENV_FILE.read_text().splitlines()
    new_lines, updated = [], False
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n")
    os.environ[key] = value


# ── GitHub Secret 更新 ────────────────────────────

def _gh_api(path: str, method: str = "GET", body: dict = None) -> dict | None:
    pat = os.environ.get("GH_PAT", "")
    if not pat:
        return None
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"token {pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read()) if r.status != 204 else {}
    except Exception as e:
        print(f"  GitHub API エラー ({path}): {e}")
        return None

def update_github_secret(repo: str, secret_name: str, secret_value: str) -> bool:
    """GitHub Secretを暗号化して更新"""
    try:
        from nacl.public import PublicKey, SealedBox
    except ImportError:
        print("  PyNaCl未インストール → Secret更新スキップ")
        return False

    pub_data = _gh_api(f"/repos/{repo}/actions/secrets/public-key")
    if not pub_data:
        return False

    key_id  = pub_data["key_id"]
    pub_key = pub_data["key"]

    # 値を暗号化
    pub = PublicKey(b64decode(pub_key))
    box = SealedBox(pub)
    encrypted = b64encode(box.encrypt(secret_value.encode())).decode()

    result = _gh_api(
        f"/repos/{repo}/actions/secrets/{secret_name}",
        method="PUT",
        body={"encrypted_value": encrypted, "key_id": key_id}
    )
    return result is not None


# ── トークンリフレッシュ ──────────────────────────

def refresh_token(acct: str) -> str | None:
    token = os.environ.get(ACCOUNTS[acct]["token_key"], "")
    if not token:
        return None
    url = f"{BASE_URL}/refresh_access_token?grant_type=th_refresh_token&access_token={token}"
    try:
        with urllib.request.urlopen(url) as r:
            data = json.loads(r.read())
        new_token = data.get("access_token", "")
        if new_token:
            save_env_key(ACCOUNTS[acct]["token_key"], new_token)
            print(f"  [{acct}] トークンリフレッシュ完了")
            return new_token
    except Exception as e:
        print(f"  [{acct}] リフレッシュ失敗: {e}")
    return None

def check_token(acct: str) -> dict:
    """トークンの有効期限を確認"""
    token = os.environ.get(ACCOUNTS[acct]["token_key"], "")
    uid   = os.environ.get(ACCOUNTS[acct]["uid_key"], "")
    if not token or not uid:
        return {"valid": False, "days_left": 0}
    url = f"{BASE_URL}/{uid}?fields=id&access_token={token}"
    try:
        with urllib.request.urlopen(url) as r:
            json.loads(r.read())
        return {"valid": True}
    except Exception:
        return {"valid": False, "days_left": 0}


# ── メイン ─────────────────────────────────────────

def sync_secrets():
    """リフレッシュしたトークンをGitHub Secretsに書き戻す"""
    load_env()
    repo = os.environ.get("GH_REPO", "")
    pat  = os.environ.get("GH_PAT", "")

    if not repo or not pat:
        print("GH_PAT / GH_REPO が未設定 → Secret同期スキップ")
        return

    print("=== トークン同期 ===")
    for acct, info in ACCOUNTS.items():
        token = os.environ.get(info["token_key"], "")
        if not token:
            continue
        # 常にリフレッシュ（60日で期限切れを防ぐ）
        new_token = refresh_token(acct)
        if new_token:
            ok = update_github_secret(repo, info["token_key"], new_token)
            print(f"  [{acct}] GitHub Secret 更新: {'✓' if ok else '失敗'}")
        else:
            print(f"  [{acct}] リフレッシュ不要またはスキップ")


def show_status():
    """トークンの状態を表示"""
    load_env()
    print("=== トークン状態 ===")
    for acct in ACCOUNTS:
        status = check_token(acct)
        mark = "✓ 有効" if status["valid"] else "✗ 無効"
        print(f"  [{acct}] {mark}")


if __name__ == "__main__":
    if "--sync-secrets" in sys.argv:
        sync_secrets()
    elif "--status" in sys.argv:
        show_status()
    elif "--refresh" in sys.argv:
        load_env()
        for acct in ACCOUNTS:
            refresh_token(acct)
    else:
        show_status()
