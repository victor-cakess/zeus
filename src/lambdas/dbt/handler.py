import os
import select

from shared import paths, s3_io, ssm, time_window

BUCKET = os.environ["BUCKET"]
KEY_SSM_PATH = os.environ["SNOWFLAKE_PRIVATE_KEY_SSM_PATH"]
PROJECT_DIR = os.path.join(os.environ["LAMBDA_TASK_ROOT"], "transform")


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


def _summarize(res) -> dict:
    """Count models built and tests by status from a dbtRunner result. res.result
    can be None (e.g. parse/connection failure before anything ran)."""
    results = getattr(res.result, "results", None) or []
    models_built = sum(
        1
        for r in results
        if str(r.node.resource_type) == "model" and str(r.status) == "success"
    )
    tests = [r for r in results if str(r.node.resource_type) == "test"]
    failed_tests = [r.node.name for r in tests if str(r.status) in ("fail", "error")]
    return {
        "status": "ok" if res.success else "failed",
        "models_built": models_built,
        "tests_passed": sum(1 for r in tests if str(r.status) == "pass"),
        "tests_failed": len(failed_tests),
        "failed_tests": failed_tests,
        "error": str(res.exception) if res.exception else None,
    }


def lambda_handler(event, context) -> dict:
    key_path = "/tmp/transformer.p8"
    with open(key_path, "w") as f:
        f.write(ssm.get_parameter(KEY_SSM_PATH))
    os.environ["SNOWFLAKE_PRIVATE_KEY_FILE"] = key_path
    os.environ["HOME"] = "/tmp"  # /var/task is read-only; dbt writes ~/.dbt

    # Lambda's sandbox has no /dev/shm, so multiprocessing SemLocks raise ENOENT.
    # dbt's parallelism is threads-only; two thread-backed swaps make it run:
    #  - dbt.mp_context: the locks dbt itself creates (Manifest binds
    #    get_mp_context().Lock at class-definition time, so patch BEFORE the
    #    dbt.cli import);
    #  - the default context's SimpleQueue: ThreadPool's change notifier is
    #    SemLock-backed even though all its work queues are plain thread queues.
    import multiprocessing
    import multiprocessing.dummy

    import dbt.mp_context

    dbt.mp_context._MP_CONTEXT = multiprocessing.dummy
    multiprocessing.get_context().SimpleQueue = _PipeNotifier

    from dbt.cli.main import dbtRunner

    res = dbtRunner().invoke(
        [
            "build",
            "--project-dir", PROJECT_DIR,
            "--profiles-dir", PROJECT_DIR,
            "--target-path", "/tmp/target",
            "--log-path", "/tmp/logs",
        ]
    )

    today = time_window.today_utc()
    summary = {"date": today.isoformat(), **_summarize(res)}
    # Report goes to S3 before raising, so the digest can read it even on failure.
    s3_io.put_json(BUCKET, paths.report_key("dbt", today), summary)

    if not res.success:
        raise RuntimeError(f"dbt build failed: {summary}")
    return summary
