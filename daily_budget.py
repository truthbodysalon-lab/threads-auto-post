"""1アカウント1日の投稿数の唯一の定義（2026-11-11 masa指示「全てのThreads投稿の数を20投稿にして」）。

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
import os

DAILY_POSTS = int(os.environ.get("DAILY_TARGET", "20"))     # 1日の目標(全種類の合計)
DAILY_CAP = int(os.environ.get("DAILY_CAP", "20"))          # 上限(これ以上は出さない)
FRONT_N = DAILY_POSTS                                       # キュー前半＝1日で消化される本数
QUEUE_RESERVE = 10                                          # 予備の原稿(重複スキップ・NG落ち用)
QUEUE_LEN = FRONT_N + QUEUE_RESERVE                         # 生成するキューの長さ
HERO_PER_DAY = 5                                            # ヒーロー生成本数/アカ/日
HERO_MAX_PER_DAY = HERO_PER_DAY + 3                         # 当日分＋繰越の安全枠
LEDGER_ONLY_ACCTS = tuple(a for a in os.environ.get("LEDGER_ONLY_ACCTS", "nagaoka").split(",") if a)
