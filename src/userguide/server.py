"""server — local studio server: workflow dashboard + in-page agent chat. Binds to 127.0.0.1 only.

    GET  /                 studio page (dashboard left, agent chat right)
    GET  /<NN_stage>.html  stage page (regenerated on every request from the case files)
    GET  /api/langs         available dictionary languages (dictionary.<lang>.yaml overlays)
    POST /api/emit         run the emitter on the approved specification -> case/build/*
    GET  /api/version      change token of the case files + current stage/page (the studio polls it)
    GET  /api/chat         chat log + model settings
    POST /api/chat         {message} -> run the agent, return new rows
    POST /api/save         {design} -> autosave form edits into draft.yaml (edited_by: user), {version}
    POST /api/submit       {design} -> experiment.yaml, compile check, advance, {next}
    POST /api/back         -> back to Review, {next}
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import app, chat, dashboard


def _y_path(p):
    import yaml as _yaml
    return _yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None


WATCH = ("request.md", "draft.yaml", "experiment.yaml", "compile_report.yaml", "state.yaml",
         "build/emit_manifest.yaml", "run/run_manifest.yaml", "restart.yaml")


def make_handler(case: Path, field_map: Path, dictionary: Path, registry: Path):
    def current_page() -> str:
        st = dashboard.load_state(case)
        i = [k for k, s in enumerate(dashboard.stages()) if s["id"] == st["current"]][0]
        return dashboard.page_name(i, st["current"])

    def rebuild(lang: str | None = None) -> str:
        dashboard.build(case, field_map, dictionary, registry, lang)
        return current_page()

    def version() -> dict:
        tok = "|".join(f"{n}:{(case / n).stat().st_mtime_ns if (case / n).exists() else 0}" for n in WATCH if n != "state.yaml")
        st = dashboard.load_state(case)
        return {"version": tok + "|" + st["current"], "stage": st["current"], "page": current_page()}

    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json")

        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            if path == "/api/version":
                return self._json(version())
            if path == "/api/langs":
                langs = ["en"]
                if dictionary.exists():
                    import re as _re
                    langs += sorted(_re.findall(r"dictionary\.([a-z-]+)\.yaml", "".join(
                        f.name + "\n" for f in dictionary.parent.glob("dictionary.*.yaml"))))
                return self._json({"langs": langs})
            if path == "/api/chat":
                return self._json({"rows": chat.history(case), "settings": chat.settings()})
            if path == "/api/discussion":
                return self._json(self.discussion_state())
            lang = (self.path.split("lang=", 1)[1].split("&")[0] if "lang=" in self.path else None)
            cur = rebuild(lang)
            if path in ("/", "/app", "/index.html"):
                return self._send(200, app.page(cur).encode(), "text/html; charset=utf-8")
            name = path.lstrip("/")
            p = case / "dashboard" / name
            if "/" in name or ".." in name or not p.is_file():
                return self._send(404, b"not found", "text/plain")
            self._send(200, p.read_bytes(), "text/html; charset=utf-8")

        def discussion_state(self):
            import yaml as _yaml
            rec = _y_path(case / "discussion.yaml") or {}
            draft = _y_path(case / "draft.yaml") or {}
            shown, selected = [], draft.get("selected_alternative")
            for alt in ((rec.get("synthesis") or {}).get("alternatives") or []):
                shown.append({"id": alt.get("id"), "tradeoffs": alt.get("tradeoffs") or [],
                              "selected": alt.get("id") == selected})
            return {"exists": bool(rec), "status": rec.get("status"), "model": rec.get("model"),
                    "error": rec.get("error"), "snapshot_hash": rec.get("snapshot_hash"),
                    "draft_hash": draft.get("discussion_hash"), "selected": selected,
                    "alternatives": shown,
                    "disagreements": (rec.get("synthesis") or {}).get("disagreements") or [],
                    "open_questions": (rec.get("synthesis") or {}).get("open_questions") or [],
                    "running": (case / "discussion.running").exists()}

        def do_POST(self):  # noqa: N802
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/api/select":
                    from .flow import discussion as _disc
                    try:
                        draft = _disc.select(case, str(body.get("alternative") or ""))
                    except _disc.DiscussionError as e:
                        return self._send(409, str(e).encode(), "text/plain")
                    from .flow import runner as _runner
                    moved = _runner.run(case, actor="automation")   # the gate decides whether Decompose may pass
                    rebuild()
                    return self._json({"selected": draft.get("selected_alternative"),
                                       "stage": moved.get("stage"), "status": moved.get("status")})
                if self.path == "/api/discuss":
                    import threading
                    from .flow import discussion as _disc
                    marker = case / "discussion.running"
                    if marker.exists():
                        return self._send(409, b"a discussion is already running", "text/plain")
                    marker.write_text("started", encoding="utf-8")

                    def work():
                        try:
                            _disc.discuss(case)
                        except Exception as e:  # noqa: BLE001 - the record carries the error
                            (case / "discussion.error").write_text(f"{type(e).__name__}: {e}", encoding="utf-8")
                        finally:
                            marker.unlink(missing_ok=True)
                            rebuild()
                    threading.Thread(target=work, daemon=True).start()
                    return self._json({"started": True})
                if self.path == "/api/chat":
                    rows = chat.send(case, str(body.get("message", "")), body.get("model"))
                    rebuild()
                    return self._json({"rows": rows})
                if self.path == "/api/emit":
                    from . import emit as _emit
                    spec = _y_path(case / "experiment.yaml")
                    if not spec:
                        return self._send(400, b"no approved experiment.yaml", "text/plain")
                    try:
                        rep = _emit.emit(spec, case / "build")
                    except _emit.EmitError as e:
                        return self._send(409, str(e).encode(), "text/plain")
                    self._json({"manifest": rep})
                    return
                if self.path == "/api/prepare":
                    from . import runops as _ro
                    rep = _ro.prepare_launch(case)
                    if not rep.get("ok"):
                        return self._send(409, str(rep.get("error")).encode(), "text/plain")
                    self._json(rep)
                    return
                if self.path in ("/api/survey", "/api/verify", "/api/restart"):
                    from . import runops as _ro
                    tail = self.path.split("/")[-1]
                    key = {"survey": "monitor"}.get(tail, tail)  # dashboard option key
                    opt = dict(body or {})
                    path = Path(opt.get("path") or (case / "run"))
                    if not path.is_dir():
                        return self._send(400, f"not a directory: {path}".encode(), "text/plain")
                    try:
                        if tail == "survey":
                            opt["result"] = _ro.survey(path)
                        elif tail == "verify":
                            ef = opt.get("expect_frames")
                            iv = opt.get("interval_min")
                            opt["result"] = _ro.verify(path, int(ef) if ef not in (None, "") else None,
                                                       float(iv) if iv not in (None, "") else None)
                        else:
                            opt.update(_ro.restart_override(case, path) or {})
                    except Exception as e:  # noqa: BLE001
                        return self._send(500, f"{type(e).__name__}: {e}".encode(), "text/plain")
                    st = dashboard.load_state(case)
                    st.setdefault("options", {})[key] = opt
                    (case / "state.yaml").write_text(
                        __import__("yaml").safe_dump(st, allow_unicode=True, sort_keys=False), encoding="utf-8")
                    self._json({"ok": True})
                    return
                if self.path == "/api/save":
                    n_changed = dashboard.save_review(case, body.get("design") or {}, field_map)
                    return self._json({"changed": n_changed, "version": version()["version"]})
                if self.path == "/api/submit":
                    dashboard.submit(case, body.get("design") or {}, field_map)
                elif self.path == "/api/back":
                    dashboard.back_to_review(case)
                else:
                    return self._send(404, b"not found", "text/plain")
                self._json({"next": rebuild()})
            except Exception as e:  # noqa: BLE001 - report to the page
                self._send(500, f"{type(e).__name__}: {e}".encode(), "text/plain")

        def log_message(self, fmt, *args):  # quiet
            return

    return H


def serve(case: Path, field_map: Path, dictionary: Path, registry: Path, port: int = 8797,
          on_ready=None) -> None:
    case.mkdir(parents=True, exist_ok=True)
    dashboard.build(case, field_map, dictionary, registry)
    handler = make_handler(case, field_map, dictionary, registry)
    for p in range(port, port + 20):  # take the first free port
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), handler)
            port = p
            break
        except OSError:
            continue
    else:
        raise OSError(f"no free port in {port}-{port + 19}")
    url = f"http://127.0.0.1:{port}/"
    print(f"WRF-Agent Studio: {url}  (case {case})", flush=True)
    if on_ready:
        on_ready(url)
    httpd.serve_forever()
