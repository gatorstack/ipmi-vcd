import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from test_controller import FakeBackend

from ipmi_vcd.config import HardwareProfile, NodeConfig
from ipmi_vcd.controller import NodeController
from ipmi_vcd.http import ApiServer
from ipmi_vcd.metrics import Metrics


def test_health_and_inventory() -> None:
    backend = FakeBackend()
    metrics = Metrics()
    with ThreadPoolExecutor(max_workers=1) as executor:
        controller = NodeController(
            NodeConfig("ru-msk-bm-01", "8-16", 6230),
            HardwareProfile("8-16", 8, 16384),
            backend,
            executor,
            metrics,
        )
        server = ApiServer("127.0.0.1", 0, {controller.node.name: controller}, metrics)
        server.start()
        try:
            port = server.server.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz") as response:
                assert response.status == 200
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/nodes") as response:
                body = json.load(response)
            assert body["nodes"] == [
                {
                    "name": "ru-msk-bm-01",
                    "profile": "8-16",
                    "port": 6230,
                    "provisioning": False,
                    "bootDevice": "network",
                }
            ]
        finally:
            server.close()

