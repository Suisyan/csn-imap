# CSN Dovecot cross-version isolation test — 2026-11-05

Tested actual old Dovecot 2.2.22 and new 2.3.21. Neither production CSN
Maildir nor production Dovecot configuration was passed to these commands.
Old test root: /home/umeoka/csn-dsync-lab (UID1000).
New test root: /home/mailadmin/csn-dsync-lab (UID1000).
Dedicated dovecot.conf points only at its test Maildir/home, no production plugins.
No -u csn, root privileges, service reload/restart or production sync was used.

Transport: local runner connects separately to both hosts via pinned SSH keys;
new doveadm sync talks via a restricted Unix socket relay to old dsync-server.
The local runner joins the two authenticated SSH streams. No private key was
copied to either server. No TCP listener was created. The transient Unix socket
was removed by the relay. Lab files and synthetic messages remain for review.

## Results

run.py / results.log:
- Bidirectional initial message union: PASS, common GUIDs.
- Seen on new reflected on old: PASS.
- Unseen on old reflected on new: PASS.
- Message move new→old: PASS.
- Folder rename old→new: PASS.
- Message expunge old→new: PASS.
- Message expunge new→old: PASS.

external_delivery_test.py / external-results.log:
- Synthetic direct Maildir save on old (simulating procmail delivery): PASS.
- New-side Seen reflected back for that message: PASS.
- Empty folder creation new→old and deletion old→new: PASS.

Initial assertion compared the transient \\Recent flag and failed despite correct
\\Seen propagation. Final comparisons exclude only \\Recent, compare other flags,
mailbox names, message IDs and GUIDs. No production bug or fix was inferred.
New standalone test config disables stats_writer_socket_path to avoid connecting
to the production statistics socket. No production settings were altered.

## Limits and proposed production design (not deployed)

This is a small synthetic compatibility test, not proof of conflict-free behavior,
recovery after interruption or initial pairing of the real 50k-message mailboxes.
Run concurrent-write/conflict and interrupted-sync tests before production.
Old procmail delivery needs periodic mailbox scanning; do not rely solely on a
Dovecot delivery notification plugin. A new-host timer running one locked dsync
pair at a time can avoid old Dovecot reconfiguration/restart. Measure full-scan
cost before choosing an interval; do not promise instantaneous propagation.

Keep upstream collection on OLD only during this stage. New fetch timer remains
disabled. dsync exchanges data only between ConoHa servers; it does not upload
historical messages to iCLUSTA. iCLUSTA keep retains mail since that setting changed.

Before live pairing, preserve both sides, reconcile the 8 independently fetched
new messages and 4 preserved/probe messages, and establish a current authoritative
old baseline. Do not pair stale Maildir.ready blindly: old deletions and moves
since its copy could be resurrected. Do not identify duplicates by Message-ID
alone. Real deletion propagation requires explicit user approval after a concrete
bounded plan; root-owned fixed-scope helpers and a restricted transport are needed.

## Focused resilience checks completed

User deferred simultaneous-client-edit conflict testing. resilience_test.py now
checks direct Maildir arrivals while the stream is transferring and recovery.
- Cut stream after 67,418 bytes: dsync client returned75 (not false success).
- A synthetic arrival was injected during that interrupted stream.
- Retry converged; both synthetic messages' full normalized content hashes match.
- Repeating sync produced no duplicate.
- Successful stream with another arrival: exit0; follow-up sync converged to four
  scoped synthetic messages with identical content hashes and no duplicate.
Evidence: resilience-results.log.

The first cutoff exposed a test-relay EOF bug: socket receive EOF did not close
stdout, causing a45s timeout. Fixed relay.py to close stdout on EOF. Terminated
only processes whose argv named the dedicated lab config/relay, removed stale lab
socket, then reran successfully. This was not diagnosed as a Dovecot server fault.
Old production fetchmail and Dovecot remained active; new fetching stayed inactive.
