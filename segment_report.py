#!/usr/bin/env python3
"""
segment_report.py — masaの売上ステージ別セグメント×フック分析（2026-09-23 小川さんセッション反映）

なぜ: 「層を名指しした投稿を並べ、どの層が反応するか」をテストするため、
      segment_registry.json（投稿の1行目→層/フックの登録）× log_masa_posted.jsonl
      （投稿本文の台帳）× past_posts_masa.json（views/いいね/返信の実測）を
      1行目正規化40字＋post_idで結合し、segment×hook別の指標とindexを出す。

使い方:
  python3 segment_report.py            # レポート生成のみ
  python3 segment_report.py --apply    # 加えて segment_config.json の share をwinner/loserで自動調整
  python3 segment_report.py --json     # JSONを標準出力にも全量出力

入力（全てローカルJSON/JSONL。HTTP通信なし）:
  - log_masa_posted.jsonl : {"date","index","post_id","text"} 1行1件
  - segment_registry.json : {"<1行目正規化40字>": {"segment","hook","variant","created"}}
                             （担当A側が生成。無い/空でも空辞書扱いで動く）
  - past_posts_masa.json  : [{"id","text","views","like_count","replies_count",...}, ...]
  - segment_config.json   : {"masa": {"per_day","share","hook_rotation","anchors","min_n"}}
                             （--apply でのみ使用。無ければ調整をスキップ）

出力:
  - segment_report.json（git追跡）
  - Markdown: "/Users/mt112/Desktop/my files/myfiles/SNS・Threads/分析レポート/セグメント分析_masa.md"（上書き）
  - 標準出力に1行サマリ
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
LOG_FILE = BASE / "log_masa_posted.jsonl"
REGISTRY_FILE = BASE / "segment_registry.json"
PAST_POSTS_FILE = BASE / "past_posts_masa.json"
CONFIG_FILE = BASE / "segment_config.json"
HYPOTHESES_FILE = BASE / "segment_hypotheses.json"
REPORT_JSON = BASE / "segment_report.json"
REPORT_MD = Path(
    "/Users/mt112/Desktop/my files/myfiles/SNS・Threads/分析レポート/セグメント分析_masa.md"
)

SEGMENTS = ["S1_kaigyo_mae", "S2_shonen", "S3_atamauchi", "S4_staff", "S5_kougaku"]
# registryのsegment値は短縮形（kaigyo_mae等）で入る想定なので正規化する
_SEG_ALIASES = {
    "kaigyo_mae": "S1_kaigyo_mae", "S1": "S1_kaigyo_mae", "s1": "S1_kaigyo_mae",
    "shonen": "S2_shonen", "S2": "S2_shonen", "s2": "S2_shonen",
    "atamauchi": "S3_atamauchi", "S3": "S3_atamauchi", "s3": "S3_atamauchi",
    "staff": "S4_staff", "S4": "S4_staff", "s4": "S4_staff",
    "kougaku": "S5_kougaku", "S5": "S5_kougaku", "s5": "S5_kougaku",
}
HOOKS = ["low", "high"]
WINDOWS = (14, 30)
DEFAULT_MIN_N = 5
LOSER_MIN_N = 8
WINNER_INDEX = 1.3
LOSER_INDEX = 0.7


def _norm_first_line(text: str, n: int = 40) -> str:
    """投稿本文1行目を正規化して40字キーにする（registry登録と同じ規則の想定）。"""
    if not text:
        return ""
    first = text.strip().split("\n", 1)[0]
    first = "".join(first.split())  # 空白類を除去
    return first[:n]


def _load_json(path: Path, default):
    try:
        if not path.exists():
            return default
        txt = path.read_text(encoding="utf-8").strip()
        if not txt:
            return default
        return json.loads(txt)
    except Exception:
        return default


def _load_log_entries():
    """log_masa_posted.jsonl を [{"date","index","post_id","text","key"}] で返す。"""
    out = []
    if not LOG_FILE.exists():
        return out
    for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        e["key"] = _norm_first_line(e.get("text", ""))
        out.append(e)
    return out


def _load_past_posts_by_id():
    posts = _load_json(PAST_POSTS_FILE, [])
    out = {}
    if isinstance(posts, list):
        for p in posts:
            pid = p.get("id")
            if pid:
                out[str(pid)] = p
    return out


def _load_registry():
    reg = _load_json(REGISTRY_FILE, {})
    return reg if isinstance(reg, dict) else {}


def _load_hypotheses():
    """segment_hypotheses.json を読み込む。無い/不正なら None を返す
    （＝仮説機能は「未着手」として静かにスキップ。空リストは{"hypotheses":[]}として
    正常に返す＝ファイルはあるが仮説0件、の意味）。"""
    data = _load_json(HYPOTHESES_FILE, None)
    if not isinstance(data, dict) or not isinstance(data.get("hypotheses"), list):
        return None
    return data


def _norm_segment(raw):
    if not raw:
        return None
    return _SEG_ALIASES.get(raw, raw if raw in SEGMENTS else None)


def _norm_hook(raw):
    if raw in HOOKS:
        return raw
    return None


def _within_days(d: str, days: int, today: date) -> bool:
    try:
        dt = datetime.strptime(d, "%Y-%m-%d").date()
    except Exception:
        return False
    return dt >= today - timedelta(days=days)


def _percentile(values, pct):
    if not values:
        return 0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (pct / 100)
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _stats(views_list):
    n = len(views_list)
    if n == 0:
        return {"n": 0, "median_views": 0, "mean_views": 0, "p90_views": 0}
    return {
        "n": n,
        "median_views": round(statistics.median(views_list), 1),
        "mean_views": round(statistics.mean(views_list), 1),
        "p90_views": round(_percentile(views_list, 90), 1),
    }


def _rate(numer_sum, denom_sum):
    return round(numer_sum / denom_sum, 4) if denom_sum > 0 else 0.0


def _verdict(n, index, min_n):
    if n < min_n:
        return "判定保留"
    if index >= WINNER_INDEX and n >= min_n:
        return "winner"
    if index <= LOSER_INDEX and n >= LOSER_MIN_N:
        return "loser"
    return "testing"


def build_report():
    today = date.today()
    log_entries = _load_log_entries()
    registry = _load_registry()
    past_by_id = _load_past_posts_by_id()

    # ログ各行に registry(key一致) と past_posts(id一致) を突き合わせる
    joined = []
    for e in log_entries:
        pid = str(e.get("post_id", ""))
        past = past_by_id.get(pid)
        reg = registry.get(e["key"])
        joined.append({
            "date": e.get("date", ""),
            "post_id": pid,
            "text": e.get("text", ""),
            "segment": _norm_segment(reg.get("segment")) if reg else None,
            "hook": _norm_hook(reg.get("hook")) if reg else None,
            "hypothesis_id": (reg.get("hypothesis_id") if reg else None) or None,
            "views": (past or {}).get("views", 0) or 0,
            "like_count": (past or {}).get("like_count", 0) or 0,
            "replies_count": (past or {}).get("replies_count", 0) or 0,
            "has_metrics": past is not None,
        })

    cfg = _load_json(CONFIG_FILE, {})
    min_n = cfg.get("masa", {}).get("min_n", DEFAULT_MIN_N) if isinstance(cfg, dict) else DEFAULT_MIN_N

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "params": {"min_n": min_n, "loser_min_n": LOSER_MIN_N,
                    "winner_index": WINNER_INDEX, "loser_index": LOSER_INDEX,
                    "windows_days": list(WINDOWS)},
        "registry_loaded": len(registry),
        "log_entries": len(log_entries),
        "windows": {},
        "segment_summary": {},  # hook問わずsegment単位（--apply判定用）
    }

    for win in WINDOWS:
        wkey = f"{win}d"
        rows = [j for j in joined if _within_days(j["date"], win, today) and j["has_metrics"]]
        baseline_views = [j["views"] for j in rows]
        baseline = _stats(baseline_views)
        baseline_median = baseline["median_views"] or 0

        seg_hook = {}
        for seg in SEGMENTS:
            for hook in HOOKS:
                cell = [j for j in rows if j["segment"] == seg and j["hook"] == hook]
                views_l = [j["views"] for j in cell]
                st = _stats(views_l)
                index = round(st["median_views"] / baseline_median, 3) if baseline_median else 0.0
                verdict = _verdict(st["n"], index, min_n)
                seg_hook[f"{seg}__{hook}"] = {
                    **st,
                    "like_rate": _rate(sum(j["like_count"] for j in cell), sum(views_l)),
                    "reply_rate": _rate(sum(j["replies_count"] for j in cell), sum(views_l)),
                    "index": index,
                    "verdict": verdict,
                }

        seg_only = {}
        for seg in SEGMENTS:
            cell = [j for j in rows if j["segment"] == seg]
            views_l = [j["views"] for j in cell]
            st = _stats(views_l)
            index = round(st["median_views"] / baseline_median, 3) if baseline_median else 0.0
            verdict = _verdict(st["n"], index, min_n)
            seg_only[seg] = {
                **st,
                "like_rate": _rate(sum(j["like_count"] for j in cell), sum(views_l)),
                "reply_rate": _rate(sum(j["replies_count"] for j in cell), sum(views_l)),
                "index": index,
                "verdict": verdict,
            }

        # 仮説別（Obsidian由来の店舗経営の悩み・質問から作った仮説の勝ち負け判定。
        # 2026-09-23追加。segment_hypotheses.json が無い/空でもクラッシュしない）。
        hyp_data = _load_hypotheses()
        hyp_ids_from_rows = {j["hypothesis_id"] for j in rows if j["hypothesis_id"]}
        hyp_ids_from_file = {
            h.get("id") for h in (hyp_data or {}).get("hypotheses", [])
            if isinstance(h, dict) and h.get("id")
        }
        hyp_summary = {}
        for hid in sorted(hyp_ids_from_rows | hyp_ids_from_file):
            cell = [j for j in rows if j["hypothesis_id"] == hid]
            views_l = [j["views"] for j in cell]
            st = _stats(views_l)
            index = round(st["median_views"] / baseline_median, 3) if baseline_median else 0.0
            verdict = _verdict(st["n"], index, min_n)
            seg_of_hyp = next((j["segment"] for j in cell if j["segment"]), None)
            hyp_summary[hid] = {
                **st,
                "segment": seg_of_hyp,
                "like_rate": _rate(sum(j["like_count"] for j in cell), sum(views_l)),
                "reply_rate": _rate(sum(j["replies_count"] for j in cell), sum(views_l)),
                "index": index,
                "verdict": verdict,
            }

        unregistered = sum(1 for j in rows if not j["segment"])
        report["windows"][wkey] = {
            "baseline": baseline,
            "unregistered_n": unregistered,
            "segment_hook": seg_hook,
            "segment": seg_only,
            "hypothesis": hyp_summary,
        }

    # --apply の判定基盤は30日窓（サンプルが安定するため）
    report["segment_summary"] = report["windows"].get("30d", {}).get("segment", {})
    report["hypothesis_summary"] = report["windows"].get("30d", {}).get("hypothesis", {})
    return report


def write_markdown(report):
    today = date.today().strftime("%Y-%m-%d")
    lines = [f"# セグメント分析_masa {today}", "",
             f"registry登録{report['registry_loaded']}件 / 台帳{report['log_entries']}件",
             f"判定基準: index≥{report['params']['winner_index']}かつn≥{report['params']['min_n']}→winner / "
             f"index≤{report['params']['loser_index']}かつn≥{report['params']['loser_min_n']}→loser / "
             f"n<{report['params']['min_n']}→判定保留", ""]
    for wkey in ("14d", "30d"):
        w = report["windows"].get(wkey)
        if not w:
            continue
        lines.append(f"## {wkey}窓")
        b = w["baseline"]
        lines.append(f"全体ベースライン: n={b['n']} 中央値{b['median_views']} 平均{b['mean_views']} "
                     f"P90{b['p90_views']} / 未登録{w['unregistered_n']}件")
        lines.append("")
        lines.append("### segment×hook")
        lines.append("| segment | hook | n | 中央値 | index | いいね率 | 返信率 | 判定 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for seg in SEGMENTS:
            for hook in HOOKS:
                c = w["segment_hook"][f"{seg}__{hook}"]
                lines.append(f"| {seg} | {hook} | {c['n']} | {c['median_views']} | {c['index']} | "
                             f"{c['like_rate']} | {c['reply_rate']} | {c['verdict']} |")
        lines.append("")
        lines.append("### segment単独（hook問わず）")
        lines.append("| segment | n | 中央値 | index | 判定 |")
        lines.append("|---|---|---|---|---|")
        for seg in SEGMENTS:
            c = w["segment"][seg]
            lines.append(f"| {seg} | {c['n']} | {c['median_views']} | {c['index']} | {c['verdict']} |")
        lines.append("")
        lines.append("### 仮説別（Obsidian由来の店舗経営の悩み・質問）")
        hyp = w.get("hypothesis", {})
        if not hyp:
            lines.append("(仮説データなし。segment_hypotheses.json が空 or 未生成)")
        else:
            lines.append("| hypothesis_id | segment | n | 中央値 | index | いいね率 | 返信率 | 判定 |")
            lines.append("|---|---|---|---|---|---|---|---|")
            for hid in sorted(hyp.keys()):
                c = hyp[hid]
                lines.append(f"| {hid} | {c.get('segment') or '-'} | {c['n']} | {c['median_views']} | "
                             f"{c['index']} | {c['like_rate']} | {c['reply_rate']} | {c['verdict']} |")
        lines.append("")
    try:
        REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
        REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    except Exception as e:
        print(f"[warn] Markdown書き込み失敗: {e}", file=sys.stderr)


def apply_share(report):
    """segment_summary(30日窓)のwinner/loserでsegment_config.jsonのshareを調整する。"""
    cfg = _load_json(CONFIG_FILE, None)
    if not isinstance(cfg, dict) or "masa" not in cfg:
        print("[apply] segment_config.json が無い/不正のため share 調整をスキップ")
        return False
    masa_cfg = cfg["masa"]
    share = dict(masa_cfg.get("share", {}))
    summary = report.get("segment_summary", {})

    winners = [s for s in SEGMENTS if summary.get(s, {}).get("verdict") == "winner"]
    losers = [s for s in SEGMENTS if summary.get(s, {}).get("verdict") == "loser"]

    if not winners:
        print(f"[apply] winnerなし（loser{len(losers)}件）のため share は変更しない")
        return False

    freed = sum(share.get(s, 0) for s in losers)
    for s in losers:
        share[s] = 0

    base_add, rem = divmod(freed, len(winners))
    for i, s in enumerate(winners):
        share[s] = share.get(s, 0) + base_add + (1 if i < rem else 0)

    masa_cfg["share"] = share
    cfg["masa"] = masa_cfg
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[apply] share更新: winner={winners} loser={losers} → {share}")
    return True


def apply_hypotheses(report):
    """segment_hypotheses.json の各仮説の status と last_result を30日窓の実測で更新する。
    状態遷移: untested → testing(n≥1) → winner(index≥1.3,n≥min_n) / loser(index≤0.7,n≥8)。
    status=retired の仮説は手動退役なので触らない。ファイル不在/不正なら何もせずFalseを返す
    （担当Aの仮説投入が未着手でも落ちない）。"""
    data = _load_hypotheses()
    if data is None:
        print("[apply] segment_hypotheses.json が無い/不正のため仮説status更新をスキップ")
        return False

    hyp_stats = report.get("windows", {}).get("30d", {}).get("hypothesis", {})
    min_n = report.get("params", {}).get("min_n", DEFAULT_MIN_N)
    today = date.today().isoformat()
    changed = False
    updated_ids = []

    for h in data.get("hypotheses", []):
        if not isinstance(h, dict):
            continue
        hid = h.get("id")
        if not hid:
            continue
        status = h.get("status", "untested")
        if status == "retired":
            continue

        st = hyp_stats.get(hid)
        n = st.get("n", 0) if st else 0
        index = st.get("index", 0.0) if st else 0.0

        if n == 0:
            continue  # 実測がまだ無い→untestedのまま（last_resultも更新しない）

        if index >= WINNER_INDEX and n >= min_n:
            new_status = "winner"
        elif index <= LOSER_INDEX and n >= LOSER_MIN_N:
            new_status = "loser"
        elif n >= 1:
            new_status = "testing"
        else:
            new_status = status

        if new_status != status:
            h["status"] = new_status
            changed = True
            updated_ids.append(f"{hid}:{status}->{new_status}")

        last_result = {"n": n, "index": index, "updated": today}
        if h.get("last_result") != last_result:
            h["last_result"] = last_result
            changed = True

    if changed:
        HYPOTHESES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[apply] 仮説status更新: {updated_ids or '(last_resultのみ更新)'}")
    else:
        print("[apply] 仮説statusの変更なし")
    return changed


def main():
    report = build_report()
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report)

    if "--apply" in sys.argv:
        apply_share(report)
        apply_hypotheses(report)

    w30 = report["windows"].get("30d", {})
    b = w30.get("baseline", {})
    winners = [s for s, c in report.get("segment_summary", {}).items() if c.get("verdict") == "winner"]
    losers = [s for s, c in report.get("segment_summary", {}).items() if c.get("verdict") == "loser"]
    print(f"segment_report: 30日ベースn={b.get('n', 0)} 中央値{b.get('median_views', 0)} "
          f"winner={winners or 'なし'} loser={losers or 'なし'}")

    hyp_summary = report.get("hypothesis_summary", {})
    if hyp_summary:
        hwin = [h for h, c in hyp_summary.items() if c.get("verdict") == "winner"]
        hlose = [h for h, c in hyp_summary.items() if c.get("verdict") == "loser"]
        print(f"segment_report(仮説): {len(hyp_summary)}件 winner={hwin or 'なし'} loser={hlose or 'なし'}")

    if "--json" in sys.argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
