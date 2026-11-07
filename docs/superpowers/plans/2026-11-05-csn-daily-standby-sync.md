# CSN Daily Standby Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the old CSN server authoritative while copying its complete Dovecot state to the new server once per day, then leave a separately approved, verified path for final cutover.

**Architecture:** The new server runs `doveadm backup -R`, so the remote old Maildir is the source and the local new Maildir is the exact destination. A dedicated SSH key can invoke only a fixed old-server dsync helper. The daily systemd timer remains separate from the disabled new fetchmail timer, and production Maildir activation is a distinct approval-gated operation.

**Tech Stack:** Python 3 standard library, Dovecot 2.2.22/2.3.21 dsync, OpenSSH forced commands, systemd oneshot service/timer, `unittest`, `systemd-analyze`, `visudo`.

**Repository note:** `/home/suisyan/Documents/conoha-imap` is not a Git worktree. Where the normal workflow would commit, record `sha256sum` output in the implementation report instead. Do not initialize a repository as part of this work.

---

## File map

- Create `csn-standby-sync/runner.py`: fixed-scope new-server production runner; no command-line arguments.
- Create `csn-standby-sync/test_runner.py`: unit tests for direction, preconditions, locking, exit handling, and secret-free reporting.
- Create `csn-standby-sync/old_source.py`: fixed old-server `dsync-server` launcher; no command-line arguments.
- Create `csn-standby-sync/old_source.conf`: standalone old Dovecot source configuration with a separate index path.
- Create `csn-standby-sync/register_old.py`: idempotent root installer for the old helper, config, sudo rule, and forced SSH key.
- Create `csn-standby-sync/register_new.py`: idempotent root installer for the new runner, private key, pinned host key, systemd units, and state directories; leaves timer disabled.
- Create `csn-standby-sync/csn-standby-sync.service`: one-shot service.
- Create `csn-standby-sync/csn-standby-sync.timer`: daily 03:30 Asia/Tokyo timer.
- Create `csn-standby-sync/isolated_e2e.py`: two-pass server-to-server test targeting only the isolated receiver.
- Create `csn-standby-sync/prepare_live.py`: builds `/home/csn/Maildir.standby-ready` without switching production.
- Create `csn-standby-sync/activate_live.py`: approval-gated new-side Maildir swap, Dovecot restart, one sync, and timer enable.
- Create `csn-standby-sync/rollback_live.py`: disables standby timer and restores the pre-activation new Maildir.
- Create `csn-standby-sync/audit_fetchmail_uidl.py`: read-only cutover audit; never displays credentials or message bodies.
- Create `csn-standby-sync/README.md`: operating, verification, rollback, and final-cutover runbook.
- Modify `CSN-MIGRATION.md`: append dated implementation evidence only after each verified phase.

### Task 1: Lock the one-way direction and runner safety contract

**Files:**
- Create: `csn-standby-sync/test_runner.py`
- Create: `csn-standby-sync/runner.py`

- [x] **Step 1: Write failing unit tests for the exact command and exit policy**

```python
# csn-standby-sync/test_runner.py
import importlib.util
import pathlib
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("standby_runner", HERE / "runner.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

class RunnerTests(unittest.TestCase):
    def test_command_is_reverse_backup_not_sync(self):
        command = runner.build_command()
        self.assertEqual(command[0:3], ["/usr/bin/doveadm", "backup", "-R"])
        self.assertNotIn("sync", command)
        self.assertIn("-u", command)
        self.assertIn("csn", command)
        self.assertIn("-l", command)
        self.assertIn("60", command)
        self.assertIn("-T", command)
        self.assertIn("600", command)
        self.assertIn("/usr/bin/ssh", command)

    def test_only_zero_is_complete(self):
        self.assertTrue(runner.is_complete(0))
        self.assertFalse(runner.is_complete(2))
        self.assertFalse(runner.is_complete(75))

    def test_report_does_not_include_stderr(self):
        report = runner.make_report(75, 12.5, 1234, "sensitive protocol text")
        self.assertEqual(report["exit_code"], 75)
        self.assertEqual(report["stderr_bytes"], 1234)
        self.assertNotIn("stderr", report)
        self.assertNotIn("sensitive protocol text", repr(report))

    def test_preflight_rejects_active_fetchmail(self):
        with mock.patch.object(runner, "unit_state", return_value="active"):
            with self.assertRaisesRegex(RuntimeError, "fetchmail-csn.service"):
                runner.verify_fetchmail_disabled()

if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the tests and confirm they fail before implementation**

Run: `python3 -m unittest -v csn-standby-sync/test_runner.py`

Expected: import or attribute failures for `runner.py`, `build_command`, `is_complete`, `make_report`, or `verify_fetchmail_disabled`.

- [x] **Step 3: Implement the minimal fixed-scope runner**

`runner.py` must define these constants and functions exactly:

```python
DOVEADM = "/usr/bin/doveadm"
SSH = "/usr/bin/ssh"
KEY = "/etc/csn-standby-sync/id_ed25519"
KNOWN_HOSTS = "/etc/csn-standby-sync/known_hosts"
REMOTE = "umeoka@118.27.32.100"
LOCK = "/run/lock/csn-standby-sync.lock"
REPORT_DIR = "/var/lib/csn-standby-sync/reports"

def build_command():
    return [
        DOVEADM, "backup", "-R", "-u", "csn", "-l", "60", "-T", "600",
        SSH, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
        "-o", "ConnectTimeout=20", "-i", KEY, REMOTE,
    ]

def is_complete(exit_code):
    return exit_code == 0
```

The complete implementation must also:

- reject every command-line argument;
- acquire `LOCK` with `fcntl.LOCK_EX | fcntl.LOCK_NB`;
- require `fetchmail-csn.service` to be `inactive` and `fetchmail-csn.timer` to be `disabled`;
- verify `doveadm user csn` returns UID/GID 1001 and `/home/csn`;
- run the command with a 1,200-second process timeout;
- write raw stderr only to a root-owned mode-0600 file under `REPORT_DIR`;
- write a mode-0600 JSON report containing UTC start/end, elapsed seconds, exit code, stderr byte count, and `complete`;
- print only the report path, exit code, elapsed seconds, and complete boolean to stdout;
- return success only for dsync exit code 0; treat exit code 2 as incomplete/failure.

- [x] **Step 4: Run unit tests and static checks**

Run:

```bash
python3 -m unittest -v csn-standby-sync/test_runner.py
python3 -m py_compile csn-standby-sync/runner.py csn-standby-sync/test_runner.py
```

Expected: all tests pass and `py_compile` exits 0.

- [x] **Step 5: Record checksums**

Run: `sha256sum csn-standby-sync/runner.py csn-standby-sync/test_runner.py`

Expected: two SHA-256 lines copied to the phase report; no Git commit is attempted.

### Task 2: Build the restricted old-server endpoint

**Files:**
- Create: `csn-standby-sync/old_source.py`
- Create: `csn-standby-sync/old_source.conf`
- Create: `csn-standby-sync/register_old.py`

- [x] **Step 1: Write the source helper with a no-argument contract**

The helper must reject arguments, confirm effective UID 0, and then execute only:

```python
os.execv("/sbin/runuser", [
    "/sbin/runuser", "-u", "csn", "--",
    "/usr/bin/doveadm", "-c", "/etc/csn-standby-source.conf", "dsync-server",
])
```

It must not print status text to stdout because stdout is the dsync protocol.

- [x] **Step 2: Add the standalone old-source config**

Create `old_source.conf` with fixed values:

```conf
protocols =
ssl = no
log_path = /dev/stderr
info_log_path = /dev/stderr
mail_home = /home/csn
mail_location = maildir:/home/csn/Maildir:INDEX=/home/csn/.csn-standby-index
mail_uid = 1001
mail_gid = 1001
mail_plugins =
first_valid_uid = 1
namespace inbox {
  inbox = yes
  separator = /
  prefix =
}
```

- [x] **Step 3: Implement an idempotent old registrar**

`register_old.py` must:

- accept exactly one `--public-key-file` path;
- require root, validate the file is one ED25519 public key, and reject private-key markers;
- install `/usr/local/sbin/csn-standby-dsync-source` as root:root mode 0700;
- install `/etc/csn-standby-source.conf` as root:root mode 0644;
- create `/home/csn/.csn-standby-index` as csn:csn mode 0700;
- install `/etc/sudoers.d/csn-standby-source` as root:root mode 0440 with only
  `umeoka ALL=(root) NOPASSWD: /usr/local/sbin/csn-standby-dsync-source`;
- add one marker-delimited line to `/home/umeoka/.ssh/authorized_keys` containing
  `restrict,command="sudo -n /usr/local/sbin/csn-standby-dsync-source"` followed by the validated key;
- preserve unrelated authorized keys and make a timestamped root-only backup before replacement;
- run `visudo -cf` on a temporary sudoers file and then on `/etc/sudoers`;
- validate the Dovecot config with `doveconf -c /etc/csn-standby-source.conf -n`;
- report installed paths and hashes only; never print key material.

- [x] **Step 4: Test the registrar in a temporary filesystem model**

Use `unittest.mock` for ownership-changing and external commands. Verify rerunning produces one forced-key entry, preserves unrelated keys, rejects RSA keys, rejects extra lines, and never places `sync` or arbitrary shell arguments in the helper.

Run: `python3 -m unittest -v csn-standby-sync/test_register_old.py`

Expected: all tests pass.

- [x] **Step 5: Stage and syntax-check on the old host without installing**

Copy only to `/home/umeoka/csn-standby-sync-setup/`, then run:

```bash
python3 -m py_compile /home/umeoka/csn-standby-sync-setup/old_source.py /home/umeoka/csn-standby-sync-setup/register_old.py
doveconf -c /home/umeoka/csn-standby-sync-setup/old_source.conf -n
```

Expected: both commands exit 0; production files and services remain unchanged.

### Task 3: Build the new-server inactive installer and timer

**Files:**
- Create: `csn-standby-sync/csn-standby-sync.service`
- Create: `csn-standby-sync/csn-standby-sync.timer`
- Create: `csn-standby-sync/register_new.py`
- Test: `csn-standby-sync/test_register_new.py`

- [x] **Step 1: Write systemd units**

```ini
# csn-standby-sync.service
[Unit]
Description=CSN old-to-new Dovecot standby synchronization
After=network-online.target dovecot.service
Wants=network-online.target
ConditionPathExists=/etc/csn-standby-sync/armed

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/csn-standby-sync
UMask=0077
PrivateTmp=yes
NoNewPrivileges=yes
TimeoutStartSec=25min
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
```

```ini
# csn-standby-sync.timer
[Unit]
Description=Daily CSN old-to-new standby synchronization

[Timer]
OnCalendar=*-*-* 03:30:00 Asia/Tokyo
Persistent=true
AccuracySec=1min
Unit=csn-standby-sync.service

[Install]
WantedBy=timers.target
```

- [x] **Step 2: Write failing installer tests**

Tests must assert the installer leaves the timer disabled/inactive, does not create `armed`, installs key mode 0600, known_hosts mode 0644, runner mode 0700, report directory mode 0700, and rejects a private key whose public key does not match the expected public key file.

Run: `python3 -m unittest -v csn-standby-sync/test_register_new.py`

Expected: FAIL before `register_new.py` is implemented.

- [x] **Step 3: Implement `register_new.py`**

The root-only registrar must accept exactly `--private-key`, `--public-key`, and `--known-hosts`; validate the key pair with `ssh-keygen -y`; verify the pinned old ED25519 fingerprint equals `SHA256:Do8bGq2Tf7JeXRncu8lLe6KjMLavohLWuWSN+A5+3/s`; install files atomically; run `systemd-analyze verify`; call `systemctl daemon-reload`; and explicitly run `systemctl disable --now csn-standby-sync.timer`. It must not create `/etc/csn-standby-sync/armed` and must not run the service.

- [x] **Step 4: Run tests and unit verification**

Run:

```bash
python3 -m unittest -v csn-standby-sync/test_register_new.py
systemd-analyze verify csn-standby-sync/csn-standby-sync.service csn-standby-sync/csn-standby-sync.timer
python3 -m py_compile csn-standby-sync/register_new.py
```

Expected: tests pass; unit verification and compilation exit 0.

### Task 4: Install the restricted path without enabling synchronization

**Files:**
- Create during execution: `/home/mailadmin/csn-migration/standby-sync-staging/`
- Create during execution: `/home/umeoka/csn-standby-sync-setup/`

- [x] **Step 1: Generate the dedicated key in new-side staging**

Run as `mailadmin`:

```bash
install -d -m 700 /home/mailadmin/csn-migration/standby-sync-staging
ssh-keygen -q -t ed25519 -N '' -C csn-standby-sync-20261105 -f /home/mailadmin/csn-migration/standby-sync-staging/id_ed25519
chmod 600 /home/mailadmin/csn-migration/standby-sync-staging/id_ed25519
chmod 644 /home/mailadmin/csn-migration/standby-sync-staging/id_ed25519.pub
```

Expected: one new dedicated pair; never print or copy the private key to the old host or chat.

- [x] **Step 2: Obtain explicit approval for both root installers**

Present exact effects: old forced key/helper/sudo rule, new runner/key/units, daemon-reload, timer forced disabled. State that no mail copy and no service restart occurs.

- [x] **Step 3: Run the old installer after approval**

Transfer only the public key and installer payload. User runs the exact root registrar command shown after staging. Verify `visudo -c`, forced-key entry count 1, source config parse, old fetchmail active, and old Dovecot active.

- [x] **Step 4: Run the new installer after approval**

Before running the registrar, copy only `register_new.py`, `runner.py`, and the two
systemd unit payloads into a dedicated root-owned mode-0700 payload directory. The
registrar intentionally rejects payloads beside a mailadmin-owned script. Verify the
four root-owned copies against the reviewed SHA-256 values, then run the registrar
from that root-owned directory. Keep the private/public key and known_hosts inputs in
the original mode-0700 staging directory; do not duplicate the private key.

User runs the exact root registrar command shown after the root-owned payload copy.
Verify:

```bash
systemctl is-enabled csn-standby-sync.timer
systemctl is-active csn-standby-sync.timer
test ! -e /etc/csn-standby-sync/armed
systemctl is-active fetchmail-csn.service
systemctl is-enabled fetchmail-csn.timer
```

Expected: standby timer `disabled` and `inactive`; armed marker absent; fetchmail service `inactive`; fetchmail timer `disabled`.

### Task 5: Prove server-to-server synchronization against the isolated receiver

**Files:**
- Create: `csn-standby-sync/isolated_e2e.py`
- Create during execution: `/home/mailadmin/csn-migration/standby-e2e/`

- [ ] **Step 1: Implement the isolated test driver**

The driver must use the existing isolated receiver config and execute the equivalent of:

```bash
doveadm -c /home/mailadmin/csn-migration/initial-sync-receiver/dovecot.conf \
  backup -R -l 60 -T 600 \
  /usr/bin/ssh -o BatchMode=yes -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/home/mailadmin/csn-migration/standby-sync-staging/known_hosts \
  -i /home/mailadmin/csn-migration/standby-sync-staging/id_ed25519 \
  umeoka@118.27.32.100
```

It must store stderr mode 0600, print only exit code/elapsed time, and reject any target path other than the fixed isolated receiver.

- [ ] **Step 2: Run the first isolated pass**

Expected: exit 0. Confirm old fetchmail/Dovecot remain active, new fetchmail remains inactive/disabled, and production `/home/csn/Maildir` count is unchanged.

If the unprivileged mailadmin account cannot traverse `/home/csn`, prove production
isolation by validating that the fixed standalone config has `mail_home` and
`mail_location` only below `initial-sync-receiver`, contains no includes or variable
expansion, and the exact command supplies that config with `-c`. Also require the
installed production standby service to remain inactive, its timer disabled/inactive,
`armed` absent, and fetchmail inactive/disabled before and after both passes. Do not
grant new production-Maildir read privileges solely for this test.

- [ ] **Step 3: Run the second isolated pass**

Expected: exit 0 again, no duplicate GUIDs, no warning lines, and Dovecot mailbox status succeeds using the isolated config without `-u`.

- [ ] **Step 4: Test forced-command rejection**

Attempt SSH with an appended harmless command such as `true`. Expected: the forced dsync helper runs instead of `true`; the client fails its non-dsync protocol negotiation; no shell output and no file creation occur on the old host.

### Task 6: Prepare the future live Maildir without switching it

**Files:**
- Create: `csn-standby-sync/prepare_live.py`
- Test: `csn-standby-sync/test_prepare_live.py`

- [ ] **Step 1: Write tests for the preparation invariants**

Tests must require new fetchmail inactive/disabled, preserved Maildir present, isolated source owned by mailadmin, `/home/csn/Maildir.standby-ready` absent, and at least 20 GiB free. Tests must prove the script never renames or removes `/home/csn/Maildir`, never stops a service, and never enables a timer.

- [ ] **Step 2: Implement root-only preparation**

`prepare_live.py` must copy the isolated receiver to a newly created `/home/csn/Maildir.standby-ready`, exclude Dovecot index/cache/log files but preserve `dovecot-uidlist`, `dovecot-uidvalidity*`, and `dovecot-keywords`, set csn:csn ownership and directory/file permissions appropriate for the existing Maildir, then verify file count and `doveadm` mailbox status through a temporary standalone config pointing only to the ready tree.

It must write a root-only JSON report and leave `activated=false`.

- [ ] **Step 3: Run tests and a dry preparation in `/tmp`**

Run: `python3 -m unittest -v csn-standby-sync/test_prepare_live.py`

Expected: all tests pass; no server service or production Maildir changes.

- [ ] **Step 4: Request approval and build the real ready tree**

State that this creates a new ~2GB tree only and does not switch/restart. After approval, run once and verify ownership, count, 30 mailboxes, report `activated=false`, old services active, new fetchmail stopped.

### Task 7: Activate the new server as a non-receiving standby

**Files:**
- Create: `csn-standby-sync/activate_live.py`
- Create: `csn-standby-sync/rollback_live.py`
- Test: `csn-standby-sync/test_activation.py`

- [ ] **Step 1: Write activation and rollback state-machine tests**

Test these states exactly: `prepared`, `dovecot-stopped`, `maildir-switched`, `dovecot-started`, `single-sync-passed`, `timer-enabled`. Inject failure after each state and assert rollback never touches the old server, never starts new fetchmail, and restores the prior new Maildir for failures before timer enable.

- [ ] **Step 2: Implement activation with explicit confirmation token**

The root-only script must require the literal argument `--confirm-new-dovecot-restart-and-maildir-swap`. It must recheck hashes and preconditions, create `/var/backups/csn-standby-activation-<UTC>/`, stop only new Dovecot, atomically rename current `/home/csn/Maildir` into that backup, rename `Maildir.standby-ready` to `/home/csn/Maildir`, start new Dovecot, run auth/user/mailbox checks, run one production old→new sync, create `/etc/csn-standby-sync/armed`, enable/start only `csn-standby-sync.timer`, and confirm new fetchmail remains inactive/disabled.

Any nonzero dsync result must leave the timer disabled and remove `armed`.

- [ ] **Step 3: Implement rollback**

Require `--confirm-disable-standby-and-restore-new-maildir`. Disable/stop only the standby timer, remove `armed`, stop new Dovecot, move the synchronized Maildir into the activation backup, restore the original new Maildir, start Dovecot, and verify new fetchmail remains stopped. Never modify old mail or services.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest -v csn-standby-sync/test_activation.py`

Expected: all injected failure states produce the documented safe result.

- [ ] **Step 5: Obtain explicit approval before real activation**

List the exact backup path, expected new-side Dovecot interruption, Maildir rename targets, single sync, and timer enable. Do not treat approval of this plan as approval of the activation operation.

- [ ] **Step 6: Activate and verify after approval**

Fresh checks required: new Dovecot active, `doveadm user csn` UID/GID/home correct, 30 mailboxes recognized, sync report exit 0, standby timer enabled with next time near 03:30 JST, new fetchmail inactive/disabled, old fetchmail/Dovecot active.

### Task 8: Add monitoring and the final-cutover audit without performing cutover

**Files:**
- Create: `csn-standby-sync/audit_fetchmail_uidl.py`
- Create: `csn-standby-sync/README.md`
- Modify: `CSN-MIGRATION.md`

- [ ] **Step 1: Implement the read-only UIDL audit**

The audit must report only account count, presence/owner/mode/hash/record count of fetchmail ID-state files, configured `uidl/keep/fetchall` booleans, and service/timer state. It must redact usernames, server names, passwords, UIDL values, and message data. It must not contact POP3, alter files, or start fetchmail.

- [ ] **Step 2: Write the operational runbook**

Document daily checks, manual one-way run, failure interpretation (`0=complete`, `2=incomplete`, all others failed), rollback, and final cutover in this exact freeze order:

1. stop client changes;
2. verify last daily sync;
3. stop old fetchmail and confirm no process;
4. stop old Dovecot;
5. run final old→new backup and require exit 0;
6. verify mailboxes/counts/ownership;
7. audit all 11 UIDL states;
8. if UIDL safety is unproven, abort before starting new fetchmail;
9. run one guarded new fetch cycle and verify no duplicate/routing error;
10. enable new fetchmail timer;
11. switch DNS/clients;
12. keep old server stopped but recoverable.

- [ ] **Step 3: Append verified evidence to the migration record**

Record installed paths, hashes, backups, service/timer states, isolated test reports, and any approval-gated step actually executed. Do not record secrets, public-key material, message identifiers, or mail content.

- [ ] **Step 4: Run the full local verification suite**

Run:

```bash
python3 -m unittest discover -s csn-standby-sync -p 'test_*.py' -v
python3 -m py_compile csn-standby-sync/*.py
rg -n 'T[B]D|T[O]DO|implement[ ]later|fill[ ]in[ ]details|BEGIN[ ].*PRIVATE[ ]KEY' csn-standby-sync docs/superpowers/specs/2026-11-05-csn-daily-standby-sync-design.md docs/superpowers/plans/2026-11-05-csn-daily-standby-sync.md
```

Expected: all tests pass; compilation exits 0; the scan finds no placeholder phrases or private-key blocks. A separate manual review confirms that documentation contains no password values.

- [ ] **Step 5: Run final live-state verification**

Verify only the phase that received explicit approval. Before activation approval, expected state remains: old fetchmail/Dovecot active; new fetchmail inactive/disabled; new production Maildir unswitched; standby timer disabled; `armed` absent. After activation approval, expected state is the Task 7 Step 6 state. Never claim the later state using earlier evidence.
