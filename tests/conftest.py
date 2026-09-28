import difflib
import textwrap
from pathlib import Path

import pytest

FILES = {
    ".troupe/troupe.yml": """\
        # Troupe project config
        project:
          name: "demo"   # quoted on purpose
          owner: martin
        agents:
          coder: file:agents/coder.yaml
          reviewer:
            model: sonnet  # cheap reviewer
        settings:
          retries: 3
        """,
    ".troupe/agents/coder.yaml": """\
        # coder agent
        model: 'opus'
        tools:
          - bash
          - edit
        system_prompt: file:../prompts/coder.md
        limits: file:limits.yaml   # nested include
        """,
    ".troupe/agents/limits.yaml": """\
        max_tokens: 8000  # hard cap
        timeout_s: 30
        """,
    ".troupe/prompts/coder.md": """\
        # Coder

        You write code. Keep it simple.
        """,
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    for rel, body in FILES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body))
    return tmp_path


@pytest.fixture
def root(project: Path) -> Path:
    return project / ".troupe" / "troupe.yml"


def snapshot(project: Path) -> dict[str, str]:
    return {str(p.relative_to(project)): p.read_text() for p in sorted(project.rglob("*")) if p.is_file()}


def diff(before: dict[str, str], after: dict[str, str]) -> str:
    out = []
    for name in sorted(set(before) | set(after)):
        a, b = before.get(name, ""), after.get(name, "")
        if a != b:
            out += difflib.unified_diff(a.splitlines(True), b.splitlines(True), f"a/{name}", f"b/{name}")
    return "".join(out)
