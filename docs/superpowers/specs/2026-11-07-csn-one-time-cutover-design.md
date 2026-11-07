# CSNメール 一回限りの切替設計

## 方針

日次standby同期は採用しない。旧サーバーを切替日まで正本として使い、
「稼働中の事前同期 → 旧停止 → 最終差分同期 → 新Maildir切替」で移行する。

## 切替前の完成条件

- 新`csn`で`imap.umeoka.co.jp`へIMAPログインできる。
- 新Dovecot、TLS、Sieve、Postfix LMTP、11アカウントのfetchmail設定が検証済み。
- 新fetchmailは停止、timerは無効、`/etc/csn-mail/enable-fetch`は未作成。
- standby timerは無効、`/etc/csn-standby-sync/armed`は未作成。
- 旧fetchmailとDovecotは稼働を継続する。
- 既存の固定旧→新dsync経路と隔離Maildirを使える。
- Maildir切替前バックアップとロールバック手順を用意する。

## 切替当日

1. 全端末でCSNメール操作を停止する。
2. 旧稼働中に旧→隔離Maildirの事前同期を1回行う。
3. 旧fetchmail、続いて旧Dovecotを停止して旧Maildirを凍結する。
4. 最終差分同期を1回行う。
5. メール件数、フォルダ数、所有権、Dovecot認識を確認する。
6. 新Dovecotを短時間停止し、現在の`/home/csn/Maildir`をバックアップして、
   検証済みMaildirを原子的に切り替える。
7. 新Dovecotを起動し、`csn`で`imap.umeoka.co.jp`へログイン確認する。
8. `/etc/csn-mail/enable-fetch`を作成し、`fetchmail-csn.timer`を有効化する。
9. 1回の受信・振り分けを確認する。
10. 全端末の接続先を旧IPから`imap.umeoka.co.jp`へ変更する。

## 合格条件

- 新Dovecotで全フォルダを認識し、旧凍結時点の件数と一致する。
- 新fetchmail timerが15秒間隔で動作し、取得・振り分けエラーがない。
- 旧fetchmailと旧Dovecotは停止している。
- standby timerは無効、`armed`は存在しない。
- 端末から送受信、既読、移動、削除を確認できる。

## ロールバック

切替確認前に問題があれば、新fetchmail timerを停止し、enable-fetchを削除する。
新Dovecotを止めて元の新Maildirを戻し、旧Dovecotと旧fetchmailを再開する。
端末接続先も旧IPへ戻す。旧Maildirは切替後最低7日間削除しない。

## 後片付け

7日以上安定後、明示承認を得てstandby用timer、armed関連、監査ヘルパー、
一時sudoers、旧側dsync鍵・ヘルパーを撤去する。メールデータは別承認なしに削除しない。
