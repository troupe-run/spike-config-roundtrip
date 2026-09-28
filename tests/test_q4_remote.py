"""Q4: remote includes load; any set that would write into them is rejected clearly."""
import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import snapshot
from spike_config_roundtrip.config import Config, ReadOnlyIncludeError


@pytest.fixture
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("remote")
    (d / "shared.yaml").write_text("# shared org defaults\nmodel: opus\nstyle: file:style.md\n")
    (d / "style.md").write_text("Be terse.\n")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(d))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture
def remote_root(root, server):
    root.write_text(root.read_text() + f"shared: {server}/shared.yaml  # org-wide\n")
    return root


def test_remote_include_loads_with_provenance(remote_root, server):
    cfg = Config(remote_root)
    assert cfg.get("shared.model") == "opus"
    assert cfg.where("shared.model").source == f"{server}/shared.yaml"
    assert cfg.where("shared.model").remote
    # relative include inside a remote file resolves against the URL, and is remote too
    assert cfg.get("shared.style") == "Be terse.\n"
    assert cfg.where("shared.style").source == f"{server}/style.md"


@pytest.mark.parametrize("path", ["shared.model", "shared.style", "shared.new_key"])
def test_set_targeting_remote_is_rejected_and_nothing_is_written(project, remote_root, server, path):
    cfg = Config(remote_root)
    before = snapshot(project)
    with pytest.raises(ReadOnlyIncludeError) as e:
        cfg.set(path, "x")
    msg = str(e.value)
    print(msg)
    assert "read-only" in msg and server in msg and "'shared'" in msg
    assert snapshot(project) == before


def test_plan_set_rejects_before_any_side_effect(remote_root):
    """plan_set is the validation step the CLI runs BEFORE emitting a ConfigSet event (see Q5)."""
    cfg = Config(remote_root)
    with pytest.raises(ReadOnlyIncludeError):
        cfg.plan_set("shared.model", "sonnet")
