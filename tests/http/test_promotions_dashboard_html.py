"""Static contract tests for the read-only promotion dashboard."""

from axon.http.promotions_dashboard import PROMOTIONS_DASHBOARD_HTML


def test_dashboard_is_read_only_and_self_contained() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    assert "read-only decision support" in html.lower()
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


def test_dashboard_uses_system_typography_and_violet_as_its_only_accent() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    assert "font-family: system-ui, sans-serif;" in html
    assert "font-family: ui-monospace, monospace;" in html
    for forbidden in (
        "IBM Plex Sans",
        "Space Grotesk",
        "JetBrains Mono",
        "--cyan",
        "evidence-value",
    ):
        assert forbidden not in html


def test_dashboard_covers_decision_states_and_terminal_confession_design() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    for token in (
        "#0B0A0E",
        "#131019",
        "#2C2738",
        "#ECE8F2",
        "#9B93AD",
        "#9D7AE8",
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
    assert 'fetch("/api/promotion-candidates", {' in html
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


def test_selection_focuses_the_rendered_candidate_heading() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    click_handler = html.split('button.addEventListener("click"', 1)[1].split(
        "queue.appendChild(button)", 1
    )[0]
    assert "const heading = renderCandidate(candidates[index])" in click_handler
    assert "heading.focus()" in click_handler
    assert 'heading.setAttribute("tabindex", "-1")' in html
    assert "return heading" in html


def test_header_and_unsupported_state_name_the_owning_next_step() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    assert "Review evidence here, then update" in html
    assert "promotion/candidates.json in the owning evidence repository" in html
    assert "Add this target to models.json in the owning configuration" in html
    assert "repository before promotion." in html
    assert "leave this surface to act" not in html
    assert "Target capability unsupported" not in html
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
        'addListField(decisionEvidence, "Why blocked", candidate.blockers)',
        'addListField(decisionEvidence, "Evidence needed", candidate.evidence_requests)',
    ):
        assert field in html


def test_status_shows_published_and_read_source_times() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    assert '<span id="status" aria-live="polite">' in html
    assert '<span class="source-time" id="source-time"></span>' in html
    assert '"Published " + payload.generated_at' in html
    assert '"Read " + payload.observed_at' in html


def test_refresh_clears_source_times_before_reading_again() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    busy_branch = html.split("if (busy) {", 1)[1].split("}", 1)[0]
    assert 'sourceTime.textContent = ""' in busy_branch


def test_refresh_has_a_bounded_timeout_and_cleans_up_the_timer() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    assert "new AbortController()" in html
    assert "setTimeout" in html
    assert "10000" in html
    assert "signal: controller.signal" in html
    assert "clearTimeout(timeoutId)" in html


def test_refresh_marks_the_workspace_busy_and_keeps_the_button_disabled() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    assert 'class="workspace" id="workspace" aria-busy="false"' in html
    assert 'workspace.setAttribute("aria-busy", busy ? "true" : "false")' in html
    assert "refreshButton.disabled = busy" in html
    assert html.count('aria-live="polite"') == 1


def test_refresh_maps_known_source_errors_and_has_stable_fallbacks() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    assert "function sourceErrorMessage(code)" in html
    for code in (
        "PROMOTION_SOURCE_NOT_CONFIGURED",
        "PROMOTION_SOURCE_UNAVAILABLE",
        "PROMOTION_SOURCE_TOO_LARGE",
        "PROMOTION_SCHEMA_INVALID",
    ):
        assert code in html
    assert "Set AXON_EVIDENCE_REPO" in html
    assert "promotion/candidates.json" in html
    assert "The promotion source returned invalid JSON." in html
    assert "The promotion source request timed out." in html


def test_decision_evidence_precedes_native_technical_provenance() -> None:
    html = PROMOTIONS_DASHBOARD_HTML

    blocker = 'addListField(decisionEvidence, "Why blocked", candidate.blockers)'
    request = (
        'addListField(decisionEvidence, "Evidence needed", candidate.evidence_requests)'
    )
    provenance = 'addText(provenance, "summary", "", "Technical provenance")'

    assert html.index(blocker) < html.index(provenance)
    assert html.index(request) < html.index(provenance)
    assert 'document.createElement("details")' in html
    assert 'addText(provenance, "summary", "", "Technical provenance")' in html
