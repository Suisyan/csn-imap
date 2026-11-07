# CSNメール 1回切替手順

## Before maintenance

NEWで実行する。

```bash
sudo /usr/local/sbin/csn-cutover --preflight
systemctl is-active fetchmail-csn.service csn-standby-sync.service
systemctl is-enabled fetchmail-csn.timer csn-standby-sync.timer
df -h /home/csn
```

期待値は、preflight成功、両serviceは`inactive`、両timerは`disabled`。メールソフトで`imap.umeoka.co.jp`へCSNアカウントでログインできることも確認する。

## Pre-sync while OLD is live

LOCALのこの作業フォルダで実行する。

```bash
python3 csn-initial-sync/transfer.py
```

結果の`complete`が`true`、`source_exit`と`receiver_exit`がともに`0`であることを確認する。

## Freeze OLD

**STOP: ここから停止時間。実行前に切替開始の承認を得る。**

OLDで実行する。

```bash
sudo systemctl stop fetchmail-csn.service dovecot.service
systemctl is-active fetchmail-csn.service dovecot.service
```

両方`inactive`を確認する。失敗時は先へ進まない。

## Final sync

LOCALで1回だけ実行する。

```bash
python3 csn-initial-sync/transfer.py
```

再び`complete=true`、両exit=`0`を確認する。不成功ならOLDの2サービスを再開して延期する。

## Prepare and activate NEW

NEWで実行する。

```bash
sudo /usr/local/sbin/csn-cutover --prepare --confirm-old-is-frozen
```

出力のメール件数を記録する。

**STOP: Maildirを入れ替える。件数確認後、実行承認を得る。**

```bash
sudo /usr/local/sbin/csn-cutover --activate --confirm-maildir-swap
systemctl is-active dovecot.service
sudo doveadm user csn
sudo doveadm mailbox status -u csn messages '*'
```

## Login and enable NEW fetchmail

まずメールソフトで`imap.umeoka.co.jp`へログインし、フォルダとメールを確認する。

**STOP: 新サーバーの受信を開始する。ログイン確認後、実行承認を得る。**

NEWで実行する。

```bash
sudo install -o root -g root -m 600 /dev/null /etc/csn-mail/enable-fetch
sudo systemctl enable --now fetchmail-csn.timer
sudo systemctl start fetchmail-csn.service
systemctl is-enabled fetchmail-csn.timer
systemctl status --no-pager fetchmail-csn.service fetchmail-csn.timer
```

1回の取得・振り分けが正常であることを確認する。

## Client change

全PC・スマートフォンの受信サーバーを`118.27.32.100`から`imap.umeoka.co.jp`へ変更する。送信設定は今回変更しない。

## Rollback

NEWで先に実行する。

```bash
sudo /usr/local/sbin/csn-cutover --rollback --confirm-restore-old-new-maildir
```

次にOLDで実行する。

```bash
sudo systemctl start dovecot.service fetchmail-csn.service
systemctl is-active dovecot.service fetchmail-csn.service
```

クライアントの受信先を`118.27.32.100`へ戻す。

## Seven-day cleanup

旧MaildirとNEWの切替前バックアップは7日以上保持する。

**STOP: 7日間の安定稼働を確認し、別途承認を得るまで、旧メール・鍵・sudoers・バックアップを削除しない。**
