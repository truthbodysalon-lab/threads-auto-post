# threads-auto-post 健康診断レポート（2026-10-29）

## 要約（3行）
1. **最優先の1件**: `auto_post.py:2030` の例外ログ呼び出しの引数の数が違っており、1アカウントで例外が起きると「残りのアカウントに進む」はずの保護が逆に TypeError を投げて落ち、後続アカウント（nagaoka・masa）がそのサイクル丸ごと投稿されない（9/24障害の再発防止策が機能していない）。
2. HTTP の `timeout=` 漏れは **0件**（全 urlopen / requests 呼び出しに付与済み）。LINE API はプレゼント配布（顧客向け broadcast）1箇所のみで、**通知用途の LINE 送信は無し**（通知は Discord に統一されている）。
3. ワークフローは「保険cronの各実行が約10分待機してから降りる」「`timeout-minutes` 未設定」「push失敗でも成功扱い」が主な無駄・弱点。テストは 13本中 11本合格、不合格2本はいずれも**テスト側の日付・本番データ依存**で本番コードの不具合ではない。

> 調査範囲: デフォルトブランチ `main`（b644715）。コード・ワークフロー・Secrets・cron は一切変更していない。ワークフローの手動実行もしていない。
> 補足: このリポジトリは **public** のため、GitHub ホスト標準ランナーの Actions 実行分は課金・無料枠消費の対象外（GitHub の仕様）。ただし同時実行枠（無料プランは20ジョブ）とログ量は消費するので「無駄」の指摘は残している。private リポジトリ（line-stepmail-cloud 等）に同じ書き方を移植する場合は分数がそのまま無料枠を削る。

---

## 指摘一覧

| # | 重大度 | ファイル:行 | 問題 | 直し方の案 |
|---|---|---|---|---|
| 1 | **高** | `auto_post.py:2030` | `_safe_batch` の except 内で `log_error(acct, f"...")` と**2引数**で呼んでいるが、`log_error` は `def log_error(msg)`（`auto_post.py:157`）で1引数。例外発生時に TypeError が新たに発生して except から飛び出し、`_run_main` ごと終了 → 後続アカウントとuplink追加枠がそのサイクル全滅。コメントにある「9/24 masa丸1日0本」の再発防止が実際には効いていない。 | `log_error(f"[{acct}] _run_account_batch 例外: ...")` の1引数に直す。あわせて `_safe_batch` に例外を投げるダミーで「次のアカウントに進むこと」を確認するテストを1本追加。 |
| 2 | 中 | `.github/workflows/auto_post.yml:44-48` | Single keeper check は「旧runが居る間 30秒×最大20回＝約10分」待ってから降りる。常駐run稼働中は保険cron（毎時2分・32分）の**全実行が毎回約10分ランナーを占有**する（実績: 直近20件の schedule 実行はすべて約10分20秒で success）。引き継ぎ待ちが必要なのは「旧runが終了間際のとき」だけ。 | 旧runの `run_started_at` を取得し、開始から330分未満（＝まだ終わらない）なら即 `skip=1`、終了間際のときだけ待つ。または cron を `2 * * * *`（毎時1回）に減らす。※cron変更は本人確認のうえで。 |
| 3 | 中 | `.github/workflows/generate.yml:14-17` / `.github/workflows/insights.yml:14-17` | job に `timeout-minutes` が無い（既定360分）。insights / analyze_and_tune / generate_remix がハングすると最大6時間ランナーを占有し、朝の生成も止まったまま気づきにくい。 | `timeout-minutes: 20` 程度を job に付与。各 step にも `timeout-minutes` を付けると、どこで止まったか分かる。 |
| 4 | 中 | `generate.yml:79-83` / `insights.yml:55-58` / `daily_verify.yml:61-64` / `health_check.yml:58-61` | `for i in 1 2 3; do git push && break; git pull --rebase ...; done` は、3回とも push に失敗しても**最後の `git pull` が成功すれば step は success**。生成済み投稿や insights が push されていないのに緑で終わる（`insights.yml` / `daily_verify.yml` は rebase 失敗時の `--abort` も無い）。 | ループ後に `git diff --quiet origin/main HEAD -- <対象ファイル> \|\| { echo "::error::push失敗"; exit 1; }` を置く。`hpb_availability.yml:108-112` の「成功時 `exit 0`・最後に `exit 1`」の書き方に揃えるのが簡単。 |
| 5 | 中 | `auto_post.py:1241-1257` | `_recently_posted_on_threads`（API実投稿を正とする**最終**重複チェック）が例外時に `return False` で**ログ無し**。Threads API 取得が落ちている間、最後の二重投稿防止が無効になっていることが記録に残らない（選択側の `_recent_api_firstlines` はログを出しているので非対称）。 | except で `log_error(f"[{acct}] 最終重複チェック取得失敗（API側の重複回避が無効）: {type(e).__name__}: {e}")` を出す。挙動（フェイルオープン）は変えない。 |
| 6 | 中 | `auto_post.py:64-67` | 投稿直前の検品 `_inspector.inspect_post` が例外を出すと `ok=True` で素通し（設計どおりのフェイルオープン）だが**ログが無い**。inspector にバグが入ると検品ゲートが黙って全面無効になる。 | except で1行 `log_error` を出す（もしくは1日1回だけ Discord 通知）。挙動は変えない。 |
| 7 | 中 | テスト全般（`test_dead_openings_pdca.py:29` ほか） | テストが**本番データファイルを書き換える**（実行後 `weights_nagaoka.json` が変更された）。また `test_url_avoidance.py` / `test_buffer_overlap.py` は実データ（`log_*_posted.jsonl`）と実 Threads API（トークンがあれば）を読みに行く。本番チェックアウト上や CI の commit_logs 前に実行すると、テスト由来の値がコミットされ得る。 | テストは `tempfile` のコピー上で動かす（`BASE` を差し替える）か、`weights_*.json` を書く関数に出力先引数を足す。API 取得はフェイク関数を注入（`_api_posts_since(fetch=...)` は既に対応済みなので呼び出し側も同様に）。 |
| 8 | 低 | `test_buffer_overlap.py:19`（`buffer_overlap.py:280-283`） | テストの日付が `D = "2026-10-23"` 固定だが、本体の「自系統の投稿」判定は**実行時の今日から直近3日**だけを見る。10/26以降は自系統投稿を除外できず `Buffer分だけ拾う: 2件` で不合格になる（時限式に壊れるテスト）。本番コードの不具合ではない。 | `D = datetime.now(JST).strftime("%Y-%m-%d")` にする、または `_buffer_ctx` に `now` を注入できるようにする。 |
| 9 | 低 | `test_url_avoidance.py:134-157` | 架空日付 2099-01-01 のキューでも、重複判定は実データ `log_truth_posted.jsonl` を参照する。候補「揉んでも治らない理由があります。」は 10/22 に実投稿済みのため直近7日の重複扱いになり不合格（推定原因。10/30以降は再び合格する見込み）。 | 一時ディレクトリの台帳だけを見るよう `_INDIVIDUAL_LOGS` / `SHARED_LOG` も差し替えるか、テスト固有の文言（例:「【テスト】…」）を使う。 |
| 10 | 低 | `auto_post.py:28-41` | `duplicate_guard` の import が ImportError のとき、重複チェック関数が全部「何もしない」版に**黙って**差し替わる（全二重投稿防止が無効）。現状ファイルは存在するので実害は無いが、リネーム等で事故る。 | fallback 定義時に `print("::error::duplicate_guard 読込失敗", file=sys.stderr)` を出す。できれば投稿自体を止める側に倒す。 |
| 11 | 低 | `auto_post.py:43-53` | `inspector` / `buffer_overlap` の import 失敗が `except Exception` でログ無し（機能が黙って無効化）。 | 1行の警告ログを出す。 |
| 12 | 低 | `auto_post.yml:158-162` | `prefill \|\| true`、`auto_post.py ... \|\| true` などループ内の全コマンドが `\|\| true` で、job は常に success。投稿が全失敗してもワークフローは緑。（watchdog / selfwatch が別途見張っているため緊急性は低い） | 方針として許容するなら現状維持。最低限 `auto_post.py` の終了コードが非0のとき `::warning::` を出すと Actions 画面で気づける。 |
| 13 | 低 | `auto_post.yml:102-115`（`prefill`） / `auto_post.py:1135` | `generate_remix.py` の subprocess 呼び出しに timeout が無い。prefill 部分は外側の `timeout 50m` の**外**にある。現状 generate_remix はネットワークを使わないのでハングしにくいが、将来 API 呼び出しが入ると常駐ループ全体が止まる。 | prefill を `timeout 10m python3 -c ...` で囲む。`subprocess.run(..., timeout=600)` を付ける。 |
| 14 | 低 | `generate.yml:53-54` | `insights.py` を、30分前に `insights.yml`（21:17 UTC）でも実行している。朝に同じ API 取得が2回走る。 | どちらか片方に寄せる（generate 側で必要なら insights.yml を廃止、または generate 側の step を削除）。 |
| 15 | 低 | `hpb_availability.yml:47-51` | 予備 cron（`47 3 * * …`）でも、`apt-get install fonts-noto-cjk` と `pip install` を**「本日投稿済み」判定より前に**実行する（毎回1〜2分）。pip キャッシュも未使用。 | 先頭に `hpb_availability_state.json` の当日判定 step を置き、投稿済みならそれ以降を `if:` でスキップ。`actions/setup-python` に `cache: pip` を付ける。 |
| 16 | 低 | `health_check.yml:6` | cron が24時間30分毎で、JST 23:00〜6:00 はスクリプト内で即終了するだけ（1日約14回、checkout+setup-python 分が無駄）。 | cron の時間帯を UTC で `5,35 21-23,0-13 * * *` に絞る。※cron変更は本人確認のうえで。 |
| 17 | 低 | `auto_post.py:1147-1170` | `api_request` は `URLError`（接続系）で `threads_publish` の POST も自動リトライする。サーバー側に届いていた場合の再送になる（Threads は同じ creation_id の再publishをエラーにする想定なので実害は小さい）。 | publish だけはリトライ前に `_recently_posted_on_threads` で実投稿を確認してから再送する。 |
| 18 | 情報 | `present_broadcast_cloud.py:22` / `present_broadcast.yml:72` | `api.line.me`（`/v2/bot/message/broadcast`）への送信が1箇所ある。ただし用途は「公式LINE友だち全員へのプレゼント配布」（2026-10-24 本人指示の顧客向け配信）で、**本人宛て通知ではない**。成功/失敗の通知は Discord（`notify()`）に送っており、「通知はDiscordのみ」のルールには沿っている。LINE Notify・その他の LINE 通知コードは無し。 | 対応不要。意図どおりか本人が確認するだけでよい。 |

---

## 観点別の結果

### 1. HTTP タイムアウト
- AST 解析で全 `.py`（重複コピーの `threads-auto-post/` 配下含む）の `urlopen` / `requests.*` 呼び出しを検査 → **timeout 漏れ 0件**。
- Anthropic SDK（`generate_posts.py:77`）は SDK 既定の timeout（10分）＋自動リトライ2回。CIの投稿経路では使われていない。
- 残るハング要因は subprocess（#13）と、ワークフローの `timeout-minutes` 未設定（#3）。

### 2. 例外の握りつぶし・成功扱い
- 「`except Exception:` で pass / return / continue だけ」の箇所は全体で約280箇所（多い順: `generate_remix.py` 50、`auto_post.py` 48、`verify_system.py` 28）。大半は「JSON 1行が壊れていたら飛ばす」「失敗しても投稿は止めない」という意図的なフェイルオープンで、問題ではない。
- 表には**投稿・重複防止・検品の経路で、無効化されてもログに残らないもの**だけを挙げた（#1, #5, #6, #10, #11）。
- ワークフロー側の「失敗しても緑」は #4, #12。

### 3. GitHub Actions の無駄
- #2（保険cronの10分待機）が最大。実績: 直近20件の schedule 実行は全て約10分20秒（例: [run 40815715942](https://github.com/truthbodysalon-lab/threads-auto-post/actions/runs/40815715942)）。
- その他 #3, #14, #15, #16。`actions/setup-python` はどのワークフローもキャッシュ未使用だが、pip install しているのは `auto_post.yml`（PyNaCl のみ）と `hpb_availability.yml` だけなので効果は小さい。

### 4. LINE API
- #18 のとおり、broadcast 1箇所（顧客向け）のみ。通知用途の LINE 送信は無し。`threads_hpb_availability.py:37-38` にも「masa本人への通知はDiscordのみ（LINE禁止）」と明記されている。

### 5. テスト実行結果
実行方法: リポジトリを作業用ディレクトリへコピーし、ダミー値の `.env` を置き、`sitecustomize` で **全 TCP 接続を遮断**した状態で `python3 test_*.py` を個別実行（Python 3.13。pytest 無し）。外部 API・投稿は一切発生していない。

| テスト | 結果 | 備考 |
|---|---|---|
| test_buffer_overlap.py | ✗ 25/26 | #8（日付固定）。ネットワーク取得を試みてタイムアウト→ファイルのみで判定、のログあり |
| test_dead_openings_pdca.py | ✓ 19/19 | 実行で `weights_nagaoka.json` が書き換わる（#7） |
| test_dispatcher_gates.py | ✓ 30 | |
| test_inspector.py | ✓ | 直近50件の実投稿もNG 0件 |
| test_masa_abstract.py | ✓ 17 | `.env` が無いと import 時に落ちる |
| test_ops_deadman.py | ✓ 8 | |
| test_own_winners.py | ✓ 8 | |
| test_quota_watch.py | ✓ 9 | |
| test_selfwatch.py | ✓ 24 | `.env` 必須 |
| test_stepmail_gate.py | ✓ 15（9 skip） | node / line-stepmail-cloud が無いため skip |
| test_type_templates.py | ✓ | |
| test_uplink_hooks.py | ✓ 5 | |
| test_url_avoidance.py | ✗ 13/14 | #9（本番台帳依存）。API 取得を試みて遮断されたログあり |

---

## 優先して直す順番（提案）
1. **#1**（1行の修正で、後続アカウント全滅のリスクが消える）
2. #4（push失敗を赤にする）・#3（timeout-minutes）— ワークフローの数行追加で、気づけない停止をなくす
3. #5・#6（ログ1行ずつ追加。挙動は変えない）
4. #2（保険cronの待機短縮）— cron/keeper の挙動が変わるので、本人確認のうえで
5. テスト整備（#7〜#9）
