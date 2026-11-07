import contextlib
import io
import json
import os
import signal
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, os.path.dirname(__file__))

import runner


class RunnerContractTests(unittest.TestCase):
    def test_runner_has_system_python_shebang(self):
        with open(runner.__file__, encoding="utf-8") as source:
            self.assertEqual(source.readline(), "#!/usr/bin/python3\n")

    def test_build_command_uses_fixed_one_way_backup_arguments(self):
        self.assertEqual(
            runner.build_command(),
            [
                "/usr/bin/doveadm", "backup", "-R", "-u", "csn", "-l", "60", "-T", "600",
                "/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o",
                "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=/etc/csn-standby-sync/known_hosts",
                "-o", "ConnectTimeout=20", "-i", "/etc/csn-standby-sync/id_ed25519",
                "umeoka@118.27.32.100",
            ],
        )
        self.assertNotIn("sync", runner.build_command())

    def test_completion_requires_zero_exit_code(self):
        self.assertTrue(runner.is_complete(0))
        self.assertFalse(runner.is_complete(2))
        self.assertFalse(runner.is_complete(75))

    def test_report_exposes_stderr_count_but_never_raw_stderr(self):
        report = runner.make_report(75, 12.5, 1234, "sensitive protocol text")

        self.assertEqual(report["exit_code"], 75)
        self.assertEqual(report["stderr_bytes"], 1234)
        self.assertNotIn("raw_stderr", report)
        self.assertNotIn("sensitive protocol text", str(report))

    def test_fetchmail_service_and_timer_states_are_checked(self):
        states = {"fetchmail-csn.service": "inactive", "fetchmail-csn.timer": "disabled"}
        runner.verify_fetchmail_disabled(state_lookup=states.__getitem__)
        for bad_states, message in (
            ({"fetchmail-csn.service": "active", "fetchmail-csn.timer": "disabled"}, "fetchmail-csn.service"),
            ({"fetchmail-csn.service": "inactive", "fetchmail-csn.timer": "enabled"}, "fetchmail-csn.timer"),
        ):
            with self.subTest(states=bad_states), self.assertRaisesRegex(RuntimeError, message):
                runner.verify_fetchmail_disabled(state_lookup=bad_states.__getitem__)

    def test_cli_rejects_arguments_before_running_sync(self):
        with patch.object(runner, "run_sync") as run_sync, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(runner.main(["--anything"]), 2)
        run_sync.assert_not_called()

    def test_user_lookup_accepts_only_expected_mapping(self):
        good = SimpleNamespace(returncode=0, stdout="uid\t1001\ngid\t1001\nhome\t/home/csn\n")
        runner.verify_local_user(run=Mock(return_value=good))
        for result in (
            SimpleNamespace(returncode=1, stdout="secret command output"),
            SimpleNamespace(returncode=0, stdout="uid 1002\ngid 1001\nhome /home/csn\n"),
            SimpleNamespace(returncode=0, stdout="uid 1001\ngid 1001\nhome /tmp\n"),
        ):
            with self.subTest(result=result), self.assertRaises(RuntimeError):
                runner.verify_local_user(run=Mock(return_value=result))

    def test_systemctl_timeout_is_sanitized(self):
        timed_out = subprocess_timeout("systemctl", b"secret output")
        run = Mock(side_effect=timed_out)
        with self.assertRaisesRegex(RuntimeError, "timed out") as caught:
            runner.unit_state("fetchmail-csn.service", run=run)
        self.assertNotIn("secret output", str(caught.exception))
        self.assertEqual(run.call_args.kwargs["timeout"], 10)

    def test_doveadm_user_timeout_is_sanitized(self):
        timed_out = subprocess_timeout("doveadm", b"secret output")
        run = Mock(side_effect=timed_out)
        with self.assertRaisesRegex(RuntimeError, "timed out") as caught:
            runner.verify_local_user(run=run)
        self.assertNotIn("secret output", str(caught.exception))
        self.assertEqual(run.call_args.kwargs["timeout"], 10)

    def test_lock_rejects_symlink_and_unsafe_opened_files(self):
        with tempfile.TemporaryDirectory() as directory:
            parent_stat = safe_parent_stat()
            target = os.path.join(directory, "target")
            link = os.path.join(directory, "lock")
            with open(target, "w", encoding="utf-8") as output:
                output.write("not a lock")
            os.symlink(target, link)
            with self.assertRaisesRegex(RuntimeError, "lock"):
                runner._acquire_lock(link, stat_path=lambda path: parent_stat)

            regular_path = os.path.join(directory, "regular-lock")
            for unsafe in (
                lock_stat(uid=1000, mode=0o600),
                lock_stat(uid=0, mode=0o662),
                SimpleNamespace(st_uid=0, st_mode=stat.S_IFIFO | 0o600),
            ):
                with self.subTest(unsafe=unsafe):
                    with self.assertRaisesRegex(RuntimeError, "lock"):
                        runner._acquire_lock(
                            regular_path,
                            stat_path=lambda path: parent_stat,
                            stat_fd=lambda fd, result=unsafe: result,
                        )
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(regular_path)

    def test_lock_rejects_unsafe_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "lock")
            for parent in (safe_parent_stat(uid=1000), safe_parent_stat(mode=0o777)):
                with self.subTest(parent=parent), self.assertRaisesRegex(RuntimeError, "lock"):
                    runner._acquire_lock(path, stat_path=lambda ignored, result=parent: result)

    def test_lock_accepts_root_owned_sticky_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "lock")
            fd = runner._acquire_lock(
                path,
                stat_path=lambda ignored: safe_parent_stat(mode=0o1777),
                stat_fd=lambda lock_fd: lock_stat(),
            )
            os.close(fd)

    def test_lock_contention_fails_closed_and_uses_nonblocking_exclusive_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "lock")
            flock = Mock(side_effect=BlockingIOError())
            with self.assertRaisesRegex(RuntimeError, "already running"):
                runner._acquire_lock(
                    path,
                    stat_path=lambda ignored: safe_parent_stat(),
                    stat_fd=lambda fd: lock_stat(),
                    flock=flock,
                )
            self.assertEqual(flock.call_args.args[1], runner.fcntl.LOCK_EX | runner.fcntl.LOCK_NB)

    def test_backup_return_codes_propagate_from_popen(self):
        for code in (0, 2):
            process = FakeProcess(pid=4321, returncode=code, stderr=b"private protocol text")
            popen = Mock(return_value=process)
            with self.subTest(code=code):
                result_code, stderr = runner._run_backup(popen_factory=popen)
                self.assertEqual((result_code, stderr), (code, b"private protocol text"))
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertEqual(process.communicate_timeouts[0], 1200)

    def test_backup_timeout_kills_process_group_and_reaps_child(self):
        process = FakeProcess(pid=4321, returncode=-signal.SIGKILL, stderr=b"private protocol text", timeout_count=2)
        popen = Mock(return_value=process)
        with patch.object(runner.os, "killpg") as killpg:
            code, stderr = runner._run_backup(popen_factory=popen, cleanup_timeout=0.01)
        self.assertEqual(code, 124)
        self.assertEqual(stderr, b"private protocol text")
        self.assertEqual(killpg.call_args_list, [call(4321, signal.SIGTERM), call(4321, signal.SIGKILL)])
        self.assertTrue(process.reaped)
        self.assertEqual(process.communicate_count, 3)

    def test_backup_kills_group_after_term_even_when_leader_exits(self):
        process = FakeProcess(pid=4322, returncode=0, stderr=b"captured stderr", timeout_count=1)
        process.events = []
        original_communicate = process.communicate

        def communicate(timeout=None):
            process.events.append(("communicate", timeout))
            return original_communicate(timeout)

        process.communicate = communicate
        popen = Mock(return_value=process)

        def record_killpg(pid, sig):
            process.events.append(("killpg", sig))

        with patch.object(runner.os, "killpg", side_effect=record_killpg):
            code, stderr = runner._run_backup(popen_factory=popen, cleanup_timeout=0.01)

        self.assertEqual(code, 124)
        self.assertEqual(stderr, b"captured stderr")
        self.assertEqual(
            process.events,
            [
                ("communicate", 1200),
                ("killpg", signal.SIGTERM),
                ("communicate", 0.01),
                ("killpg", signal.SIGKILL),
                ("communicate", None),
            ],
        )
        self.assertTrue(process.reaped)

    def test_backup_timeout_tolerates_process_group_disappearing(self):
        process = FakeProcess(pid=4323, returncode=0, stderr=b"captured stderr", timeout_count=1)
        popen = Mock(return_value=process)
        with patch.object(runner.os, "killpg", side_effect=[None, ProcessLookupError()]):
            code, stderr = runner._run_backup(popen_factory=popen, cleanup_timeout=0.01)
        self.assertEqual((code, stderr), (124, b"captured stderr"))
        self.assertTrue(process.reaped)

    def test_report_artifacts_are_private_and_stdout_has_no_protocol_text(self):
        with tempfile.TemporaryDirectory() as directory:
            stderr = b"sensitive protocol text"
            report = runner.make_report(2, 1.5, len(stderr), stderr)
            output = io.StringIO()
            with patch.object(runner.os, "fchown") as chown, contextlib.redirect_stdout(output):
                path = runner._write_report(stderr, report, report_dir=directory)
            self.assertEqual(chown.call_count, 2)
            self.assertEqual(chown.call_args.args[1:], (0, 0))
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            stderr_path = path.removesuffix(".json") + ".stderr"
            self.assertEqual(os.stat(stderr_path).st_mode & 0o777, 0o600)
            with open(stderr_path, "rb") as source:
                self.assertEqual(source.read(), stderr)
            with open(path, encoding="utf-8") as source:
                report_data = source.read()
            self.assertNotIn("sensitive protocol text", report_data)
            self.assertNotIn("sensitive protocol text", output.getvalue())
            self.assertEqual(json.loads(report_data)["complete"], False)

    def test_run_sync_prints_safe_summary_and_maps_exit_codes(self):
        for dsync_code, expected in ((0, 0), (2, 2)):
            process = FakeProcess(pid=4321, returncode=dsync_code, stderr=b"sensitive protocol text")
            captured = {}

            def writer(stderr, report):
                captured.update(stderr=stderr, report=report)
                return "/tmp/report.json"

            output = io.StringIO()
            with (
                self.subTest(dsync_code=dsync_code),
                patch.object(runner, "verify_fetchmail_disabled"),
                patch.object(runner, "verify_local_user"),
                patch.object(runner, "_acquire_lock", return_value=91),
                patch.object(runner.os, "close"),
                patch.object(runner, "_run_backup", return_value=(dsync_code, b"sensitive protocol text")),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(runner.run_sync(report_writer=writer), expected)
            self.assertNotIn("sensitive protocol text", output.getvalue())
            self.assertNotIn("sensitive protocol text", str(captured["report"]))
            self.assertEqual(captured["report"]["stderr_bytes"], len(captured["stderr"]))


def subprocess_timeout(command, output):
    import subprocess

    return subprocess.TimeoutExpired(command, 1, output=output, stderr=output)


def safe_parent_stat(uid=0, mode=0o755):
    return SimpleNamespace(st_uid=uid, st_mode=stat.S_IFDIR | mode)


def lock_stat(uid=0, mode=0o600):
    return SimpleNamespace(st_uid=uid, st_mode=stat.S_IFREG | mode)


class FakeProcess:
    def __init__(self, pid, returncode, stderr, timeout_count=0):
        self.pid = pid
        self.returncode = returncode
        self.stderr = stderr
        self.timeout_count = timeout_count
        self.communicate_count = 0
        self.communicate_timeouts = []
        self.reaped = False

    def communicate(self, timeout=None):
        import subprocess

        self.communicate_count += 1
        self.communicate_timeouts.append(timeout)
        if self.timeout_count:
            self.timeout_count -= 1
            raise subprocess.TimeoutExpired("backup", timeout, stderr=self.stderr)
        self.reaped = True
        return b"", self.stderr


if __name__ == "__main__":
    unittest.main()
