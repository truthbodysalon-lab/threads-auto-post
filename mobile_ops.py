#!/usr/bin/env python3
"""
モバイル／Claude Code 向け非インタラクティブ操作スクリプト。
すべてコマンドライン引数で完結し、対話入力を必要としない。

Usage:
  python3 mobile_ops.py queue truth [--today]
  python3 mobile_ops.py edit truth <index> "新しいテキスト"
  python3 mobile_ops.py delete truth <index>
  python3 mobile_ops.py status [truth|masa]
  python3 mobile_ops.py posted truth [N]
  python3 mobile_ops.py rate truth <index> good|bad [コメント]
  python3 mobile_ops.py weight <pattern> <value>
  python3 mobile_ops.py ng add "フレーズ"
  python3 mobile_ops.py ng remove "フレーズ"
  python3 mobile_ops.py ng list
  python3 mobile_ops.py feedback show
  python3 mobile_ops.py note "メモ内容"
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).parent

ACCOUNT_FILES = {
    "truth": {
        "log": BASE / "log_truth.jsonl",
        "posted": BASE / "log_truth_posted.jsonl",
    },
    "masa": {
        "log": BASE / "log_masa.jsonl",
        "posted": BASE / "log_masa_posted.jsonl",
    },
}

FEEDBACK_FILE = BASE / "feedback.json"

DEFAULT_FEEDBACK = {
    "pattern_weights": {
        "aoi_style": 1.0, "hori_style": 1.0, "hook_one_line": 1.0,
        "quote_empathy": 1.0, "insight": 1.0, "education": 1.0,
        "story": 1.0, "workmom": 1.0, "gyakusetsu": 1.0,
        "ranking": 1.0, "question": 1.0,
        "soft_line": 1.0, "cta": 1.0,
    },
    "ng_words": [],
    "extra_templates": {"truth": [], "masa": []},
    "notes": [],
    "updated_at": "",
}


def load_feedback() -> dict:
    if FEEDBACK_FILE.exists():
        try:
            data = json.loads(FEEDBACK_FILE.read_text())
            for k, v in DEFAULT_FEEDBACK.items():
                if k not in data:
                    data[k] = v
            return data
        except Exception:
            pass
    return {k: (v.copy() if isinstance(v, (dict, list)) else v)
            for k, v in DEFAULT_FEEDBACK.items()}


def save_feedback(data: dict):
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    FEEDBACK_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def load_log(acct: str) -> list[dict]:
    f = ACCOUNT_FILES[acct]["log"]
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()]


def save_log(acct: str, entries: list[dict]):
    f = ACCOUNT_FILES[acct]["log"]
    f.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n")


def load_posted(acct: str) -> list[dict]:
    f = ACCOUNT_FILES[acct]["posted"]
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()]


# ── コマンド実装 ─────────────────────────────────────

def cmd_queue(acct: str, today_only: bool = False):
    """キュー内の投稿一覧を表示"""
    entries = load_log(acct)
    if not entries:
        print(f"[{acct}] キューは空です")
        return

    today = date.today().strftime("%Y-%m-%d")
    posted_set = {
        json.loads(l)["text"]
        for l in ACCOUNT_FILES[acct]["posted"].read_text().splitlines()
        if l.strip() and json.loads(l).get("text")
    } if ACCOUNT_FILES[acct]["posted"].exists() else set()

    for entry in entries:
        d = entry.get("date", "?")
        if today_only and d != today:
            continue
        posts = entry.get("posts", [])
        print(f"\n=== {acct} / {d} ({len(posts)}件) ===")
        for i, text in enumerate(posts):
            short = text[:80].replace("\n", " ")
            status = "✓投稿済" if text in posted_set or text.split("\n")[0][:80] in {t[:80] for t in posted_set} else "未投稿"
            print(f"  [{i:2d}] ({status}) {short}")


def cmd_edit(acct: str, index: int, new_text: str):
    """指定インデックスの投稿を編集"""
    entries = load_log(acct)
    today = date.today().strftime("%Y-%m-%d")
    for entry in reversed(entries):
        if entry.get("date") == today:
            posts = entry.get("posts", [])
            if 0 <= index < len(posts):
                old = posts[index][:60].replace("\n", " ")
                posts[index] = new_text
                save_log(acct, entries)
                print(f"✓ [{acct}][{index}] を更新しました")
                print(f"  旧: {old}...")
                print(f"  新: {new_text[:60].replace(chr(10), ' ')}...")
                return
            else:
                print(f"✗ インデックス {index} は範囲外です（0〜{len(posts)-1}）")
                return
    print(f"✗ {today} のエントリが見つかりません")


def cmd_delete(acct: str, index: int):
    """指定インデックスの投稿を削除"""
    entries = load_log(acct)
    today = date.today().strftime("%Y-%m-%d")
    for entry in reversed(entries):
        if entry.get("date") == today:
            posts = entry.get("posts", [])
            if 0 <= index < len(posts):
                removed = posts.pop(index)
                save_log(acct, entries)
                print(f"✓ [{acct}][{index}] を削除しました: {removed[:60].replace(chr(10), ' ')}...")
                return
            else:
                print(f"✗ インデックス {index} は範囲外です（0〜{len(posts)-1}）")
                return
    print(f"✗ {today} のエントリが見つかりません")


def cmd_status(acct: str | None = None):
    """ステータス表示"""
    today = date.today().strftime("%Y-%m-%d")
    accts = [acct] if acct else ["truth", "masa"]
    for a in accts:
        entries = load_log(a)
        today_posts = []
        for e in entries:
            if e.get("date") == today:
                today_posts.extend(e.get("posts", []))
        posted = [p for p in load_posted(a) if p.get("date") == today]
        total = len(today_posts)
        done = len(posted)
        print(f"[{a}] {today}: 生成={total}件, 投稿済={done}件, 残り={total - done}件")


def cmd_posted(acct: str, n: int = 10):
    """投稿済みの直近N件を表示"""
    records = load_posted(acct)
    for rec in records[-n:]:
        d = rec.get("date", "?")
        idx = rec.get("index", "?")
        pid = rec.get("post_id", "?")
        text = rec.get("text", "")[:60].replace("\n", " ")
        print(f"  [{d}][{idx}] {pid} | {text}")


def cmd_rate(acct: str, index: int, rating: str, comment: str = ""):
    """投稿済みの投稿を評価"""
    if rating not in ("good", "bad", "skip"):
        print(f"✗ rating は good / bad / skip のいずれか")
        return
    fb_file = BASE / "feedback.jsonl"
    entry = {
        "account": acct,
        "date": date.today().strftime("%Y-%m-%d"),
        "index": index,
        "rating": rating,
        "comment": comment,
        "reviewed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    with open(fb_file, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"✓ [{acct}][{index}] → {rating}" + (f" ({comment})" if comment else ""))


def cmd_weight(pattern: str, value: float):
    """パターン重みを変更"""
    fb = load_feedback()
    if pattern not in fb["pattern_weights"]:
        print(f"✗ '{pattern}' は存在しません。有効なパターン:")
        for p in fb["pattern_weights"]:
            print(f"  - {p}")
        return
    value = max(0.0, min(3.0, value))
    old = fb["pattern_weights"][pattern]
    fb["pattern_weights"][pattern] = value
    save_feedback(fb)
    print(f"✓ {pattern}: {old:.1f} → {value:.1f}")


def cmd_ng(action: str, word: str = ""):
    """NGワード管理"""
    fb = load_feedback()
    if action == "list":
        words = fb.get("ng_words", [])
        print(f"NGワード ({len(words)}件): {words or 'なし'}")
    elif action == "add" and word:
        if word not in fb["ng_words"]:
            fb["ng_words"].append(word)
            save_feedback(fb)
            print(f"✓ '{word}' をNGワードに追加")
        else:
            print(f"すでに登録済み: '{word}'")
    elif action == "remove" and word:
        if word in fb["ng_words"]:
            fb["ng_words"].remove(word)
            save_feedback(fb)
            print(f"✓ '{word}' を削除")
        else:
            print(f"✗ '{word}' は登録されていません")
    else:
        print("使い方: ng add|remove|list [word]")


def cmd_feedback_show():
    """現在のフィードバック設定を表示"""
    fb = load_feedback()
    print("=== パターン重み ===")
    for p, w in fb["pattern_weights"].items():
        bar = "█" * int(w * 5)
        mark = " (無効)" if w == 0 else ""
        print(f"  {p:<18} {w:.1f} {bar}{mark}")
    print(f"\nNGワード: {fb.get('ng_words', []) or 'なし'}")
    print(f"追加テンプレ truth: {len(fb.get('extra_templates', {}).get('truth', []))}件")
    print(f"追加テンプレ masa:  {len(fb.get('extra_templates', {}).get('masa', []))}件")
    notes = fb.get("notes", [])
    if notes:
        print(f"\n最新メモ: [{notes[-1]['ts']}] {notes[-1]['text']}")
    print(f"\n最終更新: {fb.get('updated_at', '未設定')}")


def cmd_note(text: str):
    """メモを追加"""
    fb = load_feedback()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    fb["notes"].append({"ts": ts, "text": text})
    save_feedback(fb)
    print(f"✓ メモ保存: {text}")


# ── メイン ───────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    cmd = args[0]

    if cmd == "queue" and len(args) >= 2:
        cmd_queue(args[1], "--today" in args)

    elif cmd == "edit" and len(args) >= 4:
        cmd_edit(args[1], int(args[2]), args[3])

    elif cmd == "delete" and len(args) >= 3:
        cmd_delete(args[1], int(args[2]))

    elif cmd == "status":
        cmd_status(args[1] if len(args) >= 2 else None)

    elif cmd == "posted" and len(args) >= 2:
        n = int(args[2]) if len(args) >= 3 else 10
        cmd_posted(args[1], n)

    elif cmd == "rate" and len(args) >= 4:
        comment = args[4] if len(args) >= 5 else ""
        cmd_rate(args[1], int(args[2]), args[3], comment)

    elif cmd == "weight" and len(args) >= 3:
        cmd_weight(args[1], float(args[2]))

    elif cmd == "ng" and len(args) >= 2:
        word = args[2] if len(args) >= 3 else ""
        cmd_ng(args[1], word)

    elif cmd == "feedback" and len(args) >= 2 and args[1] == "show":
        cmd_feedback_show()

    elif cmd == "note" and len(args) >= 2:
        cmd_note(args[1])

    else:
        print(f"不明なコマンド: {' '.join(args)}")
        print(__doc__)


if __name__ == "__main__":
    main()
