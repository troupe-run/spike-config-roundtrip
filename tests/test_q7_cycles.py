"""Q7: include cycles are detected with a clear error; a diamond is not a cycle."""
import pytest
from spike_config_roundtrip.config import Config, IncludeCycleError


def test_two_file_cycle(tmp_path):
    (tmp_path / "a.yml").write_text("b: file:b.yml\n")
    (tmp_path / "b.yml").write_text("x: 1\na: file:a.yml\n")
    with pytest.raises(IncludeCycleError) as e:
        Config(tmp_path / "a.yml")
    print(e.value)
    assert str(e.value) == f"include cycle detected: {tmp_path.resolve()}/a.yml -> {tmp_path.resolve()}/b.yml -> {tmp_path.resolve()}/a.yml"


def test_self_include_and_text_self_reference(tmp_path):
    (tmp_path / "a.yml").write_text("me: file:a.yml\n")
    with pytest.raises(IncludeCycleError):
        Config(tmp_path / "a.yml")


def test_cycle_through_different_relative_spellings(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.yml").write_text("b: file:sub/b.yml\n")
    (tmp_path / "sub/b.yml").write_text("a: file:../sub/../a.yml\n")
    with pytest.raises(IncludeCycleError):
        Config(tmp_path / "a.yml")


def test_diamond_is_not_a_cycle_but_writes_affect_both_sites(tmp_path):
    (tmp_path / "root.yml").write_text("x: file:shared.yml\ny: file:shared.yml\n")
    (tmp_path / "shared.yml").write_text("v: 1\n")
    cfg = Config(tmp_path / "root.yml")
    assert cfg.get("x.v") == cfg.get("y.v") == 1
    cfg.set("x.v", 2)
    assert cfg.get("y.v") == 2  # one file, two mount points: a set via one path changes the other
