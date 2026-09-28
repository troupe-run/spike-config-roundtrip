"""Surgical YAML editing: change one value by splicing the source text, never by re-serialising.

Mechanism (all operations):
1. `YAML().compose(text)` gives a node tree whose start/end marks are exact character offsets.
2. The edit is a text splice at those offsets. New content is rendered in the ORIGINAL style
   (quote style, tag, block indicator/chomping/indent, flow vs block, indentation, line endings).
   For block scalars, unchanged lines are reused verbatim via a line diff of old vs new value.
3. Verification: the edited text is re-parsed and its ENTIRE data (incl. tags) must equal the old
   data with exactly the one intended change applied. Anything else raises SpliceError and the caller
   may fall back to a full ruamel dump.
"""

from __future__ import annotations

import copy
import difflib
import io
import re
from dataclasses import dataclass

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq, TaggedScalar
from ruamel.yaml.nodes import MappingNode, ScalarNode, SequenceNode
from ruamel.yaml.scalarstring import (
    DoubleQuotedScalarString,
    ScalarString,
    SingleQuotedScalarString,
)

DEFAULT_TAG_PREFIX = "tag:yaml.org,2002:"


class SpliceError(Exception):
    pass


class RejectedSet(Exception):
    """The set is refused on policy grounds (not a splice limitation)."""


class AliasTargetError(RejectedSet):
    pass


@dataclass
class Ref:
    node: object
    parent: object | None
    key: object | None  # key node when parent is a mapping
    aliased: bool
    flow: bool  # node sits inside a flow collection


# --------------------------------------------------------------------------- text helpers


def newline_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def line_start(text: str, i: int) -> int:
    return text.rfind("\n", 0, i) + 1


def line_end(text: str, i: int) -> int:
    """Index of the line break ending the line containing i (or len(text))."""
    j = text.find("\n", i)
    if j == -1:
        return len(text)
    return j - 1 if j > 0 and text[j - 1] == "\r" else j


def after_eol(text: str, i: int) -> int:
    """Position just after the line break ending the line containing i."""
    j = text.find("\n", i)
    return len(text) if j == -1 else j + 1


def col(text: str, i: int) -> int:
    return i - line_start(text, i)


def indent_of_line(text: str, i: int) -> int:
    ls = line_start(text, i)
    k = ls
    while k < len(text) and text[k] == " ":
        k += 1
    return k - ls


# --------------------------------------------------------------------------- index


def index(text: str) -> dict[tuple, Ref]:
    root = YAML().compose(text)
    refs: dict[tuple, Ref] = {}
    seen: set[int] = set()

    def walk(node, path, parent, key, aliased, flow):
        if id(node) in seen:
            aliased = True
        seen.add(id(node))
        refs[path] = Ref(node, parent, key, aliased, flow)
        inner_flow = flow or bool(getattr(node, "flow_style", False))
        if isinstance(node, MappingNode):
            for k, v in node.value:
                if k.value == "<<":  # merge key: merged-in keys have no location of their own
                    continue
                walk(v, (*path, k.value), node, k, aliased, inner_flow)
        elif isinstance(node, SequenceNode):
            for i, v in enumerate(node.value):
                walk(v, (*path, i), node, None, aliased, inner_flow)

    if root is not None:
        walk(root, (), None, None, False, False)
    return refs


def lookup(refs, path):
    """Paths from the config layer may carry ints for digit-like mapping keys; try both."""
    if path in refs:
        return refs[path]
    alt = tuple(str(p) for p in path)
    for k, r in refs.items():
        if tuple(str(p) for p in k) == alt:
            return r
    return None


# --------------------------------------------------------------------------- data (for verification)


def to_data(node):
    if isinstance(node, CommentedMap) or isinstance(node, dict):
        return {k: to_data(v) for k, v in node.items()}
    if isinstance(node, (CommentedSeq, list)):
        return [to_data(v) for v in node]
    if isinstance(node, TaggedScalar):
        return ("!tag", node.tag.value, str(node.value))
    if isinstance(node, ScalarString):
        return str(node)
    return node


def load_data(text: str):
    y = YAML()
    return to_data(y.load(text))


def set_in(data, path, value):
    node = data
    for k in path[:-1]:
        if isinstance(node, dict) and _key(node, k) not in node:
            node[k] = {}  # deep new path: intermediates are created
        node = node[_key(node, k)]
    last = path[-1]
    if isinstance(node, list) and isinstance(last, int) and last == len(node):
        node.append(value)
    else:
        node[_key(node, last)] = value


def _key(node, k):
    if isinstance(node, dict) and k not in node and str(k) in node:
        return str(k)
    return k


# --------------------------------------------------------------------------- rendering


def _yaml(flow=False) -> YAML:
    y = YAML()
    y.width = 10**6
    y.preserve_quotes = True
    if flow:
        y.default_flow_style = True
    return y


def _styled(value, style):
    if isinstance(value, str) and not isinstance(value, ScalarString):
        if style == '"':
            return DoubleQuotedScalarString(value)
        if style == "'":
            return SingleQuotedScalarString(value)
    return value


def render_inline(value, style, flow) -> str | None:
    """Single-line rendering in the requested quote style, or None if it can't be single-line.
    ruamel escalates quoting by itself when a plain rendering would be unsafe (': ', ' #', '*', ...)."""
    v = _styled(value, style)
    if flow and isinstance(v, (dict, list)):
        out = _dump(v, flow=True).rstrip("\n")  # NOT via [v]: ruamel shortens [{k: 1}] to [k: 1]
        return None if "\n" in out else out
    if flow:
        out = _dump([v], flow=True).rstrip("\n")
        if not (out.startswith("[") and out.endswith("]")):
            return None
        out = out[1:-1]
    else:
        out = _dump({"k": v})
        if not out.startswith("k: "):
            return None
        out = out[3:].rstrip("\n")
    if "\n" in out:
        return None
    return out


def render_block_lines(value, nl: str) -> list[str]:
    """Block-context rendering of a mapping/sequence as lines (no base indent)."""
    out = _dump(value)
    return out.rstrip("\n").split("\n")


def _dump(obj, flow=False) -> str:
    buf = io.StringIO()
    _yaml(flow).dump(obj, buf)
    return buf.getvalue()


HEADER_RE = re.compile(r"^([|>])([1-9]?)([+-]?)([1-9]?)")


def _chomp_for(value: str) -> tuple[str, int]:
    """(chomping indicator, extra trailing blank lines) that reproduce value's trailing newlines."""
    n = len(value) - len(value.rstrip("\n"))
    if n == 0:
        return "-", 0
    if n == 1:
        return "", 0
    return "+", n - 1


def _literal_source_lines(value: str) -> list[str]:
    body = value.rstrip("\n")
    return body.split("\n") if body else []


def _folded_source_lines(value: str) -> list[str] | None:
    """Value -> folded source lines (one source line per paragraph). None if not simply foldable."""
    body = value.rstrip("\n")
    out: list[str] = []
    for i, piece in enumerate(body.split("\n")):
        if piece[:1] in (" ", "\t"):
            return None  # more-indented lines don't fold; not handled
        if i > 0:
            out.append("")
        if piece:
            out.append(piece)
    return out


def _folded_paragraph_groups(src_lines: list[str]) -> list[list[str]]:
    groups, cur = [], []
    for ln in src_lines:
        if ln.strip() == "":
            groups.append(cur)
            cur = []
        else:
            cur.append(ln)
    groups.append(cur)
    return groups


# --------------------------------------------------------------------------- operations


def surgical_set(text: str, path: tuple, value) -> str:
    """Return new text with the value at `path` (inside this file) set to `value`."""
    refs = index(text)
    ref = lookup(refs, path)
    if ref is not None and ref.aliased:
        raise AliasTargetError(f"{'.'.join(map(str, path))!r} is reached through a YAML alias; "
                               f"setting it would also change the anchor and every other alias")
    old_data = load_data(text)
    eff = [value]
    if ref is not None:
        new_text = _replace(text, ref, value, old_data, path, eff)
        changed = path
    else:
        new_text, changed = _add(text, refs, path, value, old_data, eff)
    _verify(text, new_text, path, eff[0], changed)
    return new_text


def source_map(text: str) -> dict:
    """Source-level fingerprint of every node location: kind, tag, style, raw scalar text.
    Alias re-visits are dropped (their anchor is compared); merge-key contents are not walked."""
    out = {}
    for p, r in index(text).items():
        if r.aliased and p:
            continue
        n = r.node
        key = tuple(str(x) for x in p)
        if isinstance(n, ScalarNode):
            out[key] = ("scalar", n.tag, n.style, n.value, getattr(n, "anchor", None))
        else:
            out[key] = (type(n).__name__, n.tag, bool(getattr(n, "flow_style", False)), getattr(n, "anchor", None),
                        tuple(k.value for k, _ in n.value) if isinstance(n, MappingNode) else len(n.value))
    return out


def _verify(old_text, new_text, path, value, changed):
    try:
        new_data = load_data(new_text)
    except Exception as e:
        raise SpliceError(f"splice produced invalid YAML: {e}") from e
    # 1. the target now holds exactly the new value
    try:
        got = _old_value(new_data, path)
    except (KeyError, IndexError, TypeError) as e:
        raise SpliceError(f"target missing after splice: {e}") from e
    if got != to_data(value):
        raise SpliceError(f"splice verification failed: target is {got!r}, wanted {to_data(value)!r}")
    # 2. every OTHER source node is identical (only the changed subtree, and the key list /
    #    length of its immediate parent collection, may differ)
    cp = tuple(str(x) for x in changed)
    parent = cp[:-1]

    def outside(m):
        return {k: v for k, v in m.items() if k[: len(cp)] != cp and k != parent}

    a, b = outside(source_map(old_text)), outside(source_map(new_text))
    if a != b:
        diff = sorted(set(a.items()) ^ set(b.items()), key=str)[:4]
        raise SpliceError(f"splice verification failed: collateral change outside {cp}: {diff}")
    # the parent collection itself: same kind/tag/style/anchor
    oa, ob = source_map(old_text).get(parent), source_map(new_text).get(parent)
    if oa and ob and oa[:4] != ob[:4]:
        raise SpliceError("parent collection changed kind/style")


def _old_value(old_data, path):
    node = old_data
    for k in path:
        node = node[_key(node, k)]
    return node


def _parent_indent(text, ref) -> int:
    """Indent for block content nested under this node's key or '- '."""
    if ref.key is not None:
        return col(text, ref.key.start_mark.index) + 2
    # sequence item: content goes 2 past the dash
    ls = line_start(text, ref.node.start_mark.index)
    seg = text[ls:ref.node.start_mark.index]
    d = seg.rfind("-")
    return (d + 2) if d >= 0 else indent_of_line(text, ref.node.start_mark.index) + 2


PROPS_RE = re.compile(r"(?:[&!][^\s]*[ \t]+)+")


def _replace(text, ref, value, old_data, path, eff) -> str:
    node, nl = ref.node, newline_of(text)
    if not isinstance(node, ScalarNode):
        return _replace_container(text, ref, value, nl)
    a, b = node.start_mark.index, node.end_mark.index
    style = node.style
    tag = node.tag if not str(node.tag).startswith(DEFAULT_TAG_PREFIX) else None
    # node properties (anchor `&x`, tag `!t`) are kept verbatim; only the scalar itself is re-rendered
    props = PROPS_RE.match(text, a, b)
    if tag and not isinstance(value, (str, TaggedScalar)):
        raise RejectedSet(f"value is tagged {tag}; only a string can be set there (got {type(value).__name__})")
    if props:
        a = props.end()
        if tag and isinstance(value, str) and not isinstance(value, TaggedScalar):
            eff[0] = TaggedScalar(value, style=style if style in ("'", '"') else None, tag=tag)
        if isinstance(value, TaggedScalar):
            value = str(value.value)
    elif tag and isinstance(value, str) and not isinstance(value, TaggedScalar):
        value = eff[0] = TaggedScalar(value, style=style if style in ("'", '"') else None, tag=tag)
    empty = node.value == "" and style is None and not props  # `key:` with nothing after it (null)
    if empty and ref.key is not None:
        a = b = text.index(":", ref.key.end_mark.index) + 1  # compose marks point at the NEXT token
    lead = " " if empty else ""

    if isinstance(value, (dict, list)):
        if ref.flow:
            r = render_inline(value, None, True)
            return text[:a] + lead + r + text[b:]
        if style in ("|", ">"):
            # block scalar -> block collection: the header line keeps its comment, the body is swapped
            hdr_end = line_end(text, a)
            m = HEADER_RE.match(text[a:hdr_end])
            rest = text[a + m.end():hdr_end] if m else ""
            ce = _last_line_end(text, node)
            s = a
            while s > 0 and text[s - 1] in " \t":
                s -= 1
            ind = " " * _parent_indent(text, ref)
            block = nl.join(ind + ln if ln else ln for ln in render_block_lines(value, nl))
            tail = text[ce:]
            return text[:s] + rest + nl + block + (nl if text[:ce].endswith("\n") else "") + tail
        # scalar -> block collection: drop scalar (and the space before it), put the block after the line
        s = a
        while s > 0 and text[s - 1] in " \t" and not props:
            s -= 1
        le = line_end(text, b)
        ind = " " * _parent_indent(text, ref)
        block = nl.join(ind + ln if ln else ln for ln in render_block_lines(value, nl))
        return text[:s] + text[b:le] + nl + block + text[le:]

    old_is_block = style in ("|", ">")
    multiline = isinstance(value, str) and "\n" in value

    if not old_is_block and not multiline:
        r = render_inline(value, style, ref.flow)
        if r is None:
            raise SpliceError("cannot render single-line")
        return text[:a] + lead + r + text[b:]

    if not old_is_block and multiline:
        if ref.flow:
            r = render_inline(value, '"', True)  # escapes \n
            return text[:a] + r + text[b:]
        # inline -> literal block; any end-of-line comment stays on the header line
        chomp, extra = _chomp_for(value)
        ind = " " * _parent_indent(text, ref)
        lines = [ind + ln if ln else "" for ln in _literal_source_lines(value)] + [""] * extra
        le = line_end(text, b)
        rest = text[le:]
        body = nl.join(lines)
        if rest.startswith(nl) or rest.startswith("\n"):
            return text[:a] + lead + "|" + chomp + text[b:le] + nl + body + rest
        return text[:a] + lead + "|" + chomp + text[b:le] + nl + body + nl  # EOF: block needs a final break

    # ---- old value is a block scalar
    header_end = line_end(text, a)
    header = text[a:header_end]
    m = HEADER_RE.match(header)
    if not m:
        raise SpliceError(f"unrecognised block header {header!r}")
    kind, ind1, old_chomp, ind2 = m.groups()
    explicit = ind1 or ind2
    header_rest = header[m.end():]  # e.g. '  # comment'
    region = text[a:b]
    if old_chomp == "+":
        content_end = b
    else:
        stripped = region.rstrip(" \t\r\n")
        content_end = after_eol(text, a + len(stripped)) if len(stripped) > len(header) else after_eol(text, a)
    body_start = after_eol(text, a)
    old_src = text[body_start:content_end]
    old_src_lines = old_src.split(nl)
    if old_src_lines and old_src_lines[-1] == "":
        old_src_lines.pop()
    if explicit:
        c_indent = indent_of_line(text, a) + int(explicit)
    else:
        firsts = [ln for ln in old_src_lines if ln.strip()]
        c_indent = (len(firsts[0]) - len(firsts[0].lstrip(" "))) if firsts else _parent_indent(text, ref)
    ind = " " * c_indent

    old_val = _old_value(old_data, path)
    old_single = isinstance(old_val, str) and "\n" not in old_val.rstrip("\n")
    if not multiline and (not old_single or not isinstance(value, str)):
        # multi-line block -> inline scalar; keep header comment on the same line
        r = render_inline(value, None, False)
        if r is None:
            raise SpliceError("cannot render single-line")
        tail = text[content_end:]
        eol = nl if content_end <= len(text) and text[:content_end].endswith("\n") else ""
        return text[:a] + r + header_rest + eol + tail

    chomp, extra = _chomp_for(value)
    if kind == "|":
        new_vals = _literal_source_lines(value)
        old_vals = _literal_source_lines(old_val if isinstance(old_val, str) else "")
        # map old value lines 1:1 to their source lines, reuse verbatim where unchanged
        content_src = old_src_lines[: len(old_vals)] if len(old_src_lines) >= len(old_vals) else None
        new_src = _reuse(old_vals, content_src, new_vals, lambda s: ind + s if s else "")
    else:
        new_pieces = _folded_source_lines(value)
        if new_pieces is None:
            # more-indented lines: re-emit THIS value as a literal block (style change confined to it)
            kind = "|"
            new_src = [ind + ln if ln else "" for ln in _literal_source_lines(value)] + [""] * extra
            new_header = kind + (ind1 or "") + chomp + (ind2 or "") + header_rest
            return text[:a] + new_header + nl + nl.join(new_src) + nl + text[content_end:]
        old_groups = _folded_paragraph_groups(old_src_lines)
        old_pars = [" ".join(l.strip() for l in g) for g in old_groups]
        new_pars = value.rstrip("\n").split("\n")
        # reuse the original (possibly wrapped) source lines of unchanged paragraphs
        new_src = []
        sm = difflib.SequenceMatcher(a=old_pars, b=new_pars, autojunk=False)
        chunks: list[list[str] | None] = [None] * len(new_pars)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                for off in range(i2 - i1):
                    chunks[j1 + off] = old_groups[i1 + off]
        for j, p in enumerate(new_pars):
            if j > 0:
                new_src.append("")
            if chunks[j] is not None:
                new_src.extend(chunks[j])
            elif p:
                new_src.append(ind + p)
    new_src += [""] * extra
    new_header = kind + (ind1 or "") + chomp + (ind2 or "") + header_rest
    out_body = nl.join(new_src) + nl
    if content_end >= len(text) and not text.endswith("\n") and not value.endswith("\n"):
        out_body = out_body[: -len(nl)]  # file had no final line break; keep it that way
    return text[:a] + new_header + nl + out_body + text[content_end:]


def _replace_container(text, ref, value, nl) -> str:
    node = ref.node
    if getattr(node, "flow_style", False) or ref.flow:
        r = render_inline(value, None, True) if isinstance(value, (dict, list)) else render_inline(value, None, True)
        return text[: node.start_mark.index] + r + text[node.end_mark.index:]
    if ref.key is None:
        raise SpliceError("replacing a block collection that is a sequence item is not spliced")
    # block container under `key:` -- keep the key line (and its comment), swap the body lines
    body_start = line_start(text, node.start_mark.index)
    body_end = _last_line_end(text, node)
    if isinstance(value, (dict, list)) and value:
        ind = " " * _parent_indent(text, ref)
        if isinstance(value, list) and _seq_dash_col(text, node) is not None:
            ind = " " * _seq_dash_col(text, node)  # keep the list's own dash indentation
        lines = [ind + ln if ln else ln for ln in render_block_lines(value, nl)]
        return text[:body_start] + nl.join(lines) + nl + text[body_end:]
    # -> scalar (or empty collection): value goes on the key line after the colon
    r = render_inline(value, None, False)
    k_end = ref.key.end_mark.index
    colon = text.index(":", k_end) + 1
    le = line_end(text, colon)
    rest = text[colon:le]  # e.g. '  # comment'
    return text[:colon] + " " + r + rest + nl + text[body_end:]


def _seq_dash_col(text, node):
    if not isinstance(node, SequenceNode) or not node.value:
        return None
    first = node.value[0]
    ls = line_start(text, first.start_mark.index)
    d = text[ls:first.start_mark.index].rfind("-")
    return d if d >= 0 else None


def _reuse(old_vals, old_src, new_vals, render):
    if old_src is None:
        return [render(v) for v in new_vals]
    out = []
    sm = difflib.SequenceMatcher(a=old_vals, b=new_vals, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out.extend(old_src[i1:i2])
        else:
            out.extend(render(v) for v in new_vals[j1:j2])
    return out


def _last_line_end(text, node) -> int:
    """Position right after the last source line that belongs to `node`'s meaningful content
    (excludes trailing blank lines / comments that ruamel's end mark sweeps in)."""
    if isinstance(node, ScalarNode):
        if node.style in ("|", ">"):
            header = text[node.start_mark.index:line_end(text, node.start_mark.index)]
            m = HEADER_RE.match(header)
            if m and m.group(3) == "+":
                return node.end_mark.index
            region = text[node.start_mark.index:node.end_mark.index]
            return after_eol(text, node.start_mark.index + len(region.rstrip(" \t\r\n")) - 1)
        return after_eol(text, max(node.end_mark.index - 1, node.start_mark.index))
    if getattr(node, "flow_style", False):
        return after_eol(text, node.end_mark.index - 1)
    last = node.value[-1][1] if isinstance(node, MappingNode) else node.value[-1]
    return _last_line_end(text, last)


def _insert_lines(text, pos, lines, nl) -> str:
    block = nl.join(lines)
    if pos >= len(text) and not text.endswith("\n"):
        return text + nl + block  # keep "no trailing newline"
    return text[:pos] + block + nl + text[pos:]


def _add(text, refs, path, value, old_data, eff):
    """Returns (new_text, path of the subtree that changed)."""
    nl = newline_of(text)
    i = len(path) - 1
    while i > 0 and lookup(refs, path[:i]) is None:
        i -= 1
    parent = lookup(refs, path[:i])
    if parent is None:
        raise SpliceError("no existing ancestor")
    if parent.aliased:
        raise AliasTargetError("parent is reached through a YAML alias")
    key, rest = path[i], path[i + 1:]
    for k in reversed(rest):
        value = {k: value}
    pnode = parent.node
    if isinstance(pnode, ScalarNode) and pnode.tag.endswith(":null"):
        # `a:` (null) gains a child: replace the null with a mapping
        return _replace(text, parent, {key: value}, old_data, path[:i], [None]), path[:i]
    changed = path[: i + 1]
    return _add_into(text, parent, pnode, key, value, nl), changed


def _add_into(text, parent, pnode, key, value, nl) -> str:
    flow = bool(getattr(pnode, "flow_style", False))

    if isinstance(pnode, SequenceNode):
        if not (isinstance(key, int) and key == len(pnode.value)):
            raise SpliceError("can only append at index len(list)")
        if flow:
            r = render_inline(value, None, True)
            if pnode.value:
                e = pnode.value[-1].end_mark.index
                return text[:e] + ", " + r + text[e:]
            e = pnode.start_mark.index + 1
            return text[:e] + r + text[e:]
        first = pnode.value[0]
        ls = line_start(text, first.start_mark.index)
        dash = text[ls:first.start_mark.index].rfind("-")
        if isinstance(value, (dict, list)):
            blines = render_block_lines(value, nl)
            lines = [" " * dash + "- " + blines[0]] + [" " * (dash + 2) + l for l in blines[1:]]
        else:
            r = render_inline(value, _sibling_style(pnode), False)
            lines = [" " * dash + "- " + r]
        return _insert_lines(text, _last_line_end(text, pnode), lines, nl)

    if not isinstance(pnode, MappingNode):
        raise SpliceError("parent is not a collection")
    flow = bool(getattr(pnode, "flow_style", False))
    if flow:
        r = render_inline({key: value}, None, True)  # "{k: v}"
        r = r[1:-1] if r and r.startswith("{") else r
        if pnode.value:
            e = pnode.value[-1][1].end_mark.index
            return text[:e] + ", " + r + text[e:]
        e = pnode.start_mark.index + 1
        return text[:e] + r + text[e:]
    kcol = col(text, pnode.value[0][0].start_mark.index)
    if isinstance(value, (dict, list)):
        blines = render_block_lines({key: value}, nl)
    else:
        blines = [f"{render_inline(key, None, False)}: {render_inline(value, None, False)}"]
    lines = [" " * kcol + l for l in blines]
    return _insert_lines(text, _last_line_end(text, pnode), lines, nl)


def _sibling_style(seq):
    styles = {getattr(v, "style", None) for v in seq.value if isinstance(v, ScalarNode)}
    return styles.pop() if len(styles) == 1 else None
