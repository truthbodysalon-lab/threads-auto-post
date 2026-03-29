#!/usr/bin/env python3
"""
フィードバック管理
- iTerm から入力されたフィードバックを feedback.json に保存
- generate_remix.py がパターン重みとNG表現に反映
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
FEEDBACK_FILE = BASE / "feedback.json"

DEFAULT = {
    "pattern_weights": {
        # truth
        "aoi_style":    1.0,
        "hori_style":   1.0,
        "hook_one_line":1.0,
        "quote_empathy":1.0,
        "insight":      1.0,
        "education":    1.0,
        "story":        1.0,
        "workmom":      1.0,
        "gyakusetsu":   1.0,
        "ranking":      1.0,
        "question":     1.0,
        # masa
        "soft_line":    1.0,
        "cta":          1.0,
    },
    "ng_words": [],           # この表現を含む投稿は生成しない
    "extra_templates": {      # 追加テンプレ
        "truth": [],
        "masa":  [],
    },
    "notes": [],              # メモ（自動投稿には影響しない）
    "updated_at": "",
}


def load() -> dict:
    if FEEDBACK_FILE.exists():
        try:
            data = json.loads(FEEDBACK_FILE.read_text())
            # 欠損キーをデフォルトで補完
            for k, v in DEFAULT.items():
                if k not in data:
                    data[k] = v
            return data
        except Exception:
            pass
    return DEFAULT.copy()


def save(data: dict):
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    FEEDBACK_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def interactive():
    """iTerm からフィードバックを入力するインタラクティブUI"""
    fb = load()

    print("\n" + "="*52)
    print("  フィードバックメニュー")
    print("="*52)
    print("  1. パターンの出現頻度を調整（増やす/減らす）")
    print("  2. NGワードを追加（特定の表現を出さない）")
    print("  3. テンプレを追加（新しい投稿パターン）")
    print("  4. メモを残す")
    print("  5. 現在の設定を確認")
    print("  q. 終了")
    print()

    while True:
        raw = input("選択 > ").strip()
        if raw in ("q", "quit", ""):
            break

        elif raw == "1":
            print("\nパターン一覧（現在の重み）:")
            for p, w in fb["pattern_weights"].items():
                bar = "█" * int(w * 5)
                print(f"  {p:<18} {w:.1f}  {bar}")
            print("\n変更するパターン名を入力（例: aoi_style）")
            pname = input("パターン > ").strip()
            if pname not in fb["pattern_weights"]:
                print(f"  '{pname}' は存在しません")
                continue
            print("新しい重み（0.0〜3.0、0で無効化、1.0がデフォルト）")
            try:
                w = float(input("重み > ").strip())
                fb["pattern_weights"][pname] = max(0.0, min(3.0, w))
                save(fb)
                print(f"  ✓ {pname} → {fb['pattern_weights'][pname]:.1f}")
            except ValueError:
                print("  数値を入力してください")

        elif raw == "2":
            print("\n現在のNGワード:", fb["ng_words"] or "なし")
            print("追加するNGワード（空欄でスキップ）")
            word = input("NGワード > ").strip()
            if word:
                if word not in fb["ng_words"]:
                    fb["ng_words"].append(word)
                    save(fb)
                    print(f"  ✓ '{word}' をNGワードに追加")
                else:
                    print(f"  すでに登録済みです")
            # 削除オプション
            print("削除するNGワード（空欄でスキップ）")
            rm = input("削除 > ").strip()
            if rm and rm in fb["ng_words"]:
                fb["ng_words"].remove(rm)
                save(fb)
                print(f"  ✓ '{rm}' を削除")

        elif raw == "3":
            print("\nどちらのアカウント用？ (truth / masa)")
            acct = input("アカウント > ").strip().lower()
            if acct not in ("truth", "masa"):
                print("  truth か masa を入力してください")
                continue
            print("追加するテンプレを入力（{symptom} などの変数も使用可）")
            print("複数行の場合は \\n で改行。入力後 Enter:")
            tmpl = input("テンプレ > ").strip().replace("\\n", "\n")
            if tmpl:
                fb["extra_templates"][acct].append(tmpl)
                save(fb)
                print(f"  ✓ {acct} にテンプレを追加（{len(fb['extra_templates'][acct])}件目）")

        elif raw == "4":
            print("メモを入力:")
            note = input("メモ > ").strip()
            if note:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                fb["notes"].append({"ts": ts, "text": note})
                save(fb)
                print(f"  ✓ メモ保存")

        elif raw == "5":
            print("\n=== 現在の設定 ===")
            print("パターン重み:")
            for p, w in fb["pattern_weights"].items():
                mark = "（無効）" if w == 0 else ""
                print(f"  {p:<18} {w:.1f} {mark}")
            print(f"\nNGワード: {fb['ng_words'] or 'なし'}")
            print(f"追加テンプレ truth: {len(fb['extra_templates']['truth'])}件")
            print(f"追加テンプレ masa:  {len(fb['extra_templates']['masa'])}件")
            if fb["notes"]:
                print(f"\n最新メモ: [{fb['notes'][-1]['ts']}] {fb['notes'][-1]['text']}")

        else:
            print("  1〜5 か q を入力してください")

    print("\n設定を保存しました。次回の生成から反映されます。\n")


if __name__ == "__main__":
    interactive()
