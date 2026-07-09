# platform: Reddit — r/cursor
# suggested_title: AXON puts a .axon/context.md in your repo so Cursor always
#                  has project context — even without MCP
# notes: Post as a text post. Markdown renders normally on Reddit.
#        Strip this header before posting.

---

**AXON puts a `.axon/context.md` in your repo so Cursor always has project
context — even without MCP**

One thing that has frustrated me about AI coding assistants in general is that
the context you build up over multiple sessions just evaporates. Cursor is
great at in-session work, but if you switch machines, hand off to a teammate,
or come back after a week away, it starts blank.

I built AXON to fix this. The short version: AXON installs git hooks in your
repo, captures context at commit and session boundaries, and keeps a
`.axon/context.md` file in the repo root that it updates automatically.

**Why a file instead of only MCP?**

MCP is the primary interface — if you configure the AXON MCP server in Cursor,
you get query-able tools (`axon_get_context`, `axon_search`) that pull only the
context slice you need. But not every setup has MCP configured, not every
agent supports it, and sometimes you just want a plain file you can read,
grep, or paste.

The `.axon/context.md` fallback exists for exactly those cases. It is not
generated on demand — AXON writes it synchronously every time a capture event
fires (git commit, session end) so it is always current. It includes
architectural decisions inferred from commit messages, open questions from
session summaries, and a code index entry point.

**What this looks like in practice for Cursor:**

- You commit a change. AXON's post-commit hook fires, updates SQLite, and
  rewrites `.axon/context.md`.
- Next time you open Cursor on this repo, you (or Cursor's context picker) can
  reference `.axon/context.md` at the top of the session. The model gets the
  project's decision history without you typing it.
- If you also configure the MCP server, Cursor can query it directly for a
  more targeted context slice.

**Honest status:**

Alpha, not on PyPI, install from source. The file-based fallback is the most
stable part of the system — the MCP tools and session hook coverage are still
getting hardened. Apache-2.0 license, fully self-hosted.

Repo: https://github.com/sammyjdev/axon

Would be curious to hear from anyone who has set up custom context files for
Cursor — wondering how people are handling this today.
