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
