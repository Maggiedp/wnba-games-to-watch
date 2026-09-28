"""Tests for the shared helpers in conftest.py.

These exist because the helpers are load-bearing for OTHER tests' verdicts. A
concurrency helper that quietly ran one thread, or ran n threads one after the
other, would not fail loudly -- it would make every single-flight test pass
vacuously, since `builds["n"] == 1` is exactly what a single serial caller
produces. The assertions below are what keep those tests able to fail.
"""

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path


def test_run_concurrently_runs_the_callable_once_per_thread(run_concurrently):
    calls = []
    calls_lock = threading.Lock()

    def record():
        with calls_lock:
            calls.append(1)

    run_concurrently(5, record)

    assert len(calls) == 5


def test_run_concurrently_overlaps_the_threads(run_concurrently):
    # The property the single-flight tests actually depend on: the callables must
    # be in flight AT THE SAME TIME, so a build held open by one thread is still
    # open when the others arrive. Run serially, every one of those tests would
    # count one build and report PASS while proving nothing.
    live = {"now": 0, "peak": 0}
    live_lock = threading.Lock()

    def overlap():
        with live_lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        time.sleep(0.05)
        with live_lock:
            live["now"] -= 1

    run_concurrently(5, overlap)

    assert live["peak"] > 1, "threads ran serially, not concurrently"
    assert live["now"] == 0  # every thread was joined before returning


def test_a_raising_worker_fails_the_test_even_when_its_assertions_pass(tmp_path):
    # A bare Thread SWALLOWS whatever its target raises: Python routes it to
    # threading.excepthook and pytest downgrades it to a warning, so a test
    # asserting only a side effect (a build count, a send count) stays green
    # while every concurrent caller died. The helper no longer catches worker
    # exceptions itself, so pytest.ini's filter is the ONLY thing that turns
    # that warning into a failure. Run it against the repo's real pytest.ini in
    # a child session, with workers that raise and assertions that pass.
    repo = Path(__file__).resolve().parent.parent
    shutil.copy(repo / "pytest.ini", tmp_path / "pytest.ini")
    (tmp_path / "test_inner.py").write_text(
        "from tests.conftest import run_threads_concurrently\n"
        "\n"
        "def test_inner():\n"
        "    def boom():\n"
        "        raise RuntimeError('worker failed')\n"
        "    run_threads_concurrently(5, boom)\n"
        "    assert True\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "test_inner.py"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(repo)},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 failed" in result.stdout
    # Every worker's exception is reported, not only the last one.
    assert result.stdout.count("RuntimeError: worker failed") >= 5
