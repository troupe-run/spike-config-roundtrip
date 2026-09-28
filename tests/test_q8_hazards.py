"""Q8: hazards found along the way, pinned as tests that describe CURRENT (not desired) behaviour."""
from spike_config_roundtrip.config import Config


def test_set_through_alias_is_rejected(tmp_path):
    """Before Q9 this silently mutated the anchor and every alias. The splice engine now refuses."""
    import pytest
    from spike_config_roundtrip.config import ConfigError
    f = tmp_path / "m.yml"
    src = "defaults: &d\n  model: opus\nagents:\n  reviewer: *d\n"
    f.write_text(src)
    cfg = Config(f)
    with pytest.raises(ConfigError, match="alias"):
        cfg.set("agents.reviewer.model", "haiku")
    assert f.read_text() == src
    cfg.set("defaults.model", "haiku")  # setting at the anchor itself is allowed (it's the definition)
    assert f.read_text() == src.replace("opus", "haiku")


def test_set_on_merge_key_value_adds_a_local_override(tmp_path):
    f = tmp_path / "m.yml"
    f.write_text("defaults: &d\n  model: opus\nagents:\n  coder:\n    <<: *d\n    retries: 5\n")
    cfg = Config(f)
    assert cfg.where("agents.coder.model").line is None  # provenance can't point at a merged-in key
    cfg.set("agents.coder.model", "sonnet")
    assert cfg.get("defaults.model") == "opus"
    assert "    model: sonnet\n" in f.read_text()


def test_block_scalar_set_is_spliced_and_keeps_literal_style(tmp_path):
    f = tmp_path / "m.yml"
    f.write_text("a: 1  # keep\nprompt: |\n  one\n  two\n")
    cfg = Config(f)
    cfg.set("prompt", "one\ntwo\nthree\n")
    assert cfg.last_write_strategy == "splice"
    assert f.read_text() == "a: 1  # keep\nprompt: |\n  one\n  two\n  three\n"
