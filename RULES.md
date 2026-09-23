# threads-auto-post 運用ルール

## 複数対象ループ処理の単一障害点禁止（2026-09-23〜）

複数アカウント/複数対象をループ処理する自動化(watchdog_ci.py 等)は、1対象の失敗・例外・タイムアウトが他対象の処理を止めてはならない。ループ本体は必ずtry/exceptで個別に隔離し、失敗は記録して次へ継続する（2026-09-23 nagaokaの自己修復タイムアウトがmasa監視を巻き込みクラッシュした再発防止）。

- 背景: 2026-09-23 18:09 JST、`watchdog_ci.py` の `main()` 内 `for acct in ACCTS:` ループで、nagaokaの自己修復 `subprocess.run(...auto_post.py, acct..., timeout=780)` が13分でタイムアウトし `subprocess.TimeoutExpired` が送出された。これがどこにもcatchされずmain()全体がクラッシュし、後続のmasaのチェック・修復に到達できなかった。結果、masaの投稿停止(18本/目標43)が長時間放置された。
- 対応: `watchdog_ci.py` の `for acct in ACCTS:` ループ本体を try/except で包み、`subprocess.TimeoutExpired` を明示的にcatchして通知記録（`timeout_{acct}`、1日1回/acct抑制）した上で次のacctへ`continue`する。その他の想定外例外も広くcatchして次のacctへ継続する。stateの保存(`STATE.write_text`)はループの外で必ず実行する構造は維持。
- 今後、新規に複数対象ループを持つ自動化スクリプトを書く/改修する際は、同様に「1対象の失敗が全体を止めない」構造を必須とする。
