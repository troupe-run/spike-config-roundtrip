"""Spike: include-aware, provenance-tracking, write-back-capable troupe config loader.

Throwaway code. Deliberately simple.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq, TaggedScalar
from ruamel.yaml.scalarstring import ScalarString

INCLUDE_RE = re.compile(r"^(file:|https?://)\S+$")
YAML_SUFFIXES = {".yml", ".yaml"}
LITERAL_TAG = "!literal"

Path_ = tuple  # tuple[str | int, ...]


class ConfigError(Exception):
    pass


class IncludeCycleError(ConfigError):
    pass


class ReadOnlyIncludeError(ConfigError):
    pass


@dataclass(frozen=True)
class Provenance:
    source: str  # absolute file path or URL
    inner_path: tuple  # path of this node inside `source`
    kind: str  # "yaml" | "text"
    line: int | None = None  # 1-based line in source, when known
    via: str | None = None  # the include reference that pulled this source in

    @property
    def remote(self) -> bool:
        return self.source.startswith(("http://", "https://"))


def make_yaml() -> YAML:
    y = YAML()  # round-trip
    y.preserve_quotes = True
    y.width = 4096  # don't re-wrap long lines we didn't touch
    return y


def dotted(path: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in path.split(".")) if path else ()


def fmt_path(path: tuple) -> str:
    return ".".join(str(p) for p in path)


# --------------------------------------------------------------------------- schema


def subschema(schema: dict | None, key) -> dict | None:
    """Very small JSON-Schema walker: properties / additionalProperties / items."""
    if not schema:
        return None
    if isinstance(key, int):
        items = schema.get("items")
        return items if isinstance(items, dict) else None
    props = schema.get("properties", {})
    if key in props:
        return props[key]
    ap = schema.get("additionalProperties")
    return ap if isinstance(ap, dict) else None


def schema_says_literal_uri(schema: dict | None) -> bool:
    return bool(schema) and schema.get("format") in ("uri", "uri-reference", "iri")


# --------------------------------------------------------------------------- loader


class Config:
    """Loads a root troupe.yml, resolving includes, recording provenance."""

    def __init__(self, root: str | os.PathLike, schema: dict | None = None):
        self.root = str(Path(root).resolve())
        self.schema = schema
        self.reload()

    # ---- reading -------------------------------------------------------------

    def reload(self) -> None:
        self.docs: dict[str, Any] = {}  # source -> ruamel round-trip doc (yaml sources)
        self.texts: dict[str, str] = {}  # source -> raw text (all sources)
        self.provenance: dict[tuple, Provenance] = {}
        self.include_sites: dict[tuple, tuple[str, tuple]] = {}  # merged path -> (file, inner path) of the URL scalar
        self.merged = self._load_source(self.root, (), self.schema, stack=(), via=None)

    def _read(self, source: str) -> str:
        if source.startswith(("http://", "https://")):
            with urllib.request.urlopen(source, timeout=5) as r:
                return r.read().decode()
        return Path(source).read_text()

    def _resolve_ref(self, ref: str, including: str) -> str:
        if ref.startswith(("http://", "https://")):
            return ref
        rel = ref[len("file:"):]
        if including.startswith(("http://", "https://")):
            # relative file: include inside a remote file -> resolve against the URL
            return urllib.parse.urljoin(including, rel)
        return str((Path(including).parent / rel).resolve())

    def _load_source(self, source, merged_path, schema, stack, via):
        if source in stack:
            chain = " -> ".join([*stack, source])
            raise IncludeCycleError(f"include cycle detected: {chain}")
        stack = (*stack, source)
        text = self._read(source)
        self.texts[source] = text
        suffix = Path(urllib.parse.urlparse(source).path).suffix.lower()
        if suffix in YAML_SUFFIXES:
            doc = make_yaml().load(text)
            self.docs[source] = doc
            return self._walk(doc, source, (), merged_path, schema, stack, via, line=1)
        # text include: whole file is one string scalar
        self.provenance[merged_path] = Provenance(source, (), "text", 1, via)
        return text

    def _walk(self, node, source, inner, merged_path, schema, stack, via, line):
        self.provenance[merged_path] = Provenance(source, inner, "yaml", line, via)
        if isinstance(node, CommentedMap):
            out = {}
            for k, v in node.items():
                ln = node.lc.key(k)[0] + 1 if k in node.lc.data else None
                out[k] = self._child(v, source, (*inner, k), (*merged_path, k), subschema(schema, k), stack, ln)
            return out
        if isinstance(node, CommentedSeq):
            return [
                self._child(v, source, (*inner, i), (*merged_path, i), subschema(schema, i), stack,
                            node.lc.item(i)[0] + 1 if i in node.lc.data else None)
                for i, v in enumerate(node)
            ]
        return _plain(node)

    def _child(self, v, source, inner, merged_path, schema, stack, line):
        if isinstance(v, TaggedScalar) and v.tag.value == LITERAL_TAG:
            self.provenance[merged_path] = Provenance(source, inner, "yaml", line)
            return str(v.value)
        if isinstance(v, str) and INCLUDE_RE.match(v) and not schema_says_literal_uri(schema):
            target = self._resolve_ref(str(v), source)
            self.include_sites[merged_path] = (source, inner)
            return self._load_source(target, merged_path, schema, stack, via=str(v))
        return self._walk(v, source, inner, merged_path, schema, stack, None, line)

    # ---- querying ------------------------------------------------------------

    def get(self, path: str):
        node = self.merged
        for p in dotted(path):
            node = node[p]
        return node

    def where(self, path: str) -> Provenance:
        return self.provenance[dotted(path)]

    # ---- writing -------------------------------------------------------------

    def plan_set(self, path: str, value) -> tuple[Provenance, tuple, str]:
        """Decide where a set lands without writing. Returns (owner provenance, key path
        relative to owner node, mode). Raises ReadOnlyIncludeError for remote targets."""
        p = dotted(path)
        if p in self.provenance:
            prov = self.provenance[p]
            mode = "replace-text" if prov.kind == "text" else "replace"
            rel = ()
        else:
            # new key: owner is the deepest existing ancestor (the mapping that will hold it)
            i = len(p) - 1
            while p[:i] not in self.provenance:
                i -= 1
            prov = self.provenance[p[:i]]
            rel = p[i:]
            if prov.kind == "text":
                raise ConfigError(f"cannot add key {path!r}: parent {fmt_path(p[:i])!r} is a text include ({prov.source})")
            mode = "add"
        if prov.remote:
            raise ReadOnlyIncludeError(
                f"cannot set {path!r}: value comes from remote include {prov.source} "
                f"(included via {self._site_desc(p)}), which is read-only. "
                f"Override it locally by replacing the include, or edit the remote source."
            )
        return prov, rel, mode

    def _site_desc(self, p):
        for i in range(len(p), -1, -1):
            if p[:i] in self.include_sites:
                f, inner = self.include_sites[p[:i]]
                return f"{fmt_path(p[:i])!r} in {f}"
        return "?"

    def set(self, path: str, value) -> str:
        """Write value into the file that owns it. Returns the file written."""
        prov, rel, mode = self.plan_set(path, value)
        if mode == "replace-text":
            if not isinstance(value, str):
                raise ConfigError(f"{path!r} is a text include ({prov.source}); value must be a string")
            _atomic_write(prov.source, value)
        else:
            doc = self.docs[prov.source]
            parent_inner = prov.inner_path[:-1] if mode == "replace" else prov.inner_path
            keys = (prov.inner_path[-1],) if mode == "replace" else rel
            if not parent_inner and mode == "replace" and not keys:
                raise ConfigError("cannot replace a whole file root")
            node = doc
            for k in parent_inner:
                node = node[k]
            for k in keys[:-1]:  # create intermediate mappings for deep new keys
                node = node.setdefault(k, CommentedMap())
            last = keys[-1]
            old = node[last] if (isinstance(node, dict) and last in node) or (isinstance(node, list) and isinstance(last, int) and last < len(node)) else None
            styled = _restyle(old, value)
            text = self.texts[prov.source]
            spliced = None
            if mode == "replace" and not isinstance(old, (dict, list)) and not isinstance(value, (dict, list)):
                spliced = splice_scalar(text, prov.inner_path, styled)
            if spliced is not None:
                self.last_write_strategy = "splice"
                _atomic_write(prov.source, spliced)
            else:
                self.last_write_strategy = "dump"
                node[last] = styled
                _atomic_write(prov.source, dump_like(doc, text))
        self.reload()
        return prov.source

    # ---- flat leaf view (for event-sourced diffing) ------------------------------

    def flat(self) -> dict[str, Any]:
        out: dict[str, Any] = {}

        def rec(n, p):
            if isinstance(n, dict):
                for k, v in n.items():
                    rec(v, (*p, k))
            elif isinstance(n, list):
                for i, v in enumerate(n):
                    rec(v, (*p, i))
            else:
                out[fmt_path(p)] = n

        rec(self.merged, ())
        return out

    def local_sources(self) -> set[str]:
        return {s for s in self.texts if not s.startswith(("http://", "https://"))}


def _plain(v):
    if isinstance(v, ScalarString):
        return str(v)
    if isinstance(v, TaggedScalar):
        return str(v.value)
    return v


def _restyle(old, new):
    """Keep the old scalar's quoting style when replacing a string with a string."""
    if isinstance(old, ScalarString) and isinstance(new, str):
        return type(old)(new)
    if isinstance(old, TaggedScalar) and isinstance(new, str):
        return TaggedScalar(new, style=old.style, tag=old.tag.value)
    return new


def scalar_span(text: str, inner_path: tuple):
    """Exact [start, end) character span of the scalar at inner_path, via parser events."""
    stack: list[list] = []  # frames: ["map", pending_key|None, expecting_key] / ["seq", next_index]
    path: list = []

    def current_child_path():
        f = stack[-1]
        return (*path, f[1]) if f[0] == "map" else (*path, f[1])

    for e in YAML().parse(text):
        name = type(e).__name__
        if name in ("MappingStartEvent", "SequenceStartEvent", "ScalarEvent", "AliasEvent"):
            is_key = bool(stack) and stack[-1][0] == "map" and stack[-1][2]
            if is_key:
                stack[-1][1] = e.value
                stack[-1][2] = False
                continue
            child = current_child_path() if stack else ()
            if name == "ScalarEvent" or name == "AliasEvent":
                if tuple(child) == tuple(inner_path):
                    return e.start_mark.index, e.end_mark.index
                _advance(stack)
            else:
                if stack:
                    path.append(stack[-1][1])
                stack.append(["map", None, True] if name == "MappingStartEvent" else ["seq", 0])
        elif name in ("MappingEndEvent", "SequenceEndEvent"):
            stack.pop()
            if stack:
                path.pop()
                _advance(stack)
    return None


def _advance(stack):
    f = stack[-1]
    if f[0] == "map":
        f[2] = True
    else:
        f[1] += 1


def render_scalar(value) -> str | None:
    buf = io.StringIO()
    make_yaml().dump({"k": value}, buf)
    out = buf.getvalue()
    assert out.startswith("k: ")
    out = out[3:].rstrip("\n")
    if out.endswith("\n...") or "\n" in out:
        return None  # multi-line rendering: let the dump path handle it
    return out


def splice_scalar(text: str, inner_path: tuple, value) -> str | None:
    """Replace one scalar's characters in-place. Everything else stays byte-identical.
    Returns None if not applicable (then caller falls back to a full ruamel dump)."""
    span = scalar_span(text, inner_path)
    rendered = render_scalar(value)
    if span is None or rendered is None:
        return None
    a, b = span
    if "\n" in text[a:b]:
        return None  # old value was a multi-line/block scalar
    new_text = text[:a] + rendered + text[b:]
    # safety net: re-parse and confirm we changed exactly what we meant to
    check = make_yaml().load(new_text)
    for k in inner_path:
        check = check[k]
    return new_text if _plain(check) == _plain(value) else None


def detect_indent(text: str) -> tuple[int, int, int]:
    """(mapping, sequence, offset) guessed from the first block sequence in the file."""
    lines = text.splitlines()
    for i, line in enumerate(lines[1:], 1):
        m = re.match(r"^(\s*)- ", line)
        prev = lines[i - 1]
        if m and re.match(r"^(\s*)[^\s#-][^:]*:\s*(#.*)?$", prev):
            off = len(m.group(1)) - (len(prev) - len(prev.lstrip()))
            return (2, off + 2, off)
    return (2, 2, 0)


def dump_like(doc, original_text: str) -> str:
    y = make_yaml()
    m, s, o = detect_indent(original_text)
    y.indent(mapping=m, sequence=s, offset=o)
    buf = io.StringIO()
    y.dump(doc, buf)
    return buf.getvalue()


def _atomic_write(path: str, text: str) -> None:
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".troupe-tmp-")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


import urllib.parse  # noqa: E402  (kept at bottom for spike brevity)
