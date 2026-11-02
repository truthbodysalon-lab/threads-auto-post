# ダイエットThreads(@truth_nagaoka_daietto) API自動投稿 runbook

Grok Botのブラウザ投稿(1日10本: 7,8,10,12,14,16,17,19,21,22時)とは別に、APIで1日5本を追加する(9,11,13,15,20時 JST)。

## 仕組み
- `diet_threads.py` が1枠ぶんを処理: 直近投稿の取得(Grok分を含む) → 45分間隔の確認 → 案内(@886oidmv)の可否判定 → 生成 → 検品 → 類似チェック → 2段階投稿 → `log_diet_posted.jsonl` に記録。
- 起動は `schedule_table.json` の `diet-threads-0900/1100/1300/1500/2000` を中央ディスパッチャが `workflow_dispatch` で行う(`.github/workflows/diet_threads.yml` にcronは無い)。
- ルール正本は `diet_threads_rules.md`、栄養の根拠は `diet_threads_facts.md`(どちらも生成プロンプトに全文入る)。変更はこの2ファイルを直す。
- トークン未設定(`THREADS_ACCESS_TOKEN_DIET` が空)の間は「トークン未設定のためスキップ」と出して正常終了する。

## 認可手順(初回だけ)
1. Meta for Developers のアプリ(1418049959563052)で「Threadsテスター」に `@truth_nagaoka_daietto` を追加する(アプリの役割 > 役割 > テスターを追加 > Threadsテスター)。
2. `@truth_nagaoka_daietto` でThreadsアプリにログインし、設定 > アカウント > ウェブサイトのアクセス許可(招待)から招待を承認する。
3. Macで `cd ~/threads-auto-post && python auth.py diet` を実行し、ブラウザで `@truth_nagaoka_daietto` にログインして「許可」する。
   - 別アカウントでログインすると「想定と不一致」で保存せず中断する(誤保存ガード)。
   - 成功すると `.env` に `THREADS_ACCESS_TOKEN_DIET` / `THREADS_USER_ID_DIET` が保存され、GitHub Secrets にも自動登録される(`GH_PAT` / `GH_REPO` が `.env` にある場合)。
   - 自動登録されなかった場合は、リポジトリの Settings > Secrets で同名の2つを手動登録する。
4. 動作確認: Actions の「Diet Threads Post」を手動実行(slot=15:00、dry_run=1)。生成文がログに出るだけで投稿はされない。dry_run を空にすると実際に投稿する。
5. 以降は60日ごとの更新が必要。`token_manager.py --sync-secrets` が常駐ループから10分ごとに実行され、dietのトークンも自動更新してSecretsへ書き戻す(`auto_post.yml` の .env に DIET の2行を追加済み)。

## 止め方
- 全部止める: `schedule_table.json` の `diet-threads-*` を `enabled: false` にする。
- 1枠だけ止める: 該当entryを `enabled: false`。
- 緊急: Secrets の `THREADS_ACCESS_TOKEN_DIET` を空にする(投稿せず正常終了する)。

## 見送りになる条件(ログに理由が出る。Discord通知は生成失敗・投稿失敗・取得失敗のみ)
- 同じ枠が本日投稿済み / 本日のAPI投稿が5本に達した
- 直前の投稿(Grok分を含む)から45分未満(5分以内の不足なら待つ。それ以上は見送り)
- 直近50件のどれかと文字バイグラムJaccardが0.5以上(作り直し1回、それでも類似なら見送り)
- 3回生成しても検品に通らない

## 公式LINE案内(@886oidmv)
- 案内は 15:00 / 20:00 枠だけ。本日の案内つき投稿(Grokの分を含む。`/me/threads` から数える)が2本なら不可、直前の投稿に入っていれば不可。
- 固定文は一字一句 `diet_threads_rules.md` / `diet_threads.py` の `CTA_TEXT`。

## テスト
`python3 test_diet_threads.py`(ネットワーク不要)。生成だけ試す: `DRY_RUN=1 python3 diet_threads.py --slot 15:00`(`.env` に `ANTHROPIC_API_KEY` が必要。Threadsへは一切アクセスしない)。
