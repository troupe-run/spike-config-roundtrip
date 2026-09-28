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
