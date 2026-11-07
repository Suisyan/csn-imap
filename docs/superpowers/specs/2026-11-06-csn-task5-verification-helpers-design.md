# CSN Task 5 Verification Helpers Design

## Purpose

Complete the isolated standby synchronization proof without stopping the
authoritative old server. The proof must establish that a reverse `doveadm
backup` run does not add GUID duplicates beyond those already present on the
authoritative source, and must prove that the production standby timer is not
armed.

## Safety boundary

- The old `fetchmail` and Dovecot services remain running and are not restarted.
- The new production Maildir, new fetchmail units, production standby service,
  production standby timer, and activation marker are not modified.
- Synchronization targets only the existing isolated receiver:
  `/home/mailadmin/csn-migration/initial-sync-receiver/Maildir`.
- No message body, header, subject, sender, recipient, Message-ID, GUID, mailbox
  name, password, private key, or dsync protocol stream is printed or recorded.
- A Dovecot metadata query may update UID control metadata and directory times.
  It must not move, delete, rewrite, or deduplicate message files.

## Components

### New-server armed-state helper

A root-owned, non-writable, no-argument helper reports only whether
`/etc/csn-standby-sync/armed` is absent or present. A narrowly scoped sudoers
rule permits `mailadmin` to execute only this fixed helper. The isolated test
driver calls it before and after every pass. `present`, an execution error, an
argument, or an unrecognized response makes the test fail closed.

The helper does not expose the protected directory, change the marker, invoke
systemd, or alter any service.

### Anonymous duplicate-distribution audit

Fixed audit helpers inspect the authoritative old Maildir and the isolated new
Maildir. Each helper computes a canonical representation of the per-mailbox
GUID multiplicities internally. It returns only:

- message count;
- mailbox count;
- duplicate group count;
- duplicate excess count;
- a comparison digest over the complete canonical distribution.

The orchestration layer captures digests privately, compares them, and stores
only equality booleans and aggregate counts in the report. It never displays or
persists either digest. Helpers accept no paths, users, mailboxes, search
expressions, or other arguments.

## Verification flow

1. Confirm old fetchmail and Dovecot are active.
2. Confirm new fetchmail is inactive, its timer is disabled, the production
   standby service is inactive, its timer is disabled/inactive, and `armed` is
   absent through the fixed helper.
3. Validate the isolated config, receiver tree, SSH key, known-hosts file, and
   absence of symlinks.
4. Run the fixed reverse backup into the isolated receiver.
5. Run the authoritative-old and isolated-new anonymous audits as close
   together as possible.
6. Accept the pass only when the complete distribution digest and all aggregate
   counts match, stderr is empty, and all service/armed guards remain unchanged.
7. If the first comparison differs, repeat steps 4 through 6. If it still
   differs, repeat once more. This is one initial attempt plus at most two
   additional synchronization attempts.
8. If the third comparison still differs, stop automatically. Do not pause a
   service, deduplicate mail, change the source, or broaden privileges. Present
   a separate method for approval, such as a short old-fetchmail maintenance
   window or a separately frozen snapshot comparison.

New mail may arrive on the old server between synchronization and comparison.
Such an arrival causes a retry, not a failure attribution. No retry occurs after
the third mismatch.

## Error handling and reporting

- Any stderr output, timeout, malformed audit response, unsafe ownership/mode,
  symlink, service-state change, or inaccessible armed-state proof fails the
  attempt.
- Child processes run in a separate process group and are terminated and reaped
  on timeout.
- Diagnostic files are private mode `0600`; helpers and sudoers files are
  root-owned and not writable by their callers.
- Reports contain aggregate counts, elapsed time, exit status, attempt number,
  comparison equality, and guard results only.
- Existing detailed Task 5 artifacts remain unchanged.

## Testing and acceptance

Tests are written before implementation and cover argument rejection, fixed
paths, ownership/mode checks, output redaction, malformed results, nonempty
stderr, timeout cleanup, armed present/unreadable states, comparison mismatch,
and the maximum of two additional attempts.

Task 5 passes only after a corrected isolated run produces matching anonymous
source/destination distributions and all pre/post guards pass. If three attempts
do not match, Task 5 remains partial and Task 6 remains blocked pending approval
of another verification method.
