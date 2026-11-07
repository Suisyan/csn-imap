#!/bin/sh
set -eu
test "$(id -u)" -eq 0
helper=/usr/local/sbin/csn-export-credentials
rule=/etc/sudoers.d/csn-export-credentials
for path in "$helper" "$rule"; do
  if [ -e "$path" ] || [ -L "$path" ]; then
    echo "Existing file; stopped: $path"
    exit 1
  fi
done
/usr/sbin/visudo -c
install -o root -g root -m 700 /home/umeoka/.csn-export-credentials.py "$helper"
if ! echo "1ada55f457b88bb53f6b5b6fdeb43f0f6d87b6d3c728f7f8c7aef1d01728452b  $helper" | sha256sum -c -; then
  rm -f "$helper"
  exit 1
fi
tmp=$(mktemp /etc/sudoers.d/.csn-export-credentials.XXXXXXXX)
trap 'rm -f "$tmp"' EXIT
cat > "$tmp" <<'RULES'
umeoka ALL=(root) NOPASSWD: /usr/local/sbin/csn-export-credentials ""
RULES
chmod 0440 "$tmp"
/usr/sbin/visudo -cf "$tmp"
ln "$tmp" "$rule"
if /usr/sbin/visudo -c; then
  echo "Credential transfer helper registered (old). No credentials transferred yet."
else
  rm -f "$rule" "$helper"
  echo "Registration rolled back."
  exit 1
fi
