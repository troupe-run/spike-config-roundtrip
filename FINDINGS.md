# Spike: troupe config round-trip (includes, provenance, write-back, watcher loop safety)

Throwaway feasibility spike. Answers, not production code.
Stack: Python 3.12+, ruamel.yaml 0.19.1, watchfiles 1.3.0, eventsourcing 9.5.5, jsonschema 4.26.0, pytest.
Run: `uv run pytest -q`

## Questions

1. Load root + nested includes (yaml-in-yaml-in-yaml, md text include) into a merged view with provenance per node? — see below
2. `set(dotted_path, value)` writes into the correct source file (nested yaml include, md text include), preserving untouched formatting? — see below
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

## Q2. `set()` writes into the owning file, preserving the rest — **YES, with caveats (needs a splice strategy, not plain ruamel dump)**

Tests: `tests/test_q2_writeback.py` (8 tests; each asserts the exact set of changed lines).

Rule: `provenance[path].source` is the file written; `inner_path` is where inside it. Text includes
(`kind="text"`) are written by replacing the whole target file's contents; the `file:` reference in the
including YAML is untouched.

**Surprise: plain ruamel round-trip dump is NOT good enough on its own.** First attempt (load -> mutate
`CommentedMap` -> dump) failed 4 of 5 tests:
- It re-indents every block sequence in the touched file (`  - bash` becomes `- bash`) because ruamel's
  default `sequence/offset` indent is global, not remembered per file. (`test_naive_ruamel_dump_would_reindent_sequences`)
- It does not preserve spacing before a trailing comment on the changed line (`"demo"   # c` -> `"renamed" # c`).
- `preserve_quotes=True` is required or `"demo"` loses its quotes.

Fix adopted: **two-tier write strategy**
1. **Splice (default for scalar -> scalar):** use `YAML().parse()` events, which carry exact
   `start_mark.index`/`end_mark.index`, to find the old scalar's character span; render the new value in
   the old value's style (plain / `'single'` / `"double"` / `!literal` tag); splice it into the raw text;
   re-parse and verify the value at that path is the new value, else fall back. Everything else in the file
   is byte-identical, including comment spacing.
2. **Dump (structural changes: new keys, scalar->mapping, block scalars):** ruamel round-trip dump with
   per-file indentation detected from the original text (`detect_indent`). Comments and key order survive;
   list style survives with detection. Collateral risk is limited to the file being changed.

Actual diffs produced by the tests:

```diff
--- a/.troupe/troupe.yml                       # set project.name renamed  (root, splice)
-  name: "demo"   # quoted on purpose
+  name: "renamed"   # quoted on purpose

--- a/.troupe/agents/limits.yaml               # set agents.coder.limits.max_tokens 16000  (2 levels of include)
-max_tokens: 8000  # hard cap
+max_tokens: 16000  # hard cap

--- a/.troupe/agents/coder.yaml                # set agents.coder.model 'sonnet'  (single quotes kept)
-model: 'opus'
+model: 'sonnet'

--- a/.troupe/prompts/coder.md                 # set agents.coder.system_prompt "<text>"  (md text include)
 You write code. Keep it simple.
+Always add tests.

--- a/.troupe/agents/coder.yaml                # set agents.coder.model {name: opus, temperature: 0.2}  (dump path)
-model: 'opus'
+model:
+  name: opus
+  temperature: 0.2
 tools:
   - bash          # untouched list keeps its indent thanks to detect_indent
```

Caveats:
- `detect_indent` is a heuristic (first block sequence in the file). Files mixing list styles would be
  normalised on a dump-path write. Splice path has no such risk.
- Text include `set` is whole-file replace; there is no "edit line 3 of the prompt" operation. A CLI should
  probably offer `troupe config edit <path>` (open $EDITOR) for text values.
- Replacing a whole include root (e.g. `set agents.coder <scalar>`) is refused in the spike ("cannot replace a
  whole file root"). The real design must decide: replace the include reference in the parent file, or
  rewrite the included file. Recommend: explicit `--unlink-include` flag; default refuse.

## Recommendation

_pending_
