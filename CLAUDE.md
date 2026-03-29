# Threads Auto Post - Claude Code ガイド

## プロジェクト概要
Threads（Meta）への自動投稿システム。2アカウント（truth, masa）の投稿を自動生成・スケジュール投稿する。

## モバイル／Claude Code からのフィードバック操作

ユーザーがスマホなどから投稿の確認・修正・フィードバックを行う場合、
`mobile_ops.py` を使って非インタラクティブに操作する。

### よく使うコマンド

```bash
# ステータス確認
python3 mobile_ops.py status

# 今日のキュー一覧
python3 mobile_ops.py queue truth --today
python3 mobile_ops.py queue masa --today

# 投稿の編集（インデックス指定）
python3 mobile_ops.py edit truth 5 "修正後のテキスト"

# 投稿の削除
python3 mobile_ops.py delete truth 5

# 投稿済み一覧（直近10件）
python3 mobile_ops.py posted truth 10

# 投稿の評価
python3 mobile_ops.py rate truth 3 good "いいコピー"
python3 mobile_ops.py rate truth 7 bad "硬すぎる"

# パターン重み調整（0.0〜3.0、0で無効化）
python3 mobile_ops.py weight aoi_style 1.5
python3 mobile_ops.py weight education 0.0

# NGワード管理
python3 mobile_ops.py ng add "使いたくない表現"
python3 mobile_ops.py ng remove "解除する表現"
python3 mobile_ops.py ng list

# 現在のフィードバック設定を表示
python3 mobile_ops.py feedback show

# メモを残す
python3 mobile_ops.py note "次回は柔らかめのトーンで"
```

### ユーザーへの対応ガイド

ユーザーが日本語で自然に指示を出した場合、上記コマンドに変換して実行する。

例:
- 「今日の投稿見せて」→ `queue truth --today` + `queue masa --today`
- 「5番目の投稿を直して」→ まず `queue` で確認 → `edit` で修正
- 「この表現やめて」→ `ng add`
- 「aoi_style 多すぎ」→ `weight aoi_style` を下げる
- 「最近の投稿どう？」→ `posted` + `status`

変更後は必ずコミット＆プッシュして、次回の自動投稿に反映させる。

## アーキテクチャ

- `generate_remix.py` - 投稿生成（feedback.json の重み・NG を参照）
- `auto_post.py` - 30分おき自動投稿（GitHub Actions）
- `insights.py` - Threads API からインサイト取得
- `analyze_and_tune.py` - フィードバック＋APIデータで重み自動調整
- `feedback_manager.py` - ターミナル向けインタラクティブUI
- `mobile_ops.py` - Claude Code／モバイル向け非インタラクティブ操作
- `cli.py` - マスターCLI
- `review.py` - 投稿レビュー

## データファイル

| ファイル | 内容 |
|---------|------|
| `log_{acct}.jsonl` | 生成済み投稿キュー |
| `log_{acct}_posted.jsonl` | 投稿済み記録 |
| `feedback.json` | 手動フィードバック設定 |
| `feedback.jsonl` | 投稿評価ログ |
| `weights_{acct}.json` | 自動チューニング済み重み |
| `insights_{acct}.json` | APIインサイトデータ |
