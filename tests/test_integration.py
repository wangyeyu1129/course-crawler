"""Offline full-document regression and real HTTP transport boundaries."""

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

from crawler.http import HttpClient, HttpPolicy
from crawler.utm_course import parse_course_page


class Handler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.calls.append(self.path)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/destination")
            self.end_headers()
        elif self.path == "/truncated":
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"partial")
            self.close_connection = True
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("course 文本".encode())


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.calls = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_redirect_never_reaches_destination(self):
        with HttpClient(HttpPolicy(interval=0, retries=2)) as client:
            with self.assertRaises(requests.HTTPError):
                client.get_text(self.base + "/redirect")
        self.assertEqual(self.server.calls, ["/redirect"])

    def test_truncated_body_retries_and_never_returns_partial_text(self):
        with HttpClient(HttpPolicy(interval=0, retries=1, backoff=0)) as client:
            with self.assertRaises(requests.exceptions.ChunkedEncodingError):
                client.get_text(self.base + "/truncated")
        self.assertEqual(self.server.calls, ["/truncated", "/truncated"])

    def test_success_decodes_real_response(self):
        with HttpClient(HttpPolicy(interval=0)) as client:
            self.assertEqual(client.get_text(self.base + "/ok"), "course 文本")


class FullDocumentTests(unittest.TestCase):
    def test_full_documents_match_retained_dom_excerpts(self):
        fixtures = Path(__file__).parent / "fixtures"
        for path in (fixtures / "full").glob("*.html"):
            with self.subTest(course=path.stem):
                actual = parse_course_page(path.read_text(encoding="utf-8"))
                expected = parse_course_page((fixtures / path.name).read_text())
                self.assertEqual(actual, expected)
        self.assertEqual(len(list((fixtures / "full").glob("*.html"))), 5)
