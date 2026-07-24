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
