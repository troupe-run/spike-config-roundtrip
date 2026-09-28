"""Q2: set(dotted_path) writes into the owning file and preserves everything else."""
from conftest import diff, snapshot
from spike_config_roundtrip.config import Config


def changed_lines(d: str) -> list[str]:
    return [l for l in d.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]


def test_set_in_root_preserves_quotes_and_comments(project, root):
    cfg = Config(root)
    before = snapshot(project)
    assert cfg.set("project.name", "renamed").endswith("troupe.yml")
    d = diff(before, snapshot(project))
    print(d)
    assert changed_lines(d) == [
        '-  name: "demo"   # quoted on purpose',
        '+  name: "renamed"   # quoted on purpose',
    ]


def test_set_in_nested_yaml_include(project, root):
    cfg = Config(root)
    before = snapshot(project)
    assert cfg.set("agents.coder.limits.max_tokens", 16000).endswith("agents/limits.yaml")
    d = diff(before, snapshot(project))
    print(d)
    assert changed_lines(d) == ["-max_tokens: 8000  # hard cap", "+max_tokens: 16000  # hard cap"]
    assert cfg.get("agents.coder.limits.max_tokens") == 16000


def test_set_single_quoted_scalar_in_include_keeps_style(project, root):
    cfg = Config(root)
    before = snapshot(project)
    cfg.set("agents.coder.model", "sonnet")
    assert changed_lines(diff(before, snapshot(project))) == ["-model: 'opus'", "+model: 'sonnet'"]


def test_set_list_item_in_include(project, root):
    cfg = Config(root)
    before = snapshot(project)
    cfg.set("agents.coder.tools.1", "write")
    assert changed_lines(diff(before, snapshot(project))) == ["-  - edit", "+  - write"]


def test_set_text_include_rewrites_markdown_file(project, root):
    cfg = Config(root)
    before = snapshot(project)
    new = "# Coder\n\nYou write code. Keep it simple.\nAlways add tests.\n"
    assert cfg.set("agents.coder.system_prompt", new).endswith("prompts/coder.md")
    d = diff(before, snapshot(project))
    print(d)
    assert changed_lines(d) == ["+Always add tests."]
    # the include reference in coder.yaml is untouched
    assert "system_prompt: file:../prompts/coder.md" in (root.parent / "agents/coder.yaml").read_text()


def test_scalar_replacements_use_byte_exact_splice(root):
    cfg = Config(root)
    cfg.set("settings.retries", 5)
    assert cfg.last_write_strategy == "splice"


def test_scalar_to_mapping_is_spliced_not_dumped(project, root):
    """Q2 originally needed the dump path here; since Q9 the splice engine handles it."""
    cfg = Config(root)
    before = snapshot(project)
    cfg.set("agents.coder.model", {"name": "opus", "temperature": 0.2})
    assert cfg.last_write_strategy == "splice"
    d = diff(before, snapshot(project))
    print(d)
    assert changed_lines(d) == ["-model: 'opus'", "+model:", "+  name: opus", "+  temperature: 0.2"]
    assert cfg.get("agents.coder.model.temperature") == 0.2


def test_dump_fallback_keeps_detected_sequence_indent():
    """The (non-surgical) fallback still exists; with indent detection it doesn't re-indent lists."""
    from spike_config_roundtrip.config import dump_like, make_yaml
    src = "a: 1\ntools:\n  - bash\n  - edit\n"
    assert dump_like(make_yaml().load(src), src) == src


def test_naive_ruamel_dump_would_reindent_sequences(root):
    """Documents the trap: a default-configured ruamel dump rewrites untouched lists."""
    import io
    from spike_config_roundtrip.config import make_yaml
    src = (root.parent / "agents/coder.yaml").read_text()
    y = make_yaml()
    buf = io.StringIO()
    y.dump(y.load(src), buf)
    assert "  - bash" in src and "  - bash" not in buf.getvalue()
