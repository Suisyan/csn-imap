# CSNメール移行の作業記録

更新日: 2026-11-04（日本時間）

## 合意済みの範囲

- iCLUSTAをメール受信元として継続する。Xserverへの切り替えは行わない。
- ConoHa v2.0からv3.0へCSNの保存済みメールとIMAP環境を移行する。
- 現在承認済みなのは、本番Maildirとは別領域への初回コピーとその検証。
- 旧サーバーの取得停止、新サーバーでの取得開始、本番への反映は未承認・未実施。
- 新環境の「出品」条件は破棄せずx_Trashへ保存する。振り分け検証は後で行う。

## 旧サーバーで確認した構成

- 接続先: 118.27.32.100、SSHユーザー: umeoka
- Ubuntu 16、i686、Dovecot 2.2.22
- csn: UID/GID 1001、Maildir: /home/csn/Maildir、容量約2GB
- fetchmail-csn.service: csnで常駐、15秒間隔、Restart=on-failure
- iCLUSTAの7アカウントをPOP3S/995で取得、sslcertckあり
- procmailへ直接配送し、件名条件で既存の分類フォルダへ振り分ける
- keep指定なし。配送成功後に取得元のメールを削除する設定
- procmailの現行「出品」条件は/dev/nullへ配送。旧設定は変更していない

## 新サーバーで確認した構成

- 接続先: 163.44.111.155、SSHユーザー: mailadmin
- csn: UID/GID 1001、ホーム権限750、Maildir権限700
- /home/csn/Maildirには管理ファイル6個とnew内のメール1通が存在
- 不明な1通は保持対象。本番Maildirは変更していない

## 初回コピーの経路

1. 旧 /home/csn/Maildir/
2. 作業端末 .migration-data/csn-Maildir/
3. 新 /home/mailadmin/csn-migration/Maildir/

作業端末の.migration-dataおよび新サーバーのcsn-migrationは権限700。
コピーは削除オプションなし。稼働中のデータのため最終切り替え時に差分同期が必要。
初回ドライランは終了コード0、通常ファイル50,743個、ディレクトリ116個、2,021,405,471バイト。
実コピーとハッシュ検証の結果は .migration-data/staging-verification.json に記録する。
同ファイルが存在し、matchがtrueであることを確認するまでは完了と扱わない。

## 移行完了後の撤去対象

- 旧umeokaのauthorized_keysに追加した csn-migration-20261104 公開鍵
- 旧 /etc/sudoers.d/csn-migration-read
- 旧 /etc/sudoers.d/csn-migration-copy
- 作業端末 .migration-ssh/csn-v2-migration（秘密鍵）と関連公開鍵・known_hosts
- 作業端末と新サーバーの一時メールコピー（削除は別途承認後）

秘密鍵・認証情報・メールデータはGitや外部共有先へ登録しない。

## 初回コピー検証結果

- 旧サーバーからの取得、新サーバーへの転送とも終了コード0。
- 通常ファイル: 50726個、合計: 2020256143バイト、ルートを除くディレクトリ: 115個。
- cur/new内のメールファイル: 50528個（cur: 50397、new: 131）。
- 作業端末と新サーバー一時領域の全ファイル内容・相対パス・ディレクトリ構造のSHA-256照合は一致。
- SHA-256: `7283b2645058db47ca1bedfc1a486e538f659c635c96c961acd78e8f89692ed6`
- 旧サーバーは稼働継続中。取得時点が異なるドライランとは件数が異なる。初回コピーは稼働中データの取得であり、停止時点の一貫した最終スナップショットではない。
- 本番Maildirへの反映、既存1通との統合、CSNの新環境での取得開始は未実施。

## 検証用Maildirの準備完了

2026-11-04、ユーザー承認後に専用スクリプトを実行し、終了コード0を確認。

- 既存Maildirの退避先: /var/backups/csn-maildir-before-20261104T081223Z/Maildir
- 検証用Maildir: /home/csn/Maildir.migration
- 旧環境からのメール50,528通と、既存の1通を合わせて50,529通。
- 既存の1通は検証用Maildirの .Migration-Preserved に保持。
- 旧データの内容照合と検証用コピーの内容・構造照合に成功。
- 検証用コピーに限り、再生成用索引97ファイルを除外。UID管理・キーワード・購読情報は保持。
- 所有者csn:csn。Maildir.migrationの権限700。
- 検証用ツリーSHA-256: 7a8150da9c2832a7156a0eac88d7949a25268a48ee7d1a71f9df21bdd115fcfd
- 検証終了時点で本番Maildirの内容が変更されていないことを確認。
- 本番への切り替え、Dovecotによる検証用Mailboxの認識テスト、メール取得開始は未実施。
- 特殊名の192,779バイトのファイル（メールヘッダーらしい記述あり）も保持。取り扱いは別途調査。

追加された一時作業用ファイル（完了後の撤去対象）:
- 新 /usr/local/sbin/csn-prepare-maildir
- 新 /etc/sudoers.d/csn-maildir-prepare
- 新 /home/mailadmin/csn-migration/csn-prepare-maildir.py

準備スクリプトは既存の検証用Maildirがあれば停止する。再実行で上書きしない。

## Dovecot認識テスト完了

2026-11-04、専用スクリプトを実行し終了コード0を確認。

- 検証先: /home/csn/Maildir.migration（コマンド単位で保存先を上書き）。
- Dovecotが30メールボックス、50,529通を認識。
- Migration-Preservedの1通を認識。
- 検証前後でメール本文・フラグ・フォルダごとの件数が一致。
- 本番Maildirは検証前後で変更なし。
- 警告出力0行。Dovecotによる索引生成は検証用Maildir内のみ。
- 本番切り替え・CSNの新サーバーでの取得開始は未実施。
- 認識テストはメールストレージの確認であり、端末からのIMAPSログインや振り分け・新規受信の動作確認ではない。
- 詳細結果: 新 /var/backups/csn-maildir-recognition.json

追加された一時作業用ファイル（完了後の撤去対象）:
- 新 /usr/local/sbin/csn-check-maildir
- 新 /etc/sudoers.d/csn-maildir-check
- 新 /home/mailadmin/csn-migration/csn-check-maildir.py

## 方式Aの配送設定案を準備

ユーザー承認「Aですすめて」を受け、csn-delivery/に設定案を作成。
新サーバーの /home/mailadmin/csn-migration/delivery-draft/ に同じ案を配置。

- fetchmailのテンプレートを解析し、7アカウントのPOP3S/995・証明書検証・ローカルSMTP・csn宛先固定を確認。
- パスワードは識別子のみ。実値は取得・保存していない。
- Sieve案は実機のsievecで構文コンパイルに成功。振り分け実動作は未検証。
- systemdユニットは専用検索パスでの構文検査に成功。最初の通常検索パスでの警告は既存タイマーの閲覧権限に関するもの。
- fetchcsnは未作成。/etcへの導入、タイマーの有効化・起動、Sieveの有効化は未実施。
- 起動許可ファイル /etc/csn-mail/enable-fetch は存在しないことを確認。
- 詳細な変更点と導入前確認事項は csn-delivery/README.md を参照。

## CSN配送設定を停止状態で導入

2026-11-04、ユーザー承認と専用sudo権限登録後、--checkおよび--installが終了コード0。

- ユーザー管理ファイルの事前退避: /var/backups/csn-delivery-before-20261104T085009Z
- 追加ユーザー: fetchcsn（UID 999 / GID 988、nologin）。メール所有ユーザーcsnとは別。
- /home/fetchcsn: 700。パスワード未設定のテンプレート .fetchmailrc.template のみ配置。
- /etc/systemd/system/fetchmail-csn.service: loaded / inactive / static。
- /etc/systemd/system/fetchmail-csn.timer: loaded / inactive / disabled。
- /etc/csn-mail/csn.sieve.draftへSieve案を保管。本番Sieveは有効化していない。
- systemctl daemon-reloadのみ実施。サービスのstart/enable/restartは実施していない。
- 実パスワード、実行用.fetchmailrc、起動許可ファイルenable-fetchはいずれも未配置。
- 本番Maildirは操作していない。既存Dovecot/Postfix/Rspamd/Redisの稼働状態に変化なし。
- 専用スクリプトは既存ユーザー・導入先を検知すると停止する。再実行しない。

追加の一時権限・補助ファイル（移行後の撤去対象）:
- /usr/local/sbin/csn-install-inactive
- /etc/sudoers.d/csn-install-inactive
- /home/mailadmin/csn-migration/csn-install-inactive.py

以降の未実施事項: 認証情報の保護された設定、Sieve振り分けテスト、経路配送テスト、IMAPS認証確認、最終差分同期、本番Maildir切り替え、旧新取得切り替え。

## 認証情報の暗号化移送を準備（未実施）

2026-11-04、ユーザーの継続指示を受け準備。

- 新サーバー内だけで一時RSA鍵を生成。公開証明書のみ旧側へ配置。
- 実パスワードを含まないデータで旧OpenSSL→新OpenSSLの互換性を確認。
- 架空パスワードの全ASCII記号の往復確認に成功。非対応文字は停止して扱う。
- 旧側の固定設定暗号化、新側の固定7アカウント検査・停止状態の取り込みスクリプトを配置。
- 限定sudo権限登録はユーザー操作待ち。実パスワードの読み取り・転送・新設定への取り込みは未実施。
- 詳細はCSN-CREDENTIAL-TRANSFER.md。秘密鍵と将来の暗号文は同文書の撤去対象。

## 認証情報の事前検査でアカウント数の相違を検出

2026-11-04。旧設定を暗号化して新サーバーへ転送し、暗号文SHA-256が一致。
暗号文SHA-256: bf2cf7e523dc93f56126cf4c1436953e969f9944ee574d49be48eaf5f6bddbf4

取り込み前検査は「想定7アカウントに対し実設定11アカウント」のため停止。
追加のアカウント: support@coolingshop.net、bank@coolingshop.net、error-mail@coolingshop.net、2013@coolingshop.net。
実行用.fetchmailrcへの反映は未実施。POP接続・サービス起動は行っていない。
7アカウントの設定案・検証結果は旧設定全体を網羅していなかったため、追加4件の扱いを確認してから修正する。
平文は新サーバー内だけで一時的に解析し、会話・作業端末・ログには出していない。

## 並行受信前の配送確認（2026-11-04）
認証情報11件の導入と新旧keep/nofetchall変更は完了（詳細はCSN-CREDENTIAL-TRANSFER.md）。
新側Postfixのmydestinationにimap.umeoka.co.jp、mailbox_transportにDovecot LMTPを確認。
SMTP RCPT TO csn@imap.umeoka.co.jpは250。DATA送信は未実施。
Postfix check成功。mail_locationはmaildir:~/Maildir、Sieveは~/sieveと~/.dovecot.sieve。
新側取得は停止。過去メールの本番切り替え・Sieve有効化は未実施。
root読み取り用csn-delivery-preflight.pyを新側作業ディレクトリに配置。
ユーザーによるsudo実行待ち。メール本文・認証情報は表示せず保存先・件数・有効Sieveを確認する。

## 最新作業コピーを追加（2026-11-04、並行受信準備）
ユーザーのpreflight結果: 本番1通・1Mailbox、検証用50,529通・30Mailbox、
CSN Sieve無し、LMTP sieve有効、新取得停止・起動許可ファイル無し。
旧側稼働中の差分dry-run: 511ファイル/31,480,056bytes転送対象。
初回コピーは保持し、別ツリーへrsync --link-destで最新コピーを作成。
- local .migration-data/csn-Maildir-refresh
- new /home/mailadmin/csn-migration/Maildir-refresh
両ツリーの全内容・パスSHA256が一致:
351fa34b473f48a539a7e04d52a2986c2134371668c6a62ae43f394bb0294823
51,178 files, 115 directories excluding root, 2,027,021,461 bytes, 50,980 messages。
旧側は稼働中なので切替時点の最終同期ではない。コピー以降の差分が発生し得る。
新側csn-prepare-ready.pyを作業フォルダへ配置、構文確認済み。
SHA256 3c0950d543802a5dbd354fefefe6b1eedb8c0fb0ba584d2257b420f19e2efe02。
ユーザーsudo実行待ち。/home/csn/Maildir.readyを作成し最新50,980通と本番1通を
統合、Dovecotオフライン認識および本文・フラグ不変確認を行う。
既存Maildir/Maildir.migrationを上書きせず、取得・Sieve有効化・本番切替はしない。

## 2026-11-05: Maildir.ready認識成功・Sieve配送試験の準備
ユーザー実行結果: /home/csn/Maildir.ready 50,981通・30Mailbox、警告0、
本文照合成功、本番不変。backup /var/backups/csn-maildir-before-20261104T231121Z。
準備ツリーSHA256 f3de7131aed773dbf6530743eae326b4798471b0d2f3d3f1f008c637bcf19733。
本番切替なし。
新サーバー上でcsn-sieve-selftest.pyの22ケース×UTF-8/ISO-2022-JP=44件が成功。
sieve-testは-eなし、一時Maildir指定で非配送。実機本番設定でsievecコンパイルも成功。
結果 /home/mailadmin/csn-migration/delivery-draft/routing-test-report.json。
業務ルールの妥当性や旧nkfの全角半角正規化の完全再現を保証する検査ではない。

次の管理者実行用スクリプトを配置し構文確認のみ実施:
/home/mailadmin/csn-migration/csn-enable-sieve-test.py
SHA256 f551db40acdbb5340612794160e0ec1554b435386b639598b801bad7a8add21c。
実行すると本番CSN Maildir（現在1通）を退避し、CSN専用Sieveを有効化、
127.0.0.1のPostfixへCSN宛の架空メール3通を投入、INBOX/x_Trash/INBOX.Junkで
Message-ID照合する。既存1通の不変も確認。取得開始・Maildir.readyへの切替なし。
実行にはユーザーのsudo操作と、この変更範囲の承認が必要。まだ実行していない。
途中失敗時は有効Sieve/投入メールが残る可能性があるので再実行しない。
成功後もテスト3通は本番側に残り、後の切替時に既存1通とともに保持または
明示的に識別して扱う必要がある。旧prepare-readyの「本番1通」前提で再実行不可。

## 2026-11-05: 本番CSNローカル配送成功、単発取得準備
ユーザー実行結果: Sieve有効、通常→INBOX、出品→x_Trash、SPAM→INBOX.Junkの
3通ともSMTP受理・実ファイルでMessage-ID照合成功、既存1通不変。
backup /var/backups/csn-sieve-before-20261104T232348Z。
新側Maildirには既存1通＋架空テスト3通がある。取得・過去Maildir切替なし。

ユーザー継続指示により単発取得ヘルパーを準備:
/home/mailadmin/csn-migration/csn-fetch-once.py
SHA256 6607decf1284246e6d0ee9a1e67d6b6a2eaf3e4a7bfd9d8f4ab47e6a5e1e7761。
構文、実機serviceハッシュ一致、コマンドパスを確認。実行はまだ。
Root実行時は11件のkeep/nofetchall/uidl・配送固定先・Sieve・初期4通・空CSNキューを検査。
設定と既存UIDLをroot-only退避、起動許可を一時作成しsystemctl startで1回実行。
終了後許可を除去、取得件数・配送待ち・旧4通不変・終了状態を報告。
タイマー無効のまま、旧側取得は変更なし。本番Maildir切替なし。
テスト取得後は実メールが新側本番Maildirに増える。過去Maildirへの統合時には
旧側コピーと重複し得るため、Maildir.readyへの単純上書きや無条件併合は不可。

## 2026-11-05: dsync双方向同期の隔離検証
単発取得結果: 11件keep/nofetchall/uidl、実メール8通増、合計12通、
配送待ち0、既存4通不変、service正常終了inactive、timer無効。
backup /var/backups/csn-fetch-once-20261104T232732Z。
ユーザーは旧側停止不可、既読・移動・削除も同期したいと指定。
旧取得継続＋新側取得停止＋Dovecot双方向同期の構成を検討。

実機2.2.22/2.3.21上の管理ユーザー所有テストMaildirに架空メールのみ作成し、
既読/未読、移動、名前変更、双方向削除、直接Maildir配送、空フォルダ削除が成功。
本番CSNデータ・設定には同期を実行していない。root権限不要、サービス再起動なし。
詳細と制約はcsn-dsync-lab/README.md、結果ログは同ディレクトリ。
本番同期・定期取得開始・Maildir切替は未実施。同期競合と初期統合は別途検証必要。
ユーザーへの説明: 約5万通はConoHa間のみ同期され、iCLUSTAへ戻らない。

## 2026-11-05: 中断再開・初回統合の読み取り検査
ユーザーは同時端末操作の競合検査を後回しにし、受信継続と再実行に限定。
隔離通信を67,418bytesで切断し、dsyncは75で失敗を報告。途中新着追加後の
再実行・再々実行で本文ハッシュ一致、欠落・重複なし。通常転送中の新着追加も
追いかけ同期後に4通の本文ハッシュ一致。詳細はcsn-dsync-lab/resilience-results.log。

旧側は停止せず最新を別の作業ツリー.migration-data/csn-Maildir-sync-auditへ取得。
50,700files、2,016,046,252bytes。旧メール50,503通の比較用ハッシュ索引を
.migration-data/csn-source-fingerprints.jsonへ保存（600）。以前のコピーは保持。
現在の件数は旧コピーと異なるため、古いMaildir.readyをそのまま同期開始しない。

新側12通の比較用スクリプトを配置:
/home/mailadmin/csn-migration/csn-mail-fingerprints.py --new
ユーザーsudo実行待ち。メールは読み取りのみ、結果はmailadmin所有600の
/home/mailadmin/csn-migration/new-mail-fingerprints.jsonへ排他的に保存。
本文・アドレスは表示せず、Message-ID/元ヘッダー/本文/全体のハッシュとフォルダ等のみ。
--selftestで配送ヘッダー差、同一ID別本文、IDなし、不正アドレスを確認。
古いメールのheaderregistryによるIndexErrorを検出し、compat32で表記を維持して解析。
候補一致は削除の承認ではない。Message-IDのみで同一視しない。
次はユーザーの新側レポート生成後、SSHでハッシュレポートを取得し旧索引と比較。
本番同期・旧側停止・新側定期取得の開始はいずれも未実施。

## 2026-11-05: 新側12通の照合完了
ユーザーが読み取り専用スクリプトを実行。レポートを保護された作業端末へ取得。
旧50,503通に対し、新12通中6通でMessage-ID/元ヘッダー/本文hashが一意一致。
全ファイルhashはいずれも相違（配送ヘッダー等を含むため）。一致候補2通は
新側INBOX、旧側_03質問/_04支払い完了というフォルダ相違あり。
不一致6通は既知試験3通＋その他3通。他3通は本文単独でも旧コピーに一致なし。
来歴の個別確認はバックアップ記録照合が必要。メールの削除・移動・同期なし。
統合案をCSN-INITIAL-MERGE-PLAN.mdに記録。旧を現状の基準、新12通全件退避、
不一致は同期対象外の確認用フォルダに保護する案。実装・本番反映は未実施。

## 2026-11-05: 一方向取り込みの実装準備（限定sudo登録待ち）
旧版送信開始backupと新版backup -Rの両方で、受け側INBOXへ独自メールを
追加した後のexact backupに INBOX can't be deleted が再現。
GUIDは一致、受け側UIDNEXTが送信元より進んだ状態。バージョン差だけの問題とは
結論しない。旧データを強制再現するbackupでINBOX再作成が必要になる制約を確認。
新側本番12通はこのbackup先に使わない。新旧とも本番メール変更なし。

Maildir未作成の専用受け側に旧側からfixed backup -fを実行すると成功。
受け側を独自に変更しなければ、旧側新着+既読更新後の再コピーと再実行も成功。
結果: csn-initial-sync/follow-results.log（7通→8通→8通、旧側不変）。
一方向完了後にsyncへ切替、新側架空メール追加が旧側にも反映（9通一致）。
結果: csn-initial-sync/transition-results.log。

旧側登録用ファイル /home/umeoka/csn-initial-sync-setup/ に配置:
source.py, connector.py, source.conf, register-old.py。
register-old.py SHA256 36b2123830bc9218687b31a0a258fe5a577b0dc49096b2b4f81dffca020e08d0。
Ubuntu16実機で構文・全payload hash確認済み。
ユーザー sudo python3 /home/umeoka/csn-initial-sync-setup/register-old.py 待ち。
登録先 /usr/local/sbin/csn-initial-backup-source（root700、引数なし）;
/etc/csn-initial-source.conf と /usr/local/libexec/csn-initial-connector.py、限定sudoers。
helperはroot起動後runuserでcsnへ降格し、固定旧Maildirをbackup -fの送信元にする。
外部引数なし、双方向syncコマンドなし、stdoutはバイナリ同期プロトコルなので
直接端末で実行しない。サービス再起動不要。root registrar実行だけではコピーしない。

新側受け取り専用設定は /home/mailadmin/csn-migration/initial-sync-receiver/dovecot.conf。
Maildirは未作成・未オープン。初回コピー前のdoveadm mailbox list/create禁止。
次のローカルrunnerはSSH old sudo -n helperのstdout/stdinと、SSH new
'doveadm -c <上記conf> dsync-server'のstdin/stdoutを交差接続する。
旧sourceと新receiver両方のexitを確認し、秘密プロトコルをログ表示しない。
実メール約2GBの転送には時間がかかり得る。旧サービス稼働継続を確認する。
新側本番12通の全件退避・保護フォルダ準備・本番切替・双方向権限は未実施。

## 2026-11-05: 実コピー開始前に補助ディレクトリ権限で停止
ユーザーがold register-old.pyを実行済み。新側空き82GB、旧fetchmail/dovecot active。
実runner csn-initial-sync/transfer.pyを開始したが40秒でsource1/receiver75。
source_stream_bytes0、receiver_stream_bytes1264（handshakeのみ）、メール本体転送なし。
report .migration-data/initial-sync-20261105T003623Z.json。
原因: registrarのumask077下で/usr/local/libexecをmkdir755し、実際は700になった。
rootからcsnへ降格した後、connector.pyを読み込めず接続待ち30秒で停止。
実機statで/usr/local/libexec root:root700を確認。旧メールサービスは継続稼働。

限定修復スクリプトをold /home/umeoka/csn-initial-sync-setup/fix-access.pyへ配置、
Ubuntu16構文確認済み。ユーザーsudo実行待ち。
ディレクトリがroot所有、今回のconnector.pyしかなく、内容hashとmode755が
期待どおりの場合だけ親ディレクトリを755へ変更し、csnでファイル読込・構文検証。
コピーや再起動は行わない。ローカルのregister-old.pyは再発防止修正済みだが、
インストール済みの環境へregister-old.pyを再実行しない。
修復後は受け側Maildirの有無と空状態を再確認してから同runnerを再実行する。

## 2026-11-05: 権限修復後、実データコピーが75で中断
ユーザーがfix-access.pyを実行しcsnからのconnector読込成功。
transfer.py再開: report .migration-data/initial-sync-20261105T004059Z.json。
source75/receiver75、10秒、source_stream573041bytes、receiver_stream3015bytes。
専用受け側24通/562954bytesまで到達。本番Maildirへの切替なし。
receiver stderr空、source stderrは固定exit75のみで詳細診断は現行helper内で破棄。
終了75の根本原因は未確定。推測で転送条件を変更しない。

旧側へ診断追加だけの更新を配置:
/home/umeoka/csn-initial-sync-setup/enable-source-diagnostics.py
payload source-diagnostic.py。Ubuntu16で両方の構文確認済み。
更新は旧helperhash/newpayloadhashを固定し、root-only退避後に原子的置換。
sudoers/送信元/backupコマンド/受け側/サービス設定は変更しない。
失敗時のみDovecotの通常診断最大64KiBをstderrへ出し、local runnerが600の
.migration-data/initial-source-<stamp>.stderrへ捕捉する。生の診断は会話へ出さず
アドレス・メールボックス等を伏せて読む。同期プロトコルや本文はログしない。
ユーザーsudo実行待ち。現行source.pyは旧版としてローカルに残し、
source-diagnostic.pyが更新版。register-old.pyを再実行しない。

## 2026-11-05: source75原因判明、取り込み専用索引への変更準備
ユーザーが診断更新を実行済み。backup /var/backups/csn-source-helper-before-20261105T004603Z。
再実行report .migration-data/initial-sync-20261105T004637Z.json。
旧Dovecot: transaction log view seq2291..2293のmiddle seq2292欠落、
Failed to read transaction log for mailbox INBOX。受け側エラーなし。
旧の本番索引削除・修復・サービス停止は行わない。

INDEX=<別ディレクトリ>を取り込み専用設定にだけ付ける方法を隔離環境で検証。
csn-initial-sync/shadow-index-results.logでsource0/receiver0、9通一致。
UIDVALIDITY/UIDNEXT/mailboxGUID、メールUID/GUID/永続flags一致を確認。
一時Recentのみ索引間で異なるため永続flags比較から除外。
公式: https://doc.dovecot.org/2.3/configuration_manual/mail_location/ (INDEX設定)
UID対応表dovecot-uidlist/keywordsは保持、CONTROL設定は変更しない。

old /home/umeoka/csn-initial-sync-setup/use-separate-index.py 配置、Ubuntu16構文確認。
ユーザーsudo実行待ち。現行/etc/csn-initial-source.confのhash固定、root-only退避後、
mail_locationのINDEXを/home/csn/.csn-initial-backup-indexへ指定する。
新ディレクトリcsn:csn700。通常/etc/dovecot設定・元Maildir索引に変更なし。
設定検証・失敗時復元あり。スクリプト実行だけではコピーや再起動を行わない。
修正後transfer.pyを再実行し、旧サービス継続と転送結果を確認する。

## 2026-11-05: 専用索引によるコピー進行中、新側保護スクリプト準備
ユーザーがuse-separate-index.py実行済み。
索引 /home/csn/.csn-initial-backup-index、設定退避 /var/backups/csn-source-index-before-20261105T005052Z。
transfer.pyを再開、unified exec session43659（引き続き稼働中）。
予定report .migration-data/initial-sync-20261105T005118Z.json。
最終確認時90秒、source_stream89,499,351bytes。従来の索引エラー箇所を通過。
まだ完了扱いにしない。次ターンでwrite_stdin session43659を確認し、停止済みなら
reportファイルとprivate stderrを確認する。旧側取得は継続、新側本番切替なし。

コピー中に新側12通の保護用scriptを配置:
/home/mailadmin/csn-migration/preserve-new.py
SHA256 581cf6f8ed3efd7453f75cd993cf602f1f40db032fe5473273ab7369728777f6。
ユーザーsudo実行待ち。現在12通のファイル識別子とrawhashを固定し、相違があれば停止。
全12通をroot-only /var/backups/csn-new-side-before-<stamp>/Maildirへcp-aで退避・照合。
不一致6通を/home/csn/Maildir.new-side-preservedへコピー、csn:csn700、元データは残す。
新規保護Maildirは初期同期のreceiverとは別で、まだ同期やIMAP公開には含めない。
pre-Sieve state.jsonの元1通hashと突合し、元1通・既知テスト3通・他2通を分類する。
本番Maildir切替、取得開始、source停止、コピー途中receiver変更はいずれもなし。
スクリプト失敗時は出力と作成状態を確認し、上書き再実行しない。

## 2026-11-05: 新側12通の全件退避と不一致6通の保護完了
ユーザーがpreserve-new.pyを実行し成功。
退避 /var/backups/csn-new-side-before-20261105T005414Z（全12通）。
別保存先 /home/csn/Maildir.new-side-preserved（6通）。
既存の元1通を過去backupのhashで識別、既知テスト3通、その他不一致2通。
本番12通不変、移動ではなくコピー、同期対象には未追加。
source転送session43659は継続中、180秒時点167,344,103bytes。完了未確認。
