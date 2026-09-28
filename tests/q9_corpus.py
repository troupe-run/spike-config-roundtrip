"""Q9 corpus: nasty YAML + golden expected output. Each case: exact bytes before, set() ops,
exact bytes after. `expect` is the write strategy we expect ("splice" = surgical)."""
from dataclasses import dataclass, field


@dataclass
class Case:
    name: str
    group: str
    before: str
    ops: list  # [(dotted path, value)]
    after: str
    expect: str = "splice"
    note: str = ""


CASES: list[Case] = []


def case(*a, **k):
    CASES.append(Case(*a, **k))


# ------------------------------------------------------------------ block scalars
G = "block scalars"
case("literal | change one line", G,
     "name: demo\nprompt: |\n  line one\n  line two\n  line three\nafter: 1\n",
     [("prompt", "line one\nline 2\nline three\n")],
     "name: demo\nprompt: |\n  line one\n  line 2\n  line three\nafter: 1\n")
case("literal |- (strip) change one line", G,
     "script: |-\n  echo a\n  echo b\nnext: 1\n",
     [("script", "echo a\necho B")],
     "script: |-\n  echo a\n  echo B\nnext: 1\n")
case("literal |+ (keep) with trailing blank lines", G,
     "keep: |+\n  x\n  y\n\n\nnext: 1\n",
     [("keep", "x\nY\n\n\n")],
     "keep: |+\n  x\n  Y\n\n\nnext: 1\n")
case("folded > change second paragraph, first stays wrapped", G,
     "desc: >\n  This is a long\n  wrapped paragraph.\n\n  Second para\n  here.\nnext: 1\n",
     [("desc", "This is a long wrapped paragraph.\nSecond paragraph changed.\n")],
     "desc: >\n  This is a long\n  wrapped paragraph.\n\n  Second paragraph changed.\nnext: 1\n",
     note="changed paragraph is re-emitted on one line; unchanged paragraphs keep their wrapping")
case("folded >- change", G,
     "summary: >-\n  one two\n  three\nnext: 1\n",
     [("summary", "one two four")],
     "summary: >-\n  one two four\nnext: 1\n",
     note="a changed folded paragraph loses its original line wrapping")
case("explicit indentation indicator |2", G,
     "code: |2\n    indented first\n  normal\nnext: 1\n",
     [("code", "  indented first\nchanged\n")],
     "code: |2\n    indented first\n  changed\nnext: 1\n")
case("clip block followed by blank lines", G,
     "a: |\n  x\n\n\nb: 1\n",
     [("a", "y\n")],
     "a: |\n  y\n\n\nb: 1\n")
case("block followed by a comment", G,
     "a: |\n  x\n  y\n# about b\nb: 1\n",
     [("a", "x\nz\n")],
     "a: |\n  x\n  z\n# about b\nb: 1\n")
case("block header with comment", G,
     "a: |  # keep me\n  x\nb: 1\n",
     [("a", "y\n")],
     "a: |  # keep me\n  y\nb: 1\n")
case("plain -> multi-line (EOL comment moves to header)", G,
     "a: short  # note\nb: 1\n",
     [("a", "line1\nline2\n")],
     "a: |  # note\n  line1\n  line2\nb: 1\n")
case("plain -> multi-line without final newline uses |-", G,
     "a: short\nb: 1\n",
     [("a", "line1\nline2")],
     "a: |-\n  line1\n  line2\nb: 1\n")
case("multi-line -> plain", G,
     "a: |\n  x\n  y\n\nb: 1\n",
     [("a", "single")],
     "a: single\n\nb: 1\n")
case("block scalar as sequence item", G,
     "steps:\n  - |\n    echo a\n    echo b\n  - done\n",
     [("steps.0", "echo a\necho c\n")],
     "steps:\n  - |\n    echo a\n    echo c\n  - done\n")
case("block scalar at EOF without trailing newline", G,
     "a: 1\nb: |-\n  x\n  y",
     [("b", "x\nz")],
     "a: 1\nb: |-\n  x\n  z",
     note="last line of file has no line break")
case("chomping change: clip value gains trailing newlines -> |+", G,
     "a: |\n  x\nb: 1\n",
     [("a", "x\n\n")],
     "a: |+\n  x\n\nb: 1\n",
     note="header indicator changes because the value's trailing newlines changed; that is part of the value")

# ------------------------------------------------------------------ comments everywhere
G = "comments"
case("comments above keys", G,
     "# top\n# about a\na: 1  # eol\n# about b\nb: 2\n",
     [("b", 3)],
     "# top\n# about a\na: 1  # eol\n# about b\nb: 3\n")
case("EOL comments with varied spacing (1 space, 6 spaces)", G,
     "a: 1 # one space\nb: 22      # six\nc: x  # two\n",
     [("b", 7), ("c", "yy")],
     "a: 1 # one space\nb: 7      # six\nc: yy  # two\n",
     note="EOL comment column shifts by the value's length delta; spacing bytes are untouched")
case("TAB before an EOL comment (valid YAML 1.2)", G,
     "c: x\t# tab before\n",
     [("c", "yy")],
     "c: x\t# tab before\n", expect="error",
     note="ruamel.yaml 0.19.1 cannot LOAD this (ScannerError), so no write-back is possible")
case("comments between list items; change + append", G,
     "l:\n  - a\n  # between\n  - b\n  # after last\nnext: 1\n",
     [("l.1", "c"), ("l.2", "d")],
     "l:\n  - a\n  # between\n  - c\n  - d\n  # after last\nnext: 1\n",
     note="append goes directly after the last item, above a trailing comment")
case("comments inside nested mappings; change + add", G,
     "outer:\n  # c1\n  inner:\n    # c2\n    k: v  # c3\n    # c4\n  other: 1\n",
     [("outer.inner.k", "w"), ("outer.inner.new", 1)],
     "outer:\n  # c1\n  inner:\n    # c2\n    k: w  # c3\n    new: 1\n    # c4\n  other: 1\n")
case("comment at end of file; change + add root key", G,
     "a: 1\nb: 2\n# trailing comment\n",
     [("b", 3), ("c", 4)],
     "a: 1\nb: 3\nc: 4\n# trailing comment\n",
     note="new key goes above the EOF comment (conservative: the comment may describe b)")
case("file with no trailing newline; change + add", G,
     "a: 1\nb: 2",
     [("b", 3), ("c", 4)],
     "a: 1\nb: 3\nc: 4")
case("commented-out key after mapping; add", G,
     "a:\n  x: 1\n#  y: 2\nb: 1\n",
     [("a.z", 3)],
     "a:\n  x: 1\n  z: 3\n#  y: 2\nb: 1\n")
case("explicit document start marker and header comment", G,
     "---\n# config\na: 1\n",
     [("a", 2), ("b", 3)],
     "---\n# config\na: 2\nb: 3\n")

# ------------------------------------------------------------------ flow collections
G = "flow collections"
case("flow mapping: change one, add one", G,
     "m: {a: 1, b: 2}  # flow\n",
     [("m.b", 3), ("m.c", 4)],
     "m: {a: 1, b: 3, c: 4}  # flow\n")
case("flow sequence: change one, append one", G,
     "l: [x, y]\n",
     [("l.0", "z"), ("l.2", "w")],
     "l: [z, y, w]\n")
case("nested flow; quote style kept; append inside", G,
     "m: {l: [1, 2], s: 'q'}\n",
     [("m.s", "r"), ("m.l.2", 3)],
     "m: {l: [1, 2, 3], s: 'r'}\n")
case("flow value that needs quoting", G,
     "l: [x, y]\n",
     [("l.0", "a, b")],
     "l: ['a, b', y]\n")
case("empty flow collections", G,
     "m: {}\nl: []\n",
     [("m.a", 1), ("l.0", "x")],
     "m: {a: 1}\nl: [x]\n")
case("multi-line flow mapping", G,
     "m: {\n  a: 1,\n  b: 2\n}\n",
     [("m.b", 3), ("m.c", 4)],
     "m: {\n  a: 1,\n  b: 3, c: 4\n}\n",
     note="appended element joins the last line rather than getting its own line")

# ------------------------------------------------------------------ lists
G = "lists"
case("change an item in a block list", G,
     "tools:\n  - bash\n  - edit  # the editor\n  - web\n",
     [("tools.1", "write")],
     "tools:\n  - bash\n  - write  # the editor\n  - web\n")
case("append to a block list", G,
     "tools:\n  - bash\n  - edit\nnext: 1\n",
     [("tools.2", "web")],
     "tools:\n  - bash\n  - edit\n  - web\nnext: 1\n")
case("two indentation styles in one file", G,
     "a:\n- x\n- y\nb:\n    - p\n    - q\n",
     [("a.2", "z"), ("b.2", "r"), ("b.0", "P")],
     "a:\n- x\n- y\n- z\nb:\n    - P\n    - q\n    - r\n")
case("list of mappings: append a mapping, add key to an item", G,
     "agents:\n  - name: a\n    model: x\n",
     [("agents.1", {"name": "b", "model": "y"}), ("agents.0.temp", 0.1)],
     "agents:\n  - name: a\n    model: x\n    temp: 0.1\n  - name: b\n    model: y\n")
case("append to a list whose last item is a nested list", G,
     "m:\n  - - 1\n    - 2\n  - x\n",
     [("m.2", "y")],
     "m:\n  - - 1\n    - 2\n  - x\n  - y\n")

# ------------------------------------------------------------------ quoting
G = "quoting"
case("plain stays plain", G, "k: v  # c\n", [("k", "w")], "k: w  # c\n")
case("single-quoted with apostrophe in new value", G,
     "k: 'v'\n", [("k", "it's")], "k: 'it''s'\n")
case("double-quoted with escapes", G,
     'k: "tab\\there \\u00e9"\n', [("k", "new\tval é")], 'k: "new\\tval é"\n',
     note="ruamel emits the tab as \\t; é stays literal (original used a \\u escape for it)")
case("plain -> needs quoting: contains ': '", G, "k: v\n", [("k", "a: b")], "k: 'a: b'\n")
case("plain -> needs quoting: contains ' #'", G, "k: v\n", [("k", "x # y")], "k: 'x # y'\n")
case("plain -> needs quoting: starts with '*'", G, "k: v\n", [("k", "*star")], "k: '*star'\n")
case("plain -> needs quoting: string that looks like a bool/int", G,
     "a: v\nb: v\n", [("a", "true"), ("b", "123")], "a: 'true'\nb: '123'\n")
case("quoted string -> int keeps it an int (type change is the edit)", G,
     'k: "8000"\n', [("k", 9000)], "k: 9000\n")
case("!literal tag kept on set", G,
     'u: !literal "https://x/a.yaml"  # c\n', [("u", "https://x/b.yaml")], 'u: !literal "https://x/b.yaml"  # c\n')

# ------------------------------------------------------------------ layout
G = "layout"
case("blank lines between sections and key order preserved; add in middle section", G,
     "a: 1\n\n\nb:\n  c: 2\n\n  d: 3\n\n\ne: 4\n",
     [("b.c", 5), ("b.x", 6), ("f", 7)],
     "a: 1\n\n\nb:\n  c: 5\n\n  d: 3\n  x: 6\n\n\ne: 4\nf: 7\n")
case("deep new path creates intermediate mapping in place", G,
     "a:\n  b: 1\n\nz: 0\n",
     [("a.c.d.e", 1)],
     "a:\n  b: 1\n  c:\n    d:\n      e: 1\n\nz: 0\n")
case("scalar -> mapping keeps EOL comment on the key line", G,
     "model: 'opus'  # default\nnext: 1\n",
     [("model", {"name": "opus", "temperature": 0.2})],
     "model:  # default\n  name: opus\n  temperature: 0.2\nnext: 1\n")

# ------------------------------------------------------------------ unicode / CRLF / tabs
G = "encoding"
case("unicode values and comments", G,
     "name: café ✓  # ünïcode\nemoji: 🎭\n",
     [("name", "naïve ✓✓"), ("emoji", "🎭🎭")],
     "name: naïve ✓✓  # ünïcode\nemoji: 🎭🎭\n")
case("CRLF line endings: change, append, block scalar, add key", G,
     "a: 1\r\nb:\r\n  - x\r\n  - y\r\nc: |\r\n  one\r\n  two\r\n",
     [("a", 2), ("b.2", "z"), ("c", "one\nthree\n"), ("d", 4)],
     "a: 2\r\nb:\r\n  - x\r\n  - y\r\n  - z\r\nc: |\r\n  one\r\n  three\r\nd: 4\r\n")
case("tabs inside comment text", G,
     "a: 1  # tab\there\n#\tindented\tcomment\nb: 2\n",
     [("a", 5), ("b", 6)],
     "a: 5  # tab\there\n#\tindented\tcomment\nb: 6\n")

# ------------------------------------------------------------------ multi-document
G = "multi-document"
case("multi-document file (---)", G,
     "a: 1\n---\nb: 2\n", [("a", 2)], "a: 1\n---\nb: 2\n", expect="error",
     note="rejected at LOAD time with a clear ConfigError; file untouched")
