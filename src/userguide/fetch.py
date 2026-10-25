"""fetch — download the WRF upstream snapshot listed in sources.yaml and write MANIFEST.yaml.

The snapshot is read-only: files already present and recorded in the manifest are not downloaded again;
``--force`` overwrites them.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import time
import urllib.request
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
UA = "WRF-Agent-userguide-fetch/1.0 (research; single-shot snapshot)"


def load_sources(path: Path | None = None) -> dict:
    return yaml.safe_load((path or HERE / "sources.yaml").read_text(encoding="utf-8"))


def snapshot_dir(version: str, root: Path | None = None) -> Path:
    return (root or REPO / "references" / "upstream") / f"wrf-{version}"


def _get(url: str, retries: int = 3) -> bytes:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - every network error is retried the same way
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"download failed {url}: {last}")


def plan_downloads(src: dict) -> list[tuple[str, str]]:
    """Return [(relative path, url)]."""
    v, wt = src["version"], src["wps_tag"]
    s = src["sources"]
    items: list[tuple[str, str]] = []
    reg = s["registry"]
    tree = json.loads(_get(reg["tree_api"].format(version=v)))
    for node in tree.get("tree", []):
        p = node["path"]
        if node.get("type") == "blob" and p.startswith(reg["prefix"]):
            items.append((p, reg["base"].format(version=v) + p[len(reg["prefix"]):]))
    items.append(("run/README.namelist", s["readme_namelist"]["url"].format(version=v)))
    items.append(("wps/namelist.wps.all_options", s["wps_all_options"]["url"].format(wps_tag=wt)))
    items.append(("wps/namelist.wps", s["wps_default"]["url"].format(wps_tag=wt)))
    ug = s["users_guide"]
    for page in ug["pages"]:
        items.append((f"users_guide/{page}", ug["base"] + page))
    return items


def fetch(force: bool = False, root: Path | None = None, sources: Path | None = None) -> Path:
    src = load_sources(sources)
    out = snapshot_dir(src["version"], root)
    out.mkdir(parents=True, exist_ok=True)
    man_path = out / "MANIFEST.yaml"
    old = {}
    if man_path.exists():
        old = {f["path"]: f for f in (yaml.safe_load(man_path.read_text()) or {}).get("files", [])}
    files = []
    for rel, url in plan_downloads(src):
        dst = out / rel
        if dst.exists() and not force and rel in old:
            files.append(old[rel])
            continue
        data = _get(url)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        files.append({
            "path": rel,
            "url": url,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "fetched_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "version_locked": not rel.startswith("users_guide/"),
        })
    manifest = {
        "wrf_version": src["version"],
        "wps_tag": src["wps_tag"],
        "note": "Upstream snapshot, read-only. users_guide/ is the current website (not version-locked; see "
                "fetched_at). Registry and README.namelist are locked to the release tag. The Users' Guide text is "
                "cited and paraphrased, not redistributed.",
        "files": sorted(files, key=lambda f: f["path"]),
    }
    man_path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return out
