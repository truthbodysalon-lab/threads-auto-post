# Threads投稿停止 障害台帳

## 運用ルール（必読）
投稿停止障害を直したら、**同じcommitで (1) 再発を捕まえる検査を `verify_system.py` に追加 (2) この台帳に1行追記**する。
**検査IDの無い修正は完了扱いにしない。** 検査IDは `verify_system.py` の `CHECK_REGISTRY`（C番号→実コードのid接頭辞）に登録し、
C20（台帳整合）が「台帳の検査IDが実コードに実在するか」を毎日突合する（無ければFAIL）。
毎朝の検証D（daily-marketing-automation）は C18/C19/C20 のFAIL・WARNを最優先で原因特定→修正→本台帳追記まで行う。

表の書式: 行は `| 発生日(YYYY-MM-DD) | 症状 | 根本原因 | 修正commit | 再発防止の検査ID | 検知までの時間 |`。5列目のIDは `C16` のように `C+数字`。

## 台帳

| 発生日 | 症状（何時間・どのアカウント） | 根本原因 | 修正commit | 再発防止の検査ID | 検知までの時間 |
|---|---|---|---|---|---|
| 2026-10-21 | 【対象: Threads masa】前半50本のInstagramテーマが9〜12本（基準32本以上）。投稿数は変わらず主テーマだけが崩れる静かな不具合（10/17以降） | 10/17のuplink型作り直し(5704455a)で組合せ投稿5型のうち3型の1行目が同じ`{hook}`になり、1行目の種類が120→78に減少。台帳(segment_registry)の重複キー=1行目＋7日再投入禁止で補充候補(固定80+組合せ)が枯渇。さらに1日2回以上の再生成が別の候補を選び消費が倍増、組合せはランダム60回試行のため残りを取りこぼした | 本commit（masa_insta_content.py 7型・1行目を型ごとに別／generate_remix.py `_fill_insta_theme_posts` 当日登録分を優先・全件走査／verify閾値を32本へ） | C27 | 約4日（テスト失敗で発覚） |
| 2026-10-20 | 【対象: Threads truth/nagaoka/masa】毎朝生成するヒーロー投稿が実質0本しか投稿されない（10/14〜10/20の生成30本/アカに対し実投稿0本。10/13のみtruth6・masa7〜9本）。投稿は継続しておりstallは出ない静かな取りこぼし | ヒーローは朝6:47の生成バッチの後にpush→generate_remix再実行で追加バッチとして積まれる。get_next_postは全バッチを先着順に並べて先頭から選ぶため、ヒーローは最初のバッチ(約100本)の後ろに回り1日50本では届かない。加えて翌日にヒーローファイルが上書きされ未投稿分が消える | 本commit（auto_post.py `_hero_priority_order`・generate_remix.py `_load_hero_posts` 3日猶予+carry・hero_carry.py） | C26 | 約7日（オーナー指摘で発覚） |
| 2026-10-07 | 【対象: Threads全3アカ】10/03(全3アカ50本)・10/04(truth/masa32・nagaoka23)・10/06(truth41)の投稿台帳(log_*_posted.jsonl)が欠落し、重複ガードが効かず同文が6〜9時間後に再投稿（truth6組/nagaoka4組/masa2組）。9/25 21:04の並走で秒差二重投稿(truth2組・masa5組) | 台帳commit全失敗(10/03・d1de7c30で修正済み)と並走run(10/07修正済み)。台帳欠落を検知する検査自体が無かった | 本commit: verify_system.py C25 ledger_gap（API実投稿数と台帳行数の突合） | C25, C24 | 約4日（監査で発覚） |
| 2026-10-07 | 【対象: Instagram(masa リール)】`v0703_reel_04.mp4` が08:06:56と08:08:19に二重公開（media_id 18029372639916743/17887746264653239） | IG Reels が workflow_dispatch で2本同時起動。concurrencyで直列化されても後発runは起動時SHAをcheckoutするため先発のposted.jsonl更新を見ずalready_posted()が空振り（13本の投稿WFは実行前pullなし） | ig-autopost-cloud d91e3bf: 全13WFにmain同期step追加・already_posted()を対象日でも判定 | C24（IG版=ig_verify.py 4b同日重複検知） | 約1日（監査） |
| 2026-10-07 | 【対象: uplink(@uplink0117)】同日再投稿572件・全文一致10分以内4組(91〜122秒差) | テンプレ枯渇(ユニーク1行目203種)でDEDUP_LOOKBACK=80を超え、15回重複で最終フォールバックが重複チェック無しで出力(14日で1034件)。並走の証拠なし | ~/threads-uplink threads_auto_post.py: 最終フォールバックで本日投稿済みを避ける＋flock排他、status.pyに同日同文表示（※テンプレ素材追加と同文許容はmasa判断） | C24（uplink版=status.py「同日同文の重複」） | 長期（監査で発覚） |
| 2026-10-07 | masa「インスタで『機械が苦手』と…」が06:05に3秒差で2本（同日重複） | 常駐runのsingle-keeper判定で、in_progress照会が失敗→空を『旧run無し』と誤判定し2本目が起動、2本の常駐runが並走して同じ候補を同時投稿 | auto_post.yml: 照会失敗時は起動しない＋毎サイクル古いrunがいれば降りる | C24 | 約1日（ユーザー指摘） |
| 2026-10-06 | 12:34〜13:44の約70分、CIの台帳commit/pullが全失敗・uplink追加枠がJSONDecodeError停止（truth 13:05分3本が台帳欠落）／truth診断アンカー「頭痛改善の第一歩は…」が06:09と13:05に同日二重投稿 | ①手元から uplink_repost_pool.json をpush→常駐runの旧ymlはこのファイルをcommit対象外で未コミット保持→autostash復元が競合し未解決(U)のまま残り以後のcommit/pullが全滅 ②_shindan_anchor_okが台帳text(URL抽出後)に_is_shindanを掛けており当日同一テンプレ判定が8/18以降一度も効いていなかった（台帳失敗とは独立・8/27〜多数） | auto_post.yml safe_pull（U自動解消）＋auto_post.py 1文目一致判定（本commit） | C23 | 約70分（ログ）／二重投稿は約1.5か月未検知 |
| 2026-10-03 | 全アカウントの投稿ログpushが約16時間停滞（10/02 18:04以降。投稿自体は継続・ログ/重複ガード/集計が古い） | 常駐runのpull --rebaseが未コミット変更（unstaged）で毎回失敗しpush rejectedが続いた | auto_post.yml `pull --rebase --autostash`（本commit） | C21 | 約16時間（検証D） |
| 2026-10-02 | 全アカウント約5.5時間0本（6:06/9:43/15:17の3回しか起動せず） | GitHub Actionsのschedule(cron)が混雑時に1日3回へ間引かれた。runはsuccessで気付けず | ea3bdceb / 9a7e231f / 0c9457ca（常駐ループ化＋keeper連鎖＋ローカルwatchdogのdispatch起動） | C18, C19 | 約5.5時間（人が気付くまで） |
| 2026-09-29 | truth 約4時間0本 | 重複で弾かれたLINE候補を同一実行内で再選択し続け、3連続失敗で毎ラン諦めた（疑問符正規化の漏れも併発） | 01487898 / 392087dd（実行内ブロックリスト） | C17 | 約4時間 |
| 2026-09-23 | masa 約24時間0本（9/23 08:50〜） | `_listin_last_used_order` 未定義のNameErrorで処理全体が落ち、最後のmasaに到達しなかった（アカウント処理が非隔離） | a71f8111 / 12b2ede9 / 19bbc9e4（アカウント隔離＋次候補スモーク） | C16 | 約24時間 |
| 2026-09-21 | masa 44/50本（日次の取りこぼし） | 本文がURL抽出で全消滅する候補を再選択し続け、バースト予算(n+6)を消費 | 0fd6300e / 85d7ac09 | C8 | 翌朝の検証（約1日） |
| 2026-09-12 | masa AI活用系投稿が実質0本（日単位） | AI活用系テンプレの全期間dedup枯渇／LINEリストイン7日重複ガードのURL抽出後判定バグ | 9830abda / 1d6daef4 | C10 | 翌日の検証（約1日） |
| 2026-08-27 | CIが打切りでfailureとなり遅れバースト時に投稿・ログ消失 | auto_post CIのtimeout 15分が遅れ時のバースト投稿に不足 | a4ec9864 / 8785b27f | C10 | 不明（検証で発覚） |
| 2026-08-21 | nagaoka LINE導線が3日停止 | LINEリストインのプール枯渇→先頭固定→API重複→未投稿のまま枠消化 | 0f1c6b45（プール16本化・LRU・API重複事前回避） | C14 | 3日 |
| 2026-08-03 | masa cta_profile(診断導線)が約2週間0本（〜08-17） | 全期間dedupで全テンプレが枯渇（アンカー投稿の永久ブロック） | e818403c | C8 | 約2週間（検証D・08-17） |
| 2026-07-21 | 全アカウント47/50本（21:45以降に実行なし） | 終盤(21〜23時)にcronが発火せず取りこぼし（100%到達時刻が22時で遅すぎた） | 45a53051（21時100%按分・終盤cron追加） | C10 | 翌朝（約半日） |
| 2026-07-16 | truth 5本で停止／HPB CTAが同日重複ブロックを再選択し続け1日停止 | 通常投稿・CTA候補の無限再選択ループ＋ログ同期ラグ | 2427fd36 / ce465192 | C17 | 約1日 |
| 2026-07-10 | masa 50本を12:56に使い切り午後無音／truth LINE重複の無限再選択で16本のまま夜まで全停止 | ペースフロアバグ（固め打ち）＋LINE重複ループ | 5d98bb2c / d00d8885（watchdog常駐＋配分検査） | C10, C11 | 数時間〜半日 |
| 2026-06-26 | nagaoka 約1週間0本（〜07上旬） | テンプレ約9種が7日ガードで全滅し候補が尽きた | （auto_post.py「テンプレ約9種が7日ガードで全滅」の修正・commit特定不能） | C8 | 約1週間 |

## 検査IDの意味（verify_system.py）
- C8 `exec:daily50` / C10 `exec:pacing` … 昨日の実投稿数（外形API）が50本・40本を下回らないか
- C11 `exec:watchdog` … 見張り番(watchdog-ci)の死活
- C14 `exec:imagepost` … 画像投稿パイプラインの停止
- C16 `next_post` … get_next_post が3アカウントとも例外なく動く（NameError系の全停止）
- C17 `dup_reselect` … 重複で弾かれた候補を同一実行内で再選択しない
- C18 `exec:chain_gap` … 常駐連鎖(auto_post.yml)の24h最大空白（30分WARN/90分FAIL・in_progress0本はFAIL）
- C19 `exec:post_interval` … 昨日6-23時の最大投稿間隔（90分WARN/180分FAIL・外部ツール投稿は除外）
- C21 `exec:log_sync` … 投稿ログpushの停滞（7-23時に90分WARN/180分FAIL）
- C25 `ledger_gap` … 昨日のAPI実投稿数に対し台帳(log_*_posted.jsonl)が欠けていないか（0行/半数未満でFAIL）
- C24 `dup_sameday` … 同日に同じ投稿が2回以上（外形API）。常駐run並走の検知
- C23 `same_day_anchor` … 診断アンカーの同日同一テンプレ拒否＋auto_post.ymlのautostash競合自動解消
- C20 `ledger` … 本台帳の検査IDが実コードに実在するか

- 2026-10-04追記: 上記ログ停滞の副作用で10/03分の台帳(log_*_posted.jsonl)が欠落（API実投稿は50/55/50本）。10/04分は10:23のpushから復旧済み。C19 `exec:post_interval` は10/03を見るため、10/04 JSTの翌日判定でPASSに戻る見込み（欠落分は復元不能・修正は d1de7c30 で済み）。

- 2026-10-09追記: C19 `exec:post_interval:nagaoka` が10/08 06:00〜9:07の187分でFAIL。実投稿は53本(API)で停止ではなく、外部ツール投稿(約20本/日)がペース計算に算入され自前投稿の開始が遅れる設計のため（誤検知）。nagaokaのみ外部ツール分を含む全投稿で間隔判定するよう verify_system.py を修正。

- 2026-10-13追記: truth診断アンカー「自分の頭痛のタイプ、答えられますか？」が10/01・10/02・10/07・10/12に同日2回投稿（C24 dup_sameday FAIL）。原因=`_recent_api_firstlines` / `_recently_posted_on_threads` のAPI取得が limit=25 で、返信(コメント)も数えるため直近数時間分しか見えず、12h窓の重複ガードが9時間前(10/12 07:03→16:02)の投稿を検出できなかった。修正=両関数を limit=100 に拡大。検知は既存C24。

- 2026-10-20追記(ヒーロー照合キーの誤判定と取りこぼし): 「全てのヒーローを投稿する」修正(8171981)の後も、(1)照合キーが正規化1行目だったため通常テンプレの1行目がヒーローと同じだとテンプレがヒーロー扱いで先に出てヒーロー本体が捨てられる、(2)投稿台帳の `index` を「最終バッチの位置」と読む旧形式互換(`old_posted_keys`)が text 付きエントリーにも効いて、無関係な最終バッチ原稿(前半に差し込まれたヒーロー)が投稿済み扱いで飛ばされる(模擬でtruthのヒーロー4/17・nagaokaの1/19が50本内に出ず)、(3)masaのヒーローが検品(僕は始まり)・LINE言及キャップで投稿直前に黙って弾かれる、の3経路でヒーローが落ちた。対策=照合キーを本文全体のsha1(`generate_remix._hero_key`)にして台帳へ `hk` を併記(hk無しの旧エントリーは最初の段落で照合)・旧index互換は text 無しの真の旧エントリーに限定・最初の段落がヒーローと同じテンプレは未投稿ヒーローがある間は走査から外す・`hero_check.py` で保存前に落ちるヒーローを洗い出す(SKILL.mdのヒーロー手順に配線)。検査=verify C26(`hero_posted`)。
