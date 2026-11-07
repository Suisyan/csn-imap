# DRAFT: syntax checked separately; routing tests are still required.
# Mailbox names are UTF-8 IMAP names, not modified-UTF-7 directory names.
require ["fileinto", "mailbox", "regex"];

# New spam uses the existing Rspamd/IMAPSieve integration.
# The historical "spam" mailbox is retained without moving its old messages.
if anyof (
    header :contains "X-Spam-Status" "Yes",
    header :is "X-Rspamd-Action" ["add header", "reject"],
    header :contains "Subject" "[SPAM]"
) {
    fileinto :create "INBOX.Junk";
    stop;
}

# Approved change: preserve listing/relisting messages rather than discard them.
if anyof (
    header :contains "Subject" ["出品", "取り消し"],
    header :regex "Subject" "Yahoo!オークション[[:space:]　]*-[[:space:]　]*終了"
) {
    fileinto :create "x_Trash";
    stop;
}

if header :contains "Subject" "注文確認" {
    fileinto :create "_01注文確認";
    stop;
}

# Whitespace handling and the old nkf normalization need sample-based review.
if header :regex "Subject" "クーリングショップ[[:space:]　]*ドット[[:space:]　]*ネットへの" {
    fileinto :create "_03質問";
    stop;
}

# Preserve the existing compound condition until its business intent is reviewed.
if allof (
    header :contains "Subject" "評価",
    header :contains "Subject" ["支払い完了", "支払い手続き完了"]
) {
    fileinto :create "_04支払い完了";
    stop;
}

if header :contains "Subject" "評価" {
    fileinto :create "_05評価";
    stop;
}
if header :contains "Subject" "お客様情報変更" {
    fileinto :create "_06お客様情報";
    stop;
}
if header :contains "Subject" "特別会員資料請求" {
    fileinto :create "_07資料請求";
    stop;
}
if header :contains "Subject" "注文番号" {
    fileinto :create "_08直接注文";
    stop;
}
if header :contains "Subject" "お問合せ" {
    fileinto :create "_09直接問合せ";
    stop;
}
if header :contains "Subject" "適合確認" {
    fileinto :create "_10適合確認";
    stop;
}
if header :contains "Subject" "発送案内" {
    fileinto :create "_11発送案内";
    stop;
}
if header :contains "Subject" "不具合" {
    fileinto :create "_12不具合";
    stop;
}
keep;
