# CSN One-Time Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare a short, reversible one-time CSN Maildir cutover from the old server to `imap.umeoka.co.jp` without enabling daily standby synchronization.

**Architecture:** Reuse the existing fixed old-to-isolated dsync and the verified fetchmail/Sieve setup. Add one root-owned cutover helper for candidate creation, atomic Maildir swap, and rollback; keep actual cutover behind a later explicit approval.

**Tech Stack:** Python 3.12 standard library, Dovecot/doveadm, rsync, systemd, unittest.

---

### Task 1: Read-only readiness check

**Files:**
- No file changes.

- [x] Confirm old fetchmail and Dovecot are active.
- [x] Confirm new fetchmail service/timer and standby timer are inactive/disabled; both enable markers are absent.
- [x] Confirm `doveadm user csn` returns UID/GID 1001, home `/home/csn`, and `maildir:~/Maildir`.
- [x] Confirm TLS for `imap.umeoka.co.jp:993` validates for that hostname.
- [x] Require one successful manual IMAP login as `csn`; never record the password.
- [x] Confirm at least 10 GiB free on NEW and that the isolated receiver is present and owned by `mailadmin` without symlinks.

Expected: no service, timer, marker, Maildir, or mail changes.

### Task 2: Implement the fixed cutover helper with TDD

**Files:**
- Create: `csn-cutover/cutover.py`
- Create: `csn-cutover/test_cutover.py`

- [x] Write failing tests for exact CLI actions:

```text
--preflight
--prepare --confirm-old-is-frozen
--activate --confirm-maildir-swap
--rollback --confirm-restore-old-new-maildir
```

Tests must prove that missing/extra arguments fail; `--preflight` is read-only;
prepare accepts only the fixed isolated receiver; activate requires NEW fetchmail
inactive/disabled, standby disabled, both markers absent, and a verified ready
tree; rollback disables NEW fetchmail before restoring. Mock all command and
filesystem boundaries.

- [x] Run RED:

```bash
python3 -m unittest discover -s csn-cutover -p 'test_cutover.py'
```

Expected: failure because `cutover.py` is absent.

- [x] Implement fixed constants only:

```python
SOURCE = '/home/mailadmin/csn-migration/initial-sync-receiver/Maildir'
LIVE = '/home/csn/Maildir'
READY = '/home/csn/Maildir.cutover-ready'
BACKUP_ROOT = '/var/backups'
FETCH_MARKER = '/etc/csn-mail/enable-fetch'
STANDBY_MARKER = '/etc/csn-standby-sync/armed'
STATE = '/var/lib/csn-cutover/active-backup.json'
```

`--prepare` copies SOURCE to a newly created READY with csn:csn ownership,
preserves mail files and `dovecot-uidlist`, `dovecot-uidvalidity*`,
`dovecot-keywords`, and `subscriptions`, excludes Dovecot index/cache/log files,
then validates message/folder counts using a temporary standalone config.

`--activate` creates a mode-0700 backup directory, stops NEW Dovecot, atomically
renames LIVE into the backup and READY to LIVE, starts Dovecot, then verifies
`doveadm user csn` and mailbox status. It does not create FETCH_MARKER or enable
any timer. After verification it atomically records the exact backup path in
root-owned mode-0600 STATE. On failure before Dovecot verification, it restores
LIVE, restarts Dovecot, and does not create STATE.

`--rollback` first disables/stops `fetchmail-csn.timer`, removes FETCH_MARKER if
present, stops Dovecot, moves the cutover LIVE into the recorded backup, restores
the prior LIVE selected only from STATE, starts Dovecot, and clears STATE. It
rejects missing, malformed, symlinked, or non-root-owned STATE and leaves
old-server services untouched.

- [x] Run GREEN and syntax checks:

```bash
python3 -m unittest discover -s csn-cutover -p 'test_cutover.py'
python3 -m py_compile csn-cutover/cutover.py csn-cutover/test_cutover.py
```

- [x] Record SHA-256 hashes. The workspace is not a Git repository; do not initialize one.

### Task 3: Write the concise operator runbook

**Files:**
- Create: `docs/runbooks/csn-one-time-cutover.md`

- [x] Limit the runbook to these sections:

```text
Before maintenance
Pre-sync while OLD is live
Freeze OLD
Final sync
Prepare and activate NEW
Login and enable NEW fetchmail
Client change
Rollback
Seven-day cleanup
```

- [x] Include exact commands but no passwords, private keys, message identifiers, or mail content.
- [x] Put a bold STOP checkpoint before stopping OLD, swapping Maildir, enabling NEW fetchmail, and cleanup.
- [x] State that clients change from `118.27.32.100` to `imap.umeoka.co.jp`.

### Task 4: Local review and staging only

**Files:**
- Review Task 2 and Task 3 files.

- [x] Run all cutover tests; abandoned standby-sync tests are outside this one-time cutover path.
- [x] Review fixed paths, no-follow checks, rollback order, service scope, output redaction, and confirmation tokens.
- [x] Copy reviewed helper and hash to a private mailadmin staging directory on NEW.
- [x] Present exact root installation commands for the user to run.
- [x] Run only `--preflight` after installation.

Expected end state: old services still active; new fetchmail and standby remain
disabled; no marker created; production Maildir unchanged; actual cutover not
started.

### Task 5: Future cutover window — separate approval required

- [ ] Run one pre-sync while OLD remains live.
- [ ] Obtain explicit approval, then stop OLD fetchmail and OLD Dovecot.
- [ ] Run exactly one final differential sync.
- [ ] Run `--prepare`, inspect counts, and obtain explicit Maildir-swap approval.
- [ ] Run `--activate`, test IMAP login, then obtain explicit fetch-activation approval.
- [ ] Create FETCH_MARKER and enable/start `fetchmail-csn.timer`.
- [ ] Verify one fetch/routing cycle; change all clients to `imap.umeoka.co.jp`.
- [ ] Keep OLD Maildir intact for at least seven days.
