#!/usr/bin/env python3
"""
投稿フィードバックを1コマンドでルール化＋反映＋pushする。
どの端末・ツールからでも、リポジトリがあれば実行できる。

使い方:
  python3 fb.py "1文目に長岡市を入れない"            # 全アカウント
  python3 fb.py "もっと具体的な店舗性を出す" truth     # truthのみ
  python3 fb.py "軽症者向けに" nagaoka
  python3 fb.py "予告型は使わない" masa

これで:
  1. feedback.json と 全アカウント投稿ルール集.md にルールを記録
  2. 該当アカウントのキューを新ルールで再生成（即反映）
  3. git_sync で GitHub へ push（投稿システムに反映）
"""
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent
ACCTS = {"truth", "nagaoka", "masa", "all"}


def main():
    if len(sys.argv) < 2:
        print('使い方: python3 fb.py "フィードバック内容" [truth|nagaoka|masa|all]')
        sys.exit(1)
    text = sys.argv[1]
    acct = sys.argv[2].lower() if len(sys.argv) > 2 else "all"
    if acct not in ACCTS:
        print(f"アカウントは truth/nagaoka/masa/all のいずれか（指定: {acct}）")
        sys.exit(1)

    py = sys.executable
    # 1. ルール記録
    subprocess.run([py, str(BASE / "add_feedback.py"), text, "--account", acct], cwd=str(BASE))
    # 2. 再生成（即反映）
    targets = ["truth", "nagaoka", "masa"] if acct == "all" else [acct]
    for a in targets:
        subprocess.run([py, str(BASE / "generate_remix.py"), a], cwd=str(BASE), capture_output=True)
    print(f"  ✓ {', '.join(targets)} のキューを新ルールで再生成")
    # 3. push
    subprocess.run([py, str(BASE / "git_sync.py")], cwd=str(BASE))
    print("✓ ルール化 → 反映 → push 完了")


if __name__ == "__main__":
    main()
