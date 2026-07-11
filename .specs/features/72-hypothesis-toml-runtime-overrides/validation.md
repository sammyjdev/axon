## Validation: issue #72 — PASS
Spec-anchored check: 3/3 ACs matched (no spec.md for this issue — fallback to
assertion-exists-and-covers-the-criterion per quench.md).
- AC1 (new test module using hypothesis): `tests/config/test_runtime_toml_properties.py`,
  4 of 5 tests use `@given`/`@settings` (hypothesis-driven); 1
  (`test_unexpected_value_types_coerced_to_strings`) is a fixed-example test —
  noted as a minor rigor gap below, not blocking under Common-tier's
  spec-compliance-only bar.
- AC2a (malformed TOML): `test_malformed_toml_handling` — generates invalid TOML
  via hypothesis, asserts `tomllib.TOMLDecodeError` (a typed, clear error) or
  empty dict; explicitly rejects untyped/generic exceptions
  (`RecursionError`, bare `AttributeError`/`TypeError` outside that path).
- AC2b (unexpected value types for known keys): `test_unexpected_value_types_coerced_to_strings`
  — int/float/bool/list/dict values for allowed keys, asserts safe `str()`
  coercion, all values are `str` instances.
- AC2c (large/deeply-nested input): `test_large_deeply_nested_toml_no_crash`
  (10-100 nested table levels) + `test_very_large_string_value_no_crash`
  (1KB-100KB strings) — both assert no uncontrolled crash and correct values.
- AC3 (runs under normal pytest): plain pytest file, `tmp_path`/`monkeypatch`
  fixtures only, no new CI infra.
- Bonus test: `test_unknown_keys_are_filtered_out` — allowlist enforcement.

Mutation sensor: 1 injected (Common tier), 1 killed, 0 survived.
- Mutation: flipped `if key in allowed` to `if key not in allowed` in
  `_load_toml_runtime_overrides` (src/axon/config/runtime.py:239) — primary
  happy-path filtering logic.
- Result: killed by 4/5 tests in the new file
  (`test_unexpected_value_types_coerced_to_strings`,
  `test_large_deeply_nested_toml_no_crash`, `test_very_large_string_value_no_crash`,
  `test_unknown_keys_are_filtered_out`); `test_malformed_toml_handling`
  correctly unaffected (input never reaches the mutated line).
- Mutation applied/reverted in scratch state only; `git diff` on
  `src/axon/config/runtime.py` confirmed clean before and after.

Real-bug finding: none. `_load_toml_runtime_overrides` already raises
`tomllib.TOMLDecodeError` (a clear, typed, `ValueError` subclass) on malformed
TOML, and its `str(value)` coercion for allowed keys never raises for any
TOML-decodable Python type (str/int/float/bool/list/dict/date/datetime/time).
No source fix was required or made.

Gate-coverage note: `gate_cmd` (`ruff check src/axon/router src/axon/resilience
tests/router tests/resilience && pytest tests/router tests/resilience
tests/store tests/scripts tests/cli tests/doctor -q`) does not exercise
`tests/config/` at all — this pass's new test file is NOT covered by the
configured gate. Validated manually instead: `pytest
tests/config/test_runtime_toml_properties.py -q` → 5 passed; `pytest
tests/config/ -q` (full existing directory) → no regression; `ruff check
tests/config/test_runtime_toml_properties.py` → zero issues (pre-commit
ruff-check hook from #67 also lints this new file, not excluded).

Report: .specs/features/72-hypothesis-toml-runtime-overrides/validation.md
