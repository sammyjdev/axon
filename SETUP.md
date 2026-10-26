# Setup per machine

How a machine gets the `axon` CLI, the MCP server and the git hooks. The
quickstarts under `docs/` cover a first install with its own Postgres; this file
covers each further machine of one owner.

## Roles

| Role | Runs | Needs |
|---|---|---|
| Server | the `axon-postgres` container (Postgres, port 5433) | Docker, `./setup.sh` |
| Client | the `axon` CLI, the MCP server, the git hooks | network access to the server's port 5433 |
| No AXON | nothing | no `~/.local/bin/axon`; the agent hooks that call it are left out |

No role needs a provider key: no commit and no MCP tool calls an LLM or an
embedding provider (dec-138). The optional vault hook is the only LLM call left.

## Client

1. Install the CLI from the engine checkout. It is a pipx snapshot, so repeat
   this after every pull that lands on master:

   ```bash
   pipx install --force ~/dev/axon
   ```

   The binary lands in `~/.local/bin/axon`, the path the agent hooks call.

2. Point it at the server. A plain pipx install reads the environment only
   (an editable install also loads the checkout's `.env.local`). Put these in
   your shell rc: the git hooks run with whatever environment git inherits, and
   without them a commit silently falls back to `localhost`:

   ```bash
   export AXON_PG_URL=postgresql://axon:axon@<server>:5433/axon
   export AXON_LESSONS_PG_URL=postgresql://axon:axon@<server>:5433/axon_lessons
   export AXON_VAULT="$HOME/vault"
   ```

3. Register the MCP server (stdio) once, with the same values, because the
   agent does not inherit your shell:

   ```bash
   claude mcp add axon -s user \
     -e AXON_PG_URL="$AXON_PG_URL" \
     -e AXON_LESSONS_PG_URL="$AXON_LESSONS_PG_URL" \
     -e AXON_VAULT="$AXON_VAULT" \
     -- ~/.local/bin/axon serve
   ```

4. Onboard each repo where decisions should be captured, and check:

   ```bash
   axon init /path/to/repo
   axon health
   ```

   `axon health` reports Postgres, vault and git. List the repo in
   `~/.claude/axon/ROUTER.md`, the single source of truth for onboarded repos.

## No AXON

Leave `~/.local/bin/axon` absent and do not register the MCP server. The agent
settings generator in agents-skills drops the hooks that call the binary when
it is missing.
