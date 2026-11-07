#!/usr/bin/python3
"""Install only the fixed new-server armed-state verification endpoint."""
from __future__ import print_function

import json
import os
import sys

import register_old_audit as fixed_installer


HELPER = '/usr/local/sbin/csn-standby-armed-status'
SUDOERS_PATH = '/etc/sudoers.d/csn-standby-armed-status'
LOCK_PATH = '/var/lib/csn-standby-armed-status.install.lock'
SUDOERS = (b'mailadmin ALL=(root) NOPASSWD: '
           b'/usr/local/sbin/csn-standby-armed-status\n')
InstallError = fixed_installer.InstallError
run_command = fixed_installer.run_command


class Installer(fixed_installer.Installer):
    def __init__(self, root='/', payload_dir=None, owner=(0, 0),
                 runner=run_command, checkpoint=None):
        fixed_installer.Installer.__init__(
            self, root=root, payload_dir=payload_dir, owner=owner,
            runner=runner, checkpoint=checkpoint, helper=HELPER,
            sudoers_path=SUDOERS_PATH, lock_path=LOCK_PATH,
            sudoers=SUDOERS, payload_name='armed_status.py',
            backup_prefix='csn-standby-verification-before-')


def main(argv=None, geteuid=os.geteuid):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        return 64
    if geteuid() != 0:
        return 1
    try:
        report = Installer().install()
        sys.stdout.write(json.dumps(report, sort_keys=True) + '\n')
        return 0
    except InstallError:
        sys.stderr.write('register_new_verification: installation failed\n')
        return 1


if __name__ == '__main__':
    sys.exit(main())
