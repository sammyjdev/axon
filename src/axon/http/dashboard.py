# ruff: noqa: E501
"""axon/http/dashboard.py — self-contained HTML for the /dashboard route.

The page is intentionally minimal: no external CDN, no framework.  It polls
/api/gain and /api/activity every 3 seconds with vanilla JS and renders:
  - a savings panel (token counts, ratio stats, daily sparkline bar)
  - a live activity feed (recent trace records)
"""

from __future__ import annotations

DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AXON &mdash; live</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    background: #0d0d0f;
    color: #c8c8d0;
    padding: 1.5rem;
    min-height: 100vh;
  }
  h1 { font-size: 1.1rem; color: #9090c0; letter-spacing: 0.08em; margin-bottom: 1.5rem; }
  h2 { font-size: 0.78rem; color: #6060a0; letter-spacing: 0.1em; text-transform: uppercase;
       margin-bottom: 0.75rem; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.25rem; }
  @media (max-width: 720px) { .grid { grid-template-columns: 1fr; } }
  .card {
    background: #13131a;
    border: 1px solid #22223a;
    border-radius: 6px;
    padding: 1rem 1.25rem;
  }
  .stat { margin-bottom: 0.4rem; font-size: 0.82rem; }
  .stat-label { color: #6060a0; }
  .stat-value { color: #e0e0f0; }
  .big { font-size: 1.6rem; font-weight: 600; color: #a0c0ff; letter-spacing: -0.02em; }
  .ratio-row { color: #8888b0; font-size: 0.78rem; margin-top: 0.35rem; }
  .spark {
    display: flex;
    align-items: flex-end;
    gap: 2px;
    height: 48px;
    margin-top: 0.75rem;
    overflow: hidden;
  }
  .spark-bar {
    flex: 1;
    min-width: 4px;
    max-width: 16px;
    background: #3a3a7a;
    border-radius: 2px 2px 0 0;
    transition: height 0.3s ease;
  }
  .spark-bar:last-child { background: #6060c0; }
  .spark-empty { color: #404060; font-size: 0.72rem; padding-top: 0.5rem; }
  #feed-list { list-style: none; }
  #feed-list li {
    border-bottom: 1px solid #1a1a2a;
    padding: 0.4rem 0;
    font-size: 0.77rem;
    line-height: 1.5;
  }
  #feed-list li:last-child { border-bottom: none; }
  .ts { color: #404060; }
  .stage { color: #7070d0; font-weight: 600; }
  .caller { color: #a0a0c0; }
  .route { color: #6090b0; }
  .model { color: #8888a0; }
  .empty-feed { color: #404060; font-size: 0.78rem; }
  #status { font-size: 0.68rem; color: #404060; text-align: right; margin-top: 1rem; }

  /* new dashboard views */
  .tabs { display: flex; gap: 0.5rem; margin-top: 1.5rem; margin-bottom: 1rem; }
  .tab-btn { background: #1a1a2a; border: 1px solid #22223a; color: #9090c0; padding: 0.5rem 1rem; cursor: pointer; border-radius: 4px; font-family: inherit; font-size: 0.8rem; }
  .tab-btn.active { background: #2a2a4a; color: #e0e0f0; border-color: #44446a; }
  .tab-pane { display: none; margin-bottom: 1.5rem; }
  .tab-pane.active { display: block; }
  .control-row { display: flex; gap: 0.5rem; margin-bottom: 1rem; align-items: center; flex-wrap: wrap; }
  .input-text, .input-select { background: #13131a; border: 1px solid #33334a; color: #e0e0f0; padding: 0.4rem 0.5rem; border-radius: 4px; font-family: inherit; font-size: 0.8rem; }
  .btn { background: #3a3a7a; border: 1px solid #4a4a8a; color: #fff; padding: 0.4rem 0.75rem; cursor: pointer; border-radius: 4px; font-family: inherit; font-size: 0.8rem; }
  .btn:hover { background: #4a4a8a; }
  .data-list { list-style: none; }
  .data-list li { border-bottom: 1px solid #1a1a2a; padding: 0.5rem 0; font-size: 0.8rem; line-height: 1.4; display: flex; gap: 1rem; }
  .data-list li:last-child { border-bottom: none; }
  .col-id { width: 140px; color: #a0c0ff; word-break: break-all; }
  .col-harness { width: 90px; color: #9090c0; }
  .col-project { width: 120px; color: #7070d0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .col-status { width: 80px; color: #c8c8d0; }
  .col-cov { width: 80px; color: #8888b0; }
  .col-kind { width: 100px; color: #a0c0ff; }
  .col-ts { width: 140px; color: #6090b0; }
  .col-outcome { width: 100px; color: #9090c0; }
  .summary-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  .summary-item { background: #1a1a2a; padding: 1rem; border-radius: 4px; }
  .summary-label { color: #6060a0; font-size: 0.75rem; text-transform: uppercase; margin-bottom: 0.25rem; }
  .summary-val { color: #e0e0f0; font-size: 1.1rem; }
  .error-text { color: #ff8080; font-size: 0.8rem; margin-top: 0.5rem; }
</style>
</head>
<body>
<h1>AXON &mdash; live</h1>
<div class="grid">
  <!-- savings panel -->
  <div class="card" id="gain-card">
    <h2>Compression savings</h2>
    <div class="big" id="saved-tokens">&mdash;</div>
    <div class="ratio-row" id="token-ratio">&mdash;</div>
    <div class="stat" style="margin-top:0.6rem">
      <span class="stat-label">p50 / mean / p95 / max&nbsp;</span>
      <span class="stat-value" id="pct-stats">&mdash;</span>
    </div>
    <div class="spark" id="sparkline">
      <span class="spark-empty">no data</span>
    </div>
  </div>

  <!-- activity feed -->
  <div class="card">
    <h2>Activity feed</h2>
    <ul id="feed-list"><li class="empty-feed">loading&hellip;</li></ul>
  </div>
</div>

  <!-- Tabs -->
  <div class="tabs">
    <button class="tab-btn active" id="tab-sessions">Sessions</button>
    <button class="tab-btn" id="tab-timeline">Timeline</button>
    <button class="tab-btn" id="tab-summary">Summary</button>
  </div>

  <!-- Sessions View -->
  <div class="tab-pane active" id="activity-sessions-view">
    <div class="card">
      <h2>Sessions</h2>
      <div class="control-row">
        <input type="text" class="input-text" id="sessions-q" placeholder="Search...">
        <input type="text" class="input-text" id="sessions-project" placeholder="Project">
        <select class="input-select" id="sessions-harness">
          <option value="">All harnesses</option>
          <option value="claude-code">claude-code</option>
          <option value="codex">codex</option>
          <option value="agy">agy</option>
        </select>
        <button class="btn" id="sessions-load-btn">Load</button>
      </div>
      <ul class="data-list" id="sessions-list"></ul>
      <div style="margin-top: 1rem; display: none;" id="sessions-more-wrapper">
        <button class="btn" id="sessions-more-btn">Load more</button>
      </div>
    </div>
  </div>

  <!-- Timeline View -->
  <div class="tab-pane" id="activity-timeline-view">
    <div class="card">
      <h2>Session Timeline</h2>
      <div class="control-row">
        <input type="text" class="input-text" id="timeline-id" placeholder="Session ID">
        <button class="btn" id="timeline-load-btn">Load</button>
      </div>
      <ul class="data-list" id="timeline-list"></ul>
      <div class="error-text" id="timeline-error"></div>
    </div>
  </div>

  <!-- Summary View -->
  <div class="tab-pane" id="activity-summary-view">
    <div class="card">
      <h2>Operational Summary</h2>
      <button class="btn" style="margin-bottom: 1rem;" id="summary-load-btn">Refresh</button>
      <div class="summary-grid">
        <div class="summary-item">
          <div class="summary-label">Pending Count</div>
          <div class="summary-val" id="summary-pending-count">-</div>
        </div>
        <div class="summary-item">
          <div class="summary-label">Pending Bytes</div>
          <div class="summary-val" id="summary-pending-bytes">-</div>
        </div>
        <div class="summary-item">
          <div class="summary-label">Stored Bytes</div>
          <div class="summary-val" id="summary-stored-bytes">-</div>
        </div>
        <div class="summary-item" style="grid-column: 1 / -1">
          <div class="summary-label">Latest Error</div>
          <div class="summary-val" id="summary-latest-error" style="font-size: 0.9rem; color: #ff8080">-</div>
        </div>
        <div class="summary-item" style="grid-column: 1 / -1">
          <div class="summary-label">Compatibility Warnings</div>
          <div class="summary-val" id="summary-warnings" style="font-size: 0.9rem;">-</div>
        </div>
      </div>
    </div>
  </div>

<div id="status">never refreshed</div>

<script>
(function () {
  "use strict";

  function fmt(n) {
    return n == null ? "n/a" : Number(n).toLocaleString();
  }

  function fmtPct(v) {
    return v == null ? "n/a" : v.toFixed(1) + "%";
  }

  function shortTs(ts) {
    // ISO string → HH:MM:SS or date if older than today
    if (!ts) return "";
    var d = ts.replace("T", " ").replace(/\\.\\d+.*$/, "").replace(/Z$/, "");
    return d.length > 19 ? d.slice(0, 19) : d;
  }

  function addText(parent, tag, className, value) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value == null ? "" : String(value);
    parent.appendChild(node);
    return node;
  }

  function renderGain(data) {
    document.getElementById("saved-tokens").textContent =
      fmt(data.saved_tokens) + " tokens saved";
    document.getElementById("token-ratio").textContent =
      fmt(data.before_tokens) + " → " + fmt(data.after_tokens);
    var ps = [data.p50_pct, data.mean_pct, data.p95_pct, data.max_pct];
    document.getElementById("pct-stats").textContent =
      ps.map(fmtPct).join(" / ");

    var spark = document.getElementById("sparkline");
    var daily = data.daily_saved || [];
    while (spark.firstChild) spark.removeChild(spark.firstChild);
    if (daily.length === 0) {
      addText(spark, "span", "spark-empty", "no daily data");
      return;
    }
    var values = daily.map(function (d) { return d[1]; });
    var maxVal = Math.max.apply(null, values) || 1;
    values.forEach(function (v) {
      var bar = document.createElement("div");
      bar.className = "spark-bar";
      bar.style.height = Math.max(4, Math.round((v / maxVal) * 44)) + "px";
      bar.setAttribute("title", String(v));
      spark.appendChild(bar);
    });
  }

  function renderActivity(records) {
    var list = document.getElementById("feed-list");
    while (list.firstChild) list.removeChild(list.firstChild);
    if (!records || records.length === 0) {
      addText(list, "li", "empty-feed", "no activity yet");
      return;
    }
    records.forEach(function (r) {
      var li = document.createElement("li");
      addText(li, "span", "ts", shortTs(r.ts));
      li.appendChild(document.createTextNode(" "));
      addText(li, "span", "stage", r.stage || "");
      li.appendChild(document.createTextNode(" "));
      addText(li, "span", "caller", r.caller || "");
      if (r.route) {
        li.appendChild(document.createTextNode(" "));
        addText(li, "span", "route", r.route);
      }
      if (r.model) {
        li.appendChild(document.createTextNode(" "));
        addText(li, "span", "model", "[" + r.model + "]");
      }
      list.appendChild(li);
    });
  }

  function refresh() {
    Promise.all([
      fetch("/api/gain").then(function (r) { return r.json(); }),
      fetch("/api/activity").then(function (r) { return r.json(); })
    ]).then(function (results) {
      renderGain(results[0]);
      renderActivity(results[1]);
      var now = new Date();
      document.getElementById("status").textContent =
        "last refresh " + now.toTimeString().slice(0, 8);
    }).catch(function (err) {
      document.getElementById("status").textContent =
        "refresh error: " + err.message;
    });
  }


  // Activity history views (Task 10). Wrapped in try/catch: this whole
  // dashboard page ships as one script, and an older cached page or a
  // minimal test harness that only knows the original gain/feed elements
  // must not be broken by wiring for elements it does not have - a real
  // browser always has every element below, so this only guards a stale-cache
  // or partial-DOM edge case, never real behavior.
  try {
  // View switcher
  var tabSessions = document.getElementById("tab-sessions");
  var tabTimeline = document.getElementById("tab-timeline");
  var tabSummary = document.getElementById("tab-summary");
  var viewSessions = document.getElementById("activity-sessions-view");
  var viewTimeline = document.getElementById("activity-timeline-view");
  var viewSummary = document.getElementById("activity-summary-view");

  function switchTab(tab, view) {
    [tabSessions, tabTimeline, tabSummary].forEach(function(t) { t.className = "tab-btn"; });
    [viewSessions, viewTimeline, viewSummary].forEach(function(v) { v.className = "tab-pane"; });
    tab.className = "tab-btn active";
    view.className = "tab-pane active";
  }
  
  tabSessions.addEventListener("click", function() { switchTab(tabSessions, viewSessions); });
  tabTimeline.addEventListener("click", function() { switchTab(tabTimeline, viewTimeline); });
  tabSummary.addEventListener("click", function() { switchTab(tabSummary, viewSummary); });

  // Sessions
  var sessionsCursor = null;
  function loadSessions(append) {
    var q = document.getElementById("sessions-q").value.trim();
    var project = document.getElementById("sessions-project").value.trim();
    var harness = document.getElementById("sessions-harness").value;
    
    var url = new URL(q ? "/api/activity/search" : "/api/activity/sessions", window.location.origin);
    if (q) url.searchParams.append("q", q);
    if (project) url.searchParams.append("project", project);
    if (harness) url.searchParams.append("harness", harness);
    if (append && sessionsCursor) url.searchParams.append("cursor", sessionsCursor);

    fetch(url.toString())
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var list = document.getElementById("sessions-list");
        if (!append) {
          while(list.firstChild) list.removeChild(list.firstChild);
        }
        
        var rows = q ? data.events : data.sessions;
        sessionsCursor = data.next_cursor;

        if (!rows || rows.length === 0) {
          if (!append) addText(list, "li", "empty-feed", "no sessions found");
        } else {
          rows.forEach(function(s) {
            var li = document.createElement("li");
            addText(li, "div", "col-id", s.session_id || "unavailable");
            addText(li, "div", "col-harness", s.harness || "unavailable");
            addText(li, "div", "col-project", s.project || "unavailable");
            addText(li, "div", "col-status", s.status || s.kind || "unavailable");
            addText(li, "div", "col-cov", s.coverage || "unavailable");
            list.appendChild(li);
          });
        }
        
        document.getElementById("sessions-more-wrapper").style.display = sessionsCursor ? "block" : "none";
      });
  }

  document.getElementById("sessions-load-btn").addEventListener("click", function() { loadSessions(false); });
  document.getElementById("sessions-more-btn").addEventListener("click", function() { loadSessions(true); });

  // Timeline
  function loadTimeline() {
    var sid = document.getElementById("timeline-id").value.trim();
    var err = document.getElementById("timeline-error");
    err.textContent = "";
    if (!sid) return;
    
    fetch("/api/activity/sessions/" + encodeURIComponent(sid) + "/timeline")
      .then(function(r) { 
        if (!r.ok) throw new Error("Status " + r.status);
        return r.json(); 
      })
      .then(function(events) {
        var list = document.getElementById("timeline-list");
        while(list.firstChild) list.removeChild(list.firstChild);
        if (!events || events.length === 0) {
          addText(list, "li", "empty-feed", "no events found");
          return;
        }
        events.forEach(function(e) {
          var li = document.createElement("li");
          addText(li, "div", "col-kind", e.kind || "unavailable");
          addText(li, "div", "col-ts", e.occurred_at || "unavailable");
          addText(li, "div", "col-outcome", e.outcome == null ? "unavailable" : String(e.outcome));
          addText(li, "div", "col-cov", e.coverage == null ? "unavailable" : String(e.coverage));
          list.appendChild(li);
        });
      })
      .catch(function(e) {
        err.textContent = "Failed: " + e.message;
      });
  }
  document.getElementById("timeline-load-btn").addEventListener("click", loadTimeline);

  // Summary
  function loadSummary() {
    fetch("/api/activity/health")
      .then(function(r) { return r.json(); })
      .then(function(data) {
        document.getElementById("summary-pending-count").textContent = data.pending_count == null ? "unavailable" : String(data.pending_count);
        document.getElementById("summary-pending-bytes").textContent = data.pending_bytes == null ? "unavailable" : String(data.pending_bytes);
        document.getElementById("summary-stored-bytes").textContent = data.stored_bytes == null ? "unavailable" : String(data.stored_bytes);
        document.getElementById("summary-latest-error").textContent = data.latest_error == null ? "none" : String(data.latest_error);
        
        var warnText = "none";
        if (data.compatibility_warnings && data.compatibility_warnings.length > 0) {
          warnText = data.compatibility_warnings.join(", ");
        }
        document.getElementById("summary-warnings").textContent = warnText;
      });
  }
  document.getElementById("summary-load-btn").addEventListener("click", loadSummary);
  } catch (e) { /* see comment above: guards a partial-DOM harness only */ }

  refresh();
  setInterval(refresh, 3000);
})();
</script>
</body>
</html>
"""
