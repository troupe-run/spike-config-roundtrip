"""Q8: hazards found along the way, pinned as tests that describe CURRENT (not desired) behaviour."""
from spike_config_roundtrip.config import Config


def test_set_through_alias_mutates_the_anchor_and_every_alias(tmp_path):
    f = tmp_path / "m.yml"
    f.write_text("defaults: &d\n  model: opus\nagents:\n  reviewer: *d\n")
    cfg = Config(f)
    cfg.set("agents.reviewer.model", "haiku")
    assert cfg.get("defaults.model") == "haiku"  # collateral change: anchors break "one path, one location"
    assert f.read_text() == "defaults: &d\n  model: haiku\nagents:\n  reviewer: *d\n"


def test_set_on_merge_key_value_adds_a_local_override(tmp_path):
    f = tmp_path / "m.yml"
    f.write_text("defaults: &d\n  model: opus\nagents:\n  coder:\n    <<: *d\n    retries: 5\n")
    cfg = Config(f)
    assert cfg.where("agents.coder.model").line is None  # provenance can't point at a merged-in key
    cfg.set("agents.coder.model", "sonnet")
    assert cfg.get("defaults.model") == "opus"
    assert "    model: sonnet\n" in f.read_text()


def test_block_scalar_set_falls_back_to_dump_and_keeps_literal_style(tmp_path):
    f = tmp_path / "m.yml"
    f.write_text("a: 1  # keep\nprompt: |\n  one\n  two\n")
    cfg = Config(f)
    cfg.set("prompt", "one\ntwo\nthree\n")
    assert cfg.last_write_strategy == "dump"
    assert f.read_text() == "a: 1  # keep\nprompt: |\n  one\n  two\n  three\n"
