# Issue #68 triage: `ruff check --select S src/` findings

Ad hoc report-mode scan (66 findings). `[tool.ruff.lint].select` stays
`["E", "F", "I", "UP"]` unchanged in this pass -- wiring `S` into the
enforced select/gate/pre-commit is issue #69, which depends on this one.

## Verdict summary

| Decision | Count | Rules |
|---|---|---|
| FIX | 4 | S314 |
| SUPPRESS | 62 | S603, S607, S110, S112, S608, S310, S101, S324, S311, S108, S104 |

## FIX: S314 untrusted XML parsing (`src/axon/expansion/extractors.py:73,101,293,296`)

`ElementTree.fromstring(payload)` parses XML fetched from remote RSS/Atom
feeds (untrusted network input) at all 4 sites. stdlib `xml.etree` is
vulnerable to entity-expansion ("billion laughs") DoS. Swapped to
`defusedxml.ElementTree.fromstring` (new dependency `defusedxml>=0.7.1`,
added to `pyproject.toml`). The `from xml.etree import ElementTree` import
stays for the `ElementTree.Element` type annotation at line ~224 (defusedxml
does not export that type).

Regression test: `tests/expansion/test_extractors_xxe.py` (new file) --
billion-laughs RSS/Atom payloads via `extract_documents` and
`resolve_article_urls` must raise `defusedxml.common.DefusedXmlException`;
one valid-RSS happy-path regression confirms the swap doesn't change normal
parsing.

## SUPPRESS: policy justifications (62 findings, inline `# noqa: S###`, code-only per line-length)

- **P1 - S603/S607 (31 findings, ~15 files):** Trusted first-party subprocess:
  fixed tool (`git`/`rtk`/`python3`/`sysctl`/`nvidia-smi`) invoked with
  list-form argv, no `shell=True`, no untrusted value interpolated into a
  shell string. PATH resolution (partial exe path) is intentional for a dev
  CLI.
- **P2 - S110/S112 (10 findings):** Best-effort telemetry / resource-cleanup /
  parse-skip; the failure is non-fatal by design and a fallback path follows.
- **P3 - S608 (7 findings):** No SQL injection -- interpolated tokens are a
  regex-validated table identifier (`^[a-z_][a-z0-9_]*$`), an `int()`-cast
  `LIMIT`, or fixed literal WHERE-clause fragments; every user value is bound
  as an asyncpg `$n` parameter, never interpolated.
- **P4a - S310 `rtk_bootstrap.py` (3):** Hardcoded `https://` GitHub API
  endpoint built from the fixed `RTKX_REPO` constant; no `file:`/custom
  scheme reachable.
- **P4b - S310 `transport.py` (2):** URL comes from first-party source config
  (the user's own vault), not third-party-controlled input.
- **P5a - S101 (4, `inference.py:138`, `pb.py:1074-1076`):** Type-narrowing
  assert; the invariant is already guaranteed by preceding control flow, not
  a runtime security/validation check.
- **P5b - S101 (1, `supersession.py:109`):** Assert validates fixture data in
  an offline benchmark harness, never a production code path.
- **P6 - S324 (`file_cache.py:64`):** SHA-1 is a content-dedup cache key, not
  a security primitive; the digest must stay byte-identical to `pipeline.py`
  per the function's own docstring, so `usedforsecurity=False` is
  intentionally not used.
- **P7 - S311 (`retry.py:53`):** `random.random()` supplies retry-backoff
  jitter timing, not cryptographic material.
- **P8 - S108 (`retrieval.py:92`):** Literal fixture `file_path` in a
  benchmark expectation, not a real filesystem temp path.
- **P9 - S104 (`setup.py:39`):** `"0.0.0.0"` is the default shown in an
  interactive setup prompt the user reviews/overrides before the HTTP
  transport is opted into.

Full 66-line mapping (file:line -> rule -> decision -> policy) was produced
by the Plan stage and applied verbatim by the Execute stage; this file
summarizes the policies. Per-line detail is recoverable from the diff itself
(each suppressed line carries its `# noqa: S###` marker) plus this policy
key.

## Verification

- `ruff check --select S src/` -> 0 findings (was 66).
- `ruff check src/` -> unchanged clean (`E`,`F`,`I`,`UP` still pass, no new
  E501 from noqa/reflow edits).
- `pytest tests/expansion/ -q` -> green except 2 pre-existing
  testcontainers/network failures in `test_service_integration.py`,
  confirmed identical with this issue's diff `git stash`-ed (not a
  regression).
