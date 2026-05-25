# P3 Extension Docs

This page documents the current extension surfaces that already exist in the
codebase:

- domain packs in `src/axon/domains/pack.py`
- portability export/import in `src/axon/portability/`
- local plugin/tool registry contracts in `src/axon/registry/`
- benchmark harness code in `src/axon/benchmark/`

## 1. Create a domain pack

`load_domain_pack(path)` accepts either:

- a directory containing `domain-pack.json`
- the manifest file itself

The current loader requires `schema_version`, `domain_id`, `display_name`,
`description`, and `signals`. It also reads these optional fields when present:

- `version`
- `default_profiles`
- `retrieval_defaults`
- `policy_defaults`
- `examples`

The `signals` object supports:

- `languages`
- `artifact_types`
- `task_types`

Minimal shape:

```json
{
  "schema_version": "1",
  "version": "1.0.0",
  "domain_id": "software",
  "display_name": "Software",
  "description": "General software engineering artifacts and workflows.",
  "default_profiles": ["solo-dev", "team-dev"],
  "retrieval_defaults": { "top_k": 6, "chunk_size": 1200 },
  "policy_defaults": { "cloud_policy": "avoid", "memory_tier": "balanced" },
  "examples": [
    {
      "name": "review-change",
      "prompt": "Review the following software change for correctness and tests.",
      "template": "Change summary:\n{{summary}}\n\nDiff:\n{{diff}}"
    }
  ],
  "signals": {
    "languages": ["python", "typescript"],
    "artifact_types": ["source_code", "documentation"],
    "task_types": ["implementation", "review"]
  }
}
```

Checked-in examples live under `domain-packs/`, including both flat language
packs such as `domain-packs/python.json` and directory-based packs such as
`domain-packs/software/domain-pack.json`. The repository tests cover loading
both the richer current shape and the legacy minimal shape.

## 2. Export and import a setup

The current CLI entry points are:

```bash
pb portability export <bundle-dir>
pb portability import <bundle-dir> <engine-root>
```

Export writes a bundle rooted at the destination directory with:

- `manifest.json`
- `config/axon.toml` when the config file exists
- `metadata/env.json`
- `metadata/indexed-contexts.json`
- optional store files under `stores/`

Import verifies the bundle manifest version and artifact checksums, then
restores files into the target engine root.

Path mapping during import is fixed:

- `config/*` and `metadata/*` go under the engine root
- `stores/*` goes under `<engine-root>/data/*`

The exporter only records allowlisted environment metadata. It does not export
secret values.

## 3. Register a plugin or tool

There is no separate registration command. Discovery is filesystem-based:

```python
from pathlib import Path
from axon.registry import discover_local_registry

registry = discover_local_registry(Path("/path/to/plugins"))
```

The discovery code recursively scans for `plugin.json` files below the root.
Each plugin manifest is loaded with these fields:

- required: `plugin_id`, `name`, `version`
- optional: `description`, `enabled`, `contexts`, `capability_tags`,
  `tool_descriptors`

Tool descriptor files are referenced relative to the plugin manifest directory
and are loaded from those paths. Tool descriptors use:

- required: `tool_id`, `plugin_id`, `name`, `description`
- optional: `contexts`, `packs`, `capability_tags`

`contexts` must be valid registered contexts. `packs` is the pack filter used
by `LocalRegistry.list_tools(ctx=..., pack=...)`.

Example layout:

```text
plugins/
  acme-search/
    plugin.json
    tools/
      query.json
      summarize.json
```

If you want a tool to appear for a pack, list that pack name in the tool
descriptor's `packs` array.

## 4. Run the benchmark harness

The benchmark harness is library-first. There is no dedicated `pb benchmark`
command yet.

Run the current benchmark suite through tests:

```bash
rtk pytest tests/benchmark -q
```

The default benchmark suite in `axon.benchmark.suite` contains three
fixed cases:

- retrieval
- compression fallback
- setup-mode sanity

You can also call it directly from Python:

```python
import asyncio

from axon.benchmark import run_default_benchmarks

summary = asyncio.run(run_default_benchmarks())
print(summary.total, summary.passed, summary.failed, summary.score)
```

For comparisons against a baseline, use:

```python
from axon.benchmark import compare_benchmark_runs, format_benchmark_comparison
```

Current limitation: the harness has no persistence or CLI wrapper; the tests
and library entry points are the supported surfaces today.
