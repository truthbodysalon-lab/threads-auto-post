#!/usr/bin/env python3
"""
フィードバック保存ツール
チャットで受け取ったフィードバックをfeedback.json + 全アカウント投稿ルール集.mdに保存する

使い方:
  python3 add_feedback.py "1文目に長岡市を入れない"         # 全アカウント共通
  python3 add_feedback.py "短文にする" --account truth      # truthのみ
  python3 add_feedback.py "まぁを使わない" --account nagaoka
  python3 add_feedback.py --list                             # 現在のノート一覧
"""
import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
FEEDBACK_FILE = BASE / "feedback.json"
RULES_FILE = Path("/Users/mt112/Desktop/my files/myfiles/SNS・Threads/全アカウント投稿ルール集.md")


def load_feedback():
    if FEEDBACK_FILE.exists():
        try:
            return json.loads(FEEDBACK_FILE.read_text())
        except Exception:
            pass
    return {"pattern_weights": {}, "ng_words": [], "extra_templates": {}, "notes": []}


def save_feedback(fb: dict):
    fb["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    FEEDBACK_FILE.write_text(json.dumps(fb, ensure_ascii=False, indent=2))


def add_note(text: str, account: str = "all"):
    fb = load_feedback()
    notes = fb.get("notes", [])
    today = datetime.now().strftime("%Y-%m-%d")

    # 同じ内容が既にあればスキップ
    if any(n["text"] == text for n in notes):
        print(f"⚠️  既に登録済み: {text[:60]}")
        return

    notes.append({"date": today, "text": text, "account": account})
    fb["notes"] = notes
    save_feedback(fb)
    print(f"✓ feedback.json に保存: [{account}] {text[:70]}")

    # 全アカウント投稿ルール集.md にも追記
    try:
        if RULES_FILE.exists():
            existing = RULES_FILE.read_text(encoding="utf-8")
            if text not in existing:
                append_text = f"\n### ✅ {today}追加\n- {text}\n"
                with open(RULES_FILE, "a", encoding="utf-8") as f:
                    f.write(append_text)
                print(f"✓ 全アカウント投稿ルール集.md にも追記")
    except Exception as e:
        print(f"⚠️  ルール集追記失敗: {e}")


def add_ng_word(word: str):
    fb = load_feedback()
    ng = fb.get("ng_words", [])
    if word not in ng:
        ng.append(word)
        fb["ng_words"] = ng
        save_feedback(fb)
        print(f"✓ NGワード追加: {word}")
    else:
        print(f"⚠️  既に登録済み: {word}")


def list_notes():
    fb = load_feedback()
    notes = fb.get("notes", [])
    print(f"=== フィードバックノート ({len(notes)}件) ===")
    for n in notes:
        print(f"  [{n.get('account','all')}] {n['date']}: {n['text'][:80]}")
    print()
    ng = fb.get("ng_words", [])
    if ng:
        print(f"=== NGワード ({len(ng)}件) ===")
        for w in ng:
            print(f"  {w}")


def main():
    args = sys.argv[1:]

    if not args or "--list" in args:
        list_notes()
        return

    account = "all"
    if "--account" in args:
        idx = args.index("--account")
        account = args[idx + 1]
        args = [a for i, a in enumerate(args) if i not in (idx, idx + 1)]

    if "--ng" in args:
        args.remove("--ng")
        if args:
            add_ng_word(args[0])
        return

    if args:
        add_note(args[0], account=account)


if __name__ == "__main__":
    main()
