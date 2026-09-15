
import pytest

from axon.activity.sanitize import sanitize_event
from axon.activity.spool import activity_spool_paths, drain_spool, spool_event


@pytest.fixture(autouse=True)
def isolate_spool(monkeypatch, tmp_path):
    monkeypatch.setattr("axon.activity.spool.data_root", lambda: tmp_path)
    return tmp_path


async def test_replaying_the_same_spool_item_creates_one_event():
    event = {"event_id": "evt-123", "data": "test"}
    
    # spool twice
    await spool_event(event)
    await spool_event(event)

    db = {}
    async def fake_sink(payload: dict) -> None:
        db[payload["event_id"]] = payload

    res = await drain_spool(None, sink=fake_sink)
    assert res.processed >= 1
    assert len(db) == 1
    assert db["evt-123"] == event


class RetryableDBError(Exception):
    pass


async def test_database_failure_keeps_sanitized_spool_item():
    event = {"event_id": "evt-failed", "data": "test"}
    spooled_path = await spool_event(event)
    
    assert spooled_path.exists()

    async def failing_sink(payload: dict) -> None:
        raise RetryableDBError("DB is down")

    res = await drain_spool(
        None,
        sink=failing_sink,
        is_retryable=lambda e: isinstance(e, RetryableDBError)
    )
    
    assert res.retried >= 1
    assert spooled_path.exists()


async def test_partial_or_corrupt_spool_item_is_recoverable_or_quarantined():
    paths = activity_spool_paths()
    paths.pending_dir.mkdir(parents=True, exist_ok=True)
    bad_file = paths.pending_dir / "bad.json"
    bad_file.write_text("not-a-json-dict")

    async def noop_sink(payload: dict) -> None:
        pass

    res = await drain_spool(None, sink=noop_sink)
    
    assert res.quarantined >= 1
    assert not bad_file.exists()
    
    quarantined_files = list(paths.quarantine_dir.iterdir())
    assert len(quarantined_files) == 1
    assert paths.quarantine_log.exists()
    assert paths.quarantine_log.read_text().count("\n") == 1


async def test_no_unsanitized_secret_string_survives_spooling():
    raw_content = {"api_key": "sk-should-not-appear-12345"}
    sanitized_content, redactions, coverage = sanitize_event(raw_content)
    
    event = {
        "event_id": "evt-secret",
        "content": sanitized_content,
        "redactions": redactions,
        "coverage": coverage
    }
    
    spooled_path = await spool_event(event)
    
    raw_bytes = spooled_path.read_bytes()
    assert b"sk-should-not-appear-12345" not in raw_bytes
    assert b"[REDACTED:credential]" in raw_bytes
