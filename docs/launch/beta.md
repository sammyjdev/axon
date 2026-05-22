# AXON Private Beta Kit

## Invitation Message Template

> Send individually. Adjust tone to fit your relationship with the recipient.

---

Subject: Early access to AXON — AI context continuity for coding agents

Hi [NAME],

I'm running a small private beta for AXON, a tool I've been building to solve
the "blank slate" problem with AI coding agents. Every time you switch between
Claude Code, Codex, or Cursor — or pick up a project after a few days — your
assistant starts from zero. AXON captures context at git commits and session
boundaries and surfaces it on demand over MCP or a plain markdown file.

I'm inviting ~5 developers to try it before the public launch. Given your work
with [Obsidian / AI coding tools / relevant context], I think you'd have useful
feedback.

What I'm asking: install it, try it on a real project for a few days, and answer
a short feedback form (~10 questions). That's it. No commitment beyond that.

Install takes about 5 minutes:
https://github.com/sammyjdev/axon

Interested? Reply here or reach me at [CONTACT].

Thanks,
[YOUR NAME]

---

## Getting-Started Checklist for Beta Testers

Complete these steps in order. If anything fails, note the exact error — that
friction is itself useful feedback.

- [ ] **Clone and install**
  ```bash
  git clone https://github.com/sammyjdev/axon.git
  cd axon
  pip install -e .
  axon --version   # confirm install succeeded
  ```

- [ ] **Initialize AXON in one of your existing repos**
  ```bash
  axon init /path/to/your-repo
  ```
  This installs git hooks and indexes the codebase. Watch for errors. Note how
  long indexing takes on your machine.

- [ ] **Register the MCP server with your agent**

  For Claude Code — add to `.claude/settings.json` in your repo:
  ```json
  {
    "mcpServers": {
      "axon": {
        "command": "axon",
        "args": ["serve"]
      }
    }
  }
  ```
  For other agents (Cursor, Codex), use the equivalent MCP server config
  mechanism. The command is always `axon serve`.

- [ ] **Verify the server is reachable**
  ```bash
  axon health
  ```
  Or, from inside a Claude Code session, call the `axon_health` tool.

- [ ] **Make a commit and check context capture**
  Make any small commit in the repo. Then call `axon_get_context` from your
  agent and confirm recent work shows up.

- [ ] **Try a handoff**
  Open a fresh agent session (or switch agents) and call `axon_handoff`. Verify
  that context from your previous session is present.

- [ ] **Use it normally for 2–3 days**
  Work on the repo as you normally would. Let AXON run in the background. Note
  any moments where context felt right or wrong.

- [ ] **Fill in the feedback form below and send it back**

---

## Feedback Form

*Paste these questions into a Google Form, Typeform, or reply directly by
email. Plain text answers are fine — thoroughness matters more than polish.*

1. **Setup friction** — How long did install + `axon init` take? Did anything
   fail or require troubleshooting? What OS and Python version?

2. **MCP registration** — Which agent did you register AXON with? Did the
   server appear and respond? Any errors during registration?

3. **Context quality** — After 2–3 days of use, did `axon_get_context` return
   context that felt accurate and useful? Give a concrete example of a moment
   it helped (or failed to help).

4. **Handoff experience** — Did you try switching agents or resuming after a
   gap? Did the handoff context reflect what you were actually working on?

5. **Token savings** — Did you notice a difference in how quickly your agent
   reached context limits? (Rough impression is fine — we're not asking you to
   instrument anything.)

6. **False positives / noise** — Did AXON surface context that was irrelevant
   or confusing? How often?

7. **Breaking bugs** — List any crashes, silent failures, or data that looked
   wrong. Include the command and error text if you have it.

8. **Obsidian integration** (if applicable) — Did you try the Obsidian export?
   Did the exported notes appear where expected and look correct?

9. **Would you keep using it?** — Yes / No / Maybe. One sentence on why.

10. **What's the one thing that would make you recommend this to a colleague?**
    What's the one thing that would stop you?
