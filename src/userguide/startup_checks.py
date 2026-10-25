"""startup_checks — run WRF's own start-up checks against what the generator would write, before anything is run.

The ledger (references/upstream/wrf-<v>/check_a_mundo.json, extracted from share/module_check_a_mundo.F) lists every
refusal and every value rewrite with
  chain   the exact IF-branch condition (earlier ELSE IF / ELSE siblings negated)
  loops   the enclosing DO loops with their start and end expressions
  cpp     the enclosing preprocessor branches (#if / #ifdef / #elif / #else)
This module evaluates them in source order on the namelist the generator emits (falling back to the Registry default
of the recorded build for anything the namelist does not set), applying each rewrite it can decide so a later check
sees the value wrf.exe would see. Preprocessor branches are decided from the recorded build profile.

Outcome per statement:
  fires        the condition holds (on the listed domains); a rewrite is reported only if it changes the value
  silent       the condition does not hold, or the statement is compiled out for this build
  undecidable  the condition needs something only known at run time (input files, local flags, MPI state,
               intrinsic functions); counted, never guessed

Stated limits: only the extracted statements are modelled (other local assignments are not), subroutines are taken
in source order, and the build macros not recorded in the profile take the standard configure values listed in
CORE_MACROS.
"""

from __future__ import annotations

import ast
import copy
import functools
import itertools
import json
import re
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
REGISTRY_FIELDS = REPO / "build" / "userguide" / "registry_fields.yaml"

# configure-time macros of an ARW (em_real) build that build profiles do not list explicitly
CORE_MACROS = {"EM_CORE": 1, "DA_CORE": 0, "NMM_CORE": 0, "COAMPS_CORE": 0, "WRFPLUS": 0, "WRF_CMAQ": 0,
               "RWORDSIZE": 4, "DWORDSIZE": 8, "IWORDSIZE": 4, "LWORDSIZE": 4}


# namelist variables whose -1 default means "decided at run time" (MPI decomposition), so a check on them is undecidable
RUNTIME_DETERMINED = {"nproc_x", "nproc_y", "numtiles", "numtiles_x", "numtiles_y"}


class Undecidable(Exception):
    pass


def _src():
    return yaml.safe_load((HERE / "sources.yaml").read_text(encoding="utf-8")) or {}


def snapshot() -> Path:
    return REPO / "references" / "upstream" / f"wrf-{_src().get('version')}"


@functools.lru_cache(maxsize=4)
def ledger(path: str | None = None) -> dict:
    p = Path(path) if path else snapshot() / "check_a_mundo.json"
    return json.loads(p.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=4)
def constants(reg_dir: str | None = None) -> dict[str, int]:
    """Named scheme constants (YSUSCHEME, NSSL_2MOM, ...) from the Registry ``package`` lines: name -> option value."""
    d = Path(reg_dir) if reg_dir else snapshot() / "Registry"
    out: dict[str, int] = {}
    rx = re.compile(r"^\s*package\s+([a-z0-9_]+)\s+([a-z0-9_]+)==(-?\d+)", re.I)
    for f in sorted(d.glob("*")) if d.is_dir() else []:
        for line in f.read_text(encoding="latin-1").splitlines():
            m = rx.match(line)
            if m:
                out.setdefault(m.group(1).lower(), int(m.group(3)))
    return out


def macros() -> dict[str, int]:
    """Preprocessor macros of the recorded build: profile defines over the standard core macros."""
    from . import checks
    out = dict(CORE_MACROS)
    d = checks.build_dir()
    if d:
        prof = yaml.safe_load((d / "build_profile.yaml").read_text(encoding="utf-8")) or {}
        cw = prof.get("configure_wrf") or {}
        if "WRF_CHEM" in cw:
            out["WRF_CHEM"] = int(cw["WRF_CHEM"])
    for k, v in checks.build_defines().items():
        try:
            out[k] = int(v)
        except ValueError:
            out[k] = 1
    return out


# --------------------------------------------------------------------------- values

def _lit(s):
    if isinstance(s, (bool, int, float)):
        return s
    s = str(s).strip()
    low = s.lower()
    if low in (".true.", "t", "true"):
        return True
    if low in (".false.", "f", "false"):
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(re.sub(r"(?<=\d)[dD](?=[+-]?\d)", "e", s))
    except ValueError:
        return s.strip("'\"")


@functools.lru_cache(maxsize=2)
def registry_defaults(path: str | None = None) -> dict[str, tuple[object, bool]]:
    p = Path(path) if path else REGISTRY_FIELDS
    if not p.exists():
        return {}
    reg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = {}
    for f in reg.get("fields") or []:
        if f.get("file") == "namelist.input" and f.get("default") not in (None, ""):
            out[f["name"]] = (_lit(f["default"]), bool(f.get("per_domain")))
    return out


def parse_namelist(text: str) -> dict[str, list]:
    """name -> list of values (repeat counts n*v expanded); groups are flattened (names are unique in WRF)."""
    out: dict[str, list] = {}
    for raw in text.splitlines():
        line = raw.split("!", 1)[0].strip()
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not m or line.startswith(("&", "/")):
            continue
        vals = []
        for tok in [t.strip() for t in m.group(2).rstrip(",").split(",") if t.strip()]:
            rep = re.match(r"^(\d+)\*(.+)$", tok)
            vals += [_lit(rep.group(2))] * int(rep.group(1)) if rep else [_lit(tok)]
        out[m.group(1).lower()] = vals
    return out


def config_from_namelist(nl: dict[str, list]) -> tuple[dict[str, list], int, set[str]]:
    """Registry defaults overlaid with the namelist; returns (cfg, max_dom, names written by the namelist)."""
    n = int(nl.get("max_dom", [1])[0])
    cfg: dict[str, list] = {}
    for name, (val, per) in registry_defaults().items():
        cfg[name] = [val] * (n if per else 1)
    for name, vals in nl.items():
        default, per = registry_defaults().get(name, (None, len(vals) > 1))
        vals = list(vals)
        if per and len(vals) < n:      # Fortran namelist read: entries not given keep the Registry default
            vals += [default if default is not None else vals[-1]] * (n - len(vals))
        cfg[name] = vals
    cfg["max_dom"] = [n]
    return cfg, n, set(nl)


def emitted_namelist(design: dict, wrf_version: str = "4.6.1") -> dict[str, list]:
    """The namelist.input the generator would write for this design (written to a temporary directory)."""
    from . import emit
    spec = {"wrf_version": wrf_version,
            "design": {k: (v if isinstance(v, dict) else {"value": v}) for k, v in design.items()}}
    with tempfile.TemporaryDirectory() as tmp:
        emit.emit(copy.deepcopy(spec), Path(tmp), force=True)
        return parse_namelist((Path(tmp) / "namelist.input").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- expression evaluation

_OPS = [(r"\.and\.", " and "), (r"\.or\.", " or "), (r"\.not\.", " not "), (r"\.eqv\.", " == "),
        (r"\.neqv\.", " != "), (r"\.eq\.", " == "), (r"\.ne\.", " != "), (r"\.ge\.", " >= "), (r"\.le\.", " <= "),
        (r"\.gt\.", " > "), (r"\.lt\.", " < "), (r"\.true\.", " True "), (r"\.false\.", " False "), (r"/=", " != ")]
_MCR_CALL = re.compile(r"model_config_rec\s*%\s*([a-z0-9_]+)\s*\(", re.I)
_MCR = re.compile(r"model_config_rec\s*%\s*([a-z0-9_]+)", re.I)
_ALLOWED = (ast.Expression, ast.BoolOp, ast.UnaryOp, ast.Compare, ast.Name, ast.Constant, ast.Call, ast.BinOp,
            ast.Load, ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd, ast.Eq, ast.NotEq, ast.Gt, ast.GtE, ast.Lt,
            ast.LtE, ast.Add, ast.Sub, ast.Mult, ast.Div)


def _check_tree(tree, calls_ok=("V",)):
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise Undecidable(f"construct {type(node).__name__}")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in calls_ok):
            raise Undecidable("function call")


@functools.lru_cache(maxsize=4096)
def _compile(expr: str):
    s = re.sub(r"(?<=\d)[dD](?=[+-]?\d)", "e", expr)
    s = _MCR_CALL.sub(lambda m: f'V("{m.group(1).lower()}",', s)
    s = _MCR.sub(lambda m: f'V("{m.group(1).lower()}",None)', s)
    for rx, rep in _OPS:
        s = re.sub(rx, rep, s, flags=re.I)
    try:
        tree = ast.parse(s.strip(), mode="eval")
    except SyntaxError as e:
        raise Undecidable(f"unparsed: {expr[:60]}") from e
    _check_tree(tree)
    return compile(tree, "<cam>", "eval")


def _evaluate(expr: str, cfg: dict, env: dict, consts: dict):
    code = _compile(expr)

    def V(name, idx):
        if name not in cfg:
            raise Undecidable(f"value of {name} unknown")
        vals = cfg[name]
        if idx is None:
            if len(vals) != 1:
                raise Undecidable(f"whole-array use of {name}")
            if name in RUNTIME_DETERMINED and vals[0] == -1:
                raise Undecidable(f"{name} decided at run time")
            return vals[0]
        if not isinstance(idx, int) or idx < 1:
            raise Undecidable(f"index of {name}")
        v = vals[idx - 1] if idx <= len(vals) else vals[-1]
        if name in RUNTIME_DETERMINED and v == -1:
            raise Undecidable(f"{name} decided at run time")
        return v

    names = {"V": V, "True": True, "False": False}
    names.update(env)
    for n in code.co_names:
        if n in names:
            continue
        if n.lower() in consts:
            names[n] = consts[n.lower()]
        else:
            raise Undecidable(f"identifier {n}")
    return eval(code, {"__builtins__": {}}, names)  # noqa: S307 - AST restricted to comparisons and arithmetic


@functools.lru_cache(maxsize=1024)
def _compile_cpp(expr: str):
    s = re.sub(r"defined\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", r"D_('\1')", expr)
    s = re.sub(r"defined\s+([A-Za-z_][A-Za-z0-9_]*)", r"D_('\1')", s)
    s = s.replace("&&", " and ").replace("||", " or ")
    s = re.sub(r"!(?!=)", " not ", s)
    try:
        tree = ast.parse(s.strip(), mode="eval")
    except SyntaxError as e:
        raise Undecidable(f"preprocessor: {expr[:60]}") from e
    _check_tree(tree, calls_ok=("D_",))
    return compile(tree, "<cpp>", "eval")


def cpp_true(conds: list[str], mac: dict[str, int]) -> bool:
    """C preprocessor semantics: an undefined identifier is 0."""
    for c in conds:
        code = _compile_cpp(c)
        names = {"D_": lambda k: 1 if k in mac else 0}
        for n in code.co_names:
            if n not in names:
                names[n] = mac.get(n, 0)
        if not eval(code, {"__builtins__": {}}, names):  # noqa: S307
            return False
    return True


# --------------------------------------------------------------------------- evaluation

def _domains(loops: list[dict], cfg: dict, consts: dict, base: dict):
    """All assignments of the loop variables (outer to inner)."""
    if not loops:
        return [dict(base)]
    ranges = []
    for lp in loops:
        a = _evaluate(lp["start"], cfg, base, consts)
        b = _evaluate(lp["end"], cfg, base, consts)
        if not isinstance(a, int) or not isinstance(b, int):
            raise Undecidable("loop bounds")
        ranges.append((lp["var"], range(a, b + 1)))
    return [{**base, **dict(zip([v for v, _ in ranges], combo))}
            for combo in itertools.product(*[r for _, r in ranges])]


def _apply_local(lv: dict, cfg: dict, consts: dict, mac: dict, locs: dict) -> None:
    """Track a local scalar (d1_value, oops, ...) so later conditions can use it; unknown -> removed."""
    name = lv["name"]
    try:
        if not cpp_true(lv.get("cpp") or [], mac):
            return
        if lv.get("index"):
            raise Undecidable("array local")
        for env in _domains(lv.get("loops") or [], cfg, consts, locs):
            if all(bool(_evaluate(c, cfg, env, consts)) for c in lv.get("chain", [])):
                locs[name] = _evaluate(lv["value"], cfg, env, consts)
    except Exception:  # noqa: BLE001 - an unknown local makes every later use undecidable
        locs.pop(name, None)
        locs.setdefault("__unknown__", set()).add(name)


def evaluate(namelist: dict[str, list], study_defining: set[str] | None = None) -> dict:
    led, consts, mac = ledger(), constants(), macros()
    cfg, n, written = config_from_namelist(namelist)
    S = study_defining if study_defining is not None else _design_names()
    fired_ref, fired_rw, undecided, compiled_out = [], [], [], 0
    locs: dict = {"max_dom": n}
    events = sorted([(i["line"], 1, i) for i in led["items"]] + [(l["line"], 0, l) for l in led.get("locals") or []],
                    key=lambda e: (e[0], e[1]))
    for _, is_item, it in events:
        if not is_item:
            _apply_local(it, cfg, consts, mac, locs)
            continue
        try:
            if not cpp_true(it.get("cpp") or [], mac):
                compiled_out += 1
                continue
        except Undecidable as e:
            undecided.append({"line": it["line"], "kind": it["kind"], "target": it["target"], "reason": str(e)})
            continue
        rec = {"line": it["line"], "kind": it["kind"], "target": it["target"],
               "study_defining": bool(set(it["params"]) & S),
               "replaces_study_defining": bool(it["target"] and it["target"] in S),
               "target_written_by_namelist": bool(it["target"] and it["target"] in written),
               "message": it["message"][:160], "printed_at_default": it.get("emit_level") == 0}
        hits, reason = [], None
        base = {k: v for k, v in locs.items() if k != "__unknown__"}
        try:
            envs = _domains(it.get("loops") or [], cfg, consts, base)
        except Undecidable as e:
            envs, reason = [], str(e)
        loop_vars = {lp["var"] for lp in it.get("loops") or []}
        for env in envs:
            try:
                ok = all(bool(_evaluate(c, cfg, env, consts)) for c in it.get("chain", []))
            except Undecidable as e:
                reason = str(e)
                break
            except Exception as e:  # noqa: BLE001 - a type clash counts as undecidable, never as a pass
                reason = type(e).__name__
                break
            if not ok:
                continue
            dom = next((env[v] for v in loop_vars if v in env), None)
            if it["kind"] == "refuse":
                hits.append(dom)
                continue
            try:
                new = _evaluate(it["value"], cfg, env, consts)
            except Exception as e:  # noqa: BLE001
                reason = f"new value: {e}"
                break
            idx = it.get("target_index")
            pos = env[idx] if idx in loop_vars else int(idx) if (idx or "").isdigit() else None
            vals = cfg.setdefault(it["target"], [new])
            k = (pos - 1) if pos else 0
            if k >= len(vals):
                vals.extend([vals[-1]] * (k + 1 - len(vals)))
            if _lit(vals[k]) != _lit(new):
                hits.append({"domain": pos, "from": vals[k], "to": new})
                vals[k] = new
        if reason is not None:
            undecided.append({**rec, "reason": reason})
            if it["kind"] == "rewrite" and it["target"] in cfg:
                cfg.pop(it["target"])         # its value is no longer known to later checks
        elif hits:
            (fired_ref if it["kind"] == "refuse" else fired_rw).append({**rec, "domains": hits})
    total = len(led["items"])
    return {"release": led.get("wrf_version"), "statements": total, "compiled_out": compiled_out,
            "undecidable": len(undecided), "decided": total - compiled_out - len(undecided),
            "refusals": fired_ref, "rewrites": fired_rw, "undecided": undecided,
            "rewrites_of_written_values": [r for r in fired_rw if r["target_written_by_namelist"]]}


def check_design(design: dict) -> dict:
    """Start-up checks for the namelist this design would produce."""
    return evaluate(emitted_namelist(design))


@functools.lru_cache(maxsize=1)
def _design_names_cached() -> frozenset:
    fm = yaml.safe_load((HERE / "field_map.yaml").read_text(encoding="utf-8"))["fields"]
    return frozenset(k.split(":", 1)[1] for k, v in fm.items() if v.get("class") == "design")


def _design_names() -> set[str]:
    return set(_design_names_cached())


def coverage(namelist: dict[str, list]) -> dict:
    """How many audited statements are decided before the run for this namelist (overall, involving S, replacing S)."""
    r = evaluate(namelist)
    led, S = ledger(), _design_names()
    und = {u["line"] for u in r["undecided"]}
    mac = macros()
    live = [i for i in led["items"] if _safe_cpp(i, mac)]
    out = {"compiled_out_for_build": r["compiled_out"]}
    for kind in ("refuse", "rewrite"):
        its = [i for i in live if i["kind"] == kind]
        inv = [i for i in its if set(i["params"]) & S]
        out[kind] = {"in_build": len(its), "decidable": sum(1 for i in its if i["line"] not in und),
                     "involve_S": len(inv), "involve_S_decidable": sum(1 for i in inv if i["line"] not in und)}
        if kind == "rewrite":
            rep = [i for i in its if i["target"] in S]
            out[kind].update({"replace_S": len(rep), "replace_S_decidable": sum(1 for i in rep if i["line"] not in und)})
    reasons: dict[str, int] = {}
    for u in r["undecided"]:
        key = re.sub(r"\b[a-z0-9_]+\b(?= unknown)", "<var>", u["reason"])
        reasons[key] = reasons.get(key, 0) + 1
    out["undecidable_reasons"] = dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:12])
    return out


def _safe_cpp(item, mac) -> bool:
    try:
        return cpp_true(item.get("cpp") or [], mac)
    except Undecidable:
        return True
