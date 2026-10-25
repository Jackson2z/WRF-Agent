"""parse — parse the upstream snapshot into the full field set registry_fields.yaml (machine-generated, never
hand-written).

Sources, each used for what it is reliable for:
  * Registry rconfig (locked to the tag): the full namelist.input field set, types, defaults, group, per-domain
  * Users' Guide tables (narrative layer): plain-language description + options (=N : text), source anchor
  * README.namelist (locked to the tag): further descriptions and options (fallback when the Guide has none)
  * WPS: namelist.wps.all_options gives the field set and groups; the Guide's wps.html tables give descriptions

No field is dropped silently: names that appear in the Guide/README but not in the Registry/WPS set are recorded
in ``orphans``.
"""

from __future__ import annotations

import html as _html
import re
import shlex
from pathlib import Path

import yaml

UG_BASE = "https://www2.mmm.ucar.edu/wrf/users/wrf_users_guide/build/html/"

# --------------------------------------------------------------------------- Registry


def _registry_lines(reg_dir: Path, entry: str, seen: set[str] | None = None):
    seen = seen if seen is not None else set()
    if entry in seen:
        return
    seen.add(entry)
    path = reg_dir / entry
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("include "):
            yield from _registry_lines(reg_dir, line.split()[1], seen)
        else:
            yield entry, line


def parse_registry(reg_dir: Path, entry: str = "Registry.EM") -> dict[str, dict]:
    """rconfig <type> <name> <how> <nentries> <default> <io> "<dname>" "<descrip>" "<units>" """
    out: dict[str, dict] = {}
    for src, line in _registry_lines(reg_dir, entry):
        if not line.startswith("rconfig"):
            continue
        try:
            tok = shlex.split(line, comments=True, posix=True)
        except ValueError:
            tok = line.split()
        if len(tok) < 6:
            continue
        _, typ, name, how, nent, default = tok[:6]
        if not how.startswith("namelist,"):
            continue
        block = how.split(",", 1)[1]
        descrip = tok[8] if len(tok) > 8 else ""
        units = tok[9] if len(tok) > 9 else ""
        out[name.lower()] = {
            "name": name.lower(),
            "file": "namelist.input",
            "block": block,
            "type": typ,
            "default": default,
            "per_domain": nent == "max_domains",
            "nentries": nent,
            "registry": {"file": src, "descrip": descrip, "units": units},
        }
    return out


# --------------------------------------------------------------------------- compiled build

_NL_STMT = re.compile(r"NAMELIST\s*/\s*([a-z_0-9]+)\s*/\s*([a-z_0-9]+)", re.I)

# Registry files that belong to the core model; any other file that contributes rconfig entries is an extension
# package (fire, chem, afwa, sbm, noahmp, ...). Names come from the Registry include tree, not from a hand list.
CORE_REGISTRY = {"Registry.EM_COMMON", "registry.dimspec", "registry.em_shared_collection", "registry.io_boilerplate"}


def parse_build_namelist(path: Path) -> dict[str, str]:
    """inc/namelist_statements.inc of a compiled WRF: every namelist variable this wrf.exe accepts -> its group."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return {var.lower(): grp.lower() for grp, var in _NL_STMT.findall(text)}


def _doc_mentions(doc_dir: Path) -> dict[str, list[str]]:
    """Which doc/README.* files of the source tree mention a variable (whole word)."""
    out: dict[str, list[str]] = {}
    if not doc_dir.is_dir():
        return out
    for f in sorted(doc_dir.iterdir()):
        if not f.is_file():
            continue
        for w in set(re.findall(r"\b[a-z][a-z0-9_]{2,}\b", f.read_text(encoding="latin-1", errors="replace").lower())):
            out.setdefault(w, []).append(f"doc/{f.name}")
    return out


def _sha256(p: Path) -> str | None:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


# --------------------------------------------------------------------------- WPS

_NL_VAR = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*,?\s*$")


def parse_namelist_text(path: Path, file_label: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    block = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if s.startswith("&"):
            block = s[1:].split()[0]
            continue
        if s.startswith("/") or s.startswith("!") or not s:
            continue
        m = _NL_VAR.match(s.split("!", 1)[0])
        if m and block and m.group(1).lower() not in out:
            vals = [v.strip() for v in m.group(2).split(",") if v.strip()]
            out[m.group(1).lower()] = {
                "name": m.group(1).lower(),
                "file": file_label,
                "block": block,
                "type": None,
                "default": vals[0] if vals else None,
                "per_domain": len(vals) > 1,
                "nentries": None,
                "registry": None,
            }
    return out


# --------------------------------------------------------------------------- Users' Guide tables

_SECTION = re.compile(r'<section id="([^"]+)"')
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_OPT = re.compile(r"^\s*=\s*([^:]+?)\s*:\s*(.+)$")


def _clean(cell: str) -> str:
    cell = re.sub(r"<br\s*/?>", "\n", cell)
    cell = re.sub(r"<[^>]+>", "", cell)
    return _html.unescape(cell).strip()


def _split_desc(text: str) -> tuple[str, list[dict]]:
    desc, opts = [], []
    for part in [p.strip() for p in text.split("\n")]:
        if not part:
            continue
        m = _OPT.match(part)
        if m:
            opts.append({"value": m.group(1).strip(), "label": m.group(2).strip()})
        elif opts:
            opts[-1]["label"] += " " + part
        else:
            desc.append(part)
    return " ".join(desc), opts


_ENTRIES = re.compile(r"^(max_dom|single entry|[^.;]{0,30}\bdom(ain)?s?\b[^.;]{0,15})$", re.I)


def _assign_cells(cells: list[str]) -> tuple[str | None, str, str | None]:
    """Guide tables have no header and a varying column count: first take the entries cell (short cells such as
    max_dom / single entry);
    of the remaining cells the longest is the description and the other non-empty short cells are the default.
    Returns (default, desc, entries)."""
    rest = [c for c in cells[1:]]
    entries = None
    for i in range(len(rest) - 1, -1, -1):
        if rest[i] and _ENTRIES.match(rest[i]):
            entries = rest.pop(i)
            break
    rest = [r for r in rest if r]
    if not rest:
        return None, "", entries
    di = max(range(len(rest)), key=lambda i: len(rest[i]))
    desc = rest[di]
    others = [r for i, r in enumerate(rest) if i != di]
    return (others[0] if others else None), desc, entries


def parse_ug_tables(page_path: Path, section_filter: str | None = None) -> dict[str, dict]:
    t = page_path.read_text(encoding="utf-8", errors="replace")
    sections = [(m.start(), m.group(1)) for m in _SECTION.finditer(t)]
    out: dict[str, dict] = {}
    for m in _ROW.finditer(t):
        cells = [_clean(c) for c in _CELL.findall(m.group(1))]
        if len(cells) < 3 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\s*\(.*\))?", cells[0] or "-"):
            continue
        name = cells[0].split("(")[0].strip().lower()
        sec = ""
        for pos, sid in sections:
            if pos < m.start():
                sec = sid
            else:
                break
        if section_filter and not re.search(section_filter, sec):
            continue
        default, desc_raw, entries = _assign_cells(cells)
        desc, opts = _split_desc(desc_raw)
        rec = {
            "page": page_path.name,
            "section": sec,
            "url": f"{UG_BASE}{page_path.name}#{sec}",
            "default": default,
            "desc": desc,
            "options": opts,
            "entries": entries,
        }
        out.setdefault(name, rec)  # first occurrence wins (a page may repeat a name in several sections)
    return out


# --------------------------------------------------------------------------- README.namelist

_RM_VAR = re.compile(r"^ ([A-Za-z_][A-Za-z0-9_]*)\s*(\([^)]*\))?\s*(.*)$")
_RM_OPT = re.compile(r"^\s*=\s*([^,:!]+?)\s*[,:]\s*!?\s*(.*)$")


def parse_readme(path: Path, known: set[str]) -> dict[str, dict]:
    """Accept only field names known to the Registry as variable lines (filters out Note: and similar lines)."""
    out: dict[str, dict] = {}
    cur = None
    block = None
    for no, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        s = raw.rstrip()
        if re.match(r"^\s*&\w+", s):
            block = s.strip()[1:].split()[0].rstrip(":")
            cur = None
            continue
        m = _RM_VAR.match(s)
        if m and m.group(1).lower() in known:
            name = m.group(1).lower()
            rest = m.group(3).strip()
            default, desc = None, rest
            if rest.startswith("="):
                body = rest[1:]
                default, _, desc = body.partition("!")
                default = default.strip().rstrip(",").strip()
            cur = out.setdefault(name, {"line": no, "block": block, "default": default,
                                        "desc": desc.strip(), "options": []})
            continue
        if cur is None or not s.strip():
            if not s.strip():
                cur = cur  # a blank line does not end the entry (README often has blank lines between options)
            continue
        mo = _RM_OPT.match(s)
        if mo:
            cur["options"].append({"value": mo.group(1).strip(), "label": mo.group(2).strip()})
        elif s.startswith("      "):
            if cur["options"]:
                cur["options"][-1]["label"] += " " + s.strip()
            else:
                cur["desc"] = (cur["desc"] + " " + s.strip()).strip()
        else:
            cur = None
    return out


# --------------------------------------------------------------------------- assemble


def build(snapshot: Path, build_dir: Path | None = None) -> dict:
    """Generate the parameter registry from files, and say which files.

    Layers. The floor is the documentation shipped *inside* the pristine source of this version
    (run/README.namelist, version-locked); the online Users' Guide is newer and is used for wording only.
      base       documented in the source's run/README.namelist
      core       in the core Registry files but not in the Guide
      extension  contributed by an extension package's Registry file (fire, chem, afwa, ...)
      build      accepted by a specific compiled wrf.exe only (e.g. Registry-generated aux stream variables);
                 present only when a build profile is given
    """
    profile = yaml.safe_load((build_dir / "build_profile.yaml").read_text(encoding="utf-8")) if build_dir else {}
    entry = (profile or {}).get("registry_entry", "Registry.EM")
    reg = parse_registry(snapshot / "Registry", entry)
    wps = parse_namelist_text(snapshot / "wps" / "namelist.wps.all_options", "namelist.wps")
    ug_dir = snapshot / "users_guide"
    ug_wrf = parse_ug_tables(ug_dir / "namelist_variables.html") if (ug_dir / "namelist_variables.html").exists() else {}
    ug_wps = parse_ug_tables(ug_dir / "wps.html", r"^(share|geogrid|ungrib|metgrid)-section$") if (ug_dir / "wps.html").exists() else {}
    readme = parse_readme(snapshot / "run" / "README.namelist", set(reg))
    doc_mentions = _doc_mentions(snapshot / "doc")

    built: dict[str, str] = {}
    if build_dir:
        built = parse_build_namelist(build_dir / profile["namelist_statements"]["file"])
        for name, grp in built.items():          # accepted by this wrf.exe but not declared as an rconfig line
            if name not in reg:
                reg[name] = {"name": name, "file": "namelist.input", "block": grp, "type": None, "default": None,
                             "per_domain": None, "nentries": None,
                             "registry": {"file": "(generated at compile time)", "descrip": "", "units": ""}}

    fields: list[dict] = []
    for name, rec in sorted(reg.items(), key=lambda kv: (kv[1]["block"], kv[0])):
        rec = dict(rec)
        rec["ug"] = ug_wrf.get(name)
        rec["readme"] = readme.get(name)
        rfile = rec["registry"]["file"]
        rec["source_docs"] = doc_mentions.get(name, [])
        if rec["readme"]:
            rec["layer"], rec["provided_by"] = "base", "run/README.namelist"
        elif rfile == "(generated at compile time)":
            rec["layer"], rec["provided_by"] = "build", f"{profile.get('label')}: inc/namelist_statements.inc"
        elif rfile in CORE_REGISTRY or rfile == entry:
            rec["layer"], rec["provided_by"] = "core", rfile
        else:
            rec["layer"], rec["provided_by"] = "extension", rfile
        if built and name not in built:
            rec["layer"] = "not-in-build"            # declared, but this wrf.exe does not accept it
        fields.append(rec)
    for name, rec in sorted(wps.items(), key=lambda kv: (kv[1]["block"], kv[0])):
        rec = dict(rec)
        rec["ug"] = ug_wps.get(name)
        rec["readme"] = None
        rec["layer"], rec["provided_by"] = "base", "WPS namelist.wps.all_options (shipped with the WPS source)"
        fields.append(rec)

    orphans = {
        "ug_wrf_not_in_registry": sorted(set(ug_wrf) - set(reg)),
        "ug_wps_not_in_all_options": sorted(set(ug_wps) - set(wps)),
    }
    stats = {
        "namelist_input_fields": len(reg),
        "namelist_wps_fields": len(wps),
        "with_ug_desc": sum(1 for f in fields if f["ug"]),
        "with_readme_desc": sum(1 for f in fields if f["readme"]),
        "with_options": sum(1 for f in fields if (f["ug"] or {}).get("options") or (f["readme"] or {}).get("options")),
        "undocumented": sum(1 for f in fields if not f["ug"] and not f["readme"]),
    }
    if built:   # the floor claim, checked: every Users' Guide variable must be accepted by the build
        stats["ug_missing_from_build"] = sorted(n for n in ug_wrf if n not in built)   # online Guide is newer
        stats["readme_missing_from_build"] = sorted(n for n in readme if n not in built)
    stats["by_layer"] = dict(sorted(__import__("collections").Counter(f["layer"] for f in fields).items()))
    stats["extension_files"] = dict(sorted(__import__("collections").Counter(
        f["provided_by"] for f in fields if f["layer"] == "extension").items()))
    inputs = [{"file": f"{snapshot.name}/Registry/{entry} (+ includes)", "role": "rconfig declarations"},
              {"file": f"{snapshot.name}/run/README.namelist", "role": "base layer (version-locked, identical to the pristine 4.6.1 source)", "sha256": _sha256(snapshot / "run" / "README.namelist")},
              {"file": f"{snapshot.name}/doc/README.*", "role": "package documentation shipped with the source"},
              {"file": f"{snapshot.name}/users_guide/namelist_variables.html", "role": "wording only (online, not version-locked)",
               "sha256": _sha256(ug_dir / "namelist_variables.html")},
              {"file": f"{snapshot.name}/wps/namelist.wps.all_options", "role": "WPS variables"}]
    if build_dir:
        inputs.append({"file": f"{snapshot.name}/builds/{build_dir.name}/{profile['namelist_statements']['file']}",
                       "role": "variables this wrf.exe accepts",
                       "sha256": _sha256(build_dir / profile["namelist_statements"]["file"]), "build": profile.get("label")})
    return {"generated_from": inputs, "manifest": f"{snapshot.name}/MANIFEST.yaml",
            "regenerate": "python3 -m src.userguide parse" + (f" --build {profile.get('label')}" if build_dir else ""),
            "registry_entry": entry, "build": profile.get("label") if build_dir else None,
            "stats": stats, "orphans": orphans, "fields": fields}


def write(snapshot: Path, out: Path, build_dir: Path | None = None) -> dict:
    data = build(snapshot, build_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = ("# GENERATED — do not edit. Inputs and how to regenerate are listed under generated_from / regenerate.\n"
              "# Base layer = parameters documented in run/README.namelist of the pristine source of this version;\n"
              "# everything else is core / extension (which Registry file) / build (accepted only by the named wrf.exe).\n")
    out.write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    return data["stats"]
