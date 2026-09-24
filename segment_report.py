#!/usr/bin/env python3
"""
segment_report.py — セグメント×フック分析（masa=売上ステージS1-S5・truth/nagaoka=症状生活層T1-T6）
（2026-09-23 小川さんセッション反映。2026-09-23 truth/nagaokaへ拡張＝ユーザー指示）

なぜ: 「層を名指しした投稿を並べ、どの層が反応するか」をテストするため、
      segment_registry.json（投稿の1行目→層/フック/acctの登録）× log_<acct>_posted.jsonl
      （投稿本文の台帳）× past_posts*.json（views/いいね/返信の実測）を
      1行目正規化40字＋post_idで結合し、segment×hook別の指標とindexを出す。

使い方:
  python3 segment_report.py                     # masaのレポート生成のみ（既定acct）
  python3 segment_report.py --acct truth         # truthのレポート生成のみ
  python3 segment_report.py --acct all           # masa/truth/nagaokaを順番に実行
  python3 segment_report.py --acct nagaoka --apply  # 加えて segment_config.json の
                                                     # share と segment_hypotheses.json の
                                                     # status をwinner/loserで自動調整
  python3 segment_report.py --json               # JSONを標準出力にも全量出力

入力（全てローカルJSON/JSONL。HTTP通信なし）:
  - log_<acct>_posted.jsonl : {"date","index","post_id","text"} 1行1件
  - segment_registry.json   : {"<1行目正規化40字>": {"segment","hook","variant","created","acct"}}
                               （acct省略=masaとして扱う後方互換。担当A/B側が生成。
                               無い/空でも空辞書扱いで動く）
  - past_posts_masa.json / past_posts.json(=truth) / past_posts_nagaoka.json
                             : [{"id","text","views","like_count","replies_count",...}, ...]
  - segment_config.json     : {"<acct>": {"per_day","share","hook_rotation","anchors","min_n"}}
                               （--apply でのみ使用。無ければ調整をスキップ）

出力（acctごと）:
  - JSON: masaのみ後方互換で segment_report.json も出力し、加えて全acct共通で
          segment_report_<acct>.json（git追跡）
  - Markdown: "/Users/mt112/Desktop/my files/myfiles/SNS・Threads/分析レポート/セグメント分析_<acct>.md"（上書き）
  - 標準出力に1行サマリ（acctごと）
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
CONFIG_FILE = BASE / "segment_config.json"
REGISTRY_FILE = BASE / "segment_registry.json"
HYPOTHESES_FILE = BASE / "segment_hypotheses.json"
BASELINE_FILE = BASE / "segment_baseline.json"
REPORT_MD_DIR = Path("/Users/mt112/Desktop/my files/myfiles/SNS・Threads/分析レポート")

ACCTS = ["masa", "truth", "nagaoka"]

LOG_FILES = {
    "masa": BASE / "log_masa_posted.jsonl",
    "truth": BASE / "log_truth_posted.jsonl",
    "nagaoka": BASE / "log_nagaoka_posted.jsonl",
}
# 閲覧等の実測ファイル: truthは歴史的経緯でpast_posts.json（アカウント名なし）が正本。
PAST_POSTS_FILES = {
    "masa": BASE / "past_posts_masa.json",
    "truth": BASE / "past_posts.json",
    "nagaoka": BASE / "past_posts_nagaoka.json",
}

# セグメントの短縮コード(registry/config内の値)とフルネーム(レポート表示用)の対応。
# masa=売上ステージS1-S5（既存）。truth/nagaoka=症状・生活層T1-T6（2026-09-23拡張）。
SEG_CODES = {
    "masa": ["S1", "S2", "S3", "S4", "S5"],
    "truth": ["T1", "T2", "T3", "T4", "T5", "T6"],
    "nagaoka": ["T1", "T2", "T3", "T4", "T5", "T6"],
}
SEG_NAMES = {
    "masa": {"S1": "kaigyo_mae", "S2": "shonen", "S3": "atamauchi", "S4": "staff", "S5": "kougaku"},
    "truth": {"T1": "desk", "T2": "zutsu", "T3": "sango", "T4": "tachi", "T5": "suimin", "T6": "norikae"},
    "nagaoka": {"T1": "desk", "T2": "zutsu", "T3": "sango", "T4": "tachi", "T5": "suimin", "T6": "norikae"},
}


def _segments(acct: str) -> list[str]:
    return [f"{c}_{SEG_NAMES[acct][c]}" for c in SEG_CODES.get(acct, [])]


def _seg_aliases(acct: str) -> dict:
    alias = {}
    for c in SEG_CODES.get(acct, []):
        full = f"{c}_{SEG_NAMES[acct][c]}"
        alias[full] = full
        alias[c] = full
        alias[c.lower()] = full
        alias[SEG_NAMES[acct][c]] = full
    return alias


def _short_code(acct: str, full_seg: str) -> str | None:
    """フルネーム("T1_desk"等)から config/share が使う短縮コード("T1")へ戻す。"""
    for c in SEG_CODES.get(acct, []):
        if f"{c}_{SEG_NAMES[acct][c]}" == full_seg:
            return c
    return None


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


def _load_log_entries(acct: str):
    """log_<acct>_posted.jsonl を [{"date","index","post_id","text","key"}] で返す。"""
    out = []
    path = LOG_FILES.get(acct)
    if not path or not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
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


def _load_past_posts_by_id(acct: str):
    posts = _load_json(PAST_POSTS_FILES.get(acct, Path("__missing__")), [])
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


def _entry_acct(entry: dict) -> str:
    return (entry.get("acct") or "masa") if isinstance(entry, dict) else "masa"


def _load_baseline(acct: str):
    """segment_baseline.json（過去投稿の層別実測。担当A作成・git追跡）を読み込み、
    該当acctのsegments辞書を返す。ファイル不在/不正/acct無しならNone
    （＝過去ベースライン節はレポートから省略。ファイルはあるが仮説0件と同様に落ちない設計）。"""
    data = _load_json(BASELINE_FILE, None)
    if not isinstance(data, dict):
        return None
    acct_data = data.get("accounts", {}).get(acct)
    if not isinstance(acct_data, dict):
        return None
    acct_data = dict(acct_data)
    acct_data.setdefault("generated", data.get("generated"))
    return acct_data


def _load_hypotheses():
    """segment_hypotheses.json を読み込む。無い/不正なら None を返す
    （＝仮説機能は「未着手」として静かにスキップ。空リストは{"hypotheses":[]}として
    正常に返す＝ファイルはあるが仮説0件、の意味）。"""
    data = _load_json(HYPOTHESES_FILE, None)
    if not isinstance(data, dict) or not isinstance(data.get("hypotheses"), list):
        return None
    return data


def _norm_segment(acct: str, raw):
    if not raw:
        return None
    alias = _seg_aliases(acct)
    segs = _segments(acct)
    return alias.get(raw, raw if raw in segs else None)


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


def build_report(acct: str):
    today = date.today()
    log_entries = _load_log_entries(acct)
    registry = _load_registry()
    past_by_id = _load_past_posts_by_id(acct)
    SEGMENTS = _segments(acct)

    # ログ各行に registry(key一致・acct一致) と past_posts(id一致) を突き合わせる
    joined = []
    for e in log_entries:
        pid = str(e.get("post_id", ""))
        past = past_by_id.get(pid)
        reg = registry.get(e["key"])
        # registryのキーは1行目正規化40字のみでacctを区別しないため、
        # 引き当てたエントリのacctがこのレポート対象acctと一致するかを必ず確認する
        # （偶然の文言一致で他acctの登録を誤って集計しないため）。
        if reg is not None and _entry_acct(reg) != acct:
            reg = None
        raw_seg = reg.get("segment") if reg else None
        # HAKASE（インスタ運用の原則投稿・masa専用・2026-09-23）はS1-S5と別枠の集計対象なので
        # SEGMENTSの正規化を通さずそのまま保持する（_norm_segmentはSEGMENTS以外はNoneを返し
        # 「未登録」扱いになってしまうため）。
        seg_val = "HAKASE" if raw_seg == "HAKASE" else (_norm_segment(acct, raw_seg) if reg else None)
        joined.append({
            "date": e.get("date", ""),
            "post_id": pid,
            "text": e.get("text", ""),
            "segment": seg_val,
            "hook": _norm_hook(reg.get("hook")) if reg else None,
            "hypothesis_id": (reg.get("hypothesis_id") if reg else None) or None,
            "views": (past or {}).get("views", 0) or 0,
            "like_count": (past or {}).get("like_count", 0) or 0,
            "replies_count": (past or {}).get("replies_count", 0) or 0,
            "has_metrics": past is not None,
        })

    cfg = _load_json(CONFIG_FILE, {})
    min_n = cfg.get(acct, {}).get("min_n", DEFAULT_MIN_N) if isinstance(cfg, dict) else DEFAULT_MIN_N

    report = {
        "acct": acct,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "params": {"min_n": min_n, "loser_min_n": LOSER_MIN_N,
                    "winner_index": WINNER_INDEX, "loser_index": LOSER_INDEX,
                    "windows_days": list(WINDOWS)},
        "registry_loaded": sum(1 for v in registry.values() if _entry_acct(v) == acct),
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

        # HAKASE（インスタ運用の原則を店舗向けに翻案した投稿・masa専用・2026-09-23）は
        # S1-S5の winner/loser判定(apply_share対象)には含めず、単独の集計行として出す。
        # truth/nagaokaにはHAKASE投稿が存在しないため常に空集計になる（想定どおり）。
        hakase_cell = [j for j in rows if j["segment"] == "HAKASE"]
        hakase_views = [j["views"] for j in hakase_cell]
        hakase_st = _stats(hakase_views)
        hakase_index = round(hakase_st["median_views"] / baseline_median, 3) if baseline_median else 0.0
        hakase_summary = {
            **hakase_st,
            "like_rate": _rate(sum(j["like_count"] for j in hakase_cell), sum(hakase_views)),
            "reply_rate": _rate(sum(j["replies_count"] for j in hakase_cell), sum(hakase_views)),
            "index": hakase_index,
            "verdict": _verdict(hakase_st["n"], hakase_index, min_n),
        }

        # 仮説別（Obsidian由来の店舗経営の悩み・質問から作った仮説の勝ち負け判定。
        # 2026-09-23追加。acctでフィルタする。segment_hypotheses.json が無い/空でもクラッシュしない）。
        hyp_data = _load_hypotheses()
        hyp_ids_from_rows = {j["hypothesis_id"] for j in rows if j["hypothesis_id"]}
        hyp_ids_from_file = {
            h.get("id") for h in (hyp_data or {}).get("hypotheses", [])
            if isinstance(h, dict) and h.get("id") and (h.get("acct") or "masa") == acct
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
            "hakase": hakase_summary,
            "hypothesis": hyp_summary,
        }

    # --apply の判定基盤は30日窓（サンプルが安定するため）
    report["segment_summary"] = report["windows"].get("30d", {}).get("segment", {})
    report["hakase_summary"] = report["windows"].get("30d", {}).get("hakase", {})
    report["hypothesis_summary"] = report["windows"].get("30d", {}).get("hypothesis", {})

    # 過去ベースライン（segment_baseline.json・2026-09-24追加）: テスト結果との比較用に
    # 併記する。ファイル不在ならキー自体を省略する（write_markdown側もNoneチェックで対応）。
    report["past_baseline"] = _load_baseline(acct)
    return report


def write_markdown(report, acct: str):
    SEGMENTS = _segments(acct)
    today = date.today().strftime("%Y-%m-%d")
    lines = [f"# セグメント分析_{acct} {today}", "",
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
        if acct == "masa":
            lines.append("### HAKASE（原則投稿）")
            hk = w.get("hakase", {})
            lines.append("| n | 中央値 | index | いいね率 | 返信率 | 判定 |")
            lines.append("|---|---|---|---|---|---|")
            lines.append(f"| {hk.get('n', 0)} | {hk.get('median_views', 0)} | {hk.get('index', 0)} | "
                          f"{hk.get('like_rate', 0)} | {hk.get('reply_rate', 0)} | {hk.get('verdict', '判定保留')} |")
            lines.append("")
        lines.append("### 仮説別（Obsidian由来の店舗経営の悩み・質問）")
        hyp = w.get("hypothesis", {})
        if not hyp:
            lines.append("(仮説データなし。segment_hypotheses.json が空 or 未生成、または"
                          f"{acct}向け仮説が未投入)")
        else:
            lines.append("| hypothesis_id | segment | n | 中央値 | index | いいね率 | 返信率 | 判定 |")
            lines.append("|---|---|---|---|---|---|---|---|")
            for hid in sorted(hyp.keys()):
                c = hyp[hid]
                lines.append(f"| {hid} | {c.get('segment') or '-'} | {c['n']} | {c['median_views']} | "
                             f"{c['index']} | {c['like_rate']} | {c['reply_rate']} | {c['verdict']} |")
        lines.append("")

    # 過去ベースライン（segment_baseline.json・2026-09-24追加）: 直近テスト結果(上記)と
    # 過去実測を見比べられるよう併記する。ファイル不在ならこの節ごと省略する。
    baseline = _load_baseline(acct)
    if baseline:
        lines.append("### 過去ベースライン（segment_baseline.json）")
        lines.append(f"生成日: {baseline.get('generated', '-')} / "
                     f"全体中央値: {baseline.get('baseline_median', '-')} / "
                     f"サンプル{baseline.get('sample_total', '-')}件(未分類{baseline.get('none_n', '-')}件)")
        lines.append("")
        lines.append("| segment | n | 中央値 | index | 勝ちパターン(win_structures) |")
        lines.append("|---|---|---|---|---|")
        for seg in SEGMENTS:
            code = _short_code(acct, seg)
            b = baseline.get("segments", {}).get(code, {})
            win = "、".join(b.get("win_structures", [])) or "-"
            lines.append(f"| {seg} | {b.get('n', 0)} | {b.get('median', 0)} | {b.get('index', 0)} | {win} |")
        lines.append("")
        keep = baseline.get("keep_patterns") or []
        avoid = baseline.get("avoid_patterns") or []
        if keep:
            lines.append(f"継続すべきパターン: {' / '.join(keep)}")
        if avoid:
            lines.append(f"避けるべきパターン: {' / '.join(avoid)}")
        lines.append("")

    try:
        REPORT_MD_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_MD_DIR / f"セグメント分析_{acct}.md").write_text("\n".join(lines), encoding="utf-8")
    except Exception as e:
        print(f"[warn] Markdown書き込み失敗({acct}): {e}", file=sys.stderr)


def apply_share(report, acct: str):
    """segment_summary(30日窓)のwinner/loserでsegment_config.jsonのshareを調整する。"""
    SEGMENTS = _segments(acct)
    cfg = _load_json(CONFIG_FILE, None)
    if not isinstance(cfg, dict) or acct not in cfg:
        print(f"[apply:{acct}] segment_config.json が無い/不正のため share 調整をスキップ")
        return False
    acct_cfg = cfg[acct]
    share = dict(acct_cfg.get("share", {}))
    summary = report.get("segment_summary", {})

    winners = [s for s in SEGMENTS if summary.get(s, {}).get("verdict") == "winner"]
    losers = [s for s in SEGMENTS if summary.get(s, {}).get("verdict") == "loser"]

    if not winners:
        print(f"[apply:{acct}] winnerなし（loser{len(losers)}件）のため share は変更しない")
        return False

    winner_codes = [_short_code(acct, s) for s in winners]
    loser_codes = [_short_code(acct, s) for s in losers]

    freed = sum(share.get(c, 0) for c in loser_codes)
    for c in loser_codes:
        share[c] = 0

    base_add, rem = divmod(freed, len(winner_codes))
    for i, c in enumerate(winner_codes):
        share[c] = share.get(c, 0) + base_add + (1 if i < rem else 0)

    acct_cfg["share"] = share
    cfg[acct] = acct_cfg
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[apply:{acct}] share更新: winner={winners} loser={losers} → {share}")
    return True


def apply_hypotheses(report, acct: str):
    """segment_hypotheses.json の該当acctの仮説の status と last_result を30日窓の実測で更新する。
    状態遷移: untested → testing(n≥1) → winner(index≥1.3,n≥min_n) / loser(index≤0.7,n≥8)。
    status=retired の仮説は手動退役なので触らない。ファイル不在/不正なら何もせずFalseを返す
    （担当A/Bの仮説投入が未着手でも落ちない）。"""
    data = _load_hypotheses()
    if data is None:
        print(f"[apply:{acct}] segment_hypotheses.json が無い/不正のため仮説status更新をスキップ")
        return False

    hyp_stats = report.get("windows", {}).get("30d", {}).get("hypothesis", {})
    min_n = report.get("params", {}).get("min_n", DEFAULT_MIN_N)
    today = date.today().isoformat()
    changed = False
    updated_ids = []

    for h in data.get("hypotheses", []):
        if not isinstance(h, dict):
            continue
        if (h.get("acct") or "masa") != acct:
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
        print(f"[apply:{acct}] 仮説status更新: {updated_ids or '(last_resultのみ更新)'}")
    else:
        print(f"[apply:{acct}] 仮説statusの変更なし")
    return changed


def run_for_acct(acct: str, do_apply: bool, do_json: bool):
    report = build_report(acct)

    report_json_path = BASE / f"segment_report_{acct}.json"
    report_json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if acct == "masa":
        # 後方互換: 既存の segment_report.json（アカウント名なし）も維持する
        (BASE / "segment_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    write_markdown(report, acct)

    if do_apply:
        apply_share(report, acct)
        apply_hypotheses(report, acct)

    w30 = report["windows"].get("30d", {})
    b = w30.get("baseline", {})
    winners = [s for s, c in report.get("segment_summary", {}).items() if c.get("verdict") == "winner"]
    losers = [s for s, c in report.get("segment_summary", {}).items() if c.get("verdict") == "loser"]
    print(f"segment_report[{acct}]: 30日ベースn={b.get('n', 0)} 中央値{b.get('median_views', 0)} "
          f"winner={winners or 'なし'} loser={losers or 'なし'}")

    if acct == "masa":
        hakase_summary = report.get("hakase_summary", {})
        if hakase_summary:
            print(f"segment_report[masa](HAKASE原則投稿): n={hakase_summary.get('n', 0)} "
                  f"index={hakase_summary.get('index', 0)} verdict={hakase_summary.get('verdict', '判定保留')}")

    hyp_summary = report.get("hypothesis_summary", {})
    if hyp_summary:
        hwin = [h for h, c in hyp_summary.items() if c.get("verdict") == "winner"]
        hlose = [h for h, c in hyp_summary.items() if c.get("verdict") == "loser"]
        print(f"segment_report[{acct}](仮説): {len(hyp_summary)}件 winner={hwin or 'なし'} loser={hlose or 'なし'}")

    if do_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    argv = sys.argv[1:]
    acct = "masa"
    if "--acct" in argv:
        idx = argv.index("--acct")
        if idx + 1 < len(argv):
            acct = argv[idx + 1]
    do_apply = "--apply" in argv
    do_json = "--json" in argv

    if acct == "all":
        for a in ACCTS:
            run_for_acct(a, do_apply, do_json)
    elif acct in ACCTS:
        run_for_acct(acct, do_apply, do_json)
    else:
        print(f"[error] 不明なacct指定: {acct}（masa/truth/nagaoka/all のいずれかを指定）", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
