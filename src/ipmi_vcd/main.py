from __future__ import annotations

import argparse
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from pyghmi.ipmi.bmc import Bmc

from ipmi_vcd.backend import VcdBackend
from ipmi_vcd.bmc import VcdBmc
from ipmi_vcd.config import ConfigError, load_config
from ipmi_vcd.controller import NodeController
from ipmi_vcd.http import ApiServer
from ipmi_vcd.metrics import Metrics

LOG = logging.getLogger(__name__)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IPMI facade backed by VMware Cloud Director")
    parser.add_argument(
        "--config",
        default=os.getenv("IPMI_VCD_CONFIG", "/etc/ipmi-vcd/config.yaml"),
        help="configuration file path",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = _arguments()
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        raise SystemExit(f"configuration error: {exc}") from exc

    username = os.getenv("IPMI_USERNAME")
    password = os.getenv("IPMI_PASSWORD")
    if not username or not password:
        raise SystemExit("IPMI_USERNAME and IPMI_PASSWORD must be set")

    backend = VcdBackend(config.vcd)
    metrics = Metrics()
    executor = ThreadPoolExecutor(max_workers=max(2, len(config.nodes)))
    controllers = {
        node.name: NodeController(
            node=node,
            profile=config.profiles[node.profile],
            backend=backend,
            executor=executor,
            metrics=metrics,
        )
        for node in config.nodes
    }
    bmcs = [
        VcdBmc(
            controller=controllers[node.name],
            username=username,
            password=password,
            address=config.server.ipmi_address,
        )
        for node in config.nodes
    ]
    api = ApiServer(
        config.server.http_address,
        config.server.http_port,
        controllers,
        metrics,
    )
    api.start()
    LOG.info(
        "ready nodes=%d IPMI-address=%s HTTP=%s:%d",
        len(bmcs),
        config.server.ipmi_address,
        config.server.http_address,
        config.server.http_port,
    )
    try:
        Bmc.listen()
    finally:
        api.close()
        executor.shutdown(wait=False, cancel_futures=True)

