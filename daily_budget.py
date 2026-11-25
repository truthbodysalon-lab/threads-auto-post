"""1アカウント1日の投稿数の唯一の定義（2026-11-11 masa指示「全てのThreads投稿の数を20投稿にして」→ 2026-11-12 masa指示「UPLINK以外は30投稿以内に抑えて」で30本。UP LINK(~/threads-uplink)は対象外）。

【2026-11-14〜 10本】masa指示「各アカウント合計1日10本にして」（2026-11-14 00:17）で30→10本。ヒーロー3本(＋繰越1)・画像1・uplink転載1・セグメント1〜2を含む合計。
  10本の内訳(目安): truth=画像1・HPB(頭痛)1・LINEリストイン1・アクセス1・診断/整体LINE1〜2・セグメント1・ヒーロー3・通常残り／nagaoka=画像1・HPB1・LINE1・アクセス1・長岡市相談型・セグメント1・ヒーロー3・通常残り／masa=Buffer(Grok側の約10枠)も合計に数えるため自動分はBufferの不足分だけ。
  投稿時刻はペース配分(6〜22時)＋アカウント別のずらし(auto_post.PACE_OFFSET_MIN)・同一アカウント45分以上空け(auto_post.MIN_GAP_MIN。truthは1日30本のため25分＝auto_post.MIN_GAP_BY_ACCT)。
【2026-11-12〜 30本】下の内訳表は20本時点のもの。増えた10本はすべて「通常投稿(残り)」に入る（導線アンカー・画像・転載の本数は変えていない）。
【2026-11-14〜 ヒーロー10本】masa指示「10本」でヒーローを5→10本/アカ/日に戻した。合計30本・上限30本は変えないので、増えた5本は「通常投稿(残り)」から回す。masaはBuffer約10枠も合計に数えるため、導線＋ヒーローで自動分の枠をほぼ使い切る（入りきらないヒーローは hero_carry で翌日へ繰越・最大3本）。

【20本の内訳（アカウント別・通常投稿/画像/uplink転載/ヒーローをすべて含む合計）】
優先順位: 導線アンカー → ヒーロー → 通常投稿

| 項目                         | truth | nagaoka | masa |
|------------------------------|:-----:|:-------:|:----:|
| 画像投稿(image_post)         |   2   |    2    |  -   |
| uplink転載(uplink_repost)    |   -   |    -    |  2   |
| HPB予約CTA                   |   2   |    1    |  -   |
| 公式LINEリストイン           |   1   |    1    |  -   |
| 店舗アクセス                 |   1   |    1    |  -   |
| 頭痛診断                     |   1   |    1    |  -   |
| 整体公式LINE(無料相談)       |   1   |    -    |  -   |
| 長岡市相談型                 |   -   |    2    |  -   |
| プロフィールCTA/AI/プレゼント |   -   |    -    |  3   |
| セグメント仮説テスト         |   2   |    2    |  3   |
| HAKASE(ハカセ原則)           |   -   |    -    |  1   |
| 勝ち型固定原稿               |   -   |    -    |  2   |
| LIST_HABIT / お客様の声      |  1+1  |   0-1   |  -   |
| ヒーロー                     |   5   |    5    |  5   |
| 通常投稿(残り)               |  残り |   残り  | 残り |
| 合計                         |  20   |   20    |  20  |

- masa は Threads API 実測(Buffer 約10枠を含む)を合計に数える＝自動分は約10本＋Buffer。
- nagaoka は外部ツール(約20本/日)を数えない＝台帳(自前投稿)だけで20本。
- 生成側のキューは「前半 FRONT_N(=20)本」に導線アンカーを収め、その後ろは予備(少量)だけ。
"""
from __future__ import annotations

import os

DAILY_POSTS = int(os.environ.get("DAILY_TARGET", "10"))   # 2026-11-12 masa指示「UPLINK以外は30投稿以内に抑えて」（20→30）     # 1日の目標(全種類の合計)
DAILY_CAP = int(os.environ.get("DAILY_CAP", "10"))   # 上限30（UP LINK以外のThreadsはこれを超えない）          # 上限(これ以上は出さない)
FRONT_N = DAILY_POSTS                                       # キュー前半＝1日で消化される本数
QUEUE_RESERVE = 10                                          # 予備の原稿(重複スキップ・NG落ち用)
QUEUE_LEN = FRONT_N + QUEUE_RESERVE                         # 生成するキューの長さ
HERO_PER_DAY = 3                                            # ヒーロー生成本数/アカ/日（2026-11-14 masa指示「各アカウント合計1日10本にして」で10→3。合計10本の内数）
HERO_MAX_PER_DAY = HERO_PER_DAY + 1                         # 当日分＋繰越の安全枠（2026-11-14 10本化: 繰越1本＝最大4本。hero_carry.CARRY_MAXと揃える）
LEDGER_ONLY_ACCTS = tuple(a for a in os.environ.get("LEDGER_ONLY_ACCTS", "nagaoka").split(",") if a)

# 【2026-11-25〜 アカウント別の本数】masa指示「（@truth_body_salonの）自動投稿は1日20本」。
# 上の DAILY_POSTS/DAILY_CAP/FRONT_N/QUEUE_LEN は「アカウント指定なし＝既定(10本)」として残し、
# アカウントごとの本数は必ず下の関数で引く（truth だけ20本・nagaoka/masa は10本のまま）。
# 増えた10本はすべて通常投稿。導線(HPB・LINE・アクセス・整体LINE・診断)は「各案内1日1本ずつ」のまま増やさない（同日の masa指示）。
# 【2026-11-26 30本】masa指示「（通常20本に長岡市の投稿10本を足して）1日30本にする」。
# 増えた10本は長岡市の投稿（generate_remix._insert_nagaoka_local_posts）。20本化は11/26開始で未実施のまま30本に置き換える。
DAILY_POSTS_BY_ACCT = {"truth": int(os.environ.get("DAILY_TARGET_TRUTH", "30"))}
# 切り替え日（JSTの日付）。指示が出た11/25夜に当日分の目標が10→20へ跳ねると、22時台に不足10本を一気に出してしまう。
# また翌朝の検品は「昨日(11/25)＝10本の日」を20本で測って誤FAILになる。どちらも防ぐため翌日から効かせる。
BY_ACCT_FROM = {"truth": "2026-11-26"}


def _today_jst() -> str:
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")


def daily_posts(acct: str | None = None, day: str | None = None) -> int:
    """そのアカウントの1日の目標本数（全種類の合計）。day=YYYY-MM-DD（省略時は今日JST）で、その日に有効だった本数を返す。"""
    if acct in DAILY_POSTS_BY_ACCT and (day or _today_jst()) >= BY_ACCT_FROM.get(acct, "0000-00-00"):
        return DAILY_POSTS_BY_ACCT[acct]
    return DAILY_POSTS


def daily_cap(acct: str | None = None, day: str | None = None) -> int:
    """そのアカウントの1日の上限本数。アカウント別の指定がある日はそれ、無ければ DAILY_CAP。"""
    if acct in DAILY_POSTS_BY_ACCT and (day or _today_jst()) >= BY_ACCT_FROM.get(acct, "0000-00-00"):
        return DAILY_POSTS_BY_ACCT[acct]
    return DAILY_CAP


def front_n(acct: str | None = None) -> int:
    """キュー前半＝1日で消化される本数（導線アンカー・ヒーローはこの内側に置く）。"""
    return daily_posts(acct)


def queue_len(acct: str | None = None) -> int:
    """生成するキューの長さ（前半＋予備）。"""
    return front_n(acct) + QUEUE_RESERVE
