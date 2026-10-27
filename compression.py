"""Bounded photo rendering and cancellable local PDF jobs."""

from __future__ import annotations

import math
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


class CompressionError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass
class Job:
    created: float = field(default_factory=time.monotonic)
    cancelled: bool = False
    process: subprocess.Popen[str] | None = None
    completed: int = 0
    total: int = 1
    stage: str = "PDF wird eingelesen"
    lock: threading.Lock = field(default_factory=threading.Lock)

    def check(self) -> None:
        if self.cancelled:
            raise CompressionError("Komprimierung abgebrochen.", 409)

    def update(self, completed: int, total: int, stage: str) -> None:
        with self.lock:
            self.completed, self.total, self.stage = completed, total, stage

    def snapshot(self) -> dict:
        with self.lock:
            return {"completed": self.completed, "total": self.total,
                    "stage": self.stage, "cancelled": self.cancelled}

    def cancel(self) -> None:
        with self.lock:
            self.cancelled = True
            if self.process and self.process.poll() is None:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass

    def run(self, command: list[str], timeout: int = 180) -> str:
        with self.lock:
            self.check()
            process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, errors="replace")
            self.process = process
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise CompressionError("Ein Verarbeitungsschritt dauerte länger als drei Minuten.", 408)
        finally:
            with self.lock:
                self.process = None
        self.check()
        if process.returncode:
            detail = (stderr or stdout).strip().splitlines()
            raise CompressionError("PDF konnte nicht verarbeitet werden: " +
                                   (detail[-1][:250] if detail else "Unbekannter PDF-Fehler"))
        return stdout


_jobs: dict[str, Job] = {}
_registry_lock = threading.Lock()


def start_job(job_id: str) -> Job:
    with _registry_lock:
        stale = [key for key, job in _jobs.items()
                 if time.monotonic() - job.created > 600 and job.process is None]
        for key in stale:
            _jobs.pop(key, None)
        return _jobs.setdefault(job_id, Job())


def job_status(job_id: str) -> dict | None:
    with _registry_lock:
        job = _jobs.get(job_id)
    return job.snapshot() if job else None


def cancel_job(job_id: str) -> None:
    start_job(job_id).cancel()


def finish_job(job_id: str) -> None:
    with _registry_lock:
        _jobs.pop(job_id, None)


def tool(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        package = "mupdf-tools" if name == "mutool" else "ghostscript"
        raise CompressionError(f"{name} fehlt. Bitte installieren: sudo apt install {package}", 503)
    return executable


def page_sizes(source: Path, job: Job) -> list[tuple[float, float]]:
    result = job.run([tool("mutool"), "pages", str(source)])
    start = result.find("<page ")
    if start < 0:
        raise CompressionError("Keine lesbaren PDF-Seiten gefunden.")
    try:
        root = ET.fromstring("<pages>" + result[start:] + "</pages>")
        sizes = []
        for page in root:
            box = page.find("CropBox")
            if box is None:
                box = page.find("MediaBox")
            if box is None:
                raise ValueError("Missing page box")
            width = abs(float(box.attrib["r"]) - float(box.attrib["l"]))
            height = abs(float(box.attrib["t"]) - float(box.attrib["b"]))
            if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                raise ValueError("Invalid page dimensions")
            sizes.append((width, height))
    except (ET.ParseError, KeyError, ValueError) as exc:
        raise CompressionError("Die PDF-Seitengrößen konnten nicht gelesen werden.") from exc
    if not sizes or len(sizes) > 2000:
        raise CompressionError("Die PDF muss zwischen einer und 2.000 Seiten enthalten.")
    return sizes


def compress_photos(source: Path, directory: Path, job: Job,
                    max_pixels: int = 1600, quality: int = 65) -> tuple[Path, str]:
    """Render visible pages at bounded pixel dimensions; preserve page geometry."""
    gs = tool("gs")
    sizes = page_sizes(source, job)
    parts = []
    total = len(sizes) + 1
    for number, (width, height) in enumerate(sizes, 1):
        job.check()
        job.update(number - 1, total, f"Seite {number} von {len(sizes)} wird verkleinert")
        # iPhone exports may use pixel dimensions as PDF points. A fixed DPI
        # alone then produces huge images; bound pixels independently of inches.
        dpi = min(160.0, max_pixels * 72.0 / max(width, height))
        output = directory / f"photo-page-{number}.pdf"
        job.run([gs, "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dUseCropBox",
                 "-sDEVICE=pdfimage24", "-sCompression=JPEG", f"-dJPEGQ={quality}",
                 f"-r{dpi:.8f}", f"-dFirstPage={number}", f"-dLastPage={number}",
                 f"-sOutputFile={output}", str(source)])
        parts.append(output)
        job.update(number, total, f"Seite {number} von {len(sizes)} fertig")
    output = directory / "compressed.pdf"
    job.update(len(parts), total, "PDF-Seiten werden zusammengefügt")
    job.run([tool("mutool"), "merge", "-o", str(output), *map(str, parts)])
    job.update(total, total, "Foto-PDF fertig")
    return output, f"Foto-PDF, max. {max_pixels} px"


def compress_document(source: Path, directory: Path, job: Job) -> tuple[Path, str]:
    """Keep PDF text/vector content; only resample embedded images."""
    gs = tool("gs")
    profiles = [("ebook", 150), ("screen", 96), ("screen", 72)]
    best = None
    best_label = ""
    last_error = None
    for index, (preset, dpi) in enumerate(profiles):
        job.update(index, len(profiles), f"Dokumentstufe {index + 1} von {len(profiles)}")
        output = directory / f"document-{index}.pdf"
        command = [gs, "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfwrite",
                   "-dCompatibilityLevel=1.7", f"-dPDFSETTINGS=/{preset}",
                   "-dDownsampleColorImages=true", "-dDownsampleGrayImages=true",
                   "-dColorImageDownsampleThreshold=1.0", "-dGrayImageDownsampleThreshold=1.0",
                   f"-dColorImageResolution={dpi}", f"-dGrayImageResolution={dpi}",
                   f"-sOutputFile={output}", str(source)]
        try:
            job.run(command)
        except CompressionError as exc:
            if exc.status != 422:
                raise
            last_error = exc
            continue
        if best is None or output.stat().st_size < best.stat().st_size:
            best, best_label = output, f"Dokument, {dpi} dpi"
    if best is None:
        raise last_error or CompressionError("Das Dokument konnte nicht verarbeitet werden.")
    job.update(len(profiles), len(profiles), "Dokument fertig")
    return best, best_label


def compress_pdf(source: Path, directory: Path, mode: str, job: Job) -> tuple[Path, str]:
    if mode == "photo":
        output, label = compress_photos(source, directory, job)
    elif mode == "photo-small":
        output, label = compress_photos(source, directory, job, 1200, 55)
    elif mode == "document":
        output, label = compress_document(source, directory, job)
    else:
        raise CompressionError("Unbekannter Verarbeitungsmodus.", 400)
    job.check()
    if not output.is_file() or output.stat().st_size == 0:
        raise CompressionError("Es wurde keine PDF-Ausgabe erzeugt.")
    if output.stat().st_size >= source.stat().st_size:
        raise CompressionError("Die PDF ist in dieser Stufe bereits klein. "
                               "Bitte die stärkere Foto-Stufe wählen oder das Original verwenden.")
    return output, label
