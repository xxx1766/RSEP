import unittest
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading

try:
    from issue12_image_client import validate_reply, run
except ImportError:
    validate_reply = None


class ImageClientValidationTests(unittest.TestCase):
    def test_only_verified_inference_can_count_as_success(self):
        self.assertIsNotNone(validate_reply, "image response validation missing")
        expected = dict(input_sha256="abc", output_sha256="def", checksum="123", bytes=10)
        valid = dict(expected, status="success", finite=True, shape=[1, 3549, 85])
        validate_reply(200, valid, expected)
        for patch in ({"input_sha256":"wrong"}, {"output_sha256":"wrong"},
                      {"finite":False}, {"shape":[1,2]}, {"checksum":"0"}):
            with self.assertRaises(ValueError):
                validate_reply(200, dict(valid, **patch), expected)
        with self.assertRaises(ValueError):
            validate_reply(500, valid, expected)

    def test_low_rate_reuses_active_connections_while_pool_supports_bursts(self):
        bodies = [bytes([i]) * 8 for i in range(4)]
        metas = [dict(index=i, bytes=len(b), input_sha256=hashlib.sha256(b).hexdigest(),
                      output_sha256=hashlib.sha256(b).hexdigest(),
                      checksum=str(int.from_bytes(hashlib.sha256(b).digest()[:8], "little")))
                 for i, b in enumerate(bodies)]
        ports = [set(), set()]
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            disable_nagle_algorithm = True
            def log_message(self, *_):
                pass
            def reply(self, row):
                body = json.dumps(row).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_GET(self):
                self.reply(dict(inputs=metas, tcp_nodelay=True, compute_concurrency=1)
                           if self.path == "/manifest" else dict(ready=True))
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                meta = metas[bodies.index(body)]
                ident = self.headers["X-Request-ID"]
                if ident.startswith("test-"):
                    ports[int(ident.split("-")[1]) % 2].add(self.client_address[1])
                self.reply(dict(meta, request_id=ident, finite=True, shape=[1,3549,85],
                                status="success", service_ns=1, queue_ns=0))
        class Server(ThreadingHTTPServer):
            request_queue_size = 128
        server = Server(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                for i, b in enumerate(bodies):
                    Path(folder, f"image-{i}.jpg").write_bytes(b)
                endpoint = f"127.0.0.1:{server.server_port}"
                trace = [dict(id=f"test-{i}", scheduled_offset_ns=i*50000000) for i in range(16)]
                rows, _ = run(trace, [endpoint, endpoint], Path(folder), 135, 800, 4)
                self.assertEqual(sum(r["status"] == "success" for r in rows), 16)
                self.assertEqual([len(p) for p in ports], [1, 1],
                                 "steady traffic must not rotate through idle TCP connections")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
