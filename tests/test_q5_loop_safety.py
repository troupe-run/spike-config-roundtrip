"""Q5: a CLI set's write-back is seen by the watcher and diffs to ZERO events; a hand edit yields
exactly the expected events. No echo-suppression flags anywhere."""
import threading
import time

import pytest
from spike_config_roundtrip.service import ConfigService


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def svc(root):
    s = ConfigService(root)
    stop, ready = threading.Event(), threading.Event()
    t = threading.Thread(target=s.watch_forever, args=(stop, ready), daemon=True)
    t.start()
    assert ready.wait(10), "watcher never became ready"
    yield s
    stop.set()
    t.join(5)
    assert not s.errors, s.errors


def settle(svc, quiet=0.6):
    """Wait until no new watcher batch has arrived for `quiet` seconds."""
    last = -1
    while len(svc.observed) != last:
        last = len(svc.observed)
        time.sleep(quiet)


def summary(evs):
    return [(type(e).__name__, e.path, e.old, getattr(e, "new", None), e.source) for e in evs]


def test_initial_import_emits_one_event_per_leaf(root):
    s = ConfigService(root)
    assert len(s.events()) == len(s.projection()) == 10
    assert {e.source for e in s.events()} == {"import"}


def test_cli_set_writeback_is_observed_and_produces_zero_events(svc, root):
    n0 = len(svc.events())
    emitted = svc.cli_set("agents.coder.limits.max_tokens", 16000)
    assert emitted == 1
    assert summary(svc.events()[n0:]) == [
        ("ConfigSet", "agents.coder.limits.max_tokens", 8000, 16000, "cli")
    ]
    limits = str((root.parent / "agents/limits.yaml").resolve())
    # the watcher DID see the write-back ...
    assert wait_for(lambda: any(any(p.endswith("limits.yaml") for p in paths) for paths, _ in svc.observed)), svc.observed
    settle(svc)
    # ... and every batch it processed diffed to nothing
    print("watcher batches:", svc.observed)
    assert all(n == 0 for _, n in svc.observed)
    assert len(svc.events()) == n0 + 1


def test_text_include_cli_set_also_idempotent(svc):
    n0 = len(svc.events())
    svc.cli_set("agents.coder.system_prompt", "# Coder\n\nNew prompt.\n")
    assert wait_for(lambda: any(any(p.endswith("coder.md") for p in paths) for paths, _ in svc.observed))
    settle(svc)
    assert all(n == 0 for _, n in svc.observed)
    assert len(svc.events()) == n0 + 1


def test_hand_edit_produces_exactly_the_expected_events(svc, root):
    n0 = len(svc.events())
    limits = root.parent / "agents/limits.yaml"
    limits.write_text(limits.read_text().replace("8000", "12000").replace("timeout_s: 30\n", "") + "burst: 2\n")
    prompt = root.parent / "prompts/coder.md"
    prompt.write_text(prompt.read_text() + "Always add tests.\n")
    # a formatting-only edit (comment + whitespace) must produce NO events
    main = root
    main.write_text(main.read_text().replace("retries: 3", "retries:   3   # tuned"))
    assert wait_for(lambda: len(svc.events()) >= n0 + 4)
    settle(svc)
    got = sorted(summary(svc.events()[n0:]), key=lambda t: t[1])
    print(got, svc.observed)
    assert got == [
        ("ConfigSet", "agents.coder.limits.burst", None, 2, "file-watch"),
        ("ConfigSet", "agents.coder.limits.max_tokens", 8000, 12000, "file-watch"),
        ("ConfigRemoved", "agents.coder.limits.timeout_s", 30, None, "file-watch"),
        ("ConfigSet", "agents.coder.system_prompt",
         "# Coder\n\nYou write code. Keep it simple.\n",
         "# Coder\n\nYou write code. Keep it simple.\nAlways add tests.\n", "file-watch"),
    ]


def test_rejected_set_emits_nothing(root):
    from spike_config_roundtrip.config import ConfigError
    s = ConfigService(root)
    n0 = len(s.events())
    with pytest.raises(ConfigError):
        s.cli_set("agents.coder.system_prompt.x", 1)
    assert len(s.events()) == n0


def test_cli_set_refuses_to_clobber_a_concurrent_hand_edit(root):
    """Content diffing prevents echo loops but NOT lost updates. The write-back does a
    compare-and-swap on the file text it loaded."""
    from spike_config_roundtrip.config import Config, ConcurrentEditError
    cfg = Config(root)
    limits = root.parent / "agents/limits.yaml"
    limits.write_text(limits.read_text().replace("30", "45"))  # hand edit after load
    with pytest.raises(ConcurrentEditError):
        cfg.set("agents.coder.limits.max_tokens", 1)
    assert "timeout_s: 45" in limits.read_text() and "8000" in limits.read_text()
