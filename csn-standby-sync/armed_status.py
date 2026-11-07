#!/usr/bin/python3
"""Report only whether the production standby activation marker exists."""
from __future__ import print_function

import os
import stat
import sys


MARKER = '/etc/csn-standby-sync/armed'


class StatusError(Exception):
    """A sanitized marker status failure."""


def check(lstat=os.lstat):
    try:
        info = lstat(MARKER)
    except FileNotFoundError:
        return 'absent'
    except OSError:
        raise StatusError('marker status unavailable')
    if not stat.S_ISREG(info.st_mode):
        raise StatusError('marker type rejected')
    return 'present'


def main(argv=None, geteuid=os.geteuid, checker=check):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        return 64
    if geteuid() != 0:
        return 1
    try:
        result = checker()
        if result not in ('absent', 'present'):
            raise StatusError('invalid marker status')
        sys.stdout.write(result + '\n')
        return 0
    except StatusError:
        sys.stderr.write('csn-standby-armed-status: check failed\n')
        return 1


if __name__ == '__main__':
    sys.exit(main())
