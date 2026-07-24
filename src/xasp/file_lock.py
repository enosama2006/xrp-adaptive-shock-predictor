"""Small cross-process file lock used by local XASP stores and runtimes."""

from __future__ import annotations

import ctypes
import json
import os
import time
from pathlib import Path
from threading import Lock
from types import TracebackType
from uuid import uuid4


class LockUnavailableError(RuntimeError):
    """Raised when an active process keeps a lock beyond the allowed timeout."""


_ACTIVE_LOCKS_GUARD = Lock()
_ACTIVE_LOCK_TOKENS: dict[str, str] = {}


class InterProcessFileLock:
    """Portable lock based on atomic exclusive file creation.

    The lock records its owning PID and a unique token. A lock left behind by a
    terminated process can be recovered, while a live owner is never displaced.
    """

    def __init__(
        self,
        path: Path,
        *,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.05,
        invalid_lock_stale_after_s: float = 300.0,
        release_retries: int = 5,
        release_retry_delay_s: float = 0.05,
    ) -> None:
        if timeout_s < 0:
            raise ValueError("timeout_s must be non-negative")
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive")
        if release_retries < 1:
            raise ValueError("release_retries must be at least one")
        if release_retry_delay_s < 0:
            raise ValueError("release_retry_delay_s must be non-negative")
        self.path = path
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self.invalid_lock_stale_after_s = invalid_lock_stale_after_s
        self.release_retries = release_retries
        self.release_retry_delay_s = release_retry_delay_s
        self._token = uuid4().hex
        self._acquired = False

    @property
    def _registry_key(self) -> str:
        return os.path.normcase(os.path.abspath(self.path))

    @staticmethod
    def _process_is_running(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            # os.kill(pid, 0) is not a safe existence probe on Windows: Python
            # maps non-console signals to TerminateProcess. Query the process
            # handle instead and never signal the owner.
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            process_query_limited_information = 0x1000
            synchronize = 0x00100000
            handle = kernel32.OpenProcess(
                process_query_limited_information | synchronize,
                False,
                pid,
            )
            if not handle:
                # Access denied means that the process exists but cannot be
                # inspected by this user. Other failures mean it is gone.
                return int(kernel32.GetLastError()) == 5
            try:
                wait_timeout = 0x00000102
                return int(kernel32.WaitForSingleObject(handle, 0)) == wait_timeout
            finally:
                kernel32.CloseHandle(handle)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            # Platform-specific errors are treated conservatively: do not
            # displace a lock unless the OS explicitly says the PID is gone.
            return True
        return True

    def _break_stale_lock(self) -> bool:
        try:
            original = self.path.read_bytes()
            stat = self.path.stat()
        except FileNotFoundError:
            return True

        stale = False
        try:
            payload = json.loads(original.decode("utf-8"))
            pid = int(payload["pid"])
            token = str(payload["token"])
            if pid == os.getpid():
                with _ACTIVE_LOCKS_GUARD:
                    stale = _ACTIVE_LOCK_TOKENS.get(self._registry_key) != token
            else:
                stale = not self._process_is_running(pid)
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            age_s = max(0.0, time.time() - stat.st_mtime)
            stale = age_s >= self.invalid_lock_stale_after_s

        if not stale:
            return False

        try:
            if self.path.read_bytes() != original:
                return False
            self.path.unlink()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    def acquire(self) -> None:
        if self._acquired:
            raise RuntimeError(f"lock is already acquired: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_s
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "token": self._token,
                "created_at_ns": time.time_ns(),
            },
            sort_keys=True,
        ).encode("utf-8")

        while True:
            descriptor: int | None = None
            creation_error: FileExistsError | None = None
            with _ACTIVE_LOCKS_GUARD:
                try:
                    descriptor = os.open(
                        self.path,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                        0o600,
                    )
                except FileExistsError as exc:
                    creation_error = exc
                else:
                    try:
                        os.write(descriptor, payload)
                    finally:
                        os.close(descriptor)
                    _ACTIVE_LOCK_TOKENS[self._registry_key] = self._token
                    self._acquired = True
                    return

            if creation_error is not None:
                if self._break_stale_lock():
                    continue
                if time.monotonic() >= deadline:
                    raise LockUnavailableError(
                        f"lock is held by another active process: {self.path}"
                    ) from creation_error
                time.sleep(self.poll_interval_s)
                continue

            raise RuntimeError(f"failed to create lock without an OS error: {self.path}")

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            for attempt in range(self.release_retries):
                try:
                    payload = json.loads(self.path.read_text(encoding="utf-8"))
                    if payload.get("token") != self._token:
                        break
                    self.path.unlink()
                    break
                except FileNotFoundError:
                    break
                except (OSError, ValueError, json.JSONDecodeError):
                    if attempt + 1 >= self.release_retries:
                        break
                    time.sleep(self.release_retry_delay_s * (attempt + 1))
        finally:
            with _ACTIVE_LOCKS_GUARD:
                if _ACTIVE_LOCK_TOKENS.get(self._registry_key) == self._token:
                    del _ACTIVE_LOCK_TOKENS[self._registry_key]
            self._acquired = False

    def __enter__(self) -> InterProcessFileLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
