#!/usr/bin/env python3
"""ヒーロー検査（2026-10-20 オーナー指示「生成したヒーローは全て投稿する」）。

hero_<acct>.json の各原稿が、投稿パイプラインの除外フィルタで「捨てられないか」を保存前に洗い出す。
フィルタのロジックは複製せず、generate_remix / auto_post / duplicate_guard の既存関数をそのまま呼ぶ:
  1. generate_remix._hero_reject_reason … _load_hero_posts が使う除外（15字未満・NG・1文目NG・予約系違反・
     自分の投稿のコピー・masaの売り込み/250字超/抽象度NG）
  2. auto_post.inspect_before_post … 投稿直前の検品ゲート（検品NG・masa抽象度）
  3. masaのLINE言及率キャップ（auto_post._masa_line_subject。ヒーローでLINEに触れるのは1本まで）
  4. 重複 … 投稿済みの最初の段落と同一（全期間dedup）／直近7日と1文目が同一（duplicate_guard）
     ※どちらも get_next_post が黙ってスキップする＝ヒーローが永久に出ない原因
投稿済みのヒーローは対象外。通信なし。

使い方（ヒーロー保存の直後・hero_carry.py の直前）:
    cd /Users/mt112/threads-auto-post && python3 hero_check.py [truth nagaoka masa] [--json]
終了コード: 0=全件通過 / 1=落ちるヒーローあり（書き直し対象） / 2=検査自体ができなかった
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

ACCTS = ("truth", "nagaoka", "masa")


def check_account(acct: str) -> dict:
    import generate_remix as g
    import auto_post as ap
    from duplicate_guard import normalize_text, is_duplicate

    f = BASE / f"hero_{acct}.json"
    if not f.exists():
        return {"acct": acct, "error": "hero ファイルなし", "items": []}
    d = json.loads(f.read_text(encoding="utf-8"))
    items = []   # (由来, 生成日, 本文)
    for c in d.get("carry", []) or []:
        t = c.get("text") if isinstance(c, dict) else c
        if isinstance(t, str):
            items.append(("carry", str(c.get("date") if isinstance(c, dict) else d.get("date")), t))
    for p in d.get("posts", []) or []:
        if isinstance(p, str):
            items.append(("posts", str(d.get("date")), p))

    posted = g._hero_posted(acct)
    posted_texts = ap.get_posted_texts(acct)
    seen: dict = {}
    out = []
    line_subject_n = 0    # masa: LINEに触れるコンテンツ投稿は1日2本までしか出ない（他の投稿と共有）
    for n, (src, od, t) in enumerate(items, 1):
        k = g._hero_key(t)
        rec = {"n": n, "src": src, "origin": od, "head": t.split("\n")[0][:30], "len": len(t), "reasons": [], "posted": False}
        if posted.contains(t, od):
            rec["posted"] = True            # 投稿済みは検査しない（重複ガードに自分自身で当たるため）
            out.append(rec)
            continue
        if k in seen:
            rec["reasons"].append(f"同じ原稿が重複(#{seen[k]})")
            out.append(rec)
            continue
        seen[k] = n
        r = g._hero_reject_reason(t, acct)
        if r:
            rec["reasons"].append("除外フィルタ: " + r)
        try:
            ok, rs = ap.inspect_before_post(t, acct)
            if not ok:
                rec["reasons"].append("投稿直前の検品NG: " + "; ".join(rs)[:80])
        except Exception:  # 検品自体の失敗は止めない（本番もフェイルオープン）
            pass
        if acct == "masa" and ap._masa_line_subject(t):
            line_subject_n += 1
            if line_subject_n > 1:   # 枠2本のうちヒーローは1本まで。2本目以降は他の投稿で枠が埋まると出ない
                rec["reasons"].append("LINE言及が2本目以降(コンテンツ枠は1日2本・他投稿と共有。LINE誘導は導線投稿に任せる)")
        norm = normalize_text(t)
        if norm in posted_texts:
            rec["reasons"].append("重複: 投稿済みの最初の段落と同一(全期間dedupで出ない)")
        else:
            try:
                if is_duplicate(norm, acct):
                    rec["reasons"].append("重複: 直近7日に同じ1文目(重複ガードで出ない)")
            except Exception:
                pass
        out.append(rec)
    return {"acct": acct, "date": d.get("date"), "items": out}


def main(argv: list) -> int:
    as_json = "--json" in argv
    accts = [a for a in argv if a in ACCTS] or list(ACCTS)
    results, bad_total, err_total = [], 0, 0
    for a in accts:
        try:
            res = check_account(a)
        except Exception as e:
            res = {"acct": a, "error": f"{type(e).__name__}: {e}", "items": []}
        results.append(res)
    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    for res in results:
        a = res["acct"]
        if res.get("error"):
            err_total += 1
            print(f"[{a}] 検査できず: {res['error']}")
            continue
        items = res["items"]
        bad = [i for i in items if i["reasons"]]
        done = sum(1 for i in items if i["posted"])
        bad_total += len(bad)
        if not as_json:
            print(f"[{a}] ファイル日付 {res['date']} / {len(items)}本（投稿済み{done}・落ちる{len(bad)}）")
            for i in bad:
                print(f"  NG #{i['n']}({i['src']} {i['origin']}) {i['len']}字 「{i['head']}」 → " + " / ".join(i["reasons"]))
    if not as_json:
        if err_total:
            print(f"RESULT: 検査できないアカウントあり({err_total}件)＝通過扱いにしない。原因を直すか、ヒーローを目視で確認する")
        else:
            print("RESULT:", "全件通過" if bad_total == 0 else f"落ちるヒーロー {bad_total} 本（条件を満たすよう書き直してから保存。書き直せないものは1行目を残して理由を記録）")
    return 2 if err_total else (1 if bad_total else 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
