#!/usr/bin/python3
import fcntl
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone


DOVEADM = "/usr/bin/doveadm"
SSH = "/usr/bin/ssh"
KEY = "/etc/csn-standby-sync/id_ed25519"
KNOWN_HOSTS = "/etc/csn-standby-sync/known_hosts"
REMOTE = "umeoka@118.27.32.100"
LOCK = "/run/lock/csn-standby-sync.lock"
REPORT_DIR = "/var/lib/csn-standby-sync/reports"


def build_command():
    return [
        DOVEADM,
        "backup",
        "-R",
        "-u",
        "csn",
        "-l",
        "60",
        "-T",
        "600",
        SSH,
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={KNOWN_HOSTS}",
        "-o",
        "ConnectTimeout=20",
        "-i",
        KEY,
        REMOTE,
    ]


def is_complete(exit_code):
    return exit_code == 0


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def make_report(exit_code, elapsed_seconds, stderr_bytes, stderr, start_utc=None, end_utc=None):
    return {
        "start_utc": start_utc or _utc_now(),
        "end_utc": end_utc or _utc_now(),
        "elapsed_seconds": elapsed_seconds,
        "exit_code": exit_code,
        "stderr_bytes": stderr_bytes,
        "complete": is_complete(exit_code),
    }


def unit_state(unit, *, run=None, timeout=10):
    run = run or subprocess.run
    try:
        result = run(
            ["/usr/bin/systemctl", "is-active" if unit.endswith(".service") else "is-enabled", unit],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{unit} state check timed out") from exc
    return result.stdout.strip()


def verify_fetchmail_disabled(*, state_lookup=None):
    state_lookup = state_lookup or unit_state
    service_state = state_lookup("fetchmail-csn.service")
    if service_state != "inactive":
        raise RuntimeError(f"fetchmail-csn.service must be inactive (got {service_state or 'unknown'})")
    timer_state = state_lookup("fetchmail-csn.timer")
    if timer_state != "disabled":
        raise RuntimeError(f"fetchmail-csn.timer must be disabled (got {timer_state or 'unknown'})")


def verify_local_user(*, run=None, timeout=10):
    run = run or subprocess.run
    try:
        result = run([DOVEADM, "user", "csn"], capture_output=True, text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("doveadm user lookup timed out") from exc
    if result.returncode != 0:
        raise RuntimeError("doveadm user csn failed")
    fields = dict(re.findall(r"^\s*(uid|gid|home)\s*[=:]?\s*(\S+)\s*$", result.stdout, re.MULTILINE))
    if fields.get("uid") != "1001" or fields.get("gid") != "1001" or fields.get("home") != "/home/csn":
        raise RuntimeError("doveadm user csn must report UID/GID 1001 and /home/csn")


def _write_private(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, 0, 0)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(data)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_report(stderr, report, *, report_dir=REPORT_DIR):
    os.makedirs(report_dir, mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex
    stderr_path = os.path.join(report_dir, f"{stamp}-{suffix}.stderr")
    report_path = os.path.join(report_dir, f"{stamp}-{suffix}.json")
    _write_private(stderr_path, stderr)
    _write_private(report_path, (json.dumps(report, sort_keys=True) + "\n").encode("utf-8"))
    return report_path


def _validate_lock_parent(path, *, stat_path):
    parent = os.path.dirname(os.path.abspath(path)) or "/"
    try:
        parent_info = stat_path(parent)
    except OSError as exc:
        raise RuntimeError("sync lock directory is unavailable") from exc
    parent_mode = stat.S_IMODE(parent_info.st_mode)
    writable = parent_mode & 0o022
    sticky = bool(parent_mode & stat.S_ISVTX)
    if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != 0 or (writable and not sticky):
        raise RuntimeError("sync lock directory is not secure")


def _acquire_lock(path=LOCK, *, stat_path=None, stat_fd=None, open_file=None, flock=None, close=None):
    stat_path = stat_path or os.stat
    stat_fd = stat_fd or os.fstat
    open_file = open_file or os.open
    flock = flock or fcntl.flock
    close = close or os.close
    _validate_lock_parent(path, stat_path=stat_path)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise RuntimeError("safe sync lock opening is unavailable")
    flags = os.O_CREAT | os.O_RDWR | nofollow | getattr(os, "O_CLOEXEC", 0)
    try:
        lock_fd = open_file(path, flags, 0o600)
    except OSError as exc:
        raise RuntimeError("unable to safely open sync lock") from exc
    try:
        try:
            lock_info = stat_fd(lock_fd)
        except OSError as exc:
            raise RuntimeError("unable to validate sync lock") from exc
        lock_mode = stat.S_IMODE(lock_info.st_mode)
        if (
            not stat.S_ISREG(lock_info.st_mode)
            or lock_info.st_uid != 0
            or lock_mode & 0o022
        ):
            raise RuntimeError("sync lock file is not secure")
        try:
            flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another csn standby sync is already running") from exc
        except OSError as exc:
            raise RuntimeError("unable to acquire sync lock") from exc
        return lock_fd
    except BaseException:
        close(lock_fd)
        raise


def _run_backup(*, popen_factory=None, timeout=1200, cleanup_timeout=10):
    popen_factory = popen_factory or subprocess.Popen
    try:
        process = popen_factory(
            build_command(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise RuntimeError("unable to start backup process") from exc

    try:
        _, stderr = process.communicate(timeout=timeout)
        return process.returncode, stderr or b""
    except subprocess.TimeoutExpired as timeout_exc:
        partial_stderr = timeout_exc.stderr or b""
        if isinstance(partial_stderr, str):
            partial_stderr = partial_stderr.encode("utf-8", errors="replace")
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            _, stderr = process.communicate(timeout=cleanup_timeout)
        except subprocess.TimeoutExpired as cleanup_exc:
            if cleanup_exc.stderr:
                partial_stderr = cleanup_exc.stderr
                if isinstance(partial_stderr, str):
                    partial_stderr = partial_stderr.encode("utf-8", errors="replace")
            stderr = b""
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # communicate() is safe to call again after it already observed the
        # leader exit; this also waits for pipes to close after group cleanup.
        _, final_stderr = process.communicate()
        stderr = final_stderr or stderr
        if isinstance(stderr, str):
            stderr = stderr.encode("utf-8", errors="replace")
        return 124, stderr or partial_stderr


def run_sync(*, run=None, popen_factory=None, report_writer=None, lock_acquirer=None, monotonic=None):
    run = run or subprocess.run
    report_writer = report_writer or _write_report
    lock_acquirer = lock_acquirer or _acquire_lock
    monotonic = monotonic or time.monotonic
    verify_fetchmail_disabled()
    verify_local_user(run=run)

    lock_fd = lock_acquirer()

    try:
        start_utc = _utc_now()
        started = monotonic()
        exit_code, stderr = _run_backup(popen_factory=popen_factory)
        elapsed = monotonic() - started
        end_utc = _utc_now()
        report = make_report(exit_code, elapsed, len(stderr), "", start_utc, end_utc)
        report_path = report_writer(stderr, report)
        print(
            f"report_path={report_path} exit_code={exit_code} "
            f"elapsed_seconds={elapsed:.3f} complete={str(report['complete']).lower()}"
        )
        return 0 if report["complete"] else 2
    finally:
        os.close(lock_fd)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print("this command accepts no arguments", file=sys.stderr)
        return 2
    try:
        return run_sync()
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
