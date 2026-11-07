#!/usr/bin/python3
"""Restricted root endpoint; stdout belongs exclusively to dsync."""
import os
import sys


def main(argv=None, geteuid=os.geteuid):
    if argv is None:
        argv = sys.argv[1:]
    if argv or geteuid() != 0:
        sys.stderr.write('csn-standby-source: root and no arguments required\n')
        return 1
    try:
        os.execv('/sbin/runuser', ['/sbin/runuser', '-u', 'csn', '--',
                 '/usr/bin/doveadm', '-c', '/etc/csn-standby-source.conf',
                 'dsync-server'])
    except OSError:
        sys.stderr.write('csn-standby-source: execution failed\n')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
