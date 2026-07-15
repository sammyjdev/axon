"""Self-contained HTML for the read-only Promotion Workbench."""

from __future__ import annotations

PROMOTIONS_DASHBOARD_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AXON Promotion Workbench</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0B0A0E;
      --surface: #131019;
      --line: #2C2738;
      --text: #ECE8F2;
      --dim: #9B93AD;
      --violet: #9D7AE8;
      --control-radius: 6px;
      --panel-radius: 14px;
    }

    * { box-sizing: border-box; }

    body {
      min-width: 320px;
      min-height: 100vh;
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, sans-serif;
      font-size: 15px;
      line-height: 1.55;
    }

    button { font: inherit; }

    button:focus-visible {
      outline: 2px solid var(--violet);
      outline-offset: 2px;
    }

    .shell {
      width: min(1180px, calc(100% - 40px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }

    .topbar {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 24px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--line);
    }

    .eyebrow,
    .state-label,
    .field dt,
    .queue-id,
    .source-note {
      font-family: ui-monospace, monospace;
      font-size: 12px;
      letter-spacing: 0.06em;
    }

    .eyebrow {
      margin: 0 0 6px;
      color: var(--violet);
      text-transform: uppercase;
    }

    h1,
    h2 {
      margin: 0;
      letter-spacing: -0.02em;
    }

    h1 { font-size: 30px; line-height: 1.1; }
    h2 { font-size: 22px; line-height: 1.2; }

    .read-only {
      margin: 7px 0 0;
      color: var(--dim);
      max-width: 64ch;
    }

    .refresh {
      min-height: 44px;
      padding: 0 16px;
      border: 1px solid var(--line);
      border-radius: var(--control-radius);
      background: transparent;
      color: var(--text);
      cursor: pointer;
      transition: border-color 160ms ease-out, color 160ms ease-out;
    }

    .refresh:hover { border-color: var(--violet); color: var(--violet); }
    .refresh:disabled { cursor: wait; color: var(--dim); }

    .status-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      min-height: 44px;
      color: var(--dim);
      font-family: ui-monospace, monospace;
      font-size: 12px;
    }

    .source-time { text-align: right; }

    .workspace {
      display: grid;
      grid-template-columns: minmax(220px, 0.72fr) minmax(0, 1.7fr);
      gap: 16px;
    }

    .panel {
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: var(--panel-radius);
      background: var(--surface);
    }

    .queue-panel { padding: 14px; }

    .panel-heading {
      padding: 4px 4px 12px;
      color: var(--dim);
      font-family: ui-monospace, monospace;
      font-size: 12px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }

    .queue {
      display: grid;
      gap: 7px;
    }

    .queue button {
      width: 100%;
      min-height: 58px;
      padding: 10px 11px;
      border: 1px solid transparent;
      border-radius: var(--control-radius);
      background: transparent;
      color: var(--dim);
      text-align: left;
      cursor: pointer;
      transition: background-color 160ms ease-out, border-color 160ms ease-out;
    }

    .queue button:hover { background: var(--bg); color: var(--text); }

    .queue button[aria-pressed="true"] {
      border-color: var(--violet);
      background: var(--bg);
      color: var(--text);
    }

    .queue-id { display: block; color: var(--violet); overflow-wrap: anywhere; }
    .queue-claim { display: block; margin-top: 2px; color: var(--dim); overflow-wrap: anywhere; }
    .queue-title { display: block; margin-top: 3px; overflow-wrap: anywhere; }

    .detail { padding: 24px 26px 26px; }

    .state-banner {
      display: none;
      margin-bottom: 18px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: var(--control-radius);
      color: var(--dim);
    }

    .state-banner[data-visible="true"] { display: block; }
    .state-label { color: var(--violet); text-transform: uppercase; }

    .candidate-heading { max-width: 30ch; }

    .candidate-summary {
      max-width: 68ch;
      margin: 10px 0 22px;
      color: var(--dim);
    }

    .evidence {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      margin: 0 0 22px;
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
    }

    .field {
      min-width: 0;
      margin: 0;
      padding: 15px 0;
    }

    .field:nth-child(odd) { padding-right: 18px; }
    .field:nth-child(even) { padding-left: 18px; border-left: 1px solid var(--line); }

    .field dt {
      margin-bottom: 5px;
      color: var(--dim);
      text-transform: uppercase;
    }

    .field dd { margin: 0; overflow-wrap: anywhere; }
    .field ul {
      margin: 0;
      padding-left: 18px;
    }

    .field li + li { margin-top: 6px; }

    .source-note {
      margin: 20px 0 0;
      color: var(--dim);
      overflow-wrap: anywhere;
    }

    details { margin-top: 22px; }

    summary {
      color: var(--dim);
      cursor: pointer;
      font-family: ui-monospace, monospace;
      font-size: 12px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }

    summary:focus-visible {
      outline: 2px solid var(--violet);
      outline-offset: 2px;
    }

    .empty {
      min-height: 360px;
      display: grid;
      align-content: center;
      justify-items: start;
    }

    .empty p { max-width: 54ch; color: var(--dim); }

    @media (max-width: 700px) {
      .shell { width: min(100% - 28px, 680px); padding-top: 18px; }
      .topbar { align-items: flex-start; }
      .workspace { grid-template-columns: 1fr; }
      .queue { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .detail { padding: 22px; }
    }

    @media (max-width: 430px) {
      .shell { width: min(100% - 20px, 410px); }
      .topbar { display: grid; }
      .refresh { width: 100%; }
      .status-row { align-items: flex-start; flex-direction: column; gap: 0; padding: 8px 0; }
      .source-time { text-align: left; }
      .queue { grid-template-columns: 1fr; }
      .evidence { grid-template-columns: 1fr; }
      .field:nth-child(odd),
      .field:nth-child(even) { padding: 13px 0; border-left: 0; }
      .field + .field { border-top: 1px solid var(--line); }
      .detail { padding: 18px; }
    }

    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        scroll-behavior: auto !important;
        transition-duration: 0.01ms !important;
      }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header class="topbar">
      <div>
        <p class="eyebrow">AXON // evidence review</p>
        <h1>Promotion Workbench</h1>
        <p class="read-only">
          Read only decision support. Review evidence here, then update
          promotion/candidates.json in the owning evidence repository.
        </p>
      </div>
      <button class="refresh" id="refresh" type="button">Refresh</button>
    </header>

    <div class="status-row">
      <span id="status" aria-live="polite">Loading promotion candidates</span>
      <span class="source-time" id="source-time"></span>
    </div>

    <section class="workspace" id="workspace" aria-busy="false" aria-label="Promotion candidates">
      <aside class="panel queue-panel" aria-labelledby="queue-heading">
        <h2 class="panel-heading" id="queue-heading">Candidate queue</h2>
        <div class="queue" id="queue"></div>
      </aside>

      <article class="panel detail" id="detail">
        <div class="empty">
          <p class="state-label">Loading</p>
          <h2>Reading the evidence source</h2>
          <p>The queue will appear after the initial source read completes.</p>
        </div>
      </article>
    </section>
  </main>

  <script>
    (function () {
      "use strict";

      const queue = document.getElementById("queue");
      const detail = document.getElementById("detail");
      const workspace = document.getElementById("workspace");
      const refreshButton = document.getElementById("refresh");
      const status = document.getElementById("status");
      const sourceTime = document.getElementById("source-time");
      let candidates = [];
      let selectedIndex = 0;

      function addText(parent, tag, className, value) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        node.textContent = value == null || value === "" ? "Not provided" : String(value);
        parent.appendChild(node);
        return node;
      }

      function clear(node) {
        while (node.firstChild) node.removeChild(node.firstChild);
      }

      function displayValue(value) {
        if (Array.isArray(value)) return value.join(", ");
        if (value && typeof value === "object") return JSON.stringify(value);
        return value;
      }

      function normalizedState(candidate) {
        if (candidate.evidence_state === "stale" || candidate.target_state === "stale") {
          return "stale";
        }
        if (candidate.target_state === "unsupported") return "unsupported";
        if (candidate.disposition === "request-evidence") return "request-evidence";
        return "ready";
      }

      function stateCopy(candidate) {
        const state = normalizedState(candidate);
        if (state === "stale") {
          return [
            "Stale evidence",
            "This source needs revalidation before it can support a decision."
          ];
        }
        if (state === "unsupported") {
          return [
            "Unsupported candidate",
            "Add this target to models.json in the owning configuration " +
              "repository before promotion."
          ];
        }
        if (state === "request-evidence") {
          return [
            "Request evidence",
            "More evidence is required; promotion remains ineligible."
          ];
        }
        return ["Evidence ready", "Source metadata is available for technical review."];
      }

      function addField(list, label, value) {
        const field = document.createElement("div");
        field.className = "field";
        addText(field, "dt", "", label);
        const item = addText(field, "dd", "", displayValue(value));
        list.appendChild(field);
        return item;
      }

      function addListField(list, label, values) {
        const field = document.createElement("div");
        field.className = "field";
        addText(field, "dt", "", label);
        const description = document.createElement("dd");
        const items = document.createElement("ul");
        const entries = Array.isArray(values) && values.length ? values : ["None"];
        entries.forEach(function (value) {
          const item = document.createElement("li");
          item.textContent = String(value);
          items.appendChild(item);
        });
        description.appendChild(items);
        field.appendChild(description);
        list.appendChild(field);
      }

      function renderEmpty(label, heading, copy) {
        clear(detail);
        const empty = document.createElement("div");
        empty.className = "empty";
        addText(empty, "p", "state-label", label);
        addText(empty, "h2", "", heading);
        addText(empty, "p", "", copy);
        detail.appendChild(empty);
      }

      function renderSourceError(message) {
        candidates = [];
        clear(queue);
        renderEmpty(
          "Source error",
          "The evidence source could not be read",
          message || "Unknown source error"
        );
      }

      function sourceErrorMessage(code) {
        if (code === "PROMOTION_SOURCE_NOT_CONFIGURED") {
          return "Set AXON_EVIDENCE_REPO to the owning evidence repository, then refresh.";
        }
        if (code === "PROMOTION_SOURCE_UNAVAILABLE") {
          return "Make AXON_EVIDENCE_REPO readable, then refresh.";
        }
        if (code === "PROMOTION_SOURCE_TOO_LARGE") {
          return "Reduce promotion/candidates.json in the evidence repository, then refresh.";
        }
        if (code === "PROMOTION_SCHEMA_INVALID") {
          return "Fix promotion/candidates.json in the evidence repository, then refresh.";
        }
        if (code === "PROMOTION_SOURCE_TIMEOUT") {
          return "The promotion source request timed out. Check the AXON service, then refresh.";
        }
        if (code === "PROMOTION_RESPONSE_INVALID") {
          return "The promotion source returned invalid JSON. " +
            "Check the AXON service logs, then refresh.";
        }
        return "The promotion source could not be reached. Check the AXON service, then refresh.";
      }

      function updateSelection() {
        const buttons = queue.querySelectorAll("button");
        buttons.forEach(function (button, index) {
          button.setAttribute("aria-pressed", index === selectedIndex ? "true" : "false");
        });
      }

      function renderQueue(items) {
        candidates = Array.isArray(items) ? items : [];
        selectedIndex = 0;
        clear(queue);

        candidates.forEach(function (candidate, index) {
          const button = document.createElement("button");
          button.type = "button";
          button.setAttribute("aria-pressed", index === selectedIndex ? "true" : "false");
          addText(button, "span", "queue-id", candidate.candidate_id);
          addText(button, "span", "queue-claim", candidate.claim_id);
          addText(button, "span", "queue-title", displayValue(candidate.wording));
          button.addEventListener("click", function () {
            selectedIndex = index;
            updateSelection();
            renderCandidate(candidates[index]);
            announce("Selected " + candidate.candidate_id);
          });
          queue.appendChild(button);
        });
      }

      function renderCandidate(candidate) {
        if (!candidate) {
          renderEmpty(
            "Queue empty",
            "No promotion candidates",
            "Refresh after the evidence source publishes a candidate."
          );
          return;
        }

        clear(detail);
        const copy = stateCopy(candidate);
        const banner = document.createElement("div");
        banner.className = "state-banner";
        banner.dataset.visible = "true";
        addText(banner, "span", "state-label", copy[0]);
        addText(banner, "div", "", copy[1]);
        detail.appendChild(banner);

        const decisionEvidence = document.createElement("dl");
        decisionEvidence.className = "evidence";
        addListField(decisionEvidence, "Why blocked", candidate.blockers);
        addListField(decisionEvidence, "Evidence needed", candidate.evidence_requests);
        detail.appendChild(decisionEvidence);

        addText(detail, "p", "eyebrow", candidate.claim_id);
        addText(detail, "h2", "candidate-heading", displayValue(candidate.wording));
        addText(detail, "p", "candidate-summary", candidate.limitation);

        const provenance = document.createElement("details");
        addText(provenance, "summary", "", "Technical provenance");
        const evidence = document.createElement("dl");
        evidence.className = "evidence";
        addField(evidence, "Disposition", candidate.disposition);
        addField(evidence, "Baseline", candidate.baseline);
        addField(evidence, "Owner", candidate.owner);
        addField(evidence, "Target", candidate.target);
        addField(evidence, "Scope", candidate.scope);
        addField(evidence, "Claim status", candidate.claim_status);
        addField(evidence, "Run status", candidate.run_status);
        addField(evidence, "Evidence state", candidate.evidence_state);
        addField(evidence, "Target state", candidate.target_state);
        addField(evidence, "Eligible", candidate.eligible ? "Yes" : "No");
        addListField(evidence, "Run limitations", candidate.run_limitations);
        addField(evidence, "Candidate ID", candidate.candidate_id);
        provenance.appendChild(evidence);

        addText(provenance, "p", "source-note", "Run " + candidate.run_id);
        detail.appendChild(provenance);
      }

      function announce(message) {
        status.textContent = message;
      }

      function setBusy(busy) {
        refreshButton.disabled = busy;
        workspace.setAttribute("aria-busy", busy ? "true" : "false");
        refreshButton.textContent = busy ? "Refreshing" : "Refresh";
        if (busy) {
          clear(queue);
          renderEmpty(
            "Loading",
            "Reading the evidence source",
            "Waiting for promotion candidate metadata."
          );
          announce("Loading promotion candidates");
        }
      }

      async function refresh() {
        setBusy(true);
        const controller = new AbortController();
        const timeoutId = setTimeout(function () { controller.abort(); }, 10000);
        let failureCode;
        try {
          const response = await fetch("/api/promotion-candidates", {
            signal: controller.signal
          });
          failureCode = "PROMOTION_RESPONSE_INVALID";
          const payload = await response.json();
          if (!response.ok) {
            failureCode = payload.code;
            throw new Error();
          }
          const published = "Published " + payload.generated_at;
          const read = "Read " + payload.observed_at;
          sourceTime.textContent = published + " / " + read;
          renderQueue(payload.candidates);
          renderCandidate(payload.candidates[0] || null);
          announce("Promotion candidates refreshed");
        } catch (error) {
          const code = error.name === "AbortError" ? "PROMOTION_SOURCE_TIMEOUT" : failureCode;
          renderSourceError(sourceErrorMessage(code));
          announce("Promotion source error");
        } finally {
          clearTimeout(timeoutId);
          setBusy(false);
        }
      }

      document.getElementById("refresh").addEventListener("click", refresh);
      refresh();
    })();
  </script>
</body>
</html>
"""
