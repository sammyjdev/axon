"""Static contract tests for the read-only promotion dashboard."""

from axon.http.promotions_dashboard import PROMOTIONS_DASHBOARD_HTML


def test_dashboard_is_read_only_and_self_contained() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    assert "read only" in html.lower()
    assert "/api/promotion-candidates" in html
    for forbidden in (
        "<form",
        "method=",
        'fetch("http',
        "file://",
        "setInterval",
        "innerHTML",
    ):
        assert forbidden not in html


def test_dashboard_has_accessible_interaction_contract() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    assert 'aria-live="polite"' in html
    assert 'id="refresh"' in html
    assert 'document.createElement("button")' in html
    assert ".textContent" in html
    assert "prefers-reduced-motion" in html


def test_dashboard_covers_decision_states_and_terminal_confession_design() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    for token in (
        "#0B0A0E",
        "#131019",
        "#2C2738",
        "#ECE8F2",
        "#9B93AD",
        "#9D7AE8",
        "#4EC9E8",
        "Space Grotesk",
        "IBM Plex Sans",
        "JetBrains Mono",
        "max-width: 700px",
        "max-width: 430px",
    ):
        assert token in html

    for state in (
        "Loading",
        "No promotion candidates",
        "Source error",
        "Stale evidence",
        "Unsupported candidate",
        "Request evidence",
    ):
        assert state in html

    assert 'setAttribute("aria-pressed"' in html
    assert "async function refresh()" in html
    assert 'fetch("/api/promotion-candidates")' in html
    assert "renderQueue(payload.candidates)" in html
    assert "renderCandidate(payload.candidates[0] || null)" in html
    assert "setBusy(false)" in html
    for field in (
        "candidate.candidate_id",
        "candidate.claim_id",
        "candidate.run_id",
        "candidate.disposition",
        "candidate.evidence_state",
        "candidate.target_state",
        "candidate.evidence_requests",
    ):
        assert field in html
    assert "firstValue" not in html
    for forbidden in ("Promote", "promotion command", "navigator.clipboard", 'id="copy"'):
        assert forbidden not in html


def test_queue_uses_candidate_identity_with_claim_as_secondary_metadata() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    assert 'addText(button, "span", "queue-id", candidate.candidate_id)' in html
    assert 'addText(button, "span", "queue-claim", candidate.claim_id)' in html


def test_selected_candidate_and_queue_text_remain_visible_on_mobile() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    assert 'button[aria-pressed="true"] {\n      border-color: var(--violet);' in html
    for selector in (".queue-id", ".queue-claim", ".queue-title"):
        assert f"{selector} {{" in html
        rule = html.split(f"{selector} {{", 1)[1].split("}", 1)[0]
        assert "overflow-wrap: anywhere" in rule


def test_status_is_the_only_live_region_and_selection_is_announced() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    assert html.count('aria-live="polite"') == 1
    assert '<article class="panel detail" id="detail">' in html
    assert 'announce("Selected " + candidate.candidate_id)' in html


def test_blocked_state_copy_matches_the_contract() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    assert "Target capability unsupported" in html
    assert "More evidence is required; promotion remains ineligible." in html


def test_stale_state_takes_priority_over_unsupported_target() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    stale_check = 'candidate.evidence_state === "stale"'
    unsupported_check = 'candidate.target_state === "unsupported"'
    assert html.index(stale_check) < html.index(unsupported_check)


def test_multi_value_evidence_uses_semantic_lists_and_safe_text() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    assert "function addListField" in html
    assert 'document.createElement("ul")' in html
    assert 'document.createElement("li")' in html
    assert "item.textContent = String(value)" in html
    for field in (
        'addListField(evidence, "Run limitations", candidate.run_limitations)',
        'addListField(evidence, "Blockers", candidate.blockers)',
        'addListField(evidence, "Evidence requests", candidate.evidence_requests)',
    ):
        assert field in html
