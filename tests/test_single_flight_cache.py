"""Contract tests for _SingleFlightTTLCache.

Extracted from three hand-rolled copies (shot baseline, replay-live slate, live
playoff odds), so the properties below are now shared by all three callers and a
regression in one place breaks all three at once. Each test pins a property that
a plausible future "simplification" would silently remove.
"""

import threading
import time

import src.api.app as app

Cache = app._SingleFlightTTLCache


def test_a_falsy_value_is_a_real_hit_not_a_miss():
    # THE trap this class exists to avoid. _live_playoff_odds_rows returns []
    # for "live mode does not apply", which is the COMMON case. A cache that
    # tests the value's truthiness instead of its presence would treat every []
    # as a miss and rerun a ~1.2s Monte Carlo on every single request.
    cache = Cache(ttl_s=60)
    builds = {"n": 0}

    def build():
        builds["n"] += 1
        return []

    assert cache.get(None, build) == []
    assert cache.get(None, build) == []
    assert builds["n"] == 1  # second call served from cache, not rebuilt


def test_a_new_key_evicts_the_previous_entry():
    # _get_shot_baseline keys on season: after a rollover the prior year's
    # baseline must not be served for the new one.
    cache = Cache(ttl_s=60)
    assert cache.get(2025, lambda: "old") == "old"
    assert cache.get(2026, lambda: "new") == "new"
    # ...and the evicted key rebuilds rather than resurrecting a stale value.
    assert cache.get(2025, lambda: "rebuilt") == "rebuilt"


def test_an_expired_entry_rebuilds():
    cache = Cache(ttl_s=0.05)
    assert cache.get(None, lambda: 1) == 1
    time.sleep(0.08)
    assert cache.get(None, lambda: 2) == 2


def test_a_raising_build_is_not_cached():
    # /api/replay-live surfaces a scoreboard outage as a 502. If the raise were
    # cached, one blip would darken the strip for a whole TTL; if it were cached
    # as a value, the next caller would be handed an exception object as data.
    cache = Cache(ttl_s=60)

    def boom():
        raise RuntimeError("scoreboard down")

    for _ in range(2):
        try:
            cache.get(None, boom)
            raise AssertionError("expected the build to propagate")
        except RuntimeError:
            pass
    assert cache.get(None, lambda: "recovered") == "recovered"


def test_concurrent_cold_callers_collapse_into_one_build():
    # The whole point on --max-instances=1: N viewers must wait on ONE build,
    # not start N. Without the build lock this counts 5.
    cache = Cache(ttl_s=60)
    builds = {"n": 0}
    counter_lock = threading.Lock()

    def slow_build():
        with counter_lock:
            builds["n"] += 1
        time.sleep(0.1)  # hold it open so the other threads pile up
        return "value"

    results = []
    threads = [
        threading.Thread(target=lambda: results.append(cache.get(None, slow_build)))
        for _ in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert builds["n"] == 1
    assert results == ["value"] * 5  # every waiter got the winner's value


def test_a_slow_build_does_not_block_readers_of_a_fresh_entry():
    # _lock is never held across a build. If it were, a cache hit would queue
    # behind an in-flight rebuild for another key and the cache would serialize
    # exactly the readers it exists to speed up.
    cache = Cache(ttl_s=60)
    cache.get("warm", lambda: "warm-value")

    started = threading.Event()
    release = threading.Event()

    def slow_build():
        started.set()
        release.wait(timeout=5)
        return "cold-value"

    builder = threading.Thread(target=lambda: cache.get("cold", slow_build))
    builder.start()
    started.wait(timeout=5)
    # The builder holds _build_lock and is mid-build; a read must not block.
    got = []
    reader = threading.Thread(
        target=lambda: got.append(cache.get("warm", lambda: "rebuilt"))
    )
    reader.start()
    reader.join(timeout=2)
    assert not reader.is_alive(), "a read blocked behind an in-flight build"
    release.set()
    builder.join(timeout=5)
    assert got == ["warm-value"]


def test_clear_drops_the_entry():
    cache = Cache(ttl_s=60)
    assert cache.get(None, lambda: "first") == "first"
    cache.clear()
    assert cache.get(None, lambda: "second") == "second"
