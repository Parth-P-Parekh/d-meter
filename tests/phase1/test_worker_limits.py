import subprocess
import sys

from inspection_platform.worker_limits import WorkerJob


def test_worker_job_accepts_one_process_with_approved_limits() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_SUSPENDED,
    )
    try:
        with WorkerJob(memory_bytes=2 * 1024**3, cpu_percent=25) as job:
            job.assign_process(process._handle)
            process.resume()
            assert process.poll() is None
        process.wait(timeout=5)
        assert process.returncode is not None
    finally:
        if process.poll() is None:
            process.kill()
