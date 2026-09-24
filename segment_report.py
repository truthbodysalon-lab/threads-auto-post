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
  python3 segment_report.py --acct all --apply --notify-secretary
                                                  # 上記に加え、3アカウント分を1通の日本語
                                                  # ブリーフにまとめ秘書Bot経由で#秘書からの連絡へ
                                                  # 送信する（--acct all専用。2026-09-24 本人指示:
                                                  # 「セグメントと仮説の分析は秘書経由で報告」）。
                                                  # 送信失敗時はnotify.sh --to secretaryへフォール
                                                  # バックし、両方失敗してもexit 0（理由を標準出力へ）。
                                                  # --apply または --notify-secretary のどちらかが
                                                  # あればObsidian「セグメントテスト日誌」フォルダに
                                                  # 上書きされない記録を追記する（日誌・索引・仮説履歴）
  python3 segment_report.py --acct all --apply --notify-secretary --no-send
                                                  # 検証用: 本文生成・Obsidian追記は行うがDiscord送信
                                                  # だけスキップする（同日2回目の検証実行で二重送信
                                                  # しないため。2026-09-24追加）

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
import subprocess
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

# 秘書ブリーフィング用の日本語名（専門記号「S2」「T1」等を本文に出さないため。2026-09-24追加）
SEG_JP = {
    "masa": {
        "S1": "開業前の層",
        "S2": "開業して1年の層",
        "S3": "月商50万で頭打ちの層",
        "S4": "スタッフがいる院長の層",
        "S5": "高額商品を持っている層",
    },
    "truth": {
        "T1": "デスクワークで肩や首がこる層",
        "T2": "頭痛薬を週に何度も飲む層",
        "T3": "産後で体がつらい層",
        "T4": "立ち仕事や介護で腰・脚がつらい層",
        "T5": "睡眠が浅い・疲れが取れない層",
        "T6": "他の整体やマッサージを乗り換えてきた層",
    },
}
SEG_JP["nagaoka"] = SEG_JP["truth"]
ACCT_JP = {"masa": "masahide", "truth": "truth_body_salon", "nagaoka": "truth_nagaoka"}


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
# 7d = 秘書ブリーフィングの週次まとめ（日曜）用ランキング算出のため2026-09-24追加。
# 14d/30dの判定ロジック（apply_share/apply_hypotheses・Markdown出力）は無変更。
WINDOWS = (7, 14, 30)
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
    """segment_summary(30日窓)のwinner/loserでsegment_config.jsonのshareを調整する。
    戻り値: (変更したか, detail辞書 or None)。detailは秘書ブリーフィング(--notify-secretary)が
    「配分の変更」欄を作る材料に使う（2026-09-24追加。既存の戻り値bool単体からタプルへ変更したが、
    呼び出し側run_for_acctは戻り値を無視しても動くため後方互換）。"""
    SEGMENTS = _segments(acct)
    cfg = _load_json(CONFIG_FILE, None)
    if not isinstance(cfg, dict) or acct not in cfg:
        print(f"[apply:{acct}] segment_config.json が無い/不正のため share 調整をスキップ")
        return False, None
    acct_cfg = cfg[acct]
    share = dict(acct_cfg.get("share", {}))
    summary = report.get("segment_summary", {})

    winners = [s for s in SEGMENTS if summary.get(s, {}).get("verdict") == "winner"]
    losers = [s for s in SEGMENTS if summary.get(s, {}).get("verdict") == "loser"]

    if not winners:
        print(f"[apply:{acct}] winnerなし（loser{len(losers)}件）のため share は変更しない")
        return False, None

    winner_codes = [_short_code(acct, s) for s in winners]
    loser_codes = [_short_code(acct, s) for s in losers]
    old_share = dict(share)

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
    detail = {"winners": winners, "losers": losers, "old_share": old_share, "new_share": share}
    return True, detail


def apply_hypotheses(report, acct: str):
    """segment_hypotheses.json の該当acctの仮説の status と last_result を30日窓の実測で更新する。
    状態遷移: untested → testing(n≥1) → winner(index≥1.3,n≥min_n) / loser(index≤0.7,n≥8)。
    status=retired の仮説は手動退役なので触らない。ファイル不在/不正なら何もせずFalseを返す
    （担当A/Bの仮説投入が未着手でも落ちない）。
    戻り値: (変更したか, updated_idsリスト)。--notify-secretary の「配分の変更」欄に使う
    （2026-09-24追加。既存の戻り値bool単体からタプルへ変更したが、呼び出し側run_for_acctは
    戻り値を無視しても動くため後方互換）。"""
    data = _load_hypotheses()
    if data is None:
        print(f"[apply:{acct}] segment_hypotheses.json が無い/不正のため仮説status更新をスキップ")
        return False, []

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
    return changed, updated_ids


def run_for_acct(acct: str, do_apply: bool, do_json: bool):
    report = build_report(acct)

    report_json_path = BASE / f"segment_report_{acct}.json"
    report_json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if acct == "masa":
        # 後方互換: 既存の segment_report.json（アカウント名なし）も維持する
        (BASE / "segment_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    write_markdown(report, acct)

    # apply_detail: --notify-secretary の「配分の変更」欄を作る材料（2026-09-24追加）。
    # do_apply=Falseの時はNone/空のまま＝「なし」として扱われる。
    apply_detail = {"share": None, "hypotheses": []}
    if do_apply:
        changed_s, detail_s = apply_share(report, acct)
        if changed_s:
            apply_detail["share"] = detail_s
        changed_h, updated_ids = apply_hypotheses(report, acct)
        if changed_h:
            apply_detail["hypotheses"] = updated_ids

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

    return report, apply_detail


# ============================================================
# 秘書ブリーフィング（--notify-secretary・2026-09-24追加）
# なぜ: 本人指示「セグメントと仮説の分析は秘書経由でわかる形で報告する」に基づき、
#       3アカウント分を専門用語なしの日本語1通にまとめ「アンナ秘書Bot」名義で
#       #秘書からの連絡 に送る。失敗時はnotify.sh(Webhook)にフォールバックし、
#       両方失敗しても exit 0 を維持する（記録が失われてもタスク全体は失敗にしない）。
# ============================================================

def _seg_jp_name(acct: str, full_seg: str) -> str:
    """segment_summary等のキー("S1_kaigyo_mae"等)を秘書ブリーフィング用の日本語名にする。
    未知のコードは万一のフォールバックとしてそのまま返す（記号が漏れるのは既知コード追加漏れ時のみ）。"""
    code = _short_code(acct, full_seg)
    return SEG_JP.get(acct, {}).get(code, full_seg)


def _hyp_lookup(acct: str) -> dict:
    data = _load_hypotheses() or {"hypotheses": []}
    return {h.get("id"): h for h in data.get("hypotheses", []) if isinstance(h, dict) and h.get("id")}


def _short_hyp_text(h: dict, n: int = 20) -> str:
    """hypothesis文を短く要約し、本文中の「」『』を外して返す
    （呼び出し側が外側を『』で囲むため、二重引用符で読みにくくなるのを防ぐ）。"""
    text = (h.get("hypothesis") or "")[:n]
    for ch in "「」『』":
        text = text.replace(ch, "")
    return text


def _hyp_first_line(h: dict) -> str:
    posts = h.get("posts") or []
    line = ""
    for p in posts:
        if isinstance(p, dict) and p.get("hook") == "low":
            line = (p.get("text") or "").strip().split("\n", 1)[0]
            break
    if not line and posts and isinstance(posts[0], dict):
        line = (posts[0].get("text") or "").strip().split("\n", 1)[0]
    for ch in "「」『』":
        line = line.replace(ch, "")
    return line


def _reliable_baseline_top(acct: str, baseline: dict | None):
    """過去ベースラインのうち十分な実測(n>=10)がある層で最もindexが高いものを返す(code, name) or None。
    n<10の層は参考値として比較対象から除く（segment_baseline.jsonの既存コメントに準拠）。"""
    if not baseline:
        return None
    segs = baseline.get("segments", {})
    reliable = {c: b for c, b in segs.items() if isinstance(b, dict) and b.get("n", 0) >= 10}
    if not reliable:
        return None
    top_code = max(reliable, key=lambda c: reliable[c].get("index", 0))
    return top_code, SEG_JP.get(acct, {}).get(top_code, top_code)


def _acct_section(acct: str, report: dict, apply_detail: dict, baseline: dict | None,
                   include_focus: bool = True) -> list[str]:
    summary = report.get("segment_summary", {})
    winners = [s for s, c in summary.items() if c.get("verdict") == "winner"]
    losers = [s for s, c in summary.items() if c.get("verdict") == "loser"]
    lines = [f"■ {ACCT_JP.get(acct, acct)}"]

    if winners:
        lines.append("・勝ち：" + "、".join(_seg_jp_name(acct, s) for s in winners))
    else:
        lines.append("・勝ち：まだ判定できる層なし")

    if include_focus:
        hyp_summary = report.get("hypothesis_summary", {})
        hyp_by_id = _hyp_lookup(acct)
        candidates = [
            (hid, c) for hid, c in hyp_summary.items()
            if c.get("verdict") == "testing" and c.get("index", 0) >= 1.2 and c.get("n", 0) >= 2
        ]
        candidates.sort(key=lambda x: x[1].get("index", 0), reverse=True)
        focus_bits = []
        for hid, c in candidates[:2]:
            h = hyp_by_id.get(hid, {})
            hyp_text = _short_hyp_text(h, 20)
            first_line = _hyp_first_line(h)
            n = c.get("n", 0)
            remain = max(0, 8 - n)
            gap_txt = "まもなく判定できます" if remain == 0 else f"あと{remain}本で判定"
            label = f"『{first_line}』" if first_line else f"『{hyp_text}』"
            focus_bits.append(f"{label}（{hyp_text}）全体の{round(c.get('index', 0), 1)}倍・投稿{n}本・{gap_txt}")
        if focus_bits:
            lines.append("・注目：" + " / ".join(focus_bits))

    if losers:
        lines.append("・負け：" + "、".join(_seg_jp_name(acct, s) for s in losers))

    change_parts = []
    share_detail = apply_detail.get("share")
    if share_detail:
        old_s, new_s = share_detail["old_share"], share_detail["new_share"]
        diffs = []
        for code, new_v in new_s.items():
            old_v = old_s.get(code, 0)
            if new_v == old_v:
                continue
            name = SEG_JP.get(acct, {}).get(code, code)
            diffs.append(f"{name}を{'増やしました' if new_v > old_v else '減らしました'}")
        if diffs:
            change_parts.append("、".join(diffs))
    hyp_changes = apply_detail.get("hypotheses") or []
    if hyp_changes:
        hyp_by_id = _hyp_lookup(acct)
        st_jp = {"winner": "採用", "loser": "停止", "testing": "検証継続", "untested": "未検証"}
        readable = []
        for u in hyp_changes:
            if ":" not in u or "->" not in u:
                continue
            hid, trans = u.split(":", 1)
            _, new_st = trans.split("->", 1)
            if new_st not in ("winner", "loser"):
                continue  # testingへの遷移は日次ノイズになるため配分の変更欄には出さない
            h = hyp_by_id.get(hid, {})
            hyp_text = _short_hyp_text(h, 16)
            readable.append(f"『{hyp_text}』を{st_jp.get(new_st, new_st)}に")
        if readable:
            change_parts.append("、".join(readable))
    lines.append("・配分の変更：" + (" / ".join(change_parts) if change_parts else "なし"))

    top = _reliable_baseline_top(acct, baseline)
    if top is None:
        lines.append("・過去データがまだ少なく比較なし")
    else:
        top_code, top_name = top
        winner_codes = {_short_code(acct, s) for s in winners}
        if not winners:
            lines.append(f"・過去は{top_name}が強かった→まだ判定できる層はなく様子見")
        elif top_code in winner_codes:
            lines.append(f"・過去は{top_name}が強かった→今のところ同じ傾向")
        else:
            lines.append(f"・過去は{top_name}が強かった→今のところ別の層に逆転の兆し")

    return lines


def _acct_section_weekly(acct: str, report: dict) -> list[str]:
    lines = [f"■ {ACCT_JP.get(acct, acct)}"]
    seg_only = report.get("windows", {}).get("7d", {}).get("segment", {})
    ranked = sorted(
        ((s, c) for s, c in seg_only.items() if c.get("n", 0) >= 1),
        key=lambda x: x[1].get("index", 0), reverse=True,
    )[:3]
    if ranked:
        rank_bits = [
            f"{i}位 {_seg_jp_name(acct, s)}（全体の{round(c.get('index', 0), 1)}倍・投稿{c.get('n', 0)}本）"
            for i, (s, c) in enumerate(ranked, 1)
        ]
        lines.append("・今週の層別ランキング：" + " / ".join(rank_bits))
    else:
        lines.append("・今週の層別ランキング：対象データなし")

    summary = report.get("segment_summary", {})
    winners = [s for s, c in summary.items() if c.get("verdict") == "winner"]
    losers = [s for s, c in summary.items() if c.get("verdict") == "loser"]
    w_names = "、".join(_seg_jp_name(acct, s) for s in winners) if winners else "なし"
    l_names = "、".join(_seg_jp_name(acct, s) for s in losers) if losers else "なし"
    lines.append(f"・勝ち累計：{w_names} / 負け累計：{l_names}")
    return lines


def _next_watch_line(results: dict) -> str:
    candidates = []
    for acct, (report, _apply_detail, _baseline) in results.items():
        hyp_summary = report.get("hypothesis_summary", {})
        for hid, c in hyp_summary.items():
            verdict = c.get("verdict")
            if verdict not in ("testing", "判定保留"):
                continue
            n = c.get("n", 0)
            target = 5 if verdict == "判定保留" else 8
            remain = max(0, target - n)
            candidates.append((remain, acct, hid, n))
    if not candidates:
        return "とくに変化なし。このまま様子を見ます。"
    candidates.sort(key=lambda x: x[0])
    remain, acct, hid, n = candidates[0]
    h = _hyp_lookup(acct).get(hid, {})
    hyp_text = _short_hyp_text(h, 16)
    acct_jp = ACCT_JP.get(acct, acct)
    if remain <= 0:
        return f"{acct_jp}の『{hyp_text}』はまもなく判定できます。"
    return f"{acct_jp}の『{hyp_text}』はまだ{n}本。あと{remain}本で判定できます。"


def _build_secretary_body(results: dict, is_sunday: bool, include_focus: bool = True) -> str:
    today_str = f"{date.today().month}/{date.today().day}"
    header = f"📊 セグメントテスト {'週次まとめ' if is_sunday else '日次報告'}（{today_str}）"
    parts = [header]
    for acct in ACCTS:
        report, apply_detail, baseline = results[acct]
        if is_sunday:
            parts.extend(_acct_section_weekly(acct, report))
        else:
            parts.extend(_acct_section(acct, report, apply_detail, baseline, include_focus=include_focus))
        parts.append("")
    parts.append(f"▶ 次に見ること：{_next_watch_line(results)}")
    parts.append("")
    parts.append("質問や『この層を増やして』などの指示はこの返信で受け付けます。")
    return "\n".join(parts).strip()


def build_secretary_digest(results: dict, is_sunday: bool) -> str:
    body = _build_secretary_body(results, is_sunday, include_focus=True)
    if len(body) > 1500 and not is_sunday:
        body = _build_secretary_body(results, is_sunday, include_focus=False)
    if len(body) > 1900:
        body = body[:1880].rstrip() + "\n…（続きは省略）"
    return body


def _send_secretary(text: str):
    """秘書Bot経由での送信を試み、失敗したらnotify.sh --to secretaryへフォールバックする。
    戻り値: (成功したか, 経路/理由の説明文字列)。両方失敗しても例外を投げない
    （呼び出し元がexit 0を維持できるようにするため）。"""
    reply_script = Path.home() / ".claude" / "scripts" / "secretary_discord_reply.py"
    reason_bot = None
    try:
        r = subprocess.run(
            ["python3", str(reply_script), text],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return True, "秘書Bot(secretary_discord_reply.py)で送信"
        reason_bot = f"secretary_discord_reply.py rc={r.returncode} stderr={r.stderr.strip()[:200]}"
    except Exception as e:
        reason_bot = f"secretary_discord_reply.py 例外: {e}"

    notify_sh = Path.home() / ".claude" / "scripts" / "notify.sh"
    try:
        r2 = subprocess.run(
            ["bash", str(notify_sh), "--to", "secretary", text],
            capture_output=True, text=True, timeout=30,
        )
        # notify.shは記録を失わないため常にexit 0を返す仕様。成功可否は標準出力の"OK:"で判定する。
        if r2.returncode == 0 and "OK:" in r2.stdout:
            return True, f"notify.sh --to secretaryへフォールバックして送信（秘書Bot失敗理由: {reason_bot}）"
        reason_notify = f"notify.sh rc={r2.returncode} stdout={r2.stdout.strip()[:200]}"
        return False, f"両方失敗（ファイル記録のみ）: 秘書Bot={reason_bot} / notify.sh={reason_notify}"
    except Exception as e2:
        return False, f"両方失敗（ファイル記録なしの可能性）: 秘書Bot={reason_bot} / notify.sh例外={e2}"


# ============================================================
# Obsidian蓄積記録（2026-09-24追加。本人指示「obsidianに記録を残るようにもしておいて」）
# なぜ: セグメント分析_<acct>.md は毎回上書きで過去が残らない。秘書ブリーフィングも
#       Discord側の履歴にしか残らないため、Obsidian側に「上書きされない」記録を追加する。
#       --apply または --notify-secretary（--acct all限定）実行時のみ書く。
# ============================================================

OBSIDIAN_SEG_DIR = Path(
    "/Users/mt112/Desktop/my files/myfiles/SNS・Threads/分析レポート/セグメントテスト日誌"
)


def _obsidian_seg_dir_safe():
    """フォルダ作成に失敗しても（Desktop配下のTCC制限等）タスク全体は落とさずNoneを返す。
    write_markdown()の既存の try/except 方針に合わせる。"""
    try:
        OBSIDIAN_SEG_DIR.mkdir(parents=True, exist_ok=True)
        return OBSIDIAN_SEG_DIR
    except Exception as e:
        print(f"[warn] Obsidian日誌フォルダ作成失敗: {e}", file=sys.stderr)
        return None


def _apply_change_lines(acct: str, apply_detail: dict) -> list[str]:
    """(c) 配分・仮説statusの変更内容。日誌は人が見返す生データ記録のため、
    ブリーフィング本文と違い記号(短縮コード)をそのまま使ってよい。"""
    lines = []
    share_detail = apply_detail.get("share")
    if share_detail:
        diffs = [
            f"{code}:{share_detail['old_share'].get(code, 0)}→{new_v}"
            for code, new_v in share_detail["new_share"].items()
            if new_v != share_detail["old_share"].get(code, 0)
        ]
        if diffs:
            lines.append(
                f"share変更 {', '.join(diffs)}（winner={share_detail['winners'] or 'なし'} "
                f"loser={share_detail['losers'] or 'なし'}）"
            )
    for u in apply_detail.get("hypotheses") or []:
        lines.append(f"仮説status変更 {u}")
    return lines or ["変更なし"]


def _segment_hook_table_md(acct: str, report: dict) -> str:
    SEGMENTS = _segments(acct)
    seg_hook = report.get("windows", {}).get("30d", {}).get("segment_hook", {})
    lines = ["| 層 | フック | n | 中央値 | 全体比 | 判定 |", "|---|---|---|---|---|---|"]
    for seg in SEGMENTS:
        for hook in HOOKS:
            c = seg_hook.get(f"{seg}__{hook}", {})
            lines.append(
                f"| {seg} | {hook} | {c.get('n', 0)} | {c.get('median_views', 0)} | "
                f"{c.get('index', 0)} | {c.get('verdict', '判定保留')} |"
            )
    return "\n".join(lines)


def _hypothesis_table_md(acct: str, report: dict) -> str:
    hyp = report.get("windows", {}).get("30d", {}).get("hypothesis", {})
    if not hyp:
        return "(仮説データなし)"
    lines = ["| 仮説ID | 層 | n | 中央値 | 全体比 | 判定 |", "|---|---|---|---|---|---|"]
    for hid in sorted(hyp.keys()):
        c = hyp[hid]
        lines.append(
            f"| {hid} | {c.get('segment') or '-'} | {c.get('n', 0)} | {c.get('median_views', 0)} | "
            f"{c.get('index', 0)} | {c.get('verdict', '判定保留')} |"
        )
    return "\n".join(lines)


def _write_obsidian_journal(results: dict, secretary_body: str | None, sent_status: str) -> None:
    """(1) 日誌フォルダの YYYY-MM-DD.md に追記する。同日複数回実行しても上書きせず、
    「## 実行 HH:MM」見出しで積み増す。"""
    seg_dir = _obsidian_seg_dir_safe()
    if seg_dir is None:
        return
    today = date.today().isoformat()
    now_hm = datetime.now().strftime("%H:%M")
    path = seg_dir / f"{today}.md"

    parts = [f"## 実行 {now_hm}", ""]
    if secretary_body is not None:
        parts.append(f"### (a) 秘書へ送った本文（{sent_status}）")
        parts.append("```")
        parts.append(secretary_body)
        parts.append("```")
    else:
        parts.append("### (a) 秘書送信なし（--notify-secretary未指定の--apply単独実行）")
    parts.append("")

    parts.append("### (b) 層×フック×仮説（30日窓・数値そのまま）")
    for acct in ACCTS:
        report, _apply_detail, _baseline = results[acct]
        parts.append(f"#### {ACCT_JP.get(acct, acct)}")
        parts.append(_segment_hook_table_md(acct, report))
        parts.append("")
        parts.append(_hypothesis_table_md(acct, report))
        parts.append("")

    parts.append("### (c) 配分・仮説statusの変更")
    for acct in ACCTS:
        _report, apply_detail, _baseline = results[acct]
        parts.append(f"- {ACCT_JP.get(acct, acct)}: " + " / ".join(_apply_change_lines(acct, apply_detail)))
    parts.append("")

    block = "\n".join(parts) + "\n---\n\n"
    try:
        if not path.exists():
            path.write_text(f"# セグメントテスト日誌 {today}\n\n" + block, encoding="utf-8")
        else:
            with path.open("a", encoding="utf-8") as f:
                f.write(block)
    except Exception as e:
        print(f"[warn] Obsidian日誌書き込み失敗: {e}", file=sys.stderr)


def _update_obsidian_index(results: dict) -> None:
    """(2) 索引ファイル セグメントテスト_記録.md の表に1行追記（新しい実行が上）。"""
    seg_dir = _obsidian_seg_dir_safe()
    if seg_dir is None:
        return
    path = seg_dir / "セグメントテスト_記録.md"
    today = date.today().isoformat()

    winners_all, losers_all, change_all = [], [], []
    for acct in ACCTS:
        report, apply_detail, _baseline = results[acct]
        summary = report.get("segment_summary", {})
        w = [s for s, c in summary.items() if c.get("verdict") == "winner"]
        l = [s for s, c in summary.items() if c.get("verdict") == "loser"]
        acct_jp = ACCT_JP.get(acct, acct)
        if w:
            winners_all.append(f"{acct_jp}:" + "・".join(_seg_jp_name(acct, s) for s in w))
        if l:
            losers_all.append(f"{acct_jp}:" + "・".join(_seg_jp_name(acct, s) for s in l))
        change_lines = _apply_change_lines(acct, apply_detail)
        if change_lines != ["変更なし"]:
            change_all.append(f"{acct_jp}:" + "、".join(change_lines))

    row = (
        f"| [[{today}]] | {'; '.join(winners_all) or 'なし'} | {'; '.join(losers_all) or 'なし'} | "
        f"{'; '.join(change_all) or 'なし'} | {_next_watch_line(results)} |\n"
    )
    header = "# セグメントテスト 記録\n\n| 日付 | 勝ち | 負け | 変更 | 次に見ること |\n|---|---|---|---|---|\n"

    try:
        if not path.exists():
            path.write_text(header + row, encoding="utf-8")
            return
        existing = path.read_text(encoding="utf-8")
        marker = "|---|---|---|---|---|"
        if marker in existing:
            insert_at = existing.index(marker) + len(marker)
            nl = existing.index("\n", insert_at)
            new_content = existing[:nl + 1] + row + existing[nl + 1:]
        else:
            # 既存ファイルが想定外の形式の場合は末尾に見出しごと追記する（データを消さない）
            new_content = existing.rstrip("\n") + "\n\n" + header + row
        path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        print(f"[warn] Obsidian索引書き込み失敗: {e}", file=sys.stderr)


def _update_obsidian_hyp_history(results: dict) -> None:
    """(3) 仮説の履歴.md に今回のstatus遷移を追記する。遷移が無い回は行を足さない
    （ノイズ防止）が、ファイル自体は初回実行時に見出しだけ作っておく
    （後続の追記先として、また運用確認のために毎回存在させるため）。"""
    seg_dir = _obsidian_seg_dir_safe()
    if seg_dir is None:
        return
    path = seg_dir / "仮説の履歴.md"
    today = date.today().isoformat()

    rows = []
    for acct in ACCTS:
        report, apply_detail, _baseline = results[acct]
        hyp_by_id = _hyp_lookup(acct)
        hyp_stats = report.get("windows", {}).get("30d", {}).get("hypothesis", {})
        for u in apply_detail.get("hypotheses") or []:
            if ":" not in u or "->" not in u:
                continue
            hid, trans = u.split(":", 1)
            old_st, new_st = trans.split("->", 1)
            h = hyp_by_id.get(hid, {})
            summary_txt = _short_hyp_text(h, 24)
            st = hyp_stats.get(hid, {})
            evidence = f"n={st.get('n', 0)} 全体比{st.get('index', 0)}倍"
            rows.append(
                f"| {today} | {ACCT_JP.get(acct, acct)} | {hid} | {summary_txt} | "
                f"{old_st}→{new_st} | {evidence} |"
            )

    header = "# 仮説の履歴\n\n| 日付 | アカウント | 仮説ID | 仮説要約 | 遷移 | 根拠数値 |\n|---|---|---|---|---|---|\n"
    if not rows:
        try:
            if not path.exists():
                path.write_text(header, encoding="utf-8")
        except Exception as e:
            print(f"[warn] Obsidian仮説履歴書き込み失敗: {e}", file=sys.stderr)
        return

    try:
        if not path.exists():
            path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
        else:
            with path.open("a", encoding="utf-8") as f:
                f.write("\n".join(rows) + "\n")
    except Exception as e:
        print(f"[warn] Obsidian仮説履歴書き込み失敗: {e}", file=sys.stderr)


def main():
    argv = sys.argv[1:]
    acct = "masa"
    if "--acct" in argv:
        idx = argv.index("--acct")
        if idx + 1 < len(argv):
            acct = argv[idx + 1]
    do_apply = "--apply" in argv
    do_json = "--json" in argv
    do_notify = "--notify-secretary" in argv
    # --no-send: --notify-secretaryは指定するがDiscord送信だけ止める検証用オプション
    # （2026-09-24追加。同日に2回目の検証実行をしても秘書チャンネルへ二重送信しないため）。
    no_send = "--no-send" in argv

    if do_notify and acct != "all":
        print("[notify-secretary] --acct all との併用が前提のため、今回はスキップします", file=sys.stderr)
        do_notify = False

    if acct == "all":
        results = {}
        for a in ACCTS:
            report, apply_detail = run_for_acct(a, do_apply, do_json)
            results[a] = (report, apply_detail, _load_baseline(a))

        is_sunday = date.today().isoweekday() == 7
        secretary_body = None
        sent_status = "未送信"
        if do_notify:
            secretary_body = build_secretary_digest(results, is_sunday)
            if no_send:
                sent_status = "未送信（--no-send指定のため送信スキップ・検証用）"
                print("[notify-secretary] --no-send指定のため送信をスキップしました")
            else:
                ok, info = _send_secretary(secretary_body)
                sent_status = ("送信成功: " if ok else "送信失敗（ファイル記録のみ）: ") + info
                print(f"[notify-secretary] {'送信成功' if ok else '送信失敗（ファイル記録のみ）'}: {info}")
            print("----- 送信本文 -----")
            print(secretary_body)
            print("--------------------")

        # Obsidian蓄積記録: --apply または --notify-secretary のどちらかがあれば書く
        # （本人指示2026-09-24。セグメント分析_<acct>.mdの上書きと違い、ここは積み増し記録）。
        if do_apply or do_notify:
            _write_obsidian_journal(results, secretary_body, sent_status)
            _update_obsidian_index(results)
            _update_obsidian_hyp_history(results)
    elif acct in ACCTS:
        run_for_acct(acct, do_apply, do_json)
    else:
        print(f"[error] 不明なacct指定: {acct}（masa/truth/nagaoka/all のいずれかを指定）", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
