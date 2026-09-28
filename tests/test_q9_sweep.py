"""Q9 sweep: every scalar leaf, every mapping (add key) and every sequence (append) in every
corpus file, set to a battery of awkward values. Each must splice AND pass source-level
verification (target == value, every other node byte-identical in kind/tag/style/raw value)."""
from q9_corpus import CASES
from ruamel.yaml.nodes import MappingNode, ScalarNode, SequenceNode
from spike_config_roundtrip.surgical import RejectedSet, SpliceError, index, surgical_set

VALUES = ["changed", 42, "a: b", "multi\nline\n", "", "*x", "with 'quote' and \"dq\"", True, "ü ✓",
          {"k": 1}, [1, "two"]]


def sweep():
    tally = {"spliced": 0, "rejected": 0, "failed": []}
    for c in CASES:
        if c.expect == "error":
            continue
        refs = index(c.before)
        for p, r in refs.items():
            targets = []
            if p:
                targets.append(p)  # replace: scalar leaves AND whole collections
            if isinstance(r.node, MappingNode):
                targets.append((*p, "zz_new"))
            if isinstance(r.node, SequenceNode):
                targets.append((*p, len(r.node.value)))
            for t in targets:
                for v in VALUES:
                    try:
                        surgical_set(c.before, t, v)
                        tally["spliced"] += 1
                    except RejectedSet:
                        tally["rejected"] += 1
                    except SpliceError as e:
                        tally["failed"].append((c.name, t, v, str(e)[:120]))
    return tally


def test_every_leaf_add_and_append_in_corpus_splices():
    t = sweep()
    print(f"spliced={t['spliced']} rejected={t['rejected']} failed={len(t['failed'])}")
    for f in t["failed"][:20]:
        print(f)
    assert t["spliced"] > 2000
    assert t["failed"] == []
