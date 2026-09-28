# Spike: troupe config round-trip (includes, provenance, write-back, watcher loop safety)

Throwaway feasibility spike. Answers, not production code.
Stack: Python 3.12+, ruamel.yaml 0.19.1, watchfiles 1.3.0, eventsourcing 9.5.5, jsonschema 4.26.0, pytest.
Run: `uv run pytest -q`

## Questions

1. Load root + nested includes (yaml-in-yaml-in-yaml, md text include) into a merged view with provenance per node? — _pending_
2. `set(dotted_path, value)` writes into the correct source file (nested yaml include, md text include), preserving untouched formatting? — _pending_
3. New key under an included node: which file? — _pending_
4. Remote (https) includes: load works, set rejected clearly? — _pending_
5. Loop safety: write-back observed by watcher yields zero events; external edit yields exactly expected events? — _pending_
6. Schema-guided include detection (`format: uri` literal, untyped URL included, `!literal` round-trips)? — _pending_
7. Include cycles detected with a clear error? — _pending_
8. Surprises / hard parts / what didn't work — _pending_

## Recommendation

_pending_
