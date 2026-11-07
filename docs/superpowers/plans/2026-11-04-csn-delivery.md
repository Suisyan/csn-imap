# CSN Delivery Preparation Implementation Plan

**Goal:** 方式Aの設定一式を未導入・未稼働の状態で作成し、実機バージョンで構文を確認する。

**Architecture:** 専用ユーザーによるPOP3S取得をローカルPostfixへ渡し、既存RspamdとDovecot LMTPを使用する。CSN専用Sieveで既存分類フォルダとINBOX.Junkへ配送する。

**Tech Stack:** fetchmail 6.4、systemd、Postfix 3.8、Rspamd 3.8、Dovecot 2.3/Pigeonhole。

## 範囲

新規ファイルはcsn-delivery/内のテンプレート・systemdユニット・Sieve案・README。サーバーでは/home/mailadmin/csn-migration/delivery-draft/への配置のみ。導入・有効化・認証情報の実値取得は行わない。

## Task 1: 設定案作成

- [x] csn-delivery/fetchmailrc.template: 7アカウント、POP3S、証明書確認、SMTP宛先csn固定。パスワードは識別子のみ。
- [x] csn-delivery/fetchmail-csn.service: oneshot、fetchcsn、終了コード1正常、実行前にenable-fetch存在確認。
- [x] csn-delivery/fetchmail-csn.timer: OnBootSec=1min、OnUnitInactiveSec=15s、AccuracySec=1s。導入時も停止・無効を維持。
- [x] csn-delivery/csn.sieve: 既存分類をUTF-8名へ対応付け、出品はx_Trash、新規迷惑メールはINBOX.Junk。
- [x] csn-delivery/README.md: 変更差分、保留の動作確認、秘密情報投入方法と切り替え前提を記載。

## Task 2: 未稼働状態での構文確認

- [x] 新サーバーの専用作業領域に設定案を配置。
- [x] fetchmail --configdump -f fetchmailrc.template を子プロセスで実行し、出力は表示せず終了状態を確認する。テンプレートの架空パスワードだけを使用。POPサーバーへの接続はしない。
- [x] 最小の検証専用Dovecot設定をsievec -cへ渡し、csn.sieveを作業領域にコンパイルする。本番Sieveには書き込まない。
- [x] systemd-analyze verifyでサービス・タイマー構文を確認。ユニットのロード・起動・enableはしない。
- [x] 動作検証未実施であることを明記し、導入対象と影響を報告する。

## 次の段階（今回の実行範囲外）

fetchcsnの追加、実パスワードの保護された移送、既存CSNのSieve調査・退避、配送テスト、振り分けテスト、IMAP認証確認、最終差分同期と旧新取得切り替えは、具体的な手順を提示して承認後に行う。
