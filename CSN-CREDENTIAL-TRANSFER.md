# CSN認証情報の移送準備

2026-11-04。実パスワードはまだ読み取り・移送・設定していない。

## 準備済みの方法

- 新サーバー内で一時RSA鍵と公開証明書を生成。秘密鍵は権限600、親ディレクトリ700。秘密鍵は新サーバーから出さない。
- 旧サーバーの固定ファイル /home/csn/.fetchmailrc だけをOpenSSL CMSで暗号化する。
- 作業端末を経由するのは暗号文のみ。SSH経由で転送し、前後の暗号文SHA-256を確認する。
- 新サーバーで復号し、fetchmailのconfigdump出力をメモリ内で安全に解析。出力や例外詳細を表示しない。
- 7アカウントの一致、POP3S、証明書検証、CSN宛先固定、設定書式の往復一致を確認してから /home/fetchcsn/.fetchmailrc へ排他的に新規作成する。
- 実行用設定はfetchcsn:fetchcsn、600。既存ファイルがあれば停止する。
- サービスinactive・タイマーdisabled・起動許可ファイルなしを処理前後で確認する。POP接続は行わない。
- ASCII印字可能文字を対象として往復検証済み。空文字・制御文字・非ASCIIは変換せず停止して別途確認する。

## 検証

- 架空パスワードの全ASCII記号、引用符、バックスラッシュ、空白、識別子に似た文字列を実機fetchmailで再解析し一致確認。
- 空文字・制御文字・非ASCIIの拒否を確認。
- 旧Python 3.5で暗号化スクリプトの構文を確認。
- 実パスワードを含まないテスト文字列で旧OpenSSL暗号化と新OpenSSL復号の互換性を確認。

## これからの実施に必要な限定権限

- 旧 /usr/local/sbin/csn-export-credentials（引数なし、固定ファイルの暗号化のみ）
- 新 /usr/local/sbin/csn-import-credentials --check または --import

## 作業後の撤去対象

- 両サーバーの上記補助スクリプト・対応sudoersルール
- 旧 /home/umeoka/.csn-migration-recipient.pem と .csn-credentials-transfer.cms
- 新 /home/mailadmin/csn-migration/credentials-transfer/ 内の一時鍵・公開証明書・暗号文
- 作業端末に置いた公開証明書・暗号文
- 架空データ用の互換性テストファイルおよび検証用スクリプト

実パスワードや復号済み設定を会話、作業端末、Git、コマンド引数に保存しない。

## 2026-11-04: 11-account revision, awaiting root helper update

User authorized all 11 legacy accounts, including support, bank, error-mail and 2013.
The staged importer and template now require exactly these 11 accounts. Synthetic
native-parser tests pass, including missing/duplicate account rejection and printable
ASCII password escaping. The actual encrypted packet was decrypted only on the new
server, parsed and rendered in memory (parser temporary files in /dev/shm); all 11
passwords round-trip correctly and the fixed local SMTP delivery policy passes.
No POP connection was attempted and no live credential file was installed.
The service remains inactive and the timer disabled.

Root-owned installed helper/template still have the previous seven-account version.
User must execute `/home/mailadmin/csn-migration/update-credentials-11.py` with sudo
python3. SHA256: `5dcd918717db235b50c5f2eb5967bed5ed56249d630e895564386e7c6862a3f4`.
This installer pins old and new hashes, backs up all three changed files beneath
/var/backups, updates helper/template/manifest, and restores originals on update
failure. It does not change sudoers or activate fetching. After its success, run the
allowlisted importer --check, then --import, and verify disabled state again.
Historical seven-account installation hashes in earlier scripts remain unchanged.

## 2026-11-04 18:21 JST: credential import completed

User executed the 11-account update; backup:
`/var/backups/csn-credentials-11-before-20261104T092041Z`.
Root importer --check passed with the expected ciphertext SHA256
bf2cf7e523dc93f56126cf4c1436953e969f9944ee574d49be48eaf5f6bddbf4.
--import succeeded for all 11 accounts. Installed owner fetchcsn:fetchcsn, mode600;
backup `/var/backups/csn-credentials-before-20261104T092123Z`.
No plaintext was displayed and no POP connection attempted. New service inactive,
timer inactive/disabled, activation marker absent; Dovecot/Postfix/Rspamd active.

Follow-up finding on OLD server (no changes made): fetchmail-csn.service is
activating/auto-restart, Result=exit-code, ExecMainStatus=8, MainPID=0.
A separate csn fetchmail PID19021 PPID1 started 18:17:59 JST, matching the pidfile
modification time. Dovecot active. Unprivileged journal access returned no records.
fetchmail.log last modified18:22:18; procmail.log17:38:50. This is NOT evidence of
successful current retrieval. Investigate before cutover; do not kill/restart old
processes or activate new fetch without authorization. Root cause not established.

## Retention policy change prepared (awaiting user sudo execution)
User requested keep on both servers and reported manually adding keep/restarting old.
User confirmed old standalone options: uidl, fetchall, keep. Old service still
reported auto-restart/exit8 with separate csn fetchmail PID19021. Prepared
csn-set-keep.py on old /home/umeoka and new /home/mailadmin/csn-migration.
SHA256 dca8d44aef5d04c61978f48ad4c0854f2a21bed55aff213798975ee7de3cf989.
Native synthetic tests pass on both installed fetchmail versions. Script validates
11 effective keep/no-fetchall accounts and compares all other parsed settings,
backs up actual secret config under root-only /var/backups, writes atomically.
Old mode stops service, uses fetchmail --quit last to terminate surviving daemon,
waits up to30s then starts service. If survivor remains, leaves service stopped.
New mode requires inactive service/timer and absent marker; never starts fetching.
Not yet executed as root; installed configs and historical provisioning templates
have not been modified by this phase. Templates remain historical nokeep/fetchall;
do not redeploy them over the live retention configuration.

## 2026-11-04 18:35 JST: retention applied; old restart fix pending
User executed both modes. Old backup /var/backups/csn-keep-20261104T093523Z;
new backup /var/backups/csn-keep-20261104T093531Z. Both report all11 keep=yes,
fetchall=no and unrelated settings unchanged. New remains inactive.
Old stopped after systemctl stop because script used /usr/sbin/runuser, absent
on Ubuntu16 (actual executable /sbin/runuser). Verified old service inactive/dead,
MainPID0, separate csn fetchmail PID19021 remains. Fixed script to select existing
runuser path and uploaded to /home/umeoka/csn-set-keep.py; compiled on Ubuntu16
and verified chosen executable exists. User must rerun old mode with sudo.
No need rerun new mode. Service recovery and actual receipt remain unverified.

## 2026-11-04 18:37 JST: old service recovery verified
User reran corrected script; backup /var/backups/csn-keep-20261104T093705Z.
All11 accounts reported keep=yes, fetchall=no, other settings unchanged.
Independent SSH verification: old fetchmail-csn active/running, MainPID19950,
ExecMainStatus0; only one csn fetchmail process (PID19950), old PID19021 gone.
New fetchmail-csn.service inactive/static; timer inactive/disabled.
Configuration and process recovery complete. Actual end-to-end mail receipt was
not tested in this phase; new parallel collection has not been activated.
