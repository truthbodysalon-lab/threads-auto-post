#!/usr/bin/env python3
"""UP LINK（月73万閲覧・サロン経営者向け）の実測トップ投稿を
masaの毎朝のヒーロー生成が参照できる形に書き出す橋渡し（2026-08-14）。

出力: uplink_top.json（実測views上位20件。git追跡しない=ローカル毎朝再生成）
使い方: python3 uplink_reference.py
注意: masaへは【翻案】して使う（同一文面の再投稿は別アカウント間でも
      重複ペナルティ・アカウント関連付けのリスク。語彙/数字/構成を変える）。
"""
import json
from pathlib import Path

SRC = Path.home() / "threads-uplink" / "threads_stats.json"
OUT = Path(__file__).parent / "uplink_top.json"
HIST = Path(__file__).parent / "uplink_history300.json"   # 2026-10-17 全期間の閲覧300以上(168文面・git追跡しない)

def main():
    if not SRC.exists():
        print(f"SKIP: {SRC} なし（Mac以外の環境）")
        return
    items = json.loads(SRC.read_text(encoding="utf-8"))
    ranked = sorted(items, key=lambda x: (x.get("insights") or {}).get("views", 0), reverse=True)
    top = [{
        "views": (p.get("insights") or {}).get("views", 0),
        "kind": p.get("kind", ""),
        "angle": p.get("angle", ""),
        "text": p.get("text_preview", ""),
    } for p in ranked[:20] if (p.get("insights") or {}).get("views", 0) > 0]
    # 全期間の閲覧300以上上位30を統合（直近だけだと過去の当たり文面が参照から落ちるため）
    hist = []
    if HIST.exists():
        try:
            seen = {t["text"][:40] for t in top}
            for h in json.loads(HIST.read_text(encoding="utf-8")).get("items", [])[:30]:
                if h["text"][:40] not in seen:
                    seen.add(h["text"][:40])
                    hist.append({"views": h["views"], "kind": "history300", "angle": "", "text": h["text"]})
        except Exception as e:
            print(f"history300読込失敗: {e}")
    OUT.write_text(json.dumps({"source": "threads-uplink", "note": "翻案必須・同一文面の再投稿禁止（history300=全期間の閲覧300以上上位）",
                               "top": top + hist}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"uplink_top.json: {len(top)}件（TOP1 {top[0]['views']}v）")

if __name__ == "__main__":
    main()
