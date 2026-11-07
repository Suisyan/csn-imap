#!/usr/bin/python3
"""Fixed no-argument wrapper for the isolated new-server Maildir."""
from __future__ import print_function

import os
import pwd
import sys

import guid_audit


ISOLATED_COMMAND = list(guid_audit.ISOLATED_COMMAND)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        uid = pwd.getpwnam('mailadmin').pw_uid
        report = guid_audit.audit_main(
            ISOLATED_COMMAND, uid, argv, geteuid=os.geteuid)
        sys.stdout.write(guid_audit.encode_public(report) + '\n')
        return 0
    except (guid_audit.AuditError, KeyError):
        sys.stderr.write('isolated-guid-audit: audit failed\n')
        return 1


if __name__ == '__main__':
    sys.exit(main())
