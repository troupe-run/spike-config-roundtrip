"""Q1: nested includes (yaml -> yaml -> yaml, plus md text) load into a merged view with provenance."""
from spike_config_roundtrip.config import Config


def test_merged_view_resolves_nested_includes(root):
    cfg = Config(root)
    assert cfg.get("project.name") == "demo"
    assert cfg.get("agents.coder.model") == "opus"  # yaml include
    assert cfg.get("agents.coder.limits.max_tokens") == 8000  # yaml-in-yaml-in-yaml
    assert cfg.get("agents.coder.system_prompt").startswith("# Coder\n")  # md text include
    assert cfg.get("agents.coder.tools") == ["bash", "edit"]


def test_every_node_has_provenance(root):
    cfg = Config(root)
    d = root.parent
    cases = {
        "project.name": (d / "troupe.yml", ("project", "name"), "yaml", 3),
        "agents.reviewer.model": (d / "troupe.yml", ("agents", "reviewer", "model"), "yaml", 8),
        "agents.coder": (d / "agents/coder.yaml", (), "yaml", 1),
        "agents.coder.model": (d / "agents/coder.yaml", ("model",), "yaml", 2),
        "agents.coder.tools.1": (d / "agents/coder.yaml", ("tools", 1), "yaml", 5),
        "agents.coder.limits.max_tokens": (d / "agents/limits.yaml", ("max_tokens",), "yaml", 1),
        "agents.coder.system_prompt": (d / "prompts/coder.md", (), "text", 1),
    }
    for path, (src, inner, kind, line) in cases.items():
        p = cfg.where(path)
        assert (p.source, p.inner_path, p.kind, p.line) == (str(src.resolve()), inner, kind, line), path
    # include roots also remember the reference that pulled them in
    assert cfg.where("agents.coder.limits").via == "file:limits.yaml"
    # completeness: every leaf in the merged view has a provenance entry
    from spike_config_roundtrip.config import dotted
    assert all(dotted(k) in cfg.provenance for k in cfg.flat())
