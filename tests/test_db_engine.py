"""Engine configuration tests."""


def test_engine_pings_pooled_connections_before_use(env):
    """Cloud SQL silently drops idle pooled connections between Cloud Run and
    the database; the next checkout then fails its first query with
    "server closed the connection unexpectedly" (14 prod 500s 2026-09-04 ->
    09-27). pool_pre_ping tests each connection on checkout and replaces a
    dead one, so the engine must be built with it on."""
    assert env.get_engine().pool._pre_ping is True
