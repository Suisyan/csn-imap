# ConoHa IMAPメールサーバー移行

## プロジェクト概要

ConoHa上で稼働しているCSNメール環境を、旧サーバー（Ubuntu 16）から新サーバー（Ubuntu 24）へ移行するための設定、検証スクリプト、手順書を管理するリポジトリです。

メールの取得元は引き続きiCLUSTAを使用し、保存済みメール、Dovecot IMAP、fetchmail、振り分け設定を新環境へ移行します。

## 現在の状態

- 旧サーバーが本番環境として稼働中
- 新サーバーは`imap.umeoka.co.jp`でIMAPログイン確認済み
- 新サーバーのfetchmailと移行用同期タイマーは停止・無効
- 切替ツールの導入と事前検査は完了
- 本番Maildirの切替と新サーバーでの受信開始は未実施

## 運用ルール

- 設定変更、サービスの停止・再起動、本番切替、ファイル削除は事前承認を得る。
- 切替完了までは旧サーバーを正とし、新サーバー側で既読・移動・削除操作を行わない。
- 本番作業前にバックアップと稼働状態を確認し、実行後も結果を検証する。
- パスワード、秘密鍵、メール本文、実メールデータをGitへ登録しない。
- `.migration-data/`、`.migration-ssh/`、秘密鍵、ログは`.gitignore`で管理対象外とする。
- 本番切替は手順書の順序とSTOP確認を守り、途中の手順を省略しない。
- 切替後も旧Maildirと切替前バックアップを7日以上保持する。

## 主な資料

- [移行作業の記録](CSN-MIGRATION.md)
- [本番切替手順](docs/runbooks/csn-one-time-cutover.md)
- [CSN配送設定](csn-delivery/README.md)
- [認証情報移送手順](CSN-CREDENTIAL-TRANSFER.md)

## 主なディレクトリ

- `csn-cutover/`：本番Maildir切替・ロールバックツール
- `csn-delivery/`：fetchmail、Sieve、systemd設定
- `csn-initial-sync/`：旧サーバーからの初回・差分同期
- `csn-standby-sync/`：検証済みの待機系同期関連ツール
- `docs/`：設計、計画、運用手順書
