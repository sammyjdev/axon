# AXON Launch Runbook

Step-by-step guide for the human maintainer. Execute these in order.

---

## A Note on What This Runbook Is

> The steps below are intentionally NOT executed by the AI agent that produced
> this document. They are outward-facing, irreversible, or depend on real
> accounts and real people — PyPI uploads cannot be undone, invitations go to
> actual humans, and public posts carry your name. Each step requires your
> accounts, your authorization, and your judgement. The agent's role was to
> write the playbook, not run it.

---

## Phase 1 — Pre-Launch Checklist

### 1.1 GitHub URL — done

The repository is public at `https://github.com/sammyjdev/axon`, and that URL is
already set throughout the codebase (`README.md`, `site/`, `pyproject.toml`, the
launch posts, and this runbook). No action needed.

### 1.2 Confirm the license

`pyproject.toml` and `LICENSE` currently specify MIT. The original project plan
mentioned Apache 2.0. Decide which you intend and make them consistent:

- `LICENSE` (file in repo root)
- `pyproject.toml` → `license` field

This is your decision to make. MIT and Apache 2.0 are both fine for open-source
tools; they differ primarily in patent grant language.

### 1.3 Run the full test suite

```bash
rtk pytest tests/ -q
```

All tests must pass. Do not proceed with publish if tests are failing.

### 1.4 Run linting

```bash
rtk ruff check
```

Fix any errors. Warnings are at your discretion.

### 1.5 Run the benchmark

```bash
make bench
```

Confirm the modeled output still shows ~52% savings (or update `benchmarks/README.md`
if the model parameters changed). The benchmark is a deterministic model, not
live instrumentation — results should be stable unless you changed `benchmarks/model.py`.

### 1.6 Verify the package builds

```bash
python3 -m build
```

Confirm `dist/` contains both a `.whl` and a `.tar.gz` with the correct version.
Check `pyproject.toml` → `[project] version` matches what you want to publish.

---

## Phase 2 — PyPI Publish

> This is irreversible. A version number cannot be re-uploaded to PyPI once
> published, even if you delete the release. Bump the version before retrying
> any failed publish.

### 2.1 Test on TestPyPI first (recommended)

```bash
python3 -m twine upload --repository testpypi dist/*
```

Then install from TestPyPI in a fresh virtualenv to confirm it works end-to-end:

```bash
pip install --index-url https://test.pypi.org/simple/ axon-mcp
axon --version
```

You will need a TestPyPI account and API token. Create one at
https://test.pypi.org/account/register/ if you don't have one.

### 2.2 Publish to PyPI

```bash
python3 -m twine upload dist/*
```

Prerequisites:
- PyPI account at https://pypi.org
- API token configured (add to `~/.pypirc` or pass as `--password`)
- Package name `axon-mcp` must be available (check https://pypi.org/project/axon-mcp/)

After upload, install from PyPI to confirm:
```bash
pip install axon-mcp
axon --version
```

### 2.3 Update README install instructions

Once PyPI is live, update `README.md` to replace the "install from source"
quickstart with:

```bash
pip install axon-mcp
```

Commit and push this change.

---

## Phase 3 — Private Beta

### 3.1 Send beta invitations

Use the template in `docs/launch/beta.md`. Target ~5 developers:
- 1–2 from the Obsidian community (plugin developers, power users)
- 2–3 from the AI-coding community (people who use Claude Code, Cursor, or Codex
  heavily)

Replace `[NAME]`, `[CONTACT]`, and `[YOUR NAME]` in the template, and confirm
you have replaced the placeholder GitHub URL before sending.

### 3.2 Collect feedback

Give beta testers at least 3–5 days of active use before asking for feedback.
Collect responses using the questions in `docs/launch/beta.md` (paste into
Google Forms, Typeform, or collect via email).

### 3.3 Triage and fix critical bugs

Before moving to Phase 4, address:
- Any crash or data-loss bug reported by more than one tester
- Any install failure on a supported platform
- Any MCP registration failure on a supported agent

Defer nice-to-haves and feature requests to a post-launch backlog. Open GitHub
issues for everything so the community can see what's tracked.

---

## Phase 4 — Public Launch Posts

All posts are in `docs/launch/posts/`. Confirm each file has the real GitHub URL
(`sammyjdev/axon`) and the PyPI install command before posting.

### 4.1 Hacker News — Show HN

**File:** `docs/launch/posts/hn-show-hn.md`

**When:** Weekday morning, US Eastern time (9–11 AM ET). Tuesday through
Thursday tend to perform best. Avoid Mondays (busy) and Fridays (low traffic).

Post at https://news.ycombinator.com/submit. Title starts with "Show HN:".
Copy the post body from the file. Monitor comments for the first few hours and
respond to questions promptly — early engagement affects ranking.

### 4.2 Reddit — r/ClaudeAI

**File:** `docs/launch/posts/reddit-claudeai.md`

**When:** Same day as HN, or the day after. Stagger by at least 2 hours from
the HN post.

Post at https://www.reddit.com/r/ClaudeAI/. Follow subreddit rules on
self-promotion (read the sidebar before posting). Be present in comments.

### 4.3 Reddit — r/cursor (or r/CursorAI)

**File:** `docs/launch/posts/reddit-cursor.md`

**When:** Same day or next day as r/ClaudeAI. Check which subreddit is more
active at time of posting.

### 4.4 LinkedIn

**File:** `docs/launch/posts/linkedin.md`

**When:** 1–2 days after HN. LinkedIn engagement peaks midweek (Tuesday–Thursday)
during business hours. Avoid posting the same day as HN to avoid spreading
your own attention thin.

Post from your personal profile. Tag relevant communities or people if appropriate,
but keep it natural.

### 4.5 Bluesky

**File:** `docs/launch/posts/bluesky-thread.md`

**When:** Any time after HN, same day or next. Bluesky is less time-sensitive
than HN.

The file is formatted as a thread. Post each segment as a reply to the previous
one. Use relevant hashtags if the community norms support it.

---

## Phase 5 — Success Metrics

These are targets to track, not steps to complete. None of them are under full
control of the maintainer — treat them as signal, not pass/fail gates.

| Metric | Target | How to check |
|---|---|---|
| GitHub stars | >50 in first 48h after HN post | GitHub repo → Stars tab |
| External issues / PRs | >5 opened by non-maintainers within one week | GitHub Issues / PRs |
| Technical newsletter mention | >1 mention (e.g. TLDR, Pointer, AI digest) | Search / Google Alerts |
| PyPI live and installable | Confirmed | `pip install axon-mcp && axon --version` |

Set up a GitHub Star notification or check manually at the 24h and 48h marks.
Search for "axon-mcp" and "AXON MCP" on the relevant platforms a week after launch.

---

## Quick Reference: File Locations

| Artifact | Path |
|---|---|
| Beta invitation + feedback form | `docs/launch/beta.md` |
| HN post | `docs/launch/posts/hn-show-hn.md` |
| Reddit (Claude) | `docs/launch/posts/reddit-claudeai.md` |
| Reddit (Cursor) | `docs/launch/posts/reddit-cursor.md` |
| LinkedIn | `docs/launch/posts/linkedin.md` |
| Bluesky thread | `docs/launch/posts/bluesky-thread.md` |
| Benchmark README | `benchmarks/README.md` |
| pyproject.toml | `pyproject.toml` |
