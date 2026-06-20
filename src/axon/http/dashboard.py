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
    if (daily.length === 0) {
      spark.innerHTML = '<span class="spark-empty">no daily data</span>';
      return;
    }
    var values = daily.map(function (d) { return d[1]; });
    var maxVal = Math.max.apply(null, values) || 1;
    spark.innerHTML = values.map(function (v) {
      var h = Math.max(4, Math.round((v / maxVal) * 44));
      return '<div class="spark-bar" style="height:' + h + 'px" title="' + v + '"></div>';
    }).join("");
  }

  function renderActivity(records) {
    var list = document.getElementById("feed-list");
    if (!records || records.length === 0) {
      list.innerHTML = '<li class="empty-feed">no activity yet</li>';
      return;
    }
    list.innerHTML = records.map(function (r) {
      var parts = [
        '<span class="ts">' + shortTs(r.ts) + '</span>',
        ' <span class="stage">' + (r.stage || "") + '</span>',
        ' <span class="caller">' + (r.caller || "") + '</span>'
      ];
      if (r.route) parts.push(' <span class="route">' + r.route + '</span>');
      if (r.model) parts.push(' <span class="model">[' + r.model + ']</span>');
      return "<li>" + parts.join("") + "</li>";
    }).join("");
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

  refresh();
  setInterval(refresh, 3000);
})();
</script>
</body>
</html>
"""
