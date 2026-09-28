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
    after, strategies = run_case(tmp_path, c)
    assert all(s == c.expect for s, _ in strategies), strategies
    assert after == c.after
