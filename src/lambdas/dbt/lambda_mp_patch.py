"""Lambda-runtime compatibility patches for dbt.

Lambda's sandbox has no /dev/shm, so multiprocessing SemLocks raise
FileNotFoundError. dbt's parallelism is threads-only; two thread-backed
swaps make it run:
 - dbt.mp_context: the locks dbt itself creates (Manifest binds
   get_mp_context().Lock at class-definition time, so apply() must run
   BEFORE the dbt.cli import);
 - the default context's SimpleQueue: ThreadPool's change notifier is
   SemLock-backed even though all its work queues are plain thread queues.

Both swaps reach into private internals — re-verify them on any dbt or
Python upgrade.
"""

import multiprocessing
import multiprocessing.dummy
import os
import select


class _PipeNotifier:
    """Stand-in for the SemLock-backed SimpleQueue that multiprocessing's
    ThreadPool uses purely as a wake-up channel for its worker-handler thread
    (put/get/empty + a selectable _reader fd). Lambda has no /dev/shm, so the
    real one can't be constructed; a pipe needs no semaphores."""

    def __init__(self):
        self._rfd, self._wfd = os.pipe()
        self._reader = self._rfd  # connection.wait() registers raw fds

    def put(self, obj) -> None:
        os.write(self._wfd, b"\x00")

    def get(self):
        os.read(self._rfd, 1)
        return None

    def empty(self) -> bool:
        return not select.select([self._rfd], [], [], 0)[0]

    def __del__(self):
        for fd in (self._rfd, self._wfd):
            try:
                os.close(fd)
            except OSError:
                pass


def apply() -> None:
    """Call BEFORE importing dbt.cli — Manifest binds get_mp_context().Lock at
    class-definition time."""
    import dbt.mp_context

    dbt.mp_context._MP_CONTEXT = multiprocessing.dummy
    multiprocessing.get_context().SimpleQueue = _PipeNotifier
