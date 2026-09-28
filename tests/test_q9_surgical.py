"""Q9: every write-back is surgical. Golden before/after per case (see tests/q9_corpus.py)."""
import pytest
from q9_corpus import CASES
from spike_config_roundtrip.config import Config, read_exact


def run_case(tmp_path, c):
    f = tmp_path / "c.yml"
    with open(f, "w", encoding="utf-8", newline="") as fh:
        fh.write(c.before)
    cfg = Config(f)
    strategies = []
    for path, value in c.ops:
        cfg.set(path, value)
        strategies.append((cfg.last_write_strategy, cfg.last_splice_error))
    return read_exact(str(f)), strategies


@pytest.mark.parametrize("c", CASES, ids=[c.name for c in CASES])
def test_corpus_case(tmp_path, c):
    if c.expect == "error":
        with pytest.raises(Exception):
            run_case(tmp_path, c)
        assert (tmp_path / "c.yml").read_bytes().decode() == c.after  # untouched
        return
    after, strategies = run_case(tmp_path, c)
    assert all(s == c.expect for s, _ in strategies), strategies
    assert after == c.after


# ---------------------------------------------------------------- text (markdown) include
from conftest import diff, snapshot  # noqa: E402


def test_text_include_change_is_minimal_diff(project, root):
    """Whole-file replace, but the file IS the value, so only changed lines differ."""
    md = root.parent / "prompts/coder.md"
    md.write_text("# Coder\n\nYou write code.\nKeep it simple.\n\n## Rules\n- tests first\n- small commits\n")
    cfg = Config(root)
    before = snapshot(project)
    old = cfg.get("agents.coder.system_prompt")
    cfg.set("agents.coder.system_prompt", old.replace("- small commits", "- tiny commits"))
    d = diff(before, snapshot(project))
    print(d)
    changed = [l for l in d.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    assert changed == ["-- small commits", "+- tiny commits"]


def test_text_include_crlf_file_keeps_crlf_when_given_lf_content(project, root):
    md = root.parent / "prompts/coder.md"
    with open(md, "w", newline="") as fh:
        fh.write("# Coder\r\n\r\nline a\r\nline b\r\n")
    cfg = Config(root)
    assert cfg.get("agents.coder.system_prompt") == "# Coder\r\n\r\nline a\r\nline b\r\n"
    cfg.set("agents.coder.system_prompt", "# Coder\n\nline a\nline B\n")  # e.g. from a CLI arg
    assert read_exact(str(md)) == "# Coder\r\n\r\nline a\r\nline B\r\n"


def test_text_include_no_trailing_newline_preserved(project, root):
    md = root.parent / "prompts/coder.md"
    md.write_text("one\ntwo")
    cfg = Config(root)
    cfg.set("agents.coder.system_prompt", "one\nTWO")
    assert md.read_text() == "one\nTWO"


# ---------------------------------------------------------------- the safety net itself
def test_verifier_rejects_collateral_change():
    """If a splice ever touched anything besides the target, verification must refuse it."""
    from spike_config_roundtrip.surgical import SpliceError, _verify
    old = "a: 1\nb: 'x'\nc: [1, 2]\n"
    with pytest.raises(SpliceError, match="collateral"):
        _verify(old, "a: 2\nb: 'y'\nc: [1, 2]\n", ("a",), 2, ("a",))  # b changed too
    with pytest.raises(SpliceError, match="collateral"):
        _verify(old, 'a: 2\nb: "x"\nc: [1, 2]\n', ("a",), 2, ("a",))  # only b's quote STYLE changed
    with pytest.raises(SpliceError, match="kind/style"):
        _verify(old, "a: 1\nb: 'x'\nc:\n- 1\n- 2\n- 3\n", ("c", 2), 3, ("c", 2))  # flow -> block
    _verify(old, "a: 2\nb: 'x'\nc: [1, 2]\n", ("a",), 2, ("a",))  # the honest one passes
