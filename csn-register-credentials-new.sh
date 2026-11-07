#!/bin/sh
set -eu
test "$(id -u)" -eq 0
helper=/usr/local/sbin/csn-import-credentials
rule=/etc/sudoers.d/csn-import-credentials
for path in "$helper" "$rule"; do
  if [ -e "$path" ] || [ -L "$path" ]; then
    echo "Existing file; stopped: $path"
    exit 1
  fi
done
/usr/sbin/visudo -c
install -o root -g root -m 700 /home/mailadmin/csn-migration/csn-import-credentials.py "$helper"
if ! echo "febb426e32edd807cac37c29c8968c572597f221e2a5fbdd50f2a0fcb5ecc0f6  $helper" | sha256sum -c -; then
  rm -f "$helper"
  exit 1
fi
tmp=$(mktemp /etc/sudoers.d/.csn-import-credentials.XXXXXXXX)
trap 'rm -f "$tmp"' EXIT
cat > "$tmp" <<'RULES'
mailadmin ALL=(root) NOPASSWD: /usr/local/sbin/csn-import-credentials --check
mailadmin ALL=(root) NOPASSWD: /usr/local/sbin/csn-import-credentials --import
RULES
chmod 0440 "$tmp"
/usr/sbin/visudo -cf "$tmp"
ln "$tmp" "$rule"
if /usr/sbin/visudo -c; then
  echo "Credential transfer helper registered (new). No credentials transferred yet."
else
  rm -f "$rule" "$helper"
  echo "Registration rolled back."
  exit 1
fi
