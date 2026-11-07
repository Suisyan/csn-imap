# CSN Task 5 Verification Helpers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove, without stopping the old fetchmail service, that corrected isolated old-to-new synchronization does not add GUID duplicates beyond the authoritative source and that the production standby remains unarmed.

**Architecture:** Add two independent, fixed-purpose verification endpoints instead of modifying the production synchronization installers. A root-owned old-server audit endpoint returns only anonymous aggregate counts plus an in-memory comparison digest; a root-owned new-server endpoint returns only the armed-marker state. A local orchestration driver runs the corrected isolated sync and compares source/destination aggregates for one initial attempt plus at most two retries.

**Tech Stack:** Python 3.5-compatible standard library on the old Ubuntu 16 server, Python 3.12 standard library locally/new, Dovecot `doveadm`, OpenSSH, sudoers, transactional root-owned installers, `unittest`.

---

## File map

- Create `csn-standby-sync/guid_audit.py`: fixed-config, no-argument GUID-distribution audit engine; Python 3.5 compatible.
- Create `csn-standby-sync/isolated_guid_audit.py`: no-argument mailadmin wrapper with the fixed isolated Dovecot config.
- Create `csn-standby-sync/test_guid_audit.py`: parser, redaction, process, and aggregate tests.
- Create `csn-standby-sync/register_old_audit.py`: transactional old-server installer for the audit helper and one fixed sudoers command.
- Create `csn-standby-sync/test_register_old_audit.py`: isolated-root installer tests and rollback tests.
- Create `csn-standby-sync/armed_status.py`: no-argument helper that reports only `absent` or `present`.
- Create `csn-standby-sync/register_new_verification.py`: transactional new-server installer for `armed_status.py` and one fixed sudoers command.
- Create `csn-standby-sync/test_register_new_verification.py`: isolated-root installer and rollback tests.
- Modify `csn-standby-sync/isolated_e2e.py`: consume the fixed armed-status helper and retain fail-closed pre/post checks.
- Modify `csn-standby-sync/test_isolated_e2e.py`: require the fixed sudo command and reject malformed/present/unavailable states.
- Create `csn-standby-sync/task5_verify.py`: local fixed-endpoint orchestration, private digest comparison, and maximum-three-attempt policy.
- Create `csn-standby-sync/test_task5_verify.py`: retry, redaction, mismatch, guard, and timeout tests.
- Modify `docs/superpowers/plans/2026-11-05-csn-daily-standby-sync.md`: record the revised Task 5 acceptance criterion and evidence only after it passes.

The workspace is not a Git repository. Replace commit checkpoints with recorded SHA-256 hashes and passing test output; do not initialize a repository.

### Task 1: Fixed anonymous GUID audit engine

**Files:**
- Create: `csn-standby-sync/guid_audit.py`
- Create: `csn-standby-sync/isolated_guid_audit.py`
- Create: `csn-standby-sync/test_guid_audit.py`

- [x] **Step 1: Write failing parser and aggregation tests**

Tests must feed synthetic tab-separated `mailbox` and `guid` rows through an injected stream. Use secret-looking mailbox names and GUIDs, then assert that JSON output contains none of them.

```python
def test_aggregate_redacts_identifiers(self):
    rows = [
        ('secret-customer', 'aaaaaaaa'),
        ('secret-customer', 'aaaaaaaa'),
        ('INBOX', 'bbbbbbbb'),
    ]
    report = audit.aggregate(rows)
    self.assertEqual(report['messages'], 3)
    self.assertEqual(report['mailboxes'], 2)
    self.assertEqual(report['duplicate_groups'], 1)
    self.assertEqual(report['duplicate_excess'], 1)
    encoded = audit.encode_public(report)
    self.assertNotIn('secret-customer', encoded)
    self.assertNotIn('aaaaaaaa', encoded)
```

Also require: empty input rejection, malformed tab rows, missing fields, oversized lines, non-ASCII control characters, duplicate header rejection, and deterministic digest equality independent of input order.

- [x] **Step 2: Run the audit tests and verify RED**

Run:

```bash
python3 -m unittest discover -s csn-standby-sync -p 'test_guid_audit.py'
```

Expected: failure because `guid_audit.py` does not exist.

- [x] **Step 3: Implement the minimal fixed audit engine**

The public CLI must require EUID `csn`, accept no arguments, use only `/usr/bin/doveadm`, and execute the fixed old config. Do not use a shell.

```python
COMMAND = ['/usr/bin/doveadm', '-c', '/etc/csn-standby-source.conf',
           '-f', 'tab', 'fetch', 'mailbox guid', 'ALL']
MAX_LINE = 8192
TIMEOUT = 600
```

Expose one internal `audit_main(command, expected_uid, argv, ...)` entry point for
the wrapper, but never accept a command, path, UID, config, or environment value
from CLI input. `isolated_guid_audit.py` must require the numeric mailadmin UID
resolved with `pwd.getpwnam('mailadmin')`, accept no arguments, and call the same
engine with exactly:

```python
ISOLATED_COMMAND = [
    '/usr/bin/doveadm', '-c',
    '/home/mailadmin/csn-migration/initial-sync-receiver/dovecot.conf',
    '-f', 'tab', 'fetch', 'mailbox guid', 'ALL'
]
```

`aggregate(rows)` must build `mailbox -> guid -> multiplicity`, hash canonical length-prefixed UTF-8 bytes with SHA-256, and return:

```python
{
    'messages': int,
    'mailboxes': int,
    'duplicate_groups': int,
    'duplicate_excess': int,
    'distribution_sha256': '<64 lowercase hex characters>'
}
```

The digest may appear only on the helper's stdout for private capture by the orchestrator. Never write it to a file or journal. Capture stderr in a private unlinked temporary file; any stderr byte or nonzero exit fails with a sanitized error. Use a new process group, a 600-second deadline, SIGTERM then SIGKILL cleanup, and reap the child.

- [x] **Step 4: Verify GREEN and Python 3.5 syntax compatibility**

Run:

```bash
python3 -m unittest discover -s csn-standby-sync -p 'test_guid_audit.py'
python3 -m py_compile csn-standby-sync/guid_audit.py csn-standby-sync/isolated_guid_audit.py csn-standby-sync/test_guid_audit.py
```

Expected: all tests pass. Review source for f-strings, `subprocess.run`, keyword-only syntax unsupported by Python 3.5, and dictionary-order dependence; none may remain in `guid_audit.py`.

- [x] **Step 5: Record the checkpoint hash**

Run:

```bash
sha256sum csn-standby-sync/guid_audit.py csn-standby-sync/isolated_guid_audit.py csn-standby-sync/test_guid_audit.py
```

Record only file hashes, not sample digests or identifiers.

### Task 2: Transactional old-server audit registration

**Files:**
- Create: `csn-standby-sync/register_old_audit.py`
- Create: `csn-standby-sync/test_register_old_audit.py`

- [x] **Step 1: Write failing isolated-root installer tests**

Require exact installed state:

```text
/usr/local/sbin/csn-standby-guid-audit  root:root 0700
/etc/sudoers.d/csn-standby-guid-audit  root:root 0440
```

The sudoers payload must be exactly:

```text
umeoka ALL=(csn) NOPASSWD: /usr/local/sbin/csn-standby-guid-audit
```

Tests must reject symlinked parents/files, non-root payload ownership, writable payload directories, wrong modes, hard links, arguments, payload changes during install, concurrent install, invalid `visudo`, and simulated failures at every replacement checkpoint. Rollback must restore bytes/modes/owners and never touch the existing dsync helper, authorized key, Dovecot, fetchmail, or Maildir.

- [x] **Step 2: Run installer tests and verify RED**

```bash
python3 -m unittest discover -s csn-standby-sync -p 'test_register_old_audit.py'
```

Expected: failure because the installer is absent.

- [x] **Step 3: Implement the dedicated installer**

Follow the descriptor-pinned, no-follow, atomic-replace, backup, lock, and rollback patterns in `register_old.py`, but install only the two files above. Production CLI contract:

```text
sudo python3 /root/csn-standby-audit-install/register_old_audit.py
```

It accepts no arguments, reads `guid_audit.py` only from the root-owned payload directory beside itself, validates with `python3 -m py_compile` and `visudo -cf`, creates a mode-0700 backup below `/var/backups/csn-standby-audit-before-<UTC>-<random>/`, and prints bounded metadata-only JSON.

- [x] **Step 4: Verify GREEN and the full old endpoint suite**

```bash
python3 -m unittest discover -s csn-standby-sync -p 'test_register_old_audit.py'
python3 -m unittest discover -s csn-standby-sync -p 'test_register_old.py'
python3 -m py_compile csn-standby-sync/register_old_audit.py csn-standby-sync/test_register_old_audit.py
```

Expected: all tests pass.

- [x] **Step 5: Record hashes**

```bash
sha256sum csn-standby-sync/register_old_audit.py csn-standby-sync/test_register_old_audit.py
```

### Task 3: New-server armed-state proof endpoint

**Files:**
- Create: `csn-standby-sync/armed_status.py`
- Create: `csn-standby-sync/register_new_verification.py`
- Create: `csn-standby-sync/test_register_new_verification.py`

- [x] **Step 1: Write failing helper tests**

The helper must require EUID 0 and zero arguments. With `lstat` injected, assert these exact public results:

```python
self.assertEqual(armed_status.check(marker_missing), 'absent')
self.assertEqual(armed_status.check(marker_regular), 'present')
```

A symlink, directory, device, traversal error, extra argument, or non-root caller must return nonzero with sanitized stderr. It must never create, remove, open for writing, or chmod the marker.

- [x] **Step 2: Write failing installer tests**

Require exact installed state:

```text
/usr/local/sbin/csn-standby-armed-status  root:root 0700
/etc/sudoers.d/csn-standby-armed-status  root:root 0440
```

Exact sudoers payload:

```text
mailadmin ALL=(root) NOPASSWD: /usr/local/sbin/csn-standby-armed-status
```

Tests must reject symlinked parents/files, non-root payload ownership, writable
payload directories, wrong modes, hard links, arguments, payload changes during
install, concurrent installation, invalid `visudo`, and simulated failures at
every replacement checkpoint. Rollback must restore prior bytes, modes, and
owners. Tests must additionally prove no `systemctl` command is allowed, no unit
file is written, no service state changes, and
`/etc/csn-standby-sync/armed` is never created, removed, renamed, chmodded, or
opened for writing.

- [x] **Step 3: Run tests and verify RED**

```bash
python3 -m unittest discover -s csn-standby-sync -p 'test_register_new_verification.py'
```

Expected: failure because helper and installer are absent.

- [x] **Step 4: Implement helper and installer**

The helper prints exactly one of these lines:

```text
absent
present
```

The installer follows `register_new.py` transactional patterns but does not call `systemctl`, does not daemon-reload, and installs only the helper and sudoers file. It accepts no arguments and emits bounded metadata-only JSON with backup directory, file hashes/modes, and no marker contents.

- [x] **Step 5: Verify GREEN and record hashes**

```bash
python3 -m unittest discover -s csn-standby-sync -p 'test_register_new_verification.py'
python3 -m py_compile csn-standby-sync/armed_status.py csn-standby-sync/register_new_verification.py csn-standby-sync/test_register_new_verification.py
sha256sum csn-standby-sync/armed_status.py csn-standby-sync/register_new_verification.py csn-standby-sync/test_register_new_verification.py
```

Expected: all tests pass.

### Task 4: Corrected isolated driver integration

**Files:**
- Modify: `csn-standby-sync/isolated_e2e.py`
- Modify: `csn-standby-sync/test_isolated_e2e.py`

- [x] **Step 1: Write failing armed-helper integration tests**

Replace injected filesystem marker access with a fixed subprocess call:

```python
ARMED_COMMAND = ['/usr/bin/sudo', '-n', '/usr/local/sbin/csn-standby-armed-status']
```

Tests must require return code 0, empty stderr, bounded stdout, and exact `absent\n`. `present`, blank, extra lines, malformed bytes, timeout, or any stderr must fail. Assert no shell and no caller-supplied path/argument.

- [x] **Step 2: Run the isolated tests and verify RED**

```bash
python3 -m unittest discover -s csn-standby-sync -p 'test_isolated_e2e.py'
```

Expected: the new fixed-command tests fail against direct marker access.

- [x] **Step 3: Implement the minimal integration**

`check_services()` must call the fixed helper before and after synchronization. Preserve current service checks, config containment, transport-file checks, target-tree fail-closed symlink scan, private diagnostics, empty-stderr success rule, minimal stdout, and timeout cleanup.

- [x] **Step 4: Verify GREEN**

```bash
python3 -m unittest discover -s csn-standby-sync -p 'test_isolated_e2e.py'
python3 csn-standby-sync/test_isolated_e2e.py
python3 -m py_compile csn-standby-sync/isolated_e2e.py csn-standby-sync/test_isolated_e2e.py
```

Expected: all tests pass through both test entry points.

- [x] **Step 5: Record hashes**

```bash
sha256sum csn-standby-sync/isolated_e2e.py csn-standby-sync/test_isolated_e2e.py
```

### Task 5: Fixed orchestration and maximum-three-attempt policy

**Files:**
- Create: `csn-standby-sync/task5_verify.py`
- Create: `csn-standby-sync/test_task5_verify.py`

- [x] **Step 1: Write failing command-boundary tests**

The orchestrator must expose no general command runner. Tests must assert exact tuples for:

```text
OLD state query
NEW state query
NEW corrected isolated sync
OLD sudo -n /usr/local/sbin/csn-standby-guid-audit
NEW fixed isolated GUID audit
```

All SSH commands must use fixed identities, pinned known-hosts files, `BatchMode=yes`, `IdentitiesOnly=yes`, `StrictHostKeyChecking=yes`, and a connection timeout. Reject arguments and environment overrides.

- [x] **Step 2: Write failing comparison/redaction tests**

Feed synthetic old/new helper JSON containing private digest values. Assert:

```python
result = compare(old_report, new_report)
self.assertTrue(result['distribution_equal'])
self.assertNotIn('distribution_sha256', json.dumps(result))
self.assertNotIn(private_digest, json.dumps(result))
```

Require equality of all four aggregate counts and the private digest. Reject missing/extra keys, booleans in integer fields, negative/oversized counts, non-lowercase/non-64-byte digest syntax, duplicate JSON, trailing bytes, stderr, nonzero exit, and outputs above the fixed limit.

- [x] **Step 3: Write failing retry-state-machine tests**

Cover these exact sequences:

```text
match                       -> 1 sync, success
mismatch, match             -> 2 syncs, success
mismatch, mismatch, match   -> 3 syncs, success
mismatch x3                 -> 3 syncs, partial/stop
any guard/error             -> immediate stop, no further sync
```

Assert that mismatch never invokes deduplication, service stop/restart, timer enablement, marker creation, path rename, or a fourth sync.

- [x] **Step 4: Run tests and verify RED**

```bash
python3 -m unittest discover -s csn-standby-sync -p 'test_task5_verify.py'
```

Expected: failure because the orchestrator is absent.

- [x] **Step 5: Implement minimal orchestration**

The state machine is fixed:

```python
MAX_ATTEMPTS = 3
for attempt in range(1, MAX_ATTEMPTS + 1):
    verify_guards()
    run_isolated_sync()
    old = run_old_audit_private()
    new = run_new_audit_private()
    comparison = compare(old, new)
    verify_guards()
    write_redacted_attempt(comparison)
    if comparison['distribution_equal']:
        return 0
return 2
```

Each child uses a separate process group, bounded private stdout/stderr capture, deadline, SIGTERM/SIGKILL cleanup, and reaping. Reports are mode `0600`, created with `O_EXCL|O_NOFOLLOW`, and contain no digest or identifier. Public stdout contains final status, attempt count, and elapsed seconds only.

- [x] **Step 6: Verify GREEN and the entire local suite**

```bash
python3 -m unittest discover -s csn-standby-sync -p 'test_task5_verify.py'
python3 -m py_compile csn-standby-sync/task5_verify.py csn-standby-sync/test_task5_verify.py
python3 -m unittest discover -s csn-standby-sync -p 'test_*.py'
```

Expected: all tests pass with no warning/error output.

- [x] **Step 7: Record hashes**

```bash
sha256sum csn-standby-sync/task5_verify.py csn-standby-sync/test_task5_verify.py
```

### Task 6: Independent implementation reviews

**Files:**
- Review all files created or modified by Tasks 1-5.

- [x] **Step 1: Run an inline specification review**

Because the user selected inline execution, the primary agent performs a
separate specification pass comparing implementation against:

```text
docs/superpowers/specs/2026-11-06-csn-task5-verification-helpers-design.md
docs/superpowers/plans/2026-11-06-csn-task5-verification-helpers.md
```

Resolve every high/medium issue with a new failing test before proceeding.

- [x] **Step 2: Run an inline security/quality review**

Perform a second pass focused only on privilege boundaries, symlink/race
resistance, output redaction, Python 3.5 compatibility, process cleanup, retry
ceiling, and rollback. Resolve every high/medium issue with TDD.

- [x] **Step 3: Re-run complete local verification**

```bash
python3 -m unittest discover -s csn-standby-sync -p 'test_*.py'
python3 -m py_compile csn-standby-sync/*.py
sha256sum csn-standby-sync/*.py csn-standby-sync/*.service csn-standby-sync/*.timer
```

Expected: all tests and compilation pass. Do not record any runtime digest.

### Task 7: Approval-gated staging and registration

**Files:**
- Stage only reviewed payloads on OLD and NEW after explicit user approval.

- [x] **Step 1: Read-only preflight**

Confirm old fetchmail/Dovecot active; new fetchmail inactive and timer disabled; production standby service inactive; production timer disabled/inactive; root installer payload directories absent or root-owned mode `0700`; and at least 5 GiB free on both servers. Do not query message metadata in this step.

- [x] **Step 2: Present exact changes for approval**

List exact files, owners, modes, sudoers lines, backup directories, and state explicitly: no service restart, no timer enable, no marker change, no Maildir change. Obtain user approval before copying or executing installers.

- [x] **Step 3: Stage reviewed payloads**

OLD root payload directory:

```text
/root/csn-standby-audit-install/  root:root 0700
  register_old_audit.py           root:root 0600
  guid_audit.py                   root:root 0600
```

NEW root payload directory:

```text
/root/csn-standby-verification-install/  root:root 0700
  register_new_verification.py           root:root 0600
  register_old_audit.py                  root:root 0600
  armed_status.py                        root:root 0600
```

`register_old_audit.py` is staged on NEW only as the reviewed transactional
installer library imported by `register_new_verification.py`; running it is not
authorized there, and it does not install an old-server helper unless invoked
directly as a separate CLI.

NEW mailadmin test payloads remain below the existing mode-0700 migration directory. Do not copy private keys; reuse the existing fixed staging key in place.

Stage the reviewed `isolated_e2e.py`, `guid_audit.py`, and
`isolated_guid_audit.py` under the existing mailadmin-owned mode-0700 Task 5
directory with mode `0700`. The new-side wrapper imports only the adjacent
reviewed engine after checking the directory and both files are owned by
mailadmin and not group/world writable.

- [x] **Step 4: Execute root installers with a second explicit confirmation**

Run only:

```bash
sudo python3 /root/csn-standby-audit-install/register_old_audit.py
sudo python3 /root/csn-standby-verification-install/register_new_verification.py
```

The first command is on OLD and the second on NEW. If either fails, stop and report sanitized validation state; do not continue to synchronization.

- [x] **Step 5: Validate installation without reading mail**

Verify installed hashes/modes/owners, `visudo -cf` success, exact sudo allowlists, `armed` reports `absent`, all service/timer states are unchanged, and no helper accepts an argument. Do not display helper digests.

### Task 8: Corrected isolated proof

**Files:**
- Runtime private reports only; no code changes.

- [ ] **Step 1: Capture pre-run guards**

Record metadata-only states: old fetchmail/Dovecot active; new fetchmail inactive/timer disabled; standby service inactive/timer disabled/inactive; armed absent; isolated target/config/transport validation passed. Never print message identifiers or audit digests.

- [ ] **Step 2: Execute the maximum-three-attempt orchestrator**

Run the reviewed fixed command. An attempt consists of one corrected isolated sync, immediate old audit, immediate isolated audit, comparison, and post-guards. The first match stops successfully. A mismatch permits another attempt until exactly three attempts have run.

- [ ] **Step 3: Enforce the mismatch stop condition**

If all three comparisons differ, stop with Task 5 partial. Do not try a fourth time, stop old fetchmail, deduplicate, alter Maildir, enable a timer, or create `armed`. Present separately for approval either a short old-fetchmail maintenance window or a frozen-snapshot method.

- [ ] **Step 4: Verify successful end state**

On success require: source/destination distribution equality true; all aggregate counts equal; stderr empty; old services active; new fetchmail inactive/disabled; standby service inactive; timer disabled/inactive; armed absent; isolated config unchanged; no production Maildir switch.

- [ ] **Step 5: Update the parent plan evidence**

Only after Step 4 passes, update Task 5 in `docs/superpowers/plans/2026-11-05-csn-daily-standby-sync.md`. Replace the impossible zero-duplicate criterion with source/destination distribution equality, record attempt count and aggregate counts, record helper/install hashes and backup directories, and explicitly record that no digest or identifier was persisted. Mark Task 5 complete; Task 6 may then begin under its own approval gates.

### Task 9: Final independent evidence review

**Files:**
- Review private runtime report schema and redacted parent-plan evidence.

- [ ] **Step 1: Specification evidence review**

A fresh reviewer verifies that runtime evidence satisfies every design acceptance rule and that no old/new service or production path changed.

- [ ] **Step 2: Privacy review**

A separate reviewer searches only report keys/schema and local documentation for forbidden identifiers/digests. Do not print private report values during review. Any leak blocks completion and must be removed from documentation; runtime mail is never modified.

- [ ] **Step 3: Completion verification**

Re-run local tests, read-only service states, installed file hashes/modes, and `armed` absence. Report Task 5 complete only when all checks pass. Otherwise report the exact partial condition and keep Task 6 blocked.
