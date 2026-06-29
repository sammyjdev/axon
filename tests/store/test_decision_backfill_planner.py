from axon.store.decision_backfill import BackfillPlan, DecRef, content_key, plan_backfill


def _r(i, gh="", ck=""):
    return DecRef(id=i, git_hash=gh, content_key=ck or i)


def test_native_collision_is_renumbered_after_global_max():
    sqlite = [_r("dec-001", "h1"), _r("dec-002", "h2"), _r("dec-003", "h3")]
    pg = [_r("dec-001", "x1"), _r("dec-002", "x2")]  # collide by id, different git_hash
    plan = plan_backfill(sqlite, pg)
    assert plan.copy_legacy == ("dec-001", "dec-002", "dec-003")
    assert plan.renumber == (("dec-001", "dec-004"), ("dec-002", "dec-005"))
    assert plan.skip_dup == ()


def test_duplicate_by_git_hash_is_dropped_not_renumbered():
    sqlite = [_r("dec-001", "h1"), _r("dec-002", "h2")]
    pg = [_r("dec-001", "h2")]  # same git_hash as sqlite dec-002 -> duplicate
    plan = plan_backfill(sqlite, pg)
    assert plan.skip_dup == ("dec-001",)
    assert plan.renumber == ()


def test_noncolliding_pg_native_is_left_alone():
    sqlite = [_r("dec-001", "h1")]
    pg = [_r("dec-200", "x9")]  # native, no id collision -> no action
    plan = plan_backfill(sqlite, pg)
    assert plan.renumber == () and plan.skip_dup == ()
    assert plan.copy_legacy == ("dec-001",)


def test_empty_git_hash_duplicate_matched_by_content():
    sqlite = [_r("dec-001", "", ck="same-content")]
    pg = [_r("dec-001", "", ck="same-content")]  # empty git_hash, identical content
    plan = plan_backfill(sqlite, pg)
    assert plan.skip_dup == ("dec-001",) and plan.renumber == ()


def test_empty_git_hash_native_is_renumbered_when_content_differs():
    sqlite = [_r("dec-001", "", ck="legacy-content")]
    pg = [_r("dec-001", "", ck="new-content")]
    plan = plan_backfill(sqlite, pg)
    assert plan.renumber == (("dec-001", "dec-002"),) and plan.skip_dup == ()


def test_content_key_excludes_id():
    a = content_key({"id": "dec-001", "summary": "s", "repo": "r"})
    b = content_key({"id": "dec-999", "summary": "s", "repo": "r"})
    assert a == b  # id is excluded, rest identical
