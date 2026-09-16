# ruff: noqa: E501, S603
import json
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from axon.http.app import app

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

HOSTILE_PAYLOAD = '<img src=x onerror="alert(1)">'

_SESSIONS_PAYLOAD = {
    "sessions": [
        {
            "session_id": HOSTILE_PAYLOAD,
            "harness": HOSTILE_PAYLOAD,
            "project": HOSTILE_PAYLOAD,
            "status": HOSTILE_PAYLOAD,
            "coverage": HOSTILE_PAYLOAD,
        }
    ],
    "next_cursor": None
}

_TIMELINE_PAYLOAD = [
    {
        "kind": HOSTILE_PAYLOAD,
        "occurred_at": HOSTILE_PAYLOAD,
        "outcome": HOSTILE_PAYLOAD,
        "coverage": HOSTILE_PAYLOAD,
    }
]

_HEALTH_PAYLOAD_NULL_BYTES = {
    "pending_count": 0,
    "pending_bytes": 0,
    "stored_bytes": None,
    "latest_error": None,
    "compatibility_warnings": []
}

_HEALTH_PAYLOAD_HOSTILE = {
    "pending_count": HOSTILE_PAYLOAD,
    "pending_bytes": HOSTILE_PAYLOAD,
    "stored_bytes": HOSTILE_PAYLOAD,
    "latest_error": HOSTILE_PAYLOAD,
    "compatibility_warnings": [HOSTILE_PAYLOAD]
}

_NODE_DRIVER = r"""
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function Element(tag) {
  this.tagName = String(tag).toLowerCase();
  this.children = [];
  this.attrs = {};
  this.className = "";
  this.style = {};
  this._text = null;
  this._raw = null;
}

Element.prototype.appendChild = function (child) {
  this.children.push(child);
  return child;
};

Element.prototype.removeChild = function (child) {
  var i = this.children.indexOf(child);
  if (i !== -1) this.children.splice(i, 1);
  return child;
};

Object.defineProperty(Element.prototype, "firstChild", {
  get: function () { return this.children.length ? this.children[0] : null; }
});

Element.prototype.setAttribute = function (name, value) {
  this.attrs[String(name)] = String(value);
};

Element.prototype.addEventListener = function(event, callback) {
  // simple mock
  if (!this._listeners) this._listeners = {};
  this._listeners[event] = callback;
};

Element.prototype.click = function() {
  if (this._listeners && this._listeners['click']) {
    this._listeners['click']();
  }
};

Object.defineProperty(Element.prototype, "value", {
  get: function () { return this._value || ""; },
  set: function (v) { this._value = String(v); }
});

Object.defineProperty(Element.prototype, "textContent", {
  get: function () {
    if (this._raw != null) return this._raw;
    if (this.children.length) {
      return this.children
        .map(function (c) { return c.textContent; })
        .join("");
    }
    return this._text == null ? "" : this._text;
  },
  set: function (value) {
    this._text = value == null ? "" : String(value);
    this._raw = null;
    this.children = [];
  }
});

Object.defineProperty(Element.prototype, "innerHTML", {
  get: function () { return this._raw == null ? "" : this._raw; },
  set: function (value) {
    this._raw = value == null ? "" : String(value);
    this._text = null;
    this.children = [];
  }
});

Element.prototype.serialize = function () {
  var attrs = [];
  if (this.className) attrs.push('class="' + escapeHtml(this.className) + '"');
  var name;
  for (name in this.attrs) {
    if (Object.prototype.hasOwnProperty.call(this.attrs, name)) {
      attrs.push(name + '="' + escapeHtml(this.attrs[name]) + '"');
    }
  }
  var styleParts = [];
  var key;
  for (key in this.style) {
    if (Object.prototype.hasOwnProperty.call(this.style, key)) {
      styleParts.push(key + ":" + this.style[key]);
    }
  }
  if (styleParts.length) attrs.push('style="' + escapeHtml(styleParts.join(";")) + '"');
  var open = "<" + this.tagName + (attrs.length ? " " + attrs.join(" ") : "") + ">";
  var inner;
  if (this._raw != null) {
    inner = this._raw;
  } else if (this.children.length) {
    inner = "";
    var i;
    for (i = 0; i < this.children.length; i += 1) inner += this.children[i].serialize();
  } else {
    inner = this._text == null ? "" : escapeHtml(this._text);
  }
  return open + inner + "</" + this.tagName + ">";
};

function TextNode(value) {
  this._text = value == null ? "" : String(value);
}

TextNode.prototype.serialize = function () { return escapeHtml(this._text); };

Object.defineProperty(TextNode.prototype, "textContent", {
  get: function () { return this._text; },
  set: function (v) { this._text = v == null ? "" : String(v); }
});

var registry = {
  "gain-card": new Element("div"),
  "saved-tokens": new Element("div"),
  "token-ratio": new Element("div"),
  "pct-stats": new Element("div"),
  "sparkline": new Element("div"),
  "feed-list": new Element("ul"),
  "status": new Element("div"),
  
  "tab-sessions": new Element("button"),
  "tab-timeline": new Element("button"),
  "tab-summary": new Element("button"),
  "activity-sessions-view": new Element("div"),
  "activity-timeline-view": new Element("div"),
  "activity-summary-view": new Element("div"),
  
  "sessions-q": new Element("input"),
  "sessions-project": new Element("input"),
  "sessions-harness": new Element("select"),
  "sessions-load-btn": new Element("button"),
  "sessions-more-wrapper": new Element("div"),
  "sessions-more-btn": new Element("button"),
  "sessions-list": new Element("ul"),
  
  "timeline-id": new Element("input"),
  "timeline-load-btn": new Element("button"),
  "timeline-list": new Element("ul"),
  "timeline-error": new Element("div"),
  
  "summary-load-btn": new Element("button"),
  "summary-pending-count": new Element("div"),
  "summary-pending-bytes": new Element("div"),
  "summary-stored-bytes": new Element("div"),
  "summary-latest-error": new Element("div"),
  "summary-warnings": new Element("div")
};

var document = {
  getElementById: function (id) {
    if (!Object.prototype.hasOwnProperty.call(registry, id)) {
      throw new Error("DOM shim: unknown element id '" + id + "'");
    }
    return registry[id];
  },
  createElement: function (tag) { return new Element(tag); },
  createTextNode: function (text) { return new TextNode(text); }
};

var window = {
  location: {
    origin: "http://localhost"
  }
};

function URL(urlStr, base) {
  this.urlStr = urlStr;
  this.base = base;
  this.searchParams = {
    params: {},
    append: function(k, v) { this.params[k] = v; }
  };
  this.toString = function() { return this.urlStr; };
}

function fetch(url) {
  var body = {};
  if (url.indexOf("/api/gain") !== -1) body = {};
  else if (url.indexOf("/api/activity") !== -1 && url.indexOf("sessions") === -1 && url.indexOf("health") === -1) body = [];
  else if (url.indexOf("/api/activity/sessions") !== -1 && url.indexOf("timeline") === -1) body = SESSIONS;
  else if (url.indexOf("timeline") !== -1) body = TIMELINE;
  else if (url.indexOf("health") !== -1) body = HEALTH;
  
  return Promise.resolve({
    ok: true,
    json: function () { return Promise.resolve(body); }
  });
}

function setInterval() { return 0; }

try {
  eval(PAGE_JS);
} catch (err) {
  process.stderr.write("page JS threw: " + (err && err.stack ? err.stack : String(err)) + "\n");
  process.exit(1);
}

// Trigger loads
registry["sessions-load-btn"].click();
registry["timeline-id"].value = "sid-123";
registry["timeline-load-btn"].click();
registry["summary-load-btn"].click();

setTimeout(function () {
  setTimeout(function () {
    process.stdout.write(
      JSON.stringify({
        sessions: registry["sessions-list"].serialize(),
        timeline: registry["timeline-list"].serialize(),
        summary_bytes: registry["summary-stored-bytes"].serialize(),
        summary_count: registry["summary-pending-count"].serialize(),
        summary_error: registry["summary-latest-error"].serialize(),
        summary_warnings: registry["summary-warnings"].serialize()
      }) + "\n"
    );
  }, 10);
}, 10);
"""

def _extract_script(html: str) -> str:
    start_tag = "<script>"
    start = html.index(start_tag) + len(start_tag)
    end = html.index("</script>", start)
    return html[start:end]

@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

class _MarkupCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attrs: list[tuple[str, str | None]] = []
        self.data: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        self.tags.append(tag)
        self.attrs.extend(attrs)

    def handle_data(self, data) -> None:
        self.data.append(data)

def _parse(markup: str) -> _MarkupCollector:
    collector = _MarkupCollector()
    collector.feed(markup)
    collector.close()
    return collector

def _run_shim(client, tmp_path, health_payload):
    resp = client.get("/dashboard")
    js = _extract_script(resp.text)
    driver_js = (
        "var PAGE_JS = " + json.dumps(js) + ";\n"
        "var SESSIONS = " + json.dumps(_SESSIONS_PAYLOAD) + ";\n"
        "var TIMELINE = " + json.dumps(_TIMELINE_PAYLOAD) + ";\n"
        "var HEALTH = " + json.dumps(health_payload) + ";\n"
        + _NODE_DRIVER
    )
    driver = tmp_path / "driver.js"
    driver.write_text(driver_js, encoding="utf-8")
    
    proc = subprocess.run([shutil.which("node"), str(driver)], capture_output=True, text=True)
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    return json.loads(proc.stdout)

def test_activity_dashboard_renders_hostile_text_inertly(client: TestClient, tmp_path) -> None:
    out = _run_shim(client, tmp_path, _HEALTH_PAYLOAD_HOSTILE)
    
    for section in ["sessions", "timeline"]:
        collector = _parse(out[section])
        tags = set(collector.tags)
        assert "img" not in tags
        assert "script" not in tags
        
        attr_names = {name for name, _ in collector.attrs}
        injected = attr_names & {"onerror", "onmouseover"}
        assert not injected
        
        data = "".join(collector.data)
        assert HOSTILE_PAYLOAD in data

def test_dashboard_labels_unavailable_cost_without_zero(client: TestClient, tmp_path) -> None:
    out = _run_shim(client, tmp_path, _HEALTH_PAYLOAD_NULL_BYTES)
    
    collector = _parse(out["summary_bytes"])
    data = "".join(collector.data).strip()
    assert data == "unavailable"
    assert "0" not in data
