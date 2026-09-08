import io
import json
import os
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


def make_tarball(files):
    """Build a gzipped tarball whose members live under one top level directory."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            payload = content.encode("utf-8")
            info = tarfile.TarInfo("package/" + name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


class FakeRegistry:
    """A registry that answers the three calls tfmodcache makes."""

    def __init__(self):
        self.versions = {}
        self.archives = {}
        self.calls = []

    def publish(self, namespace, name, provider, version, files):
        path = "{}/{}/{}".format(namespace, name, provider)
        self.versions.setdefault(path, []).append(version)
        self.archives["{}/{}".format(path, version)] = make_tarball(files)


@pytest.fixture
def registry_server():
    registry = FakeRegistry()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802 - name imposed by http.server
            registry.calls.append(self.path)
            path = self.path.split("?", 1)[0]
            if path == "/.well-known/terraform.json":
                return self._json({"modules.v1": "/v1/modules/"})
            if path.startswith("/v1/modules/") and path.endswith("/versions"):
                key = path[len("/v1/modules/") : -len("/versions")]
                versions = registry.versions.get(key)
                if not versions:
                    return self._empty(404)
                return self._json(
                    {"modules": [{"versions": [{"version": item} for item in versions]}]}
                )
            if path.startswith("/v1/modules/") and path.endswith("/download"):
                key = path[len("/v1/modules/") : -len("/download")]
                if key not in registry.archives:
                    return self._empty(404)
                self.send_response(204)
                self.send_header("X-Terraform-Get", "/archives/{}.tar.gz".format(key))
                self.end_headers()
                return None
            if path.startswith("/archives/") and path.endswith(".tar.gz"):
                key = path[len("/archives/") : -len(".tar.gz")]
                payload = registry.archives.get(key)
                if payload is None:
                    return self._empty(404)
                self.send_response(200)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return None
            return self._empty(404)

        def _json(self, document):
            payload = json.dumps(document).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _empty(self, status):
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = "127.0.0.1:{}".format(server.server_port)
    previous = os.environ.get("TFMODCACHE_HTTP_REGISTRIES")
    os.environ["TFMODCACHE_HTTP_REGISTRIES"] = host
    registry.host = host
    try:
        yield registry
    finally:
        if previous is None:
            os.environ.pop("TFMODCACHE_HTTP_REGISTRIES", None)
        else:
            os.environ["TFMODCACHE_HTTP_REGISTRIES"] = previous
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def project(tmp_path):
    directory = tmp_path / "project"
    directory.mkdir()
    return directory


@pytest.fixture
def store(tmp_path):
    from tfmodcache.store import Store

    return Store(str(tmp_path / "cache"))
