from __future__ import annotations

import base64
import cgi
import csv
import io
import json
import re
import tempfile
import time
import traceback
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import load_config
from .pipeline import run_pipeline

_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_PASS_NAME_RE = re.compile(r"^[a-zA-Z0-9_!\-*&]+$")
_MAX_UPLOAD_BYTES = 110_000_000
_MAX_FILE_BYTES = 20_000_000
_MAX_UPLOAD_FILES = 400

_STATIC_ROUTES: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "application/javascript; charset=utf-8"),
    "/static/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/static/app.js": ("app.js", "application/javascript; charset=utf-8"),
}


@dataclass(frozen=True)
class WebServerSettings:
    config_path: Path
    catalog_csv: Path
    catalog_images_dir: Path
    trait_templates_dir: Path
    static_dir: Path


def run_web_server(
    host: str,
    port: int,
    config_path: Path | None = None,
    catalog_csv: Path | None = None,
    catalog_images_dir: Path | None = None,
    trait_templates_dir: Path | None = None,
) -> None:
    settings = _resolve_settings(
        config_path=config_path,
        catalog_csv=catalog_csv,
        catalog_images_dir=catalog_images_dir,
        trait_templates_dir=trait_templates_dir,
    )

    handler_class = _build_handler(settings)
    server = ThreadingHTTPServer((host, port), handler_class)

    print(f"Serving web app on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down web server...")
    finally:
        server.server_close()


def _resolve_settings(
    config_path: Path | None,
    catalog_csv: Path | None,
    catalog_images_dir: Path | None,
    trait_templates_dir: Path | None,
) -> WebServerSettings:
    package_dir = Path(__file__).resolve().parent
    project_dir = package_dir.parents[1]
    return WebServerSettings(
        config_path=config_path or (project_dir / "config" / "default_config.json"),
        catalog_csv=catalog_csv or (project_dir / "data" / "species_catalog" / "catalog.csv"),
        catalog_images_dir=catalog_images_dir or (project_dir / "data" / "species_catalog"),
        trait_templates_dir=trait_templates_dir or (project_dir / "data" / "templates" / "traits"),
        static_dir=package_dir / "web",
    )


def _build_handler(settings: WebServerSettings) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PoGoBoxAnalyzer/0.1"

        def do_HEAD(self) -> None:  # noqa: N802
            route = self.path.split("?", 1)[0]
            if route == "/health":
                self.send_response(HTTPStatus.OK.value)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            static = _STATIC_ROUTES.get(route)
            if static is None:
                self.send_response(HTTPStatus.NOT_FOUND.value)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            filename, content_type = static
            target = settings.static_dir / filename
            if not target.exists() or not target.is_file():
                self.send_response(HTTPStatus.NOT_FOUND.value)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(target.stat().st_size))
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            route = self.path.split("?", 1)[0]
            if route == "/health":
                self._send_json(HTTPStatus.OK, {"ok": True})
                return

            static = _STATIC_ROUTES.get(route)
            if static is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Route not found."})
                return

            filename, content_type = static
            self._send_static_file(settings.static_dir / filename, content_type=content_type)

        def do_POST(self) -> None:  # noqa: N802
            route = self.path.split("?", 1)[0]
            if route != "/api/analyze":
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Route not found."})
                return

            try:
                self._handle_analyze()
            except Exception as exc:
                traceback.print_exc()
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "error": f"Server error during analyze: {type(exc).__name__}: {exc}",
                    },
                )

        def _handle_analyze(self) -> None:
            started = time.time()
            content_type = self.headers.get("Content-Type", "")
            content_length = self.headers.get("Content-Length", "0")
            print(
                f"/api/analyze request content_type={content_type!r} content_length={content_length}",
                flush=True,
            )

            with tempfile.TemporaryDirectory(prefix="pogo_web_") as td:
                root = Path(td)
                input_dir = root / "input"
                output_dir = root / "output"
                input_dir.mkdir(parents=True, exist_ok=True)
                output_dir.mkdir(parents=True, exist_ok=True)

                saved = self._save_uploads(input_dir=input_dir, max_bytes=_MAX_UPLOAD_BYTES)
                if saved is None:
                    return
                if saved == 0:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "No valid image files were uploaded."})
                    return

                print(f"/api/analyze begin uploads={saved}", flush=True)

                config = load_config(settings.config_path if settings.config_path.exists() else None)
                summary = run_pipeline(
                    input_dir=input_dir,
                    output_dir=output_dir,
                    config=config,
                    catalog_csv=settings.catalog_csv,
                    catalog_images_dir=settings.catalog_images_dir,
                    trait_templates_dir=settings.trait_templates_dir,
                    manifest_path=None,
                )

                species_csv_path = Path(str(summary.get("species_counts_csv", "")))
                if not species_csv_path.exists():
                    self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Analyzer did not produce species CSV."})
                    return

                csv_text = species_csv_path.read_text(encoding="utf-8-sig")
                preview_rows = _preview_csv(csv_text, max_rows=50)

                elapsed = time.time() - started
                print(f"/api/analyze done uploads={saved} elapsed_sec={elapsed:.1f}", flush=True)

                self._send_json(
                    HTTPStatus.OK,
                    {
                        "summary": summary,
                        "csv_text": csv_text,
                        "preview": preview_rows,
                    },
                )

        def _save_uploads(self, input_dir: Path, max_bytes: int) -> int | None:
            content_type = self.headers.get("Content-Type", "").lower()
            if "multipart/form-data" in content_type:
                return self._save_multipart_uploads(input_dir=input_dir, max_bytes=max_bytes)
            return self._save_json_uploads(input_dir=input_dir, max_bytes=max_bytes)

        def _save_json_uploads(self, input_dir: Path, max_bytes: int) -> int | None:
            payload = self._read_json_body(max_bytes=max_bytes)
            if payload is None:
                return None

            uploads_obj = payload.get("uploads")
            if not isinstance(uploads_obj, list):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Expected JSON body with uploads list."})
                return None

            saved = 0
            for idx, entry in enumerate(uploads_obj):
                if not isinstance(entry, dict):
                    continue

                pass_name = self._normalize_pass_name(entry.get("pass_name"))
                filename = str(entry.get("filename", "")).strip()
                data_base64 = str(entry.get("data_base64", "")).strip()

                if not data_base64:
                    continue

                extension = Path(filename).suffix.lower()
                if extension not in _ALLOWED_EXTENSIONS:
                    extension = ".png"
                    filename = (Path(filename).stem or f"upload_{idx}") + extension

                safe_name = _safe_filename(filename)
                target_dir = input_dir / pass_name
                target_dir.mkdir(parents=True, exist_ok=True)
                target_path = target_dir / f"{idx:05d}_{safe_name}"

                try:
                    binary = base64.b64decode(data_base64, validate=True)
                except Exception:
                    continue

                if not binary or len(binary) > _MAX_FILE_BYTES:
                    continue

                target_path.write_bytes(binary)
                saved += 1

            return saved

        def _save_multipart_uploads(self, input_dir: Path, max_bytes: int) -> int | None:
            content_length = self._parse_content_length(max_bytes=max_bytes)
            if content_length is None:
                return None

            environ = {
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(content_length),
            }

            try:
                form = cgi.FieldStorage(
                    fp=self.rfile,
                    headers=self.headers,
                    environ=environ,
                    keep_blank_values=False,
                )
            except Exception:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid multipart upload."})
                return None

            if "files" not in form:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "No files were uploaded. Use multipart field name 'files'."},
                )
                return None

            files_field = form["files"]
            file_items = files_field if isinstance(files_field, list) else [files_field]
            if len(file_items) > _MAX_UPLOAD_FILES:
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": f"Too many files in one request (max {_MAX_UPLOAD_FILES})."},
                )
                return None

            global_pass = self._normalize_pass_name(form.getfirst("pass_name", "auto"))

            saved = 0
            for idx, item in enumerate(file_items):
                file_obj = getattr(item, "file", None)
                filename_raw = str(getattr(item, "filename", "") or "").strip()
                if file_obj is None or not filename_raw:
                    continue

                pass_name = self._normalize_pass_name(form.getfirst(f"pass_name_{idx}", global_pass))
                extension = Path(filename_raw).suffix.lower()
                if extension not in _ALLOWED_EXTENSIONS:
                    extension = ".png"
                    filename_raw = (Path(filename_raw).stem or f"upload_{idx}") + extension

                safe_name = _safe_filename(filename_raw)
                target_dir = input_dir / pass_name
                target_dir.mkdir(parents=True, exist_ok=True)
                target_path = target_dir / f"{idx:05d}_{safe_name}"

                written = 0
                with target_path.open("wb") as out:
                    while True:
                        chunk = file_obj.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > _MAX_FILE_BYTES:
                            break
                        out.write(chunk)

                if written == 0 or written > _MAX_FILE_BYTES:
                    try:
                        target_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue

                saved += 1

            return saved

        def _normalize_pass_name(self, value: object) -> str:
            pass_name = str(value or "auto").strip() or "auto"
            if not _PASS_NAME_RE.match(pass_name):
                return "auto"
            return pass_name

        def _parse_content_length(self, max_bytes: int) -> int | None:
            content_length_text = self.headers.get("Content-Length", "0")
            try:
                content_length = int(content_length_text)
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length header."})
                return None

            if content_length <= 0:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Empty request body."})
                return None
            if content_length > max_bytes:
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": f"Payload too large (max {max_bytes // (1024 * 1024)} MB)."},
                )
                return None
            return content_length

        def _read_json_body(self, max_bytes: int) -> dict[str, Any] | None:
            content_type = self.headers.get("Content-Type", "")
            if "application/json" not in content_type:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Content-Type must be application/json."})
                return None

            content_length = self._parse_content_length(max_bytes=max_bytes)
            if content_length is None:
                return None

            body = self.rfile.read(content_length)
            try:
                parsed = json.loads(body.decode("utf-8"))
            except Exception:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON body."})
                return None

            if not isinstance(parsed, dict):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "JSON body must be an object."})
                return None

            return parsed

        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

        def _send_static_file(self, path: Path, content_type: str) -> None:
            if not path.exists() or not path.is_file():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Static file missing."})
                return

            data = path.read_bytes()
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

    return Handler


def _safe_filename(name: str) -> str:
    base = Path(name).name
    base = re.sub(r"[^a-zA-Z0-9._-]+", "_", base)
    return base[:120] or "upload.png"


def _preview_csv(csv_text: str, max_rows: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    fh = io.StringIO(csv_text)
    reader = csv.DictReader(fh)
    for idx, row in enumerate(reader):
        if idx >= max_rows:
            break
        rows.append({k: str(v) for k, v in row.items()})
    return rows
