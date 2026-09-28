"""Engine configuration tests."""


def test_engine_pings_pooled_connections_before_use(tmp_path, monkeypatch):
    """Cloud SQL silently drops idle pooled connections between Cloud Run and
    the database; the next checkout then fails its first query with
    "server closed the connection unexpectedly" (14 prod 500s 2026-09-04 ->
    09-27). pool_pre_ping tests each connection on checkout and replaces a
    dead one, so the engine must be built with it on."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    import src.db.schema as schema

    monkeypatch.setattr(schema, "_engine", None)
    engine = schema.get_engine()
    try:
        assert engine.pool._pre_ping is True
    finally:
        engine.dispose()
