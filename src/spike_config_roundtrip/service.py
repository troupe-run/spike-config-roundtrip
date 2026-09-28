"""Spike: event-sourced config projection + bidirectional file sync via content diffing.

Loop safety comes from ONE rule: every path into the projection (CLI set, file watcher, startup)
goes through `reconcile()`, which diffs the files' current merged content against the projection and
emits only the differences. A write-back from a CLI set therefore diffs to nothing when the watcher
sees it. No echo-suppression flags, no "ignore next event for file X".
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from eventsourcing.application import Application
from eventsourcing.domain import Aggregate, event
from watchfiles import watch

from .config import Config

_MISSING = object()


class ProjectConfig(Aggregate):
    @event("Created")
    def __init__(self, project: str):
        self.project = project
        self.values: dict[str, Any] = {}  # flat dotted path -> leaf value

    @event("ConfigSet")
    def config_set(self, path: str, old: Any, new: Any, source: str):
        self.values[path] = new

    @event("ConfigRemoved")
    def config_removed(self, path: str, old: Any, source: str):
        self.values.pop(path, None)


class ConfigApp(Application):  # default POPO (in-memory) persistence
    pass


def diff_flat(before: dict[str, Any], after: dict[str, Any]):
    for k, v in after.items():
        old = before.get(k, _MISSING)
        if old is _MISSING or old != v or type(old) is not type(v):
            yield ("set", k, None if old is _MISSING else old, v)
    for k in before.keys() - after.keys():
        yield ("removed", k, before[k], None)


class ConfigService:
    def __init__(self, root: str | Path, schema: dict | None = None):
        self.root = Path(root).resolve()
        self.schema = schema
        self.app = ConfigApp()
        agg = ProjectConfig(project=str(self.root.parent.parent))
        self.app.save(agg)
        self.id = agg.id
        self.lock = threading.RLock()
        self.observed: list[tuple[set[str], int]] = []  # watcher batches: (changed paths, events emitted)
        self.errors: list[Exception] = []
        self.reconcile(source="import")

    # --- the single entry point into the projection -----------------------------------

    def reconcile(self, source: str) -> int:
        with self.lock:
            cfg = Config(self.root, self.schema)
            agg: ProjectConfig = self.app.repository.get(self.id)
            n = 0
            for kind, path, old, new in diff_flat(agg.values, cfg.flat()):
                if kind == "set":
                    agg.config_set(path=path, old=old, new=new, source=source)
                else:
                    agg.config_removed(path=path, old=old, source=source)
                n += 1
            self.app.save(agg)
            return n

    # --- CLI side ----------------------------------------------------------------------

    def cli_set(self, path: str, value: Any) -> int:
        with self.lock:
            cfg = Config(self.root, self.schema)
            cfg.plan_set(path, value)  # validate first: remote/read-only etc raise here, nothing emitted
            cfg.set(path, value)  # write back into the owning file
            return self.reconcile(source="cli")  # projection catches up with exactly the change

    # --- watcher side ------------------------------------------------------------------

    def events(self):
        return list(self.app.repository.event_store.get(self.id))[1:]  # skip Created

    def projection(self) -> dict[str, Any]:
        return dict(self.app.repository.get(self.id).values)

    def watch_forever(self, stop: threading.Event, ready: threading.Event) -> None:
        sentinel = self.root.parent / ".watch-ready"
        try:
            for changes in watch(self.root.parent, stop_event=stop, debounce=100, step=20,
                                 yield_on_timeout=True, rust_timeout=200):
                if not ready.is_set():
                    sentinel.write_text("x")  # prove the watcher is live before tests act
                    if any(Path(p).name == ".watch-ready" for _, p in changes):
                        ready.set()
                    continue
                paths = {p for _, p in changes if ".troupe-tmp-" not in p and not p.endswith(".watch-ready")}
                if not paths:
                    continue
                n = self.reconcile(source="file-watch")
                self.observed.append((paths, n))
        except Exception as e:  # surface to the test
            self.errors.append(e)
