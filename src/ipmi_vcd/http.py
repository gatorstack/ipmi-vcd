from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from ipmi_vcd.controller import IPMI_NODE_BUSY, NodeController
from ipmi_vcd.metrics import Metrics

LOG = logging.getLogger(__name__)


class ApiServer:
    def __init__(
        self,
        address: str,
        port: int,
        controllers: dict[str, NodeController],
        metrics: Metrics,
    ) -> None:
        self.controllers = controllers
        self.metrics = metrics
        self.ready = threading.Event()
        handler = self._handler()
        self.server = ThreadingHTTPServer((address, port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, name="http", daemon=True)

    def start(self) -> None:
        self.thread.start()
        self.ready.set()

    def close(self) -> None:
        self.ready.clear()
        self.server.shutdown()
        self.server.server_close()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                if path == "/healthz":
                    self._json(HTTPStatus.OK, {"status": "ok"})
                    return
                if path == "/readyz":
                    status = (
                        HTTPStatus.OK
                        if owner.ready.is_set()
                        else HTTPStatus.SERVICE_UNAVAILABLE
                    )
                    self._json(status, {"ready": owner.ready.is_set()})
                    return
                if path == "/metrics":
                    payload = generate_latest(owner.metrics.registry)
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", CONTENT_TYPE_LATEST)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if path == "/v1/nodes":
                    self._json(
                        HTTPStatus.OK,
                        {
                            "nodes": [
                                {
                                    "name": item.node.name,
                                    "profile": item.profile.name,
                                    "port": item.node.port,
                                    "provisioning": item.provisioning(),
                                    "bootDevice": item.get_boot_device(),
                                }
                                for item in owner.controllers.values()
                            ]
                        },
                    )
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                parts = urlparse(self.path).path.strip("/").split("/")
                if len(parts) != 4 or parts[:2] != ["v1", "nodes"] or parts[3] != "power-on":
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                controller = owner.controllers.get(parts[2])
                if controller is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "unknown node"})
                    return
                result = controller.power_on()
                if result == IPMI_NODE_BUSY:
                    self._json(HTTPStatus.ACCEPTED, {"status": "provisioning"})
                else:
                    self._json(HTTPStatus.OK, {"status": "powered-on"})

            def _json(self, status: HTTPStatus, body: object) -> None:
                payload = json.dumps(body, separators=(",", ":")).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, fmt: str, *args: object) -> None:
                LOG.debug("http %s", fmt % args)

        return Handler

