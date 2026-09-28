# Spike: troupe config round-trip (includes, provenance, write-back, watcher loop safety)

Throwaway feasibility spike. Answers, not production code.
Stack: Python 3.12+, ruamel.yaml 0.19.1, watchfiles 1.3.0, eventsourcing 9.5.5, jsonschema 4.26.0, pytest.
Run: `uv run pytest -q`

**Final full-suite run (2026-09-28, after Q9):** `112 passed in 16.19s`
(watcher tests in `test_q5_loop_safety.py` additionally re-run after the Q9 changes, with no flakes.)

Verdicts: Q1 yes · Q2 yes-with-caveats (superseded by Q9) · Q3 yes · Q4 yes · Q5 yes · Q6 yes-with-caveats · Q7 yes · Q8 below · Q9 yes (62/64 surgical; 2 are unloadable inputs).

## Questions

1. Load root + nested includes (yaml-in-yaml-in-yaml, md text include) into a merged view with provenance per node? — see below
2. `set(dotted_path, value)` writes into the correct source file (nested yaml include, md text include), preserving untouched formatting? — see below
3. New key under an included node: which file? — see below
4. Remote (https) includes: load works, set rejected clearly? — see below
5. Loop safety: write-back observed by watcher yields zero events; external edit yields exactly expected events? — see below
6. Schema-guided include detection (`format: uri` literal, untyped URL included, `!literal` round-trips)? — see below
7. Include cycles detected with a clear error? — see below
8. Surprises / hard parts / what didn't work — see below
9. (follow-up) Is EVERY write-back surgical — only the changed value's bytes differ — across a corpus of nasty YAML? — see below

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

> **Superseded by Q9:** the scalar-only splice described here has been replaced by the full surgical editor
> (`surgical.py`). Block scalars, structural changes and appends no longer use the dump path, and dumping is now
> opt-in. The Q2 tests still pass on the new engine.

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

## Q3. New key under an included node — **YES** (rule below)

Tests: `tests/test_q3_new_keys.py` (7 tests).

**Rule: a new key is written to the file that owns the deepest EXISTING ancestor of the path** (i.e. the
mapping that will directly contain it, or the nearest existing ancestor for deep paths; missing intermediate
mappings are created in that same file). Consequences:

| `set` | lands in | why |
|---|---|---|
| `agents.coder.temperature` | `agents/coder.yaml` | `agents.coder` *is* the root of coder.yaml |
| `agents.coder.limits.max_retries` | `agents/limits.yaml` | two-level include |
| `agents.coder.budget.daily_usd` | `agents/coder.yaml` | deepest existing ancestor = `agents.coder`; `budget:` created there |
| `agents.tester` (value `file:agents/tester.yaml`) | `troupe.yml` | the `agents` mapping lives in root; the new include is resolved on reload |
| `agents.reviewer.temperature` | `troupe.yml` | inline mapping |
| `agents.coder.system_prompt.extra` | rejected | parent is a text include (a string) |

This matches the intuition "the include IS that subtree" and keeps an included file self-contained and
reusable across projects. The alternative (always write overrides to the root/includer) would need a merge
semantic (include + local overrides at the same key) that the current include design doesn't have. If
per-project overrides of shared includes are wanted later, that's a separate feature (see Q8).

**Surprise (fixed):** ruamel attaches blank lines and a standalone comment that *follow* a mapping's last entry
to that entry (even when the comment visually introduces the next top-level section). A naive append puts the
new key underneath `# ---- settings ----`. `_steal_trailing_comment` moves that trailing block onto the new
key before dumping. (`test_new_key_goes_above_a_section_comment_not_below_it`). This is exactly the kind of
ruamel comment-attachment quirk that will keep appearing in the real implementation; it needs a corpus of
fixture files and golden diffs.

## Q4. Remote includes — **YES**

Tests: `tests/test_q4_remote.py` (5 tests). A `ThreadingHTTPServer` on 127.0.0.1 serves `shared.yaml`, which
itself includes `file:style.md` (resolved relative to the URL, so also remote).

- Loading works; provenance `source` is the URL and `Provenance.remote` is true.
- `set` on a remote scalar, on a remote text include, and a NEW key under a remote mapping are all rejected by
  `plan_set` with `ReadOnlyIncludeError` before anything is written (snapshot of project dir unchanged). Message:
  `cannot set 'shared.style': value comes from remote include http://127.0.0.1:PORT/style.md (included via
  'shared' in /…/.troupe/troupe.yml), which is read-only. Override it locally by replacing the include, or edit
  the remote source.`
- The message names the include reference in a **local** file (first version named the nearest site, which
  was inside the remote file, i.e. not actionable; fixed).

Caveats:
- Test uses `http://` (no TLS in a unit test); the code path treats `http://` and `https://` identically. The real
  implementation should probably refuse plain `http://` includes by default.
- Not addressed (by design of a spike): fetch caching, timeouts/offline behaviour, pinning (hash/ETag in the
  projection so a remote change is an explicit event, not a silent config change), auth. **Remote content
  changing underneath the projection is a real design question**: the watcher can't see it. Recommend recording
  the fetched content hash in the projection and treating a refresh as an explicit command that emits events.
- `file:` inside a remote YAML resolves against the URL. That's the natural rule but means a remote file can
  never reach the local filesystem (good; a security property worth keeping explicitly).

## Q5. Loop safety via content-diff idempotence — **YES**

Tests: `tests/test_q5_loop_safety.py` (6 tests; watcher tests run 5x in a row with no flakes, ~2.5s each).
Code: `src/spike_config_roundtrip/service.py`.

Model: `ProjectConfig` aggregate (eventsourcing 9.5.5, default POPO/in-memory store) with `ConfigSet(path, old,
new, source)` and `ConfigRemoved(path, old, source)`; `source` in {`import`, `cli`, `file-watch`}. The projection
is a flat `dotted.path -> leaf value` map.

**The one rule:** every route into the projection (startup import, CLI set, watcher batch) calls the same
`reconcile(source)`: load files -> flatten merged view -> diff against projection -> emit only differences. The
CLI path is `plan_set` (validate; rejects remote etc. with zero events) -> write back to owning file ->
`reconcile("cli")`.

Demonstrated:
- `cli_set("agents.coder.limits.max_tokens", 16000)` emits exactly one `ConfigSet(..., 8000, 16000, "cli")`. The
  watcher **does** observe the write-back (`limits.yaml` appears in its batch — asserted, not assumed) and that
  batch diffs to **0 events**. Same for a text (md) include write-back. No suppression flags exist anywhere.
- A hand edit touching three files at once (value change + key removal + key addition in `limits.yaml`, appended
  line in `coder.md`, and a **formatting-only** change `retries:   3   # tuned` in `troupe.yml`) produces exactly
  4 events, all `source="file-watch"`, and nothing for the formatting-only edit.
- Watcher readiness is proven with a sentinel file before tests act (FSEvents on macOS can otherwise miss
  writes made immediately after `watch()` starts).

Caveats / things the real implementation must handle:
- **Content diffing prevents echo loops, not lost updates.** If a user hand-edits a file between the CLI's load
  and its write-back, the write-back would clobber it. Added a compare-and-swap (re-read file, compare to loaded
  text, raise `ConcurrentEditError`). `test_cli_set_refuses_to_clobber_a_concurrent_hand_edit`. There is still a
  tiny TOCTOU window between that check and `os.replace`; acceptable for a config file, worth naming.
- **Source attribution needs a lock, idempotence doesn't.** The CLI holds a process lock across write+reconcile.
  Without it the watcher could reconcile first and the change would be attributed `file-watch` instead of `cli`
  — still exactly one event, just mislabelled. Across processes (CLI process vs daemon) this becomes a real
  design point: recommend the CLI sends the set to the daemon (which owns the lock and the watcher) rather than
  writing files itself.
- Removal needs its own event (`ConfigRemoved`); `ConfigSet(new=None)` would conflate "removed" with YAML `null`.
- Diff compares type as well as value (`1` vs `1.0` vs `true`).
- The flat leaf projection explodes lists into `tools.0`, `tools.1`; inserting at the head of a list emits an
  event per shifted item. Fine for a spike; the real design may want list values as leaves.
- Atomic writes (`tempfile` + `os.replace`) create `.troupe-tmp-*` files in the watched dir; the watcher filters
  them. The watcher reconciles on ANY change batch (full reload) rather than per-file; cheap at config sizes and
  it naturally handles newly-added include targets.

## Q6. Schema-guided include detection + `!literal` — **YES, with caveats**

Tests: `tests/test_q6_schema.py` (6 tests).

- Include detection walks the JSON Schema **in parallel with the YAML while loading** (`properties` /
  `additionalProperties` / `items`). If the subschema at a node has `format: uri` (also `uri-reference`,
  `iri`), the value stays a literal string, even when it is `file:agents/limits.yaml` and would resolve fine
  (a control test with no schema proves the same file *is* included otherwise).
- Untyped URL-shaped values (`extras.notes: file:notes.md`) are included.
- `!literal "https://…"` loads via ruamel as a `TaggedScalar`; the merged view gets the plain string; it is
  never fetched. It **round-trips with tag, quote style and trailing comment intact** both when another key is
  added to the same file (dump path) and when the literal value itself is `set` (splice path):
  `-  upstream: !literal "https://example.invalid/upstream.yaml"  # escape hatch`
  `+  upstream: !literal "https://example.invalid/v2.yaml"  # escape hatch`
- The merged view validates with `jsonschema` (Draft 2020-12) and invalid shapes are rejected.

**Surprise (fixed): `set` of a URL-shaped string silently changes meaning.** `set extras.link
https://example.invalid/x.yaml` wrote a plain scalar, which on reload became an include, which failed to
fetch, leaving a config on disk that no longer loads. Two fixes:
1. `set` writes URL-shaped strings as `!literal "…"` unless the schema says `format: uri` at that path, or the
   caller passes `include=True` (CLI: `troupe config set --include`). Adding an include is an explicit act.
2. If the post-write reload fails for any reason, the file is restored and the error re-raised.

Caveats:
- The spike's schema walker ignores `$ref`, `allOf/anyOf/oneOf`, `if/then`, `patternProperties`. A real
  implementation needs a proper "subschema at instance path" resolver; with `oneOf` the answer can depend on the
  value, which is circular (you need to know the value to know whether it's an include). Recommend: keep
  URI-typed fields out of `oneOf` branches, or require `!literal` there. Check `jsonschema`'s referencing
  support or write a small resolver over `$defs`/`$ref`.
- **`jsonschema` does not check `format: uri` unless `rfc3987` (or `jsonschema[format]`) is installed**; it
  silently passes `"not a uri"`. Install the extra in the real project.
- Validation runs on the merged (post-include) view. Errors should be reported with provenance (file + line),
  which the provenance table makes straightforward (`error.absolute_path` -> `where()`). Not built here.

## Q7. Include cycles — **YES**

Tests: `tests/test_q7_cycles.py` (4 tests).

Detection is a stack of **canonical** sources (`Path.resolve()` for files, URL string for remote) carried down
the recursive load; re-entering a source already on the stack raises
`IncludeCycleError: include cycle detected: /…/a.yml -> /…/b.yml -> /…/a.yml`. Covers two-file cycles, self
includes, and cycles spelled through different relative paths (`../sub/../a.yml`); `resolve()` also collapses
symlinks.

Caveat: a **diamond** (two keys including the same file) is correctly *not* a cycle, but it means one file has
two mount points in the merged view. `set x.v 2` writes `shared.yml` and `y.v` changes too. With content-diff
reconciliation the projection stays truthful (both `x.v` and `y.v` get `ConfigSet` events), but users may be
surprised. The real CLI should say "this value is shared with: y.v". Remote URL cycles use the literal URL
string, so `https://h/a.yaml` vs `https://h/./a.yaml` would evade detection (normalise URLs; also cap include
depth as a backstop).

## Q8. Surprises, hard parts, and what didn't work

Pinned in `tests/test_q8_hazards.py` (3 tests that describe **current** behaviour, named as hazards, not as
features) plus the fixes already noted above.

1. **(Resolved in Q9.) ruamel round-trip is "comment-preserving", not "byte-preserving".** A plain load->mutate->dump re-indents
   every block sequence in the file and loses spacing before trailing comments on changed lines. This was the
   biggest surprise: the headline library feature is weaker than its reputation. The splice strategy (parser
   event spans) is what actually makes the write-back diffs minimal. **This is the single most important
   finding for the real implementation.**
2. **ruamel attaches trailing blank lines + standalone comments to the preceding key**, so appends land under
   the next section's comment. Fixed with `_steal_trailing_comment` for mappings; **not** handled for
   sequences or for comments in other positions (e.g. a comment block at the very end of the file). Expect a
   long tail of these; build a golden-diff corpus.
3. **(Resolved in Q9: alias-path sets are now rejected.) YAML anchors/aliases break the "one merged path = one location" model.** Setting through an alias
   (`reviewer: *d`) silently mutates the anchor and every other alias. Merge keys (`<<: *d`) give no line
   provenance for merged-in keys; a set there adds a local override (arguably correct). Recommendation: forbid
   anchors/aliases in troupe config (includes already cover reuse), or reject sets whose target node is
   aliased. Not implemented in the spike.
4. **A URL-shaped `set` value silently becomes an include**, and a failed include fetch left an unloadable
   config on disk. Fixed (auto-`!literal`, explicit `include=True`, rollback on failed reload). The same issue
   applies to hand edits (a user pastes a URL into an untyped field); the watcher will try to fetch it. The
   real design should surface that as a load error event with provenance, not crash the daemon.
5. **Echo-suppression isn't needed, but concurrency control is.** Content-diff idempotence makes duplicate events
   impossible; it does nothing for lost updates (CLI write clobbering a concurrent hand edit) or for
   attribution (`cli` vs `file-watch`). Added compare-and-swap; attribution relies on an in-process lock.
6. **`jsonschema` silently ignores `format: uri`** without the `rfc3987` extra.
7. **Schema-guided detection is circular under `oneOf`/`anyOf`** (the branch depends on the value, the value
   depends on whether it's an include). Not solved; the spike walker only handles
   `properties`/`additionalProperties`/`items`.
8. **Things not attempted:** multi-document YAML files, flow-style (`{a: 1}`) collections under the splice path
   (they fall back to dump, untested), YAML 1.1 vs 1.2 booleans (`yes`/`on`), Windows paths, remote caching and
   pinning, cross-process locking between a CLI process and a daemon, performance on large configs (every
   reconcile is a full reload + full flat diff; trivially fast here).
9. **Minor:** first `detect_indent` heuristic only looks at the first block sequence; `ConfigRemoved` was needed
   in addition to `ConfigSet` (removal vs YAML null); list items as leaves means an insert at index 0 emits one
   event per shifted item.

## Q9. Surgical write-back across a nasty-YAML corpus — **YES** (62/64 corpus cases; the 2 others are files ruamel can't load at all)

Owner's bar: every write-back is surgical; only the changed value's bytes differ, never collateral reformatting.

Tests: `tests/test_q9_surgical.py` (golden corpus, text includes, verifier and strict-mode tests),
`tests/test_q9_sweep.py` (exhaustive sweep). Corpus: `tests/q9_corpus.py` (64 cases, exact bytes before and after).
Engine: `src/spike_config_roundtrip/surgical.py`. The table and diffs below are generated by
`uv run python tests/q9_report.py` from the real runs.

### Answer

**All write-backs in the corpus are surgical, and the dump path is no longer used.** Q2's splice only handled
scalar-to-scalar. It has been replaced by a text-level editor that covers every operation `set` can do.

**How it works:**
1. `YAML().compose(text)` gives every node (keys, values, block scalars, flow collections) exact
   `start_mark/end_mark` character offsets.
2. Each edit is a **text splice** at those offsets. New content is rendered in the **original style**: quote style;
   tag and anchor (kept verbatim as source text); block indicator, chomping and explicit indentation; flow vs
   block; the list's own dash indentation; the file's line endings; and a missing final newline.
3. For block scalars, a line diff of the old and new values reuses unchanged source lines **verbatim**. For
   folded (`>`) scalars this is done per paragraph, so unchanged paragraphs keep their original wrapping.
4. Appends and added keys are inserted right after the parent's last content line. Trailing blank lines and
   the next section's comment stay where they are. This replaces Q3's `_steal_trailing_comment` hack.
5. **Source-level verification** runs on every write. The target path must re-parse to exactly the new value,
   **and** every other node's (kind, tag, style, raw text, anchor, key list) must be identical before and after.
   `test_verifier_rejects_collateral_change` shows it catches a sibling value change, a sibling quote-style-only
   change, and a flow-to-block change.
6. **The dump fallback is now opt-in** (`Config(allow_dump=True)`). By default, a write that can't be spliced
   raises `ConfigError("cannot set … surgically …; Edit the file by hand.")` and leaves the file untouched
   (`test_non_surgical_write_is_refused_by_default`). Across the whole corpus and sweep, nothing reaches this
   path.

**Sweep (`test_q9_sweep.py`):** every scalar leaf **and** every collection is replaced, a key is added to every
mapping, and an item is appended to every list, in every corpus file. Each operation is tried with 11 awkward
values (`"changed"`, `42`, `"a: b"`, `"multi\nline\n"`, `""`, `"*x"`, mixed quotes, `True`, unicode, a dict and a
list). Result: **2,698 spliced and verified, 30 rejected by policy, 0 fell back to dump.** The sweep found 5
engine bugs, all fixed:
- a non-string value into a block scalar
- a block scalar at EOF without a final newline
- flow dicts rendered by ruamel as `[k: 1]` shorthand
- a block scalar replaced by a collection
- a block collection that is a list item

The 30 rejections are aliases, non-strings onto `!literal`, and anchored collections.

**Policy rejections** (clear errors, file untouched; these are refusals, not splice failures):
- Setting through an alias path (`reviewer: *d` then `set agents.reviewer.model`). Before Q9 this silently
  changed the anchor and every alias. Setting at the anchor itself is allowed, and the anchor text is kept.
- Replacing a whole collection that defines an anchor (`base: &b {...}`): its aliases would change or break.
- A non-string onto a `!literal`-tagged scalar.

**Text (markdown) includes:** still a whole-file replace, but the file *is* the value, so the diff is exactly
the changed lines (`test_text_include_change_is_minimal_diff`):
```diff
 ## Rules
 - tests first
-- small commits
+- tiny commits
```
A CRLF markdown file keeps CRLF when the caller passes LF content (the value is converted). A missing
final newline is preserved.

### What can't be made surgical, and what would fix it

| case | what happens | what would fix it |
|---|---|---|
| TAB before an end-of-line comment (`c: x\t# c`, valid YAML 1.2) | ruamel.yaml 0.19.1 **can't load the file** (ScannerError), so there's nothing to write back | A different parser for spans, e.g. a tree-sitter YAML grammar or `libyaml` events via PyYAML's C parser. Or treat it as a lint error with a clear message |
| multi-document file (`---` twice) | rejected at load with `ConfigError: multi-document YAML (---) is not supported in troupe config` | a product decision, not a technical limit: `compose_all` gives spans per document if ever needed |

### Judgement calls to confirm (all are surgical, but a reviewer might prefer otherwise)
- **A changed folded (`>`) paragraph is re-emitted on one line.** Unchanged paragraphs keep their wrapping. We
  could re-wrap to the old line width instead.
- **A folded value that gains a more-indented line becomes a `|` literal** (only that value). Folded can express
  it, but only with its more-indented rules, which aren't implemented.
- **A multi-line value set onto a plain scalar becomes `|` / `|-` / `|+`**, chosen by its trailing newlines.
  The EOL comment moves to the header line (`a: |  # note`).
- **Multi-line to single-line drops the block style. A single-line block value stays a block** (e.g. `>-`),
  because that was the author's choice.
- **Appends and added keys go directly after the last item, above any trailing comment or EOF comment.**
- **Added keys and items use plain style**, not inferred from siblings: `c: 6` in a JSON-style file. Appended
  list items *do* copy the siblings' quote style when all siblings share one.
- An element appended to a multi-line flow mapping joins its last line (`b: 3, c: 4`).
- EOL comments shift by the value's length change. The whitespace bytes between value and comment are
  untouched (we don't re-align comment columns).
- The projection value of a CRLF text include contains `\r\n`. The real design should decide whether the
  projection normalises line endings.

### Not covered
- Keys containing `.` can't be addressed by dotted paths. That's a CLI path-syntax issue, not a splice issue.
- Complex (non-scalar) mapping keys.
- Flow-style block-scalar indicators inside flow collections (invalid YAML anyway).
- Windows-only behaviours.
- Very large files: each `set` composes the file about three times and verifies twice. That's trivial at
  config sizes.

<!-- q9:begin (generated by tests/q9_report.py) -->

| group | case | strategy | surgical | changed lines | note |
|---|---|---|---|---|---|
| block scalars | literal \| change one line | splice | yes | 2 |  |
| block scalars | literal \|- (strip) change one line | splice | yes | 2 |  |
| block scalars | literal \|+ (keep) with trailing blank lines | splice | yes | 2 |  |
| block scalars | folded > change second paragraph, first stays wrapped | splice | yes | 3 | changed paragraph is re-emitted on one line; unchanged paragraphs keep their wrapping |
| block scalars | folded >- change | splice | yes | 3 | a changed folded paragraph loses its original line wrapping |
| block scalars | explicit indentation indicator \|2 | splice | yes | 2 |  |
| block scalars | clip block followed by blank lines | splice | yes | 2 |  |
| block scalars | block followed by a comment | splice | yes | 2 |  |
| block scalars | block header with comment | splice | yes | 2 |  |
| block scalars | plain -> multi-line (EOL comment moves to header) | splice | yes | 4 |  |
| block scalars | plain -> multi-line without final newline uses \|- | splice | yes | 4 |  |
| block scalars | multi-line -> plain | splice | yes | 4 |  |
| block scalars | block scalar as sequence item | splice | yes | 2 |  |
| block scalars | block scalar at EOF without trailing newline | splice | yes | 2 | last line of file has no line break |
| block scalars | chomping change: clip value gains trailing newlines -> \|+ | splice | yes | 3 | header indicator changes because the value's trailing newlines changed; that is part of the value |
| comments | comments above keys | splice | yes | 2 |  |
| comments | EOL comments with varied spacing (1 space, 6 spaces) | splice | yes | 4 | EOL comment column shifts by the value's length delta; spacing bytes are untouched |
| comments | TAB before an EOL comment (valid YAML 1.2) | error | n/a (rejected) | 0 | ruamel.yaml 0.19.1 cannot LOAD this (ScannerError), so no write-back is possible ScannerError: while scanning for the next token |
| comments | comments between list items; change + append | splice | yes | 3 | append goes directly after the last item, above a trailing comment |
| comments | comments inside nested mappings; change + add | splice | yes | 3 |  |
| comments | comment at end of file; change + add root key | splice | yes | 3 | new key goes above the EOF comment (conservative: the comment may describe b) |
| comments | file with no trailing newline; change + add | splice | yes | 3 |  |
| comments | commented-out key after mapping; add | splice | yes | 1 |  |
| comments | explicit document start marker and header comment | splice | yes | 3 |  |
| flow collections | flow mapping: change one, add one | splice | yes | 2 |  |
| flow collections | flow sequence: change one, append one | splice | yes | 2 |  |
| flow collections | nested flow; quote style kept; append inside | splice | yes | 2 |  |
| flow collections | flow value that needs quoting | splice | yes | 2 |  |
| flow collections | empty flow collections | splice | yes | 4 |  |
| flow collections | multi-line flow mapping | splice | yes | 2 | appended element joins the last line rather than getting its own line |
| lists | change an item in a block list | splice | yes | 2 |  |
| lists | append to a block list | splice | yes | 1 |  |
| lists | two indentation styles in one file | splice | yes | 4 |  |
| lists | list of mappings: append a mapping, add key to an item | splice | yes | 3 |  |
| lists | append to a list whose last item is a nested list | splice | yes | 1 |  |
| quoting | plain stays plain | splice | yes | 2 |  |
| quoting | single-quoted with apostrophe in new value | splice | yes | 2 |  |
| quoting | double-quoted with escapes | splice | yes | 2 | ruamel emits the tab as \t; é stays literal (original used a \u escape for it) |
| quoting | plain -> needs quoting: contains ': ' | splice | yes | 2 |  |
| quoting | plain -> needs quoting: contains ' #' | splice | yes | 2 |  |
| quoting | plain -> needs quoting: starts with '*' | splice | yes | 2 |  |
| quoting | plain -> needs quoting: string that looks like a bool/int | splice | yes | 4 |  |
| quoting | quoted string -> int keeps it an int (type change is the edit) | splice | yes | 2 |  |
| quoting | !literal tag kept on set | splice | yes | 2 |  |
| layout | blank lines between sections and key order preserved; add in middle section | splice | yes | 4 |  |
| layout | deep new path creates intermediate mapping in place | splice | yes | 3 |  |
| layout | scalar -> mapping keeps EOL comment on the key line | splice | yes | 4 |  |
| encoding | unicode values and comments | splice | yes | 4 |  |
| encoding | CRLF line endings: change, append, block scalar, add key | splice | yes | 6 |  |
| encoding | tabs inside comment text | splice | yes | 4 |  |
| multi-document | multi-document file (---) | error | n/a (rejected) | 0 | rejected at LOAD time with a clear ConfigError; file untouched ConfigError: /private/var/folders/nn/d2s0hr6x7p73b0gr6gsqp9yw0000gp/T/tmphzigfqtp/c.yml: multi-document YAML (---) is not supported in troupe config |
| structural | anchored scalar: anchor kept, alias follows | splice | yes | 2 | setting the ANCHOR is allowed (it's the definition); setting via the alias path is rejected |
| structural | tag + anchor on one scalar | splice | yes | 2 |  |
| structural | value inside a merge-key base | splice | yes | 2 |  |
| structural | mapping -> scalar (key-line comment kept, next-section comment kept) | splice | yes | 4 |  |
| structural | mapping -> mapping | splice | yes | 2 |  |
| structural | list -> list keeps the list's dash indentation | splice | yes | 3 |  |
| structural | folded value gains a more-indented line -> that value becomes literal | splice | yes | 3 | style change confined to the changed value; folded can't express it without the more-indented rules |
| structural | null (`a:`) -> scalar | splice | yes | 2 |  |
| structural | null with comment -> multi-line | splice | yes | 4 |  |
| structural | null gains a child key | splice | yes | 1 |  |
| structural | add key that needs quoting / has a space | splice | yes | 2 |  |
| structural | JSON-style file (it's YAML too): change + add | splice | yes | 2 | added key is plain-style, not JSON-quoted (style of NEW keys isn't inferred from siblings) |
| structural | aligned values | splice | yes | 2 |  |

**62 of 64 cases surgical.**

<details><summary>Actual diffs for every case (generated)</summary>

**block scalars / literal | change one line** (`set prompt`)

```diff
--- before
+++ after
@@ -3,3 +3,3 @@
   line one
-  line two
+  line 2
   line three
```

**block scalars / literal |- (strip) change one line** (`set script`)

```diff
--- before
+++ after
@@ -2,3 +2,3 @@
   echo a
-  echo b
+  echo B
 next: 1
```

**block scalars / literal |+ (keep) with trailing blank lines** (`set keep`)

```diff
--- before
+++ after
@@ -2,3 +2,3 @@
   x
-  y
+  Y
 
```

**block scalars / folded > change second paragraph, first stays wrapped** (`set desc`)

```diff
--- before
+++ after
@@ -4,4 +4,3 @@
 
-  Second para
-  here.
+  Second paragraph changed.
 next: 1
```

**block scalars / folded >- change** (`set summary`)

```diff
--- before
+++ after
@@ -1,4 +1,3 @@
 summary: >-
-  one two
-  three
+  one two four
 next: 1
```

**block scalars / explicit indentation indicator |2** (`set code`)

```diff
--- before
+++ after
@@ -2,3 +2,3 @@
     indented first
-  normal
+  changed
 next: 1
```

**block scalars / clip block followed by blank lines** (`set a`)

```diff
--- before
+++ after
@@ -1,3 +1,3 @@
 a: |
-  x
+  y
 
```

**block scalars / block followed by a comment** (`set a`)

```diff
--- before
+++ after
@@ -2,3 +2,3 @@
   x
-  y
+  z
 # about b
```

**block scalars / block header with comment** (`set a`)

```diff
--- before
+++ after
@@ -1,3 +1,3 @@
 a: |  # keep me
-  x
+  y
 b: 1
```

**block scalars / plain -> multi-line (EOL comment moves to header)** (`set a`)

```diff
--- before
+++ after
@@ -1,2 +1,4 @@
-a: short  # note
+a: |  # note
+  line1
+  line2
 b: 1
```

**block scalars / plain -> multi-line without final newline uses |-** (`set a`)

```diff
--- before
+++ after
@@ -1,2 +1,4 @@
-a: short
+a: |-
+  line1
+  line2
 b: 1
```

**block scalars / multi-line -> plain** (`set a`)

```diff
--- before
+++ after
@@ -1,4 +1,2 @@
-a: |
-  x
-  y
+a: single
 
```

**block scalars / block scalar as sequence item** (`set steps.0`)

```diff
--- before
+++ after
@@ -3,3 +3,3 @@
     echo a
-    echo b
+    echo c
   - done
```

**block scalars / block scalar at EOF without trailing newline** (`set b`)

```diff
--- before
+++ after
@@ -3,2 +3,2 @@
   x
-  y   [no EOL at end of file]
+  z   [no EOL at end of file]
```

**block scalars / chomping change: clip value gains trailing newlines -> |+** (`set a`)

```diff
--- before
+++ after
@@ -1,3 +1,4 @@
-a: |
+a: |+
   x
+
 b: 1
```

**comments / comments above keys** (`set b`)

```diff
--- before
+++ after
@@ -4,2 +4,2 @@
 # about b
-b: 2
+b: 3
```

**comments / EOL comments with varied spacing (1 space, 6 spaces)** (`set b, set c`)

```diff
--- before
+++ after
@@ -1,3 +1,3 @@
 a: 1 # one space
-b: 22      # six
-c: x  # two
+b: 7      # six
+c: yy  # two
```

**comments / TAB before an EOL comment (valid YAML 1.2)** (`set c`)

```diff
(no change)
```

**comments / comments between list items; change + append** (`set l.1, set l.2`)

```diff
--- before
+++ after
@@ -3,3 +3,4 @@
   # between
-  - b
+  - c
+  - d
   # after last
```

**comments / comments inside nested mappings; change + add** (`set outer.inner.k, set outer.inner.new`)

```diff
--- before
+++ after
@@ -4,3 +4,4 @@
     # c2
-    k: v  # c3
+    k: w  # c3
+    new: 1
     # c4
```

**comments / comment at end of file; change + add root key** (`set b, set c`)

```diff
--- before
+++ after
@@ -1,3 +1,4 @@
 a: 1
-b: 2
+b: 3
+c: 4
 # trailing comment
```

**comments / file with no trailing newline; change + add** (`set b, set c`)

```diff
--- before
+++ after
@@ -1,2 +1,3 @@
 a: 1
-b: 2   [no EOL at end of file]
+b: 3
+c: 4   [no EOL at end of file]
```

**comments / commented-out key after mapping; add** (`set a.z`)

```diff
--- before
+++ after
@@ -2,2 +2,3 @@
   x: 1
+  z: 3
 #  y: 2
```

**comments / explicit document start marker and header comment** (`set a, set b`)

```diff
--- before
+++ after
@@ -2,2 +2,3 @@
 # config
-a: 1
+a: 2
+b: 3
```

**flow collections / flow mapping: change one, add one** (`set m.b, set m.c`)

```diff
--- before
+++ after
@@ -1 +1 @@
-m: {a: 1, b: 2}  # flow
+m: {a: 1, b: 3, c: 4}  # flow
```

**flow collections / flow sequence: change one, append one** (`set l.0, set l.2`)

```diff
--- before
+++ after
@@ -1 +1 @@
-l: [x, y]
+l: [z, y, w]
```

**flow collections / nested flow; quote style kept; append inside** (`set m.s, set m.l.2`)

```diff
--- before
+++ after
@@ -1 +1 @@
-m: {l: [1, 2], s: 'q'}
+m: {l: [1, 2, 3], s: 'r'}
```

**flow collections / flow value that needs quoting** (`set l.0`)

```diff
--- before
+++ after
@@ -1 +1 @@
-l: [x, y]
+l: ['a, b', y]
```

**flow collections / empty flow collections** (`set m.a, set l.0`)

```diff
--- before
+++ after
@@ -1,2 +1,2 @@
-m: {}
-l: []
+m: {a: 1}
+l: [x]
```

**flow collections / multi-line flow mapping** (`set m.b, set m.c`)

```diff
--- before
+++ after
@@ -2,3 +2,3 @@
   a: 1,
-  b: 2
+  b: 3, c: 4
 }
```

**lists / change an item in a block list** (`set tools.1`)

```diff
--- before
+++ after
@@ -2,3 +2,3 @@
   - bash
-  - edit  # the editor
+  - write  # the editor
   - web
```

**lists / append to a block list** (`set tools.2`)

```diff
--- before
+++ after
@@ -3,2 +3,3 @@
   - edit
+  - web
 next: 1
```

**lists / two indentation styles in one file** (`set a.2, set b.2, set b.0`)

```diff
--- before
+++ after
@@ -3,4 +3,6 @@
 - y
+- z
 b:
-    - p
+    - P
     - q
+    - r
```

**lists / list of mappings: append a mapping, add key to an item** (`set agents.1, set agents.0.temp`)

```diff
--- before
+++ after
@@ -3 +3,4 @@
     model: x
+    temp: 0.1
+  - name: b
+    model: y
```

**lists / append to a list whose last item is a nested list** (`set m.2`)

```diff
--- before
+++ after
@@ -4 +4,2 @@
   - x
+  - y
```

**quoting / plain stays plain** (`set k`)

```diff
--- before
+++ after
@@ -1 +1 @@
-k: v  # c
+k: w  # c
```

**quoting / single-quoted with apostrophe in new value** (`set k`)

```diff
--- before
+++ after
@@ -1 +1 @@
-k: 'v'
+k: 'it''s'
```

**quoting / double-quoted with escapes** (`set k`)

```diff
--- before
+++ after
@@ -1 +1 @@
-k: "tab\there \u00e9"
+k: "new\tval é"
```

**quoting / plain -> needs quoting: contains ': '** (`set k`)

```diff
--- before
+++ after
@@ -1 +1 @@
-k: v
+k: 'a: b'
```

**quoting / plain -> needs quoting: contains ' #'** (`set k`)

```diff
--- before
+++ after
@@ -1 +1 @@
-k: v
+k: 'x # y'
```

**quoting / plain -> needs quoting: starts with '*'** (`set k`)

```diff
--- before
+++ after
@@ -1 +1 @@
-k: v
+k: '*star'
```

**quoting / plain -> needs quoting: string that looks like a bool/int** (`set a, set b`)

```diff
--- before
+++ after
@@ -1,2 +1,2 @@
-a: v
-b: v
+a: 'true'
+b: '123'
```

**quoting / quoted string -> int keeps it an int (type change is the edit)** (`set k`)

```diff
--- before
+++ after
@@ -1 +1 @@
-k: "8000"
+k: 9000
```

**quoting / !literal tag kept on set** (`set u`)

```diff
--- before
+++ after
@@ -1 +1 @@
-u: !literal "https://x/a.yaml"  # c
+u: !literal "https://x/b.yaml"  # c
```

**layout / blank lines between sections and key order preserved; add in middle section** (`set b.c, set b.x, set f`)

```diff
--- before
+++ after
@@ -4,5 +4,6 @@
 b:
-  c: 2
+  c: 5
 
   d: 3
+  x: 6
 
@@ -10 +11,2 @@
 e: 4
+f: 7
```

**layout / deep new path creates intermediate mapping in place** (`set a.c.d.e`)

```diff
--- before
+++ after
@@ -2,2 +2,5 @@
   b: 1
+  c:
+    d:
+      e: 1
 
```

**layout / scalar -> mapping keeps EOL comment on the key line** (`set model`)

```diff
--- before
+++ after
@@ -1,2 +1,4 @@
-model: 'opus'  # default
+model:  # default
+  name: opus
+  temperature: 0.2
 next: 1
```

**encoding / unicode values and comments** (`set name, set emoji`)

```diff
--- before
+++ after
@@ -1,2 +1,2 @@
-name: café ✓  # ünïcode
-emoji: 🎭
+name: naïve ✓✓  # ünïcode
+emoji: 🎭🎭
```

**encoding / CRLF line endings: change, append, block scalar, add key** (`set a, set b.2, set c, set d`)

```diff
--- before
+++ after
@@ -1,2 +1,2 @@
-a: 1\r
+a: 2\r
 b:\r
@@ -4,4 +4,6 @@
   - y\r
+  - z\r
 c: |\r
   one\r
-  two\r
+  three\r
+d: 4\r
```

**encoding / tabs inside comment text** (`set a, set b`)

```diff
--- before
+++ after
@@ -1,3 +1,3 @@
-a: 1  # tab\there
+a: 5  # tab\there
 #\tindented\tcomment
-b: 2
+b: 6
```

**multi-document / multi-document file (---)** (`set a`)

```diff
(no change)
```

**structural / anchored scalar: anchor kept, alias follows** (`set a`)

```diff
--- before
+++ after
@@ -1,2 +1,2 @@
-a: &x 1  # anchor
+a: &x 2  # anchor
 b: *x
```

**structural / tag + anchor on one scalar** (`set a`)

```diff
--- before
+++ after
@@ -1 +1 @@
-a: !literal &q "https://x"
+a: !literal &q "https://y"
```

**structural / value inside a merge-key base** (`set base.x`)

```diff
--- before
+++ after
@@ -1,3 +1,3 @@
 base: &b
-  x: 1
+  x: 5
 use:
```

**structural / mapping -> scalar (key-line comment kept, next-section comment kept)** (`set m`)

```diff
--- before
+++ after
@@ -1,4 +1,2 @@
-m:  # c
-  x: 1
-  y: 2
+m: flat  # c
 # next
```

**structural / mapping -> mapping** (`set m`)

```diff
--- before
+++ after
@@ -1,3 +1,3 @@
 m:  # c
-  x: 1
+  z: 9
 n: 1
```

**structural / list -> list keeps the list's dash indentation** (`set l`)

```diff
--- before
+++ after
@@ -1,3 +1,4 @@
 l:
-    - 1
+    - 7
+    - 8
 n: 1
```

**structural / folded value gains a more-indented line -> that value becomes literal** (`set d`)

```diff
--- before
+++ after
@@ -1,3 +1,4 @@
-d: >
+d: |
   para one
+    indented
 n: 1
```

**structural / null (`a:`) -> scalar** (`set a`)

```diff
--- before
+++ after
@@ -1,2 +1,2 @@
-a:
+a: 5
 b: 1
```

**structural / null with comment -> multi-line** (`set a`)

```diff
--- before
+++ after
@@ -1,2 +1,4 @@
-a:  # c
+a: |  # c
+  x
+  y
 b: 1
```

**structural / null gains a child key** (`set a.c`)

```diff
--- before
+++ after
@@ -1,2 +1,3 @@
 a:
+  c: 5
 b: 1
```

**structural / add key that needs quoting / has a space** (`set my key, set x: y`)

```diff
--- before
+++ after
@@ -1 +1,3 @@
 a: 1
+my key: 1
+'x: y': 2
```

**structural / JSON-style file (it's YAML too): change + add** (`set a, set c`)

```diff
--- before
+++ after
@@ -1 +1 @@
-{"a": 1, "b": [1, 2]}
+{"a": 5, "b": [1, 2], c: 6}
```

**structural / aligned values** (`set a`)

```diff
--- before
+++ after
@@ -1,2 +1,2 @@
-a:     1
+a:     10
 bb:    2
```

</details>

<!-- q9:end -->

## Recommendation

**Library choices**
- **ruamel.yaml, but as a *parser*, not a *writer*.** Use `compose()` for exact node spans and `load()` (round-trip,
  `preserve_quotes=True`) for values and provenance. Use its emitter only to render a single new scalar or a
  small new block in a chosen style. Never re-serialise a user's file; that's what the Q9 surgical editor
  replaces. The one parser gap (a TAB before a comment) is a load failure, not a write risk.
- **watchfiles** for the watcher: worked first time on macOS (FSEvents), sub-second latency with
  `debounce=100`. Prove readiness before relying on it.
- **eventsourcing** (9.5.x): little ceremony needed for `ConfigSet`/`ConfigRemoved` on a per-project aggregate;
  the POPO store is fine for tests.
- **jsonschema** with the `format` extra, plus a proper subschema-at-path resolver (`$ref`/`$defs`) written for
  include detection.

**Write-back rule set**
1. An existing value is written to `provenance[path].source` at `inner_path`. For a text include, the whole
   target file is the value (with its line endings kept).
2. A new key goes to the file that owns the deepest existing ancestor. Missing intermediate mappings are
   created there, and a null `a:` becomes a mapping in place.
3. **Every YAML write is a surgical text splice** (Q9): render in the original style, reuse unchanged
   block-scalar lines verbatim, insert new keys and items after the parent's last content line, then run
   source-level verification. **If a write can't be spliced and verified, refuse it; never re-serialise the
   file.**
4. Remote targets (a value, a text include, or a new key under a remote mapping) are rejected before writing
   or emitting, naming the local include site.
5. URL-shaped strings are written `!literal` unless the schema says `format: uri` or the user explicitly asks
   for an include (`--include`).
6. Compare-and-swap on the file text; atomic write (temp file + `os.replace`) with no newline translation;
   roll back if the result doesn't load.
7. Refuse sets through an alias, wholesale replacement of an anchored collection, non-strings onto `!literal`,
   and replacing a whole include root.
8. Every entry into the projection goes through one `reconcile(source)` = full reload + flat diff. This is the
   loop-safety mechanism; no echo flags.

**Risks for the real implementation**
- Surgical editing (Q9) is the most intricate code in the design, about 500 lines in the spike. Keep the golden
  corpus and the exhaustive sweep as permanent tests, and add every bug report to the corpus. Source-level
  verification plus refuse-by-default means a bug shows up as a refused `set`, never as a mangled file.
  That's the right failure mode.
- Cross-process concurrency: CLI and daemon both writing. Route `troupe config set` through the daemon (single
  writer, owns lock and watcher) or add file locks.
- Remote includes changing silently: pin content hashes in the projection; make refresh an explicit command.
- Schema composition (`oneOf`, `$ref`) vs include detection: constrain schema authoring, or require `!literal`
  in ambiguous places.
- Anchors/aliases and diamond includes: one location, many paths. Decide policy early (recommend: forbid
  aliases; warn on shared includes).
- Projection granularity (list items as leaves) affects event volume and will be hard to change once events
  are persisted. Decide before the first real event store.
