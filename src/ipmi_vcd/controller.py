from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from ipmi_vcd.backend import Backend
from ipmi_vcd.config import HardwareProfile, NodeConfig
from ipmi_vcd.metrics import Metrics

LOG = logging.getLogger(__name__)
IPMI_NODE_BUSY = 0xC0
IPMI_INVALID_DATA = 0xCC


class NodeController:
    def __init__(
        self,
        node: NodeConfig,
        profile: HardwareProfile,
        backend: Backend,
        executor: ThreadPoolExecutor,
        metrics: Metrics,
    ) -> None:
        self.node = node
        self.profile = profile
        self.backend = backend
        self.executor = executor
        self.metrics = metrics
        self._future: Future[None] | None = None
        self._lock = threading.Lock()
        self._boot_device = "network"

    def _record(self, operation: str, result: str) -> None:
        self.metrics.operations.labels(self.node.name, operation, result).inc()

    def power_state(self) -> int:
        try:
            active = self.backend.get_power_state(self.node)
            self._record("power_state", "success")
            return 1 if active else 0
        except Exception:  # noqa: BLE001
            self._record("power_state", "error")
            LOG.exception("cannot read power state for node=%s", self.node.name)
            return 0

    def power_on(self) -> int | None:
        with self._lock:
            if self._future is not None:
                if not self._future.done():
                    self._record("power_on", "busy")
                    return IPMI_NODE_BUSY
                try:
                    self._future.result()
                except Exception:  # noqa: BLE001
                    self._future = None
                    self.metrics.provisioning.labels(self.node.name).set(0)
                    self._record("power_on", "error")
                    LOG.exception("asynchronous power on failed for node=%s", self.node.name)
                    return IPMI_NODE_BUSY
                self._future = None
                self.metrics.provisioning.labels(self.node.name).set(0)
                self._record("power_on", "success")
                return None

            try:
                if self.backend.get_power_state(self.node):
                    self._record("power_on", "success")
                    return None
            except Exception:  # noqa: BLE001
                LOG.warning("preflight power-state check failed for node=%s", self.node.name)

            self._future = self.executor.submit(
                self.backend.ensure_power_on, self.node, self.profile
            )
            self.metrics.provisioning.labels(self.node.name).set(1)
            self._record("power_on", "busy")
            return IPMI_NODE_BUSY

    def power_off(self) -> int | None:
        with self._lock:
            if self._future is not None and not self._future.done():
                self._record("power_off", "busy")
                return IPMI_NODE_BUSY
        return self._run("power_off", self.backend.power_off)

    def power_reset(self) -> int | None:
        if self.power_state() == 0:
            return self.power_on()
        return self._run("power_reset", self.backend.power_reset)

    def shutdown(self) -> int | None:
        return self._run("shutdown", self.backend.shutdown)

    def _run(self, operation: str, action: object) -> int | None:
        try:
            action(self.node)
            self._record(operation, "success")
            return None
        except Exception:  # noqa: BLE001
            self._record(operation, "error")
            LOG.exception("%s failed for node=%s", operation, self.node.name)
            return IPMI_NODE_BUSY

    def get_boot_device(self) -> str:
        with self._lock:
            return self._boot_device

    def set_boot_device(self, device: str) -> int | None:
        if device not in {"network", "hd", "optical", "default"}:
            self._record("set_boot_device", "invalid")
            return IPMI_INVALID_DATA
        with self._lock:
            self._boot_device = device
        self._record("set_boot_device", "advisory")
        LOG.info(
            "stored advisory boot device node=%s device=%s; "
            "VCD template boot order is authoritative",
            self.node.name,
            device,
        )
        return None

    def provisioning(self) -> bool:
        with self._lock:
            return self._future is not None and not self._future.done()

