# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 nils93
"""Regression checks using generated PDFs, never personal documents."""

import io
import json
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
import zlib
from email.message import Message
from pathlib import Path

from compression import CompressionError, Job, compress_pdf, page_sizes
from pdf_reducer import Handler


def image_pdf(path, width, height, boxes, noisy=True, page_attributes=""):
    """A shared RGB photo on pages with deliberately different physical sizes."""
    pixels = random.Random(19).randbytes(width * height * 3) if noisy else b"\xff" * (width * height * 3)
    image = zlib.compress(pixels, 1)
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"",
               (f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
                f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length {len(image)} >>\nstream\n").encode()
               + image + b"\nendstream"]
    pages = []
    for page_width, page_height in boxes:
        content = f"q {page_width} 0 0 {page_height} 0 0 cm /Photo Do Q".encode()
        content_id = len(objects) + 1
        objects.append(f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
        page_id = len(objects) + 1
        objects.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] {page_attributes} "
                        f"/Resources << /XObject << /Photo 3 0 R >> >> /Contents {content_id} 0 R >>").encode())
        pages.append(page_id)
    objects[1] = (f"<< /Type /Pages /Count {len(pages)} /Kids [" +
                  " ".join(f"{number} 0 R" for number in pages) + "] >>").encode()
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    path.write_bytes(result)


class MemoryHandler(Handler):
    def __init__(self, path, body, content_type="application/pdf"):
        self.path = path
        self.headers = Message()
        self.headers["Host"] = "127.0.0.1:8766"
        self.headers["Origin"] = "http://127.0.0.1:8766"
        self.headers["Content-Length"] = str(len(body))
        self.headers["Content-Type"] = content_type
        self.rfile = io.BytesIO(body)
        self.response = None

    def send_bytes(self, status, body, content_type, **headers):
        self.response = status, body, content_type, headers


@unittest.skipUnless(shutil.which("gs") and shutil.which("mutool"), "Ghostscript and MuPDF are required")
class PhotoCompressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="pdf-reducer-tests-")
        cls.root = Path(cls.workspace.name)
        cls.source = cls.root / "iphone.pdf"
        image_pdf(cls.source, 4032, 3024, [(4032, 3024), (403.2, 302.4)])

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def test_photo_size_page_geometry_and_pixel_cap(self):
        with tempfile.TemporaryDirectory(dir=self.root) as temp_dir:
            output, _ = compress_pdf(self.source, Path(temp_dir), "photo", Job())
            self.assertLess(output.stat().st_size, self.source.stat().st_size * 0.2)
            sizes = page_sizes(output, Job())
            self.assertEqual(len(sizes), 2)
            for actual, expected in zip(sizes, [(4032, 3024), (403.2, 302.4)]):
                for left, right in zip(actual, expected):
                    self.assertAlmostEqual(left, right, places=1)
            if shutil.which("pdfimages"):
                listing = subprocess.check_output(["pdfimages", "-list", str(output)], text=True)
                images = [line.split() for line in listing.splitlines()[2:] if line.strip()]
                self.assertEqual(len(images), 2)
                for fields in images:
                    self.assertLessEqual(max(int(fields[3]), int(fields[4])), 1601)
            print(f"\nPhoto benchmark: {self.source.stat().st_size} -> {output.stat().st_size} bytes (2 pages)")

    def test_larger_output_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=self.root) as temp_dir:
            directory = Path(temp_dir)
            source = directory / "already-small.pdf"
            image_pdf(source, 8, 8, [(100, 100)], noisy=False)
            with self.assertRaises(CompressionError) as caught:
                compress_pdf(source, directory, "photo", Job())
            self.assertEqual(caught.exception.status, 422)

    def test_already_jpeg_encoded_iphone_photo(self):
        with tempfile.TemporaryDirectory(dir=self.root) as temp_dir:
            directory = Path(temp_dir)
            source = directory / "jpeg-photo.pdf"
            subprocess.run(["gs", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfimage24",
                            "-sCompression=JPEG", "-dJPEGQ=98", "-r72", "-dFirstPage=1", "-dLastPage=1",
                            f"-sOutputFile={source}", str(self.source)],
                           capture_output=True, check=True, timeout=30)
            output, _ = compress_pdf(source, directory, "photo", Job())
            self.assertLess(output.stat().st_size, source.stat().st_size * 0.2)
            print(f"\nJPEG photo benchmark: {source.stat().st_size} -> {output.stat().st_size} bytes")

    def test_rotation_and_visible_crop_are_preserved(self):
        with tempfile.TemporaryDirectory(dir=self.root) as temp_dir:
            directory = Path(temp_dir)
            source = directory / "rotated.pdf"
            image_pdf(source, 1200, 1600, [(600, 800)],
                      page_attributes="/Rotate 90 /CropBox [0 0 600 750]")
            output, _ = compress_pdf(source, directory, "photo", Job())
            self.assertEqual(page_sizes(output, Job()), [(750, 600)])

    def test_endpoint_and_readable_json_errors(self):
        handler = MemoryHandler("/compress?job=regression&mode=photo-small", self.source.read_bytes())
        handler.do_POST()
        status, body, content_type, headers = handler.response
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/pdf")
        self.assertTrue(body.startswith(b"%PDF-"))
        self.assertLess(len(body), self.source.stat().st_size * 0.2)
        self.assertIn("1200", headers["X_Compression_Profile"])
        handler = MemoryHandler("/compress?job=invalid", b"not a pdf")
        handler.do_POST()
        self.assertEqual(handler.response[0], 400)
        self.assertIn("PDF", json.loads(handler.response[1])["error"])
        self.assertNotIn(b":root", handler.response[1])


class LifecycleTests(unittest.TestCase):
    def test_cancel_kills_active_process(self):
        job = Job()
        errors = []
        def run():
            try:
                job.run([sys.executable, "-c", "import time; time.sleep(30)"])
            except CompressionError as exc:
                errors.append(exc.status)
        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 2
        while job.process is None and time.monotonic() < deadline:
            time.sleep(0.01)
        process = job.process
        self.assertIsNotNone(process)
        job.cancel()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertIsNotNone(process.poll())
        self.assertEqual(errors, [409])

    def test_zip_preserves_names_and_pdf_bytes(self):
        pdf = b"%PDF-1.4\nminimal test payload"
        boundary = "pdf-test-boundary"
        body = b""
        for name in ("erste.pdf", "zweite.pdf"):
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
                     'Content-Type: application/pdf\r\n\r\n').encode() + pdf + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        handler = MemoryHandler("/zip", body, f"multipart/form-data; boundary={boundary}")
        handler.do_POST()
        self.assertEqual(handler.response[0], 200)
        with zipfile.ZipFile(io.BytesIO(handler.response[1])) as archive:
            self.assertEqual(archive.namelist(), ["erste.pdf", "zweite.pdf"])
            self.assertEqual(archive.read("erste.pdf"), pdf)


if __name__ == "__main__":
    unittest.main()
