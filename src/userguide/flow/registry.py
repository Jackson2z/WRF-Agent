"""flow.registry — overlapping discussion perspectives and context slices.

The registry turns "who is allowed to decide what" from a prompt convention into data:

    specialists.yaml   specialist id, the design fields it owns, the slices it reads, the tools it may call
    field_map.yaml     the authoritative class of every parameter (design / derived / runtime)

Three invariants are checked here and asserted by tests:

    coverage    every design field can be discussed by specialists (overlap is intentional)
    drill       every ownership pattern matches at least one design field (no dead specialist)
    whitelist   a specialist may only use tools named in its own list

``check`` is advisory for discussion contexts, not a write authorization boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
SPECIALISTS = HERE / "specialists.yaml"
FIELD_MAP = HERE.parent / "field_map.yaml"


class RegistryError(ValueError):
    """The specialist layer and the field map disagree."""


@dataclass(frozen=True)
class Specialist:
    id: str
    title: str
    summary: str = ""
    owns: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    tools: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "summary": self.summary,
                "owns": list(self.owns), "reads": list(self.reads), "tools": list(self.tools)}


class Registry:
    def __init__(self, field_map: Path | None = None, specialists: Path | None = None):
        self.field_map_path = Path(field_map or FIELD_MAP)
        self.specialists_path = Path(specialists or SPECIALISTS)
        fm = yaml.safe_load(self.field_map_path.read_text(encoding="utf-8")) or {}
        self._fields = fm.get("fields") or {}
        raw = yaml.safe_load(self.specialists_path.read_text(encoding="utf-8")) or {}
        self.specialists = tuple(
            Specialist(id=str(r["id"]), title=str(r.get("title", r["id"])), summary=str(r.get("summary", "")),
                       owns=tuple(str(x) for x in r.get("owns") or ()),
                       reads=tuple(str(x) for x in r.get("reads") or ()),
                       tools=tuple(str(x) for x in r.get("tools") or ()))
            for r in raw.get("specialists") or ())
        self.by_id = {s.id: s for s in self.specialists}

    # ------------------------------------------------------------------ fields

    def design_ids(self) -> list[str]:
        return [k for k, v in self._fields.items() if isinstance(v, dict) and v.get("class") == "design"]

    def all_ids(self) -> list[str]:
        return sorted(self._fields)

    def __getitem__(self, sid: str) -> Specialist:
        try:
            return self.by_id[sid]
        except KeyError:
            raise RegistryError(f"unknown specialist {sid!r} (known: {sorted(self.by_id)})") from None

    # ------------------------------------------------------------------ ownership

    def owners(self, fid: str) -> tuple[str, ...]:
        return tuple(s.id for s in self.specialists if any(fnmatch(fid, pat) for pat in s.owns))

    def owned(self, sid: str) -> list[str]:
        st = self[sid]
        return [f for f in self.design_ids() if any(fnmatch(f, pat) for pat in st.owns)]

    def check(self, sid: str, fields) -> dict:
        """-> {accepted, rejected: {field: [owners]}} — the tool-layer guard for a specialist's proposal."""
        st = self[sid]
        fields = list(fields)
        rejected = {f: list(self.owners(f)) for f in fields if not any(fnmatch(f, pat) for pat in st.owns)}
        return {"specialist": sid, "accepted": not rejected, "rejected": rejected,
                "allowed": sorted(f for f in fields if f not in rejected)}

    def partition(self) -> dict:
        """Coverage audit; overlap is expected for independent scientific review."""
        design = self.design_ids()
        unowned = [f for f in design if not self.owners(f)]
        shared = {f: list(self.owners(f)) for f in design if len(self.owners(f)) > 1}
        dead = []
        for s in self.specialists:
            for pat in s.owns:
                if not any(fnmatch(f, pat) for f in design):
                    dead.append(f"{s.id}:{pat}")
        return {"design_fields": len(design), "unowned": unowned, "shared": shared, "dead_patterns": dead,
                "ok": not unowned and not dead}

    def assert_partition(self) -> None:
        p = self.partition()
        if not p["ok"]:
            raise RegistryError(f"specialist discussion coverage incomplete: {p}")

    # ------------------------------------------------------------------ tools / slices

    def tools(self, sid: str) -> tuple[str, ...]:
        return self[sid].tools

    def can_use(self, sid: str, tool: str) -> bool:
        return tool in self[sid].tools

    def slice_for(self, sid: str) -> list[str]:
        """The registry slice injected into a specialist's context: what it owns plus what it reads."""
        return sorted(set(self.owned(sid)) | {f for f in self[sid].reads if f in self._fields})

    def context(self, sid: str) -> dict:
        st = self[sid]
        return {"specialist": st.as_dict(), "fields": self.slice_for(sid), "tools": list(st.tools)}
