"""Behavioural XSS tests for the /dashboard page (issue #94).

The dashboard JS must render every dynamic field through DOM node creation
(``createElement`` + ``textContent``/``setAttribute``/``style``), never
through ``innerHTML`` string concatenation.  These tests prove that
BEHAVIOURALLY: they fetch the real page, extract its script, execute it under
Node against a hostile ``/api/gain`` + ``/api/activity`` payload, serialize the
resulting DOM, and assert the payload survives as escaped TEXT - never as
markup.

The DOM shim is faithful in exactly the respect that discriminates: assigning
``.textContent`` stores TEXT (escaped on serialization) while assigning
``.innerHTML`` stores RAW HTML (spliced verbatim on serialization).  That
asymmetry is what a real browser does and is the whole point of the test.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed; skipping dashboard tests")
pytest.importorskip("httpx", reason="httpx not installed; skipping dashboard tests")

from fastapi.testclient import TestClient  # noqa: E402

from axon.http.app import app  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

# ---------------------------------------------------------------------------
# Hostile payloads (markup that must end up as text, never as elements)
# ---------------------------------------------------------------------------

HOSTILE_STAGE = '<img src=x onerror="alert(1)">'
HOSTILE_CALLER = "</span><script>alert(2)</script>"
HOSTILE_ROUTE = 'r" onmouseover="alert(3)'
HOSTILE_SPARK_VALUE = '7"><img src=y onerror="alert(4)">'

_ACTIVITY_PAYLOAD = [
    {
        "ts": "2026-01-01T00:00:00",
        "stage": HOSTILE_STAGE,
        "caller": HOSTILE_CALLER,
        "route": HOSTILE_ROUTE,
        "model": "m",
    }
]

_GAIN_PAYLOAD = {
    "saved_tokens": 1234,
    "before_tokens": 1000,
    "after_tokens": 400,
    "p50_pct": 50.0,
    "mean_pct": 55.0,
    "p95_pct": 70.0,
    "max_pct": 80.0,
    "daily_saved": [["2026-01-01", 5], ["2026-01-02", HOSTILE_SPARK_VALUE]],
}

# ---------------------------------------------------------------------------
# Node driver: minimal DOM shim + fetch/setInterval stubs, runs the page JS
# ---------------------------------------------------------------------------

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
  "saved-tokens": new Element("div"),
  "token-ratio": new Element("div"),
  "pct-stats": new Element("div"),
  "sparkline": new Element("div"),
  "feed-list": new Element("ul"),
  "status": new Element("div")
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

function fetch(url) {
  var body = url.indexOf("/api/activity") !== -1 ? ACTIVITY : GAIN;
  return Promise.resolve({
    ok: true,
    json: function () { return Promise.resolve(body); }
  });
}

function setInterval() { return 0; }

try {
  eval(PAGE_JS);
} catch (err) {
  process.stderr.write(
    "page JS threw: " + (err && err.stack ? err.stack : String(err)) + "\n"
  );
  process.exit(1);
}

setTimeout(function () {
  setTimeout(function () {
    process.stdout.write(
      JSON.stringify({
        feed: registry["feed-list"].serialize(),
        spark: registry["sparkline"].serialize(),
        status: registry["status"].textContent
      }) + "\n"
    );
  }, 0);
}, 0);
"""


def _extract_script(html: str) -> str:
    start_tag = "<script>"
    assert start_tag in html, "dashboard page has no <script> block"
    start = html.index(start_tag) + len(start_tag)
    end = html.index("</script>", start)
    return html[start:end]


def _driver_source(page_js: str) -> str:
    return (
        "var PAGE_JS = " + json.dumps(page_js) + ";\n"
        "var ACTIVITY = " + json.dumps(_ACTIVITY_PAYLOAD) + ";\n"
        "var GAIN = " + json.dumps(_GAIN_PAYLOAD) + ";\n"
        + _NODE_DRIVER
    )


@pytest.fixture
def client():
    """TestClient backed by the shared AXON FastAPI app."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _run_dashboard_js(client: TestClient, tmp_path) -> dict:
    """Execute the page's JS under the shim and return the serialized DOM."""
    resp = client.get("/dashboard")
    assert resp.status_code == 200, (
        f"GET /dashboard returned {resp.status_code}; the page must render "
        "unauthenticated in tests (see tests/http/conftest.py)"
    )
    driver = tmp_path / "dashboard_driver.js"
    driver.write_text(_driver_source(_extract_script(resp.text)), encoding="utf-8")
    node = shutil.which("node")
    proc = subprocess.run(  # noqa: S603
        [node, str(driver)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"node driver failed:\n{proc.stderr}"
    result = json.loads(proc.stdout)
    assert not result["status"].startswith("refresh error"), (
        f"page JS crashed under the shim: {result['status']}"
    )
    return result


class _MarkupCollector(HTMLParser):
    """Collect start tags, attributes and text data from serialized HTML."""

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


# ---------------------------------------------------------------------------
# AC1: activity feed renders hostile fields as text
# ---------------------------------------------------------------------------


def test_activity_feed_renders_hostile_fields_as_text(client: TestClient, tmp_path) -> None:
    out = _run_dashboard_js(client, tmp_path)
    collector = _parse(out["feed"])

    tags = set(collector.tags)
    assert "img" not in tags, "an <img> element was injected into the activity feed"
    assert "script" not in tags, "a <script> element was injected into the activity feed"
    attr_names = {name for name, _ in collector.attrs}
    injected_handlers = attr_names & {"onerror", "onmouseover"}
    assert not injected_handlers, (
        f"event-handler attributes injected into the activity feed: "
        f"{sorted(injected_handlers)}"
    )
    allowed = {"ul", "li", "span"}
    unexpected = tags - allowed
    assert not unexpected, f"unexpected element types in the activity feed: {sorted(unexpected)}"
    assert "li" in tags, "the activity feed rendered no list item at all"

    data = "".join(collector.data)
    assert HOSTILE_STAGE in data, "the stage payload must survive as visible text"
    assert HOSTILE_CALLER in data, "the caller payload must survive as visible text"
    assert HOSTILE_ROUTE in data, "the route payload must survive as visible text"
    assert "2026-01-01 00:00:00" in data, "the ts field must still be rendered"
    assert "[m]" in data, "the model field must still render inside square brackets"


# ---------------------------------------------------------------------------
# AC2: sparkline renders hostile values as attributes, not markup
# ---------------------------------------------------------------------------


def test_sparkline_renders_hostile_value_as_attribute(client: TestClient, tmp_path) -> None:
    out = _run_dashboard_js(client, tmp_path)
    collector = _parse(out["spark"])

    tags = set(collector.tags)
    assert "img" not in tags, "an <img> element was injected into the sparkline"
    assert "script" not in tags, "a <script> element was injected into the sparkline"
    attr_names = {name for name, _ in collector.attrs}
    injected_handlers = attr_names & {"onerror", "onmouseover"}
    assert not injected_handlers, (
        f"event-handler attributes injected into the sparkline: "
        f"{sorted(injected_handlers)}"
    )
    allowed = {"div", "span"}
    unexpected = tags - allowed
    assert not unexpected, f"unexpected element types in the sparkline: {sorted(unexpected)}"
    assert "div" in tags, "the sparkline rendered no bar at all"

    titles = [value for name, value in collector.attrs if name == "title"]
    assert "5" in titles, "the benign daily value must still appear as a title attribute"
    assert HOSTILE_SPARK_VALUE in titles, (
        "the hostile daily value must be present as an attribute VALUE "
        "(escaped), not spliced into markup"
    )


# ---------------------------------------------------------------------------
# Structural tripwire (in addition to, never instead of, the tests above)
# ---------------------------------------------------------------------------


def test_dashboard_page_avoids_innerhtml(client: TestClient) -> None:
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "innerHTML" not in resp.text, (
        "the dashboard script must not use innerHTML at all "
        "(see promotions_dashboard.py for the DOM-construction pattern)"
    )


def test_promotions_dashboard_also_avoids_innerhtml() -> None:
    """The sibling page had no XSS coverage at all.

    `promotions_dashboard.py` already renders through createElement/textContent
    — it is what #94 copied. But nothing held it there, so a regression on this
    page would have shipped silently while /dashboard stayed green. This is a
    structural tripwire, not a behavioural test: it cannot prove escaping, only
    that the unsafe sink has not come back.
    """
    from axon.http import promotions_dashboard

    source = promotions_dashboard.__file__
    assert source is not None
    js = Path(source).read_text()

    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in js, f"{sink} reintroduced in promotions_dashboard.py"
