import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest

try:
    import cutover
except ImportError:
    cutover = None


class CliTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(cutover, 'cutover implementation is missing')

    def test_exact_actions_only(self):
        self.assertEqual(cutover.parse_args(['--preflight']), 'preflight')
        self.assertEqual(cutover.parse_args(['--prepare','--confirm-old-is-frozen']), 'prepare')
        self.assertEqual(cutover.parse_args(['--activate','--confirm-maildir-swap']), 'activate')
        self.assertEqual(cutover.parse_args(['--rollback','--confirm-restore-old-new-maildir']), 'rollback')
        for argv in ([],['--prepare'],['--activate'],['--rollback'],['--preflight','x'],['x']):
            with self.subTest(argv=argv):
                with self.assertRaises(cutover.CutoverError):
                    cutover.parse_args(argv)

    def test_prepare_command_is_fixed(self):
        self.assertEqual(cutover.prepare_command(),[
            '/usr/bin/rsync','-a','--delete','--no-owner','--no-group',
            '--exclude=dovecot.index*','--exclude=dovecot.mailbox.log*',
            '--exclude=fetchmail.log*','--exclude=procmail.log*',
            cutover.SOURCE+'/',cutover.READY+'/'])


class CutoverTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(cutover, 'cutover implementation is missing')
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)/'root'
        for logical in ('home/mailadmin/csn-migration/initial-sync-receiver/Maildir/cur',
                        'home/mailadmin/csn-migration/initial-sync-receiver/Maildir/new',
                        'home/mailadmin/csn-migration/initial-sync-receiver/Maildir/tmp',
                        'home/csn/Maildir/cur','home/csn/Maildir/new','home/csn/Maildir/tmp',
                        'etc/csn-mail','etc/csn-standby-sync','var/backups','var/lib/csn-cutover'):
            (self.root/logical).mkdir(parents=True,exist_ok=True)
        self.source=self.root/cutover.SOURCE.lstrip('/')
        self.live=self.root/cutover.LIVE.lstrip('/')
        self.source.chmod(0o700);self.live.chmod(0o700)
        (self.root/cutover.BACKUP_ROOT.lstrip('/')).chmod(0o755)
        (self.root/Path(cutover.STATE.lstrip('/')).parent).chmod(0o700)
        (self.source/'cur/source-message').write_bytes(b'message')
        (self.source/'dovecot-uidlist').write_bytes(b'uidlist')
        (self.source/'dovecot.index').write_bytes(b'index')
        for leaf in ('cur','new','tmp'):(self.source/'.Empty'/leaf).mkdir(parents=True,exist_ok=True)
        (self.live/'new/live-message').write_bytes(b'live')
        self.calls=[]
        self.owner=(os.getuid(),os.getgid())

    def tearDown(self):
        self.temp.cleanup()

    def runner(self,argv):
        self.calls.append(list(argv))
        if argv[0:2]==['/usr/bin/rsync','-a']:
            destination=self.root/cutover.READY.lstrip('/')
            for path in self.source.rglob('*'):
                relative=path.relative_to(self.source)
                if path.name.startswith(('dovecot.index','dovecot.mailbox.log','fetchmail.log','procmail.log')):
                    continue
                target=destination/relative
                if path.is_dir():target.mkdir(parents=True,exist_ok=True)
                else:
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(path,target)
        if argv[0:2]==['/usr/bin/systemctl','is-active']:
            return b'inactive\n' if argv[2]!='dovecot.service' else b'active\n'
        if argv[0:2]==['/usr/bin/systemctl','is-enabled']:
            return b'disabled\n'
        return b''

    def object(self):
        return cutover.Cutover(root=str(self.root),runner=self.runner,
            accounts={'root':self.owner,'csn':self.owner,'mailadmin':self.owner})

    def test_preflight_is_read_only(self):
        before=sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*'))
        report=self.object().preflight()
        after=sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*'))
        self.assertEqual(before,after)
        self.assertEqual(report['ready'],'absent')

    def test_prepare_copies_mail_and_control_but_excludes_indexes(self):
        report=self.object().prepare()
        ready=self.root/cutover.READY.lstrip('/')
        self.assertTrue((ready/'cur/source-message').is_file())
        self.assertTrue((ready/'dovecot-uidlist').is_file())
        self.assertFalse((ready/'dovecot.index').exists())
        self.assertEqual(report['messages'],1)
        self.assertEqual(report['mailboxes'],2)

    def test_prepare_rejects_missing_empty_mailbox(self):
        def omit_empty(argv):
            result=self.runner(argv)
            if argv[0:2]==['/usr/bin/rsync','-a']:
                shutil.rmtree(self.root/cutover.READY.lstrip('/')/'.Empty')
            return result
        worker=cutover.Cutover(root=str(self.root),runner=omit_empty,
            accounts={'root':self.owner,'csn':self.owner,'mailadmin':self.owner})
        with self.assertRaises(cutover.CutoverError):worker.prepare()

    def test_activate_swaps_atomically_and_records_state(self):
        self.object().prepare()
        report=self.object().activate()
        self.assertEqual((self.live/'cur/source-message').read_bytes(),b'message')
        state=self.root/cutover.STATE.lstrip('/')
        payload=json.loads(state.read_text())
        self.assertEqual(payload['backup'],report['backup'])
        self.assertIn(['/usr/bin/systemctl','stop','dovecot.service'],self.calls)
        self.assertIn(['/usr/bin/systemctl','start','dovecot.service'],self.calls)
        self.assertIn(['/usr/bin/doveadm','-f','json','mailbox','status','-u','csn','messages','*'],self.calls)

    def test_activate_refuses_markers_or_fetch_timer(self):
        self.object().prepare()
        marker=self.root/cutover.FETCH_MARKER.lstrip('/')
        marker.write_bytes(b'enabled')
        with self.assertRaises(cutover.CutoverError):self.object().activate()
        marker.unlink()
        def active(argv):
            if argv==['/usr/bin/systemctl','is-active','fetchmail-csn.timer']:
                return b'active\n'
            return self.runner(argv)
        with self.assertRaises(cutover.CutoverError):
            cutover.Cutover(root=str(self.root),runner=active,
                accounts={'root':self.owner,'csn':self.owner,'mailadmin':self.owner}).activate()

    def test_preflight_refuses_active_fetch_or_standby_service(self):
        for active_unit in ('fetchmail-csn.service','csn-standby-sync.service'):
            def state(argv,active_unit=active_unit):
                if argv==['/usr/bin/systemctl','is-active',active_unit]:return b'active\n'
                return self.runner(argv)
            with self.subTest(unit=active_unit):
                with self.assertRaises(cutover.CutoverError):
                    cutover.Cutover(root=str(self.root),runner=state,
                        accounts={'root':self.owner,'csn':self.owner,'mailadmin':self.owner}).preflight()

    def test_rollback_disables_fetch_before_restore(self):
        self.object().prepare();self.object().activate();self.calls.clear()
        marker=self.root/cutover.FETCH_MARKER.lstrip('/')
        marker.write_bytes(b'enabled');marker.chmod(0o600)
        self.object().rollback()
        self.assertEqual((self.live/'new/live-message').read_bytes(),b'live')
        self.assertFalse((self.root/cutover.STATE.lstrip('/')).exists())
        self.assertEqual(self.calls[0],['/usr/bin/systemctl','disable','--now','fetchmail-csn.timer'])

    def test_symlink_source_or_state_is_rejected(self):
        shutil.rmtree(self.source)
        self.source.symlink_to('/tmp')
        with self.assertRaises(cutover.CutoverError):self.object().preflight()

    def test_activate_rejects_unsafe_backup_or_state_parent(self):
        self.object().prepare()
        backup_root=self.root/cutover.BACKUP_ROOT.lstrip('/')
        backup_root.chmod(0o777)
        with self.assertRaises(cutover.CutoverError):self.object().activate()
        backup_root.chmod(0o755)
        state_parent=self.root/Path(cutover.STATE.lstrip('/')).parent
        shutil.rmtree(state_parent)
        state_parent.symlink_to(backup_root)
        with self.assertRaises(cutover.CutoverError):self.object().activate()

    def test_rollback_rejects_unsafe_fetch_marker(self):
        self.object().prepare();self.object().activate()
        marker=self.root/cutover.FETCH_MARKER.lstrip('/')
        marker.write_bytes(b'enabled');marker.chmod(0o666)
        with self.assertRaises(cutover.CutoverError):self.object().rollback()


if __name__=='__main__':unittest.main()
