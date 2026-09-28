# Spike: troupe config round-trip (includes, provenance, write-back, watcher loop safety)

Throwaway feasibility spike. Answers, not production code.
Stack: Python 3.12+, ruamel.yaml 0.19.1, watchfiles 1.3.0, eventsourcing 9.5.5, jsonschema 4.26.0, pytest.
Run: `uv run pytest -q`

## Questions

1. Load root + nested includes (yaml-in-yaml-in-yaml, md text include) into a merged view with provenance per node? — see below
2. `set(dotted_path, value)` writes into the correct source file (nested yaml include, md text include), preserving untouched formatting? — _pending_
3. New key under an included node: which file? — _pending_
4. Remote (https) includes: load works, set rejected clearly? — _pending_
5. Loop safety: write-back observed by watcher yields zero events; external edit yields exactly expected events? — _pending_
6. Schema-guided include detection (`format: uri` literal, untyped URL included, `!literal` round-trips)? — _pending_
7. Include cycles detected with a clear error? — _pending_
8. Surprises / hard parts / what didn't work — _pending_

## Q1. Merged view with provenance — **YES**

Test: `tests/test_q1_provenance.py` (2 tests).

How: each source file is loaded separately with ruamel.yaml round-trip (`YAML()`, `preserve_quotes=True`) and kept
as its own `CommentedMap` doc. A walker builds a plain merged dict and, alongside it, a flat
`dict[merged_path_tuple -> Provenance(source, inner_path, kind, line, via)]` for **every** node (mappings,
sequence items, scalars, include roots). Line numbers come from ruamel's `.lc` data. `via` records the include
reference string (e.g. `file:limits.yaml`) that pulled a source in; `include_sites` records where that reference
scalar lives, which is needed later for error messages and for "replace the include" operations.

Caveats:
- Provenance is a side table keyed by merged path, not attached to nodes. Simple and sufficient; it must be
  rebuilt on every reload (cheap at config sizes).
- ruamel's merged view is **not** used for writing; we write to the per-file docs. There is no single
  round-trip document for the whole tree, and there shouldn't be.
- A text include's provenance is the whole file (`inner_path=()`, `kind="text"`); there is no finer granularity.

## Recommendation

_pending_
