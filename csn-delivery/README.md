# 現在の導入状態（2026-11-04）

停止状態での導入が完了。fetchcsnを追加し、serviceはinactive/static、timerはinactive/disabled。
/home/fetchcsn/.fetchmailrc.templateと/etc/csn-mail/csn.sieve.draftを配置。
実パスワード・実行用.fetchmailrc・起動許可ファイルは未配置。Sieve未有効化。
本番Maildirへの反映、取得開始、サービス再起動は未実施。
以下は設定案作成時の説明であり、「今回しない操作」は案作成段階の範囲を示す。

# CSN配送設定案（未導入・未稼働）

## 作成したファイル

| ファイル | 導入する場合の配置先 | 現在の扱い |
|---|---|---|
| fetchmailrc.template | /home/fetchcsn/.fetchmailrc | 架空のパスワード識別子のみ。実値入力後も承認まで実行しない |
| fetchmail-csn.service | /etc/systemd/system/fetchmail-csn.service | 作業領域に置くのみ |
| fetchmail-csn.timer | /etc/systemd/system/fetchmail-csn.timer | 作業領域に置くのみ |
| csn.sieve | /home/csn/sieve/csn.sieve | 振り分け動作未検証。アクティブ化しない |
| syntax-only-dovecot.conf | 導入しない | 作業領域でのコンパイル確認専用 |

## 方針と旧環境との差分

- 7アカウントはiCLUSTAを継続。Postfixの宛先をcsn@imap.umeoka.co.jpに固定。
- fetchmailを専用ユーザーfetchcsnで実行し、Postfix→Rspamd→LMTP→Sieveへ配送する。
- 旧常駐方式から単発サービス＋タイマーへ変更。完了から15秒後に次回実行するため、実行開始の間隔は「処理時間＋約15秒」。
- UIDL管理ファイルは/home/fetchcsn/.fetchids。既存umeokaのユーザー・設定・UIDL記録とは分離する。
- 取得元の保持方針は旧環境のkeepなしに合わせ、nokeepを明示。これは成功時にiCLUSTA側から削除する設定である。開始時はこの点を明示して承認を得る。
- SMTPへ渡した時点でPostfixのキューに責任が移る。最終配送成功は別途確認する。
- 新規の迷惑メールはINBOX.Junk。旧spam内の過去メールはそのまま保持。
- 出品関連はx_Trashへ保存。Sieveにはdiscard/rejectアクションを含めない。
- [SPAM]は文字列として照合する。旧正規表現の誤った角括弧表現は再現しない。
- _04支払い完了の「評価」AND（支払い完了OR支払い手続き完了）は元の条件を保持。修正は業務上の意図を確認してから行う。
- nkfの文字幅・空白正規化は完全再現していない。MIME件名、空白、全角/半角と各条件の優先順位は有効化前に検証する。

## 今回しない操作

ユーザー追加、/etcへの導入、systemctl daemon-reload/enable/start、Sieveの有効化、旧側停止、POP接続、メール送信、パスワードの実値取得、本番Maildirの変更は行わない。

## 構文確認

新サーバーの作業領域で、テンプレートだけにfetchmail --configdumpを適用する。出力にパスワードが含まれ得るため、標準出力は表示せず終了状態だけを記録する。実パスワード投入後に同じ出力をログへ残してはならない。

sievec -c syntax-only-dovecot.conf csn.sieve csn.svbin により、専用の最小設定で作業領域へコンパイルする。systemd-analyze verifyでユニットを検証する。構文成功を、振り分け・認証・配送成功と扱わない。

## 導入前の確認

1. fetchcsnの追加と/home/fetchcsn（700）、設定・UIDL記録（600）の所有権を確認する。
2. 7アカウントのパスワードは旧設定から保護された経路で移送するか、新サーバーのターミナルで入力する。チャットには送らない。
3. 既存の/home/csn/sieveと/home/csn/.dovecot.sieveを調査・退避する。現在の設定を上書きしない。
4. 新規SMTP受付・Rspamd判定・LMTP配送の経路と、Sieveの実際の振り分けを検証する。
5. csnのIMAPS認証と、端末が使う接続先・証明書を確認する。
6. 最終同期、既存の1通の保持、旧側の取得停止を確認してから切り替える。
7. /etc/csn-mail/enable-fetchは起動許可のためのファイル。明示的な切り替え承認までは作成しない。
