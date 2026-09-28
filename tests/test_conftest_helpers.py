"""Tests for the shared helpers in conftest.py.

These exist because the helpers are load-bearing for OTHER tests' verdicts. A
concurrency helper that quietly ran one thread, or ran n threads one after the
other, would not fail loudly -- it would make every single-flight test pass
vacuously, since `builds["n"] == 1` is exactly what a single serial caller
produces. The assertions below are what keep those tests able to fail.
"""

import threading
import time


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
