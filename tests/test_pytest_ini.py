"""Tests for the repo's pytest.ini.

Its PytestUnhandledThreadExceptionWarning filter is the only thing that fails a
test whose worker threads raise (see the comment in pytest.ini). Nothing else
would notice if that line were deleted, so it is pinned here by running the real
pytest.ini in a child session.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

_INNER = """\
import threading

from tests.conftest import run_threads_concurrently


def boom():
    raise RuntimeError("worker failed")


def test_bare_threads():
    threads = [threading.Thread(target=boom) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert True


def test_run_threads_concurrently():
    run_threads_concurrently(5, boom)
    assert True
"""


def test_a_raising_worker_fails_the_test_even_when_its_assertions_pass(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    shutil.copy(repo / "pytest.ini", tmp_path / "pytest.ini")
    (tmp_path / "test_inner.py").write_text(_INNER)

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "test_inner.py"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(repo)},
        capture_output=True,
        text=True,
    )

    # Both fail: the filter covers any bare thread, and the helper adds no layer
    # that swallows the raise before the filter sees it.
    assert "2 failed" in result.stdout, result.stdout + result.stderr
    # Every worker's exception is reported, not only the last one.
    assert result.stdout.count("RuntimeError: worker failed") >= 10
