import json
import os
from pathlib import Path

import pytest

from xasp.file_lock import InterProcessFileLock, LockUnavailableError


def test_live_owner_cannot_be_displaced_and_release_allows_reacquisition(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime.lock"
    first = InterProcessFileLock(path, timeout_s=0)
    second = InterProcessFileLock(path, timeout_s=0)

    first.acquire()
    with pytest.raises(LockUnavailableError, match="another active process"):
        second.acquire()

    first.release()
    second.acquire()
    second.release()

    assert not path.exists()


def test_release_retries_transient_windows_unlink_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "runtime.lock"
    original_unlink = Path.unlink
    attempts = 0

    def flaky_unlink(target: Path, *args: object, **kwargs: object) -> None:
        nonlocal attempts
        if target == path and attempts < 2:
            attempts += 1
            raise PermissionError("simulated transient Windows lock")
        original_unlink(target, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    lock = InterProcessFileLock(
        path,
        timeout_s=0,
        release_retries=3,
        release_retry_delay_s=0,
    )

    lock.acquire()
    lock.release()

    assert attempts == 2
    assert not path.exists()


def test_lock_inspection_retries_transient_windows_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "runtime.lock"
    path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "token": "no-longer-active",
                "created_at_ns": 1,
            }
        ),
        encoding="utf-8",
    )
    original_read_bytes = Path.read_bytes
    attempts = 0

    def flaky_read_bytes(target: Path) -> bytes:
        nonlocal attempts
        if target == path and attempts == 0:
            attempts += 1
            raise PermissionError("simulated transient Windows lock")
        return original_read_bytes(target)

    monkeypatch.setattr(Path, "read_bytes", flaky_read_bytes)
    lock = InterProcessFileLock(
        path,
        timeout_s=0.1,
        poll_interval_s=0.001,
    )

    lock.acquire()
    lock.release()

    assert attempts == 1
    assert not path.exists()


def test_lock_creation_retries_transient_windows_permission_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "runtime.lock"
    original_open = os.open
    attempts = 0

    def flaky_open(
        path_arg: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal attempts
        if os.fspath(path_arg) == os.fspath(path) and attempts == 0:
            attempts += 1
            raise PermissionError("simulated transient Windows lock creation")
        return original_open(path_arg, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", flaky_open)
    lock = InterProcessFileLock(
        path,
        timeout_s=0.1,
        poll_interval_s=0.001,
    )

    lock.acquire()
    lock.release()

    assert attempts == 1
    assert not path.exists()


def test_lock_creation_permission_failure_respects_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "runtime.lock"

    def denied_open(
        path_arg: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        raise PermissionError("simulated persistent Windows lock creation")

    monkeypatch.setattr(os, "open", denied_open)
    lock = InterProcessFileLock(path, timeout_s=0)

    with pytest.raises(LockUnavailableError, match="another active process") as captured:
        lock.acquire()

    assert isinstance(captured.value.__cause__, PermissionError)


def test_orphaned_lock_from_current_process_is_recovered(tmp_path: Path) -> None:
    path = tmp_path / "runtime.lock"
    path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "token": "no-longer-active",
                "created_at_ns": 1,
            }
        ),
        encoding="utf-8",
    )

    lock = InterProcessFileLock(path, timeout_s=0)
    lock.acquire()
    lock.release()

    assert not path.exists()
