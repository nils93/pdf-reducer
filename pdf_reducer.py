#!/usr/bin/env python3
"""Lokale Browser-Oberfläche für Ghostscript-PDF-Komprimierung."""

from __future__ import annotations

import argparse
import io
import json
import shutil
import tempfile
import threading
import webbrowser
import zipfile
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from compression import CompressionError, cancel_job, compress_pdf, finish_job, job_status, start_job


APP_HTML = Path(__file__).with_name("index.html")
MAX_UPLOAD = 500 * 1024 * 1024
MAX_ZIP_UPLOAD = 1024 * 1024 * 1024
APP_VERSION = "3"


class Handler(BaseHTTPRequestHandler):
    server_version = "PDFReducer/3.0"

    def send_bytes(self, status: int, body: bytes, content_type: str, **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-PDF-Reducer-Version", APP_VERSION)
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The browser closed the request after cancellation.

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        body = json.dumps({"error": message or self.responses.get(code, ("Fehler",))[0]},
                          ensure_ascii=False).encode("utf-8")
        self.send_bytes(code, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/progress":
            job_id = parse_qs(urlparse(self.path).query).get("job", [""])[0]
            snapshot = job_status(job_id)
            if snapshot is None:
                self.send_error(404, "Auftrag noch nicht gestartet oder bereits beendet.")
                return
            self.send_bytes(200, json.dumps(snapshot).encode(), "application/json")
            return
        if route != "/":
            self.send_error(404, "Nicht gefunden")
            return
        try:
            self.send_bytes(200, APP_HTML.read_bytes(), "text/html; charset=utf-8")
        except OSError:
            self.send_error(500, "index.html konnte nicht gelesen werden")

    def do_POST(self) -> None:
        origin = self.headers.get("Origin")
        expected_origin = f"http://{self.headers.get('Host', '')}"
        if origin and origin != expected_origin:
            self.send_error(403, "Anfragen von anderen Websites sind nicht erlaubt")
            return
        route = urlparse(self.path).path
        if route == "/zip":
            self.do_zip()
            return
        query = parse_qs(urlparse(self.path).query)
        job_id = query.get("job", [""])[0]
        if route not in {"/compress", "/cancel"}:
            self.send_error(404, "Nicht gefunden")
            return
        if not job_id or len(job_id) > 80:
            self.send_error(400, "Ungültige Auftrags-ID")
            return
        if route == "/cancel":
            cancel_job(job_id)
            self.send_bytes(200, b"", "application/json")
            return
        job = start_job(job_id)
        try:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise CompressionError("Ungültige Dateigröße", 400) from exc
            if not 0 < length <= MAX_UPLOAD:
                raise CompressionError("Pro PDF sind höchstens 500 MB erlaubt.", 413)
            data = self.rfile.read(length)
            if len(data) != length or not data.startswith(b"%PDF-"):
                raise CompressionError("Ungültige oder unvollständig übertragene PDF-Datei.", 400)
            job.check()
            with tempfile.TemporaryDirectory(prefix="pdf-reducer-") as temp_dir:
                directory = Path(temp_dir)
                source = directory / "input.pdf"
                source.write_bytes(data)
                output, profile = compress_pdf(source, directory,
                                               query.get("mode", ["photo"])[0], job)
                compressed = output.read_bytes()
            self.send_bytes(200, compressed, "application/pdf",
                            X_Original_Size=str(length), X_Compressed_Size=str(len(compressed)),
                            X_Compression_Profile=profile)
        except CompressionError as exc:
            self.send_error(exc.status, str(exc))
        except OSError as exc:
            self.send_error(500, f"Lokaler Verarbeitungsfehler: {exc}")
        finally:
            finish_job(job_id)

    def do_zip(self) -> None:
        origin = self.headers.get("Origin")
        expected_origin = f"http://{self.headers.get('Host', '')}"
        if origin and origin != expected_origin:
            self.send_error(403, "Anfragen von anderen Websites sind nicht erlaubt")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "Ungültige ZIP-Anfrage")
            return
        if length <= 0 or length > MAX_ZIP_UPLOAD:
            self.send_error(413, "Die gesamte ZIP-Anfrage darf höchstens 1 GB groß sein")
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self.send_error(400, "Dateien wurden unvollständig übertragen")
            return
        content_type = self.headers.get("Content-Type", "").encode("ascii", "replace")
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + content_type + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
        )
        if not message.is_multipart():
            self.send_error(400, "Keine PDF-Dateien zum Verpacken empfangen")
            return
        files: list[tuple[str, bytes]] = []
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data" or part.get_param("name", header="content-disposition") != "files":
                continue
            filename = part.get_filename()
            payload = part.get_payload(decode=True)
            if not filename or not payload or not filename.lower().endswith(".pdf") or not payload.startswith(b"%PDF-"):
                self.send_error(400, "ZIP enthält eine ungültige oder leere PDF-Datei")
                return
            safe_name = filename.replace("\\", "/").split("/")[-1]
            files.append((safe_name, payload))
        if not files:
            self.send_error(400, "Keine PDFs zum Verpacken empfangen")
            return
        names = [name for name, _ in files]
        if len(names) != len(set(names)):
            self.send_error(409, "Mehrere PDFs haben denselben Dateinamen. Bitte Duplikate aus der Liste entfernen.")
            return
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zipped:
            for filename, contents in files:
                zipped.writestr(filename, contents)
        result = archive.getvalue()
        self.send_bytes(200, result, "application/zip",
                        Content_Disposition='attachment; filename="komprimierte-pdfs.zip"')

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="PDFs lokal für den E-Mail-Versand komprimieren")
    parser.add_argument("--port", type=int, default=8766, help="Lokaler Port (Standard: 8766)")
    parser.add_argument("--no-open", action="store_true", help="Browser nicht automatisch öffnen")
    args = parser.parse_args()

    if not APP_HTML.is_file():
        raise SystemExit(f"Oberfläche fehlt: {APP_HTML}")
    if not shutil.which("gs"):
        raise SystemExit("Ghostscript ist nicht installiert. Installiere es mit: sudo apt install ghostscript")
    if not shutil.which("mutool"):
        raise SystemExit("MuPDF ist nicht installiert. Installiere es mit: sudo apt install mupdf-tools")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"PDF Reducer läuft unter {url}")
    print("Beenden mit Ctrl+C. Dateien werden nur lokal verarbeitet.")
    if not args.no_open:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPDF Reducer beendet.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
