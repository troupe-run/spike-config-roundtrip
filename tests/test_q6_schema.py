"""Q6: schema-guided include detection + !literal escape hatch."""
import jsonschema
import pytest
from conftest import diff, snapshot
from spike_config_roundtrip.config import Config

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "project": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "homepage": {"type": "string", "format": "uri"},  # declared literal URL
                "template": {"type": "string", "format": "uri"},  # file:-shaped, still literal
            },
        },
        "agents": {"type": "object", "additionalProperties": {"type": "object"}},
        "extras": {},  # untyped: anything goes
    },
}


@pytest.fixture
def schema_root(root):
    (root.parent / "notes.md").write_text("Some notes.\n")
    root.write_text(
        root.read_text().replace(
            "  owner: martin\n",
            "  owner: martin\n"
            "  homepage: https://example.invalid/troupe.yaml\n"
            "  template: file:agents/limits.yaml   # would include if untyped\n",
        )
        + "extras:\n"
        "  notes: file:notes.md\n"
        '  upstream: !literal "https://example.invalid/upstream.yaml"  # escape hatch\n'
    )
    return root


def test_format_uri_stays_literal_untyped_url_is_included(schema_root):
    cfg = Config(schema_root, schema=SCHEMA)
    assert cfg.get("project.homepage") == "https://example.invalid/troupe.yaml"  # not fetched (host doesn't exist)
    assert cfg.get("project.template") == "file:agents/limits.yaml"  # would resolve fine, but schema says literal
    assert cfg.get("extras.notes") == "Some notes.\n"  # untyped + URL-shaped -> included
    assert cfg.get("extras.upstream") == "https://example.invalid/upstream.yaml"  # !literal -> not fetched
    assert cfg.get("agents.coder.model") == "opus"  # untyped-by-format include still works


def test_without_schema_the_same_file_is_included(schema_root):
    """Control: proves it's the schema doing the work, not something else."""
    txt = schema_root.read_text().replace("  homepage: https://example.invalid/troupe.yaml\n", "")
    schema_root.write_text(txt)
    cfg = Config(schema_root)  # no schema
    assert cfg.get("project.template") == {"max_tokens": 8000, "timeout_s": 30}


def test_merged_view_validates(schema_root):
    cfg = Config(schema_root, schema=SCHEMA)
    jsonschema.validate(cfg.merged, SCHEMA, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)
    bad = dict(cfg.merged, agents={"coder": "not-an-object"})
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMA)


def test_literal_tag_round_trips(project, schema_root):
    cfg = Config(schema_root, schema=SCHEMA)
    before = snapshot(project)
    cfg.set("extras.more", 1)  # dump path: whole root file re-serialised
    cfg.set("extras.upstream", "https://example.invalid/v2.yaml")  # splice path on the tagged scalar
    d = diff(before, snapshot(project))
    print(d)
    after = schema_root.read_text()
    assert '  upstream: !literal "https://example.invalid/v2.yaml"  # escape hatch\n' in after
    assert cfg.get("extras.upstream") == "https://example.invalid/v2.yaml"
    assert cfg.where("extras.upstream").kind == "yaml"


def test_cli_set_of_url_string_into_untyped_field_writes_literal(schema_root):
    """Without this, `set extras.link https://...` would silently become an include (and here, a
    fetch failure leaving a config that no longer loads)."""
    cfg = Config(schema_root, schema=SCHEMA)
    cfg.set("extras.link", "https://example.invalid/x.yaml")
    assert '  link: !literal "https://example.invalid/x.yaml"\n' in schema_root.read_text()
    assert cfg.get("extras.link") == "https://example.invalid/x.yaml"
    # schema-declared uri fields don't need the tag
    cfg.set("project.homepage", "https://example.invalid/new")
    assert "  homepage: https://example.invalid/new\n" in schema_root.read_text()


def test_explicit_include_that_fails_to_load_is_rolled_back(schema_root):
    cfg = Config(schema_root, schema=SCHEMA)
    before = schema_root.read_text()
    with pytest.raises(Exception):
        cfg.set("extras.link", "https://example.invalid/x.yaml", include=True)
    assert schema_root.read_text() == before
