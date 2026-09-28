"""Q3: a NEW key goes into the file that owns the deepest existing ancestor mapping."""
import pytest
from conftest import diff, snapshot
from spike_config_roundtrip.config import Config, ConfigError


@pytest.mark.parametrize(
    "path,value,expected_file",
    [
        ("agents.coder.temperature", 0.2, "agents/coder.yaml"),  # parent mapping is the included file root
        ("agents.coder.limits.max_retries", 2, "agents/limits.yaml"),  # parent is a nested include
        ("agents.tester", "file:agents/tester.yaml", "troupe.yml"),  # sibling of an include lives in the includer
        ("agents.reviewer.temperature", 0.0, "troupe.yml"),  # inline mapping
        ("agents.coder.budget.daily_usd", 5, "agents/coder.yaml"),  # deep new path: intermediates created
    ],
)
def test_new_key_lands_in_owner_of_parent(project, root, path, value, expected_file):
    if path == "agents.tester":
        (root.parent / "agents/tester.yaml").write_text("model: haiku\n")
    cfg = Config(root)
    before = snapshot(project)
    written = cfg.set(path, value, include=path == "agents.tester")  # adding an include is explicit
    assert written == str((root.parent / expected_file).resolve())
    d = diff(before, snapshot(project))
    print(d)
    # only the owner file changed, and everything that was there before is still there
    changed = {l[6:] for l in d.splitlines() if l.startswith("+++ ")}
    assert changed == {f".troupe/{expected_file}"}
    assert not [l for l in d.splitlines() if l.startswith("-") and not l.startswith("---")]
    if path == "agents.tester":
        assert cfg.get("agents.tester.model") == "haiku"  # a newly added include is resolved on reload
    else:
        assert cfg.get(path) == value


def test_new_key_under_text_include_is_rejected(root):
    cfg = Config(root)
    with pytest.raises(ConfigError, match="text include"):
        cfg.set("agents.coder.system_prompt.extra", "x")


def test_new_key_goes_above_a_section_comment_not_below_it(tmp_path):
    """ruamel pitfall: the blank line + '# section' comment after the last nested key is attached
    to that key, so a naive append lands under the next section's comment. We move it."""
    f = tmp_path / "t.yml"
    f.write_text("agents:\n  reviewer:\n    model: sonnet  # eol\n\n# ---- settings ----\nsettings:\n  retries: 3\n")
    cfg = Config(f)
    cfg.set("agents.reviewer.temperature", 0.1)
    cfg.set("agents.tester", "x")
    assert f.read_text() == (
        "agents:\n  reviewer:\n    model: sonnet  # eol\n    temperature: 0.1\n  tester: x\n"
        "\n# ---- settings ----\nsettings:\n  retries: 3\n"
    )
