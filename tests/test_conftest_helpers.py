"""Tests for the shared helpers in conftest.py.

These exist because the helpers are load-bearing for OTHER tests' verdicts. A
concurrency helper that quietly ran one thread, or ran n threads one after the
other, would not fail loudly -- it would make every single-flight test pass
vacuously, since `builds["n"] == 1` is exactly what a single serial caller
produces. The assertions below are what keep those tests able to fail.
"""

import threading
import time

import pytest


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


def test_run_concurrently_reraises_a_worker_failure(run_concurrently):
    # Without this, a bare Thread swallows the raise: Python routes it to
    # threading.excepthook, pytest downgrades it to a warning, and the caller's
    # assertions run anyway. Measured before the fix -- 5 raising workers
    # reported "1 passed". A test asserting only a side effect (build count,
    # send count) would stay green while every concurrent caller failed.
    def boom():
        raise RuntimeError("worker failed")

    with pytest.raises(ExceptionGroup) as excinfo:
        run_concurrently(5, boom)

    assert len(excinfo.value.exceptions) == 5
    assert all(isinstance(e, RuntimeError) for e in excinfo.value.exceptions)


def test_run_concurrently_joins_every_thread_before_reraising(run_concurrently):
    # The raise must not short-circuit the joins, or a surviving worker keeps
    # mutating shared state while the caller is already handling the failure.
    finished = []
    finished_lock = threading.Lock()

    def half_fail():
        time.sleep(0.05)
        with finished_lock:
            finished.append(1)
            n = len(finished)
        if n == 1:
            raise RuntimeError("one worker failed")

    with pytest.raises(ExceptionGroup):
        run_concurrently(4, half_fail)

    assert len(finished) == 4  # all four ran to completion despite the failure
