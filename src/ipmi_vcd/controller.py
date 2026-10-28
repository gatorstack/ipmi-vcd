from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from ipmi_vcd.backend import Backend
from ipmi_vcd.config import HardwareProfile, NodeConfig
from ipmi_vcd.metrics import Metrics

LOG = logging.getLogger(__name__)
IPMI_NODE_BUSY = 0xC0
IPMI_INVALID_DATA = 0xCC
_REFRESH_STATE = object()


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
        self._future: Future[Any] | None = executor.submit(
            backend.get_power_state, node
        )
        self._future_operation = "power_state"
        self._future_state: bool | object = _REFRESH_STATE
        self._future_network_boot = False
        self._power_state = False
        self._lock = threading.Lock()
        self._boot_device = "default"
        self._prepare_for_network_boot = False

    def _record(self, operation: str, result: str) -> None:
        self.metrics.operations.labels(self.node.name, operation, result).inc()

    def power_state(self) -> int:
        with self._lock:
            self._finish_future_locked()
            return 1 if self._power_state else 0

    def refresh_power_state(self) -> None:
        """Refresh cached VCD state in a worker when the node is otherwise idle."""
        with self._lock:
            self._finish_future_locked()
            if self._future is not None:
                return
            self._future = self.executor.submit(
                self.backend.get_power_state, self.node
            )
            self._future_operation = "power_state"
            self._future_state = _REFRESH_STATE

    def _finish_future_locked(self) -> bool:
        """Collect a completed VCD operation without blocking the IPMI loop."""
        if self._future is None:
            return True
        if not self._future.done():
            return False
        operation = self._future_operation
        state = self._future_state
        try:
            result = self._future.result()
            self._power_state = bool(result) if state is _REFRESH_STATE else bool(state)
            if operation == "power_on" and self._future_network_boot:
                self._prepare_for_network_boot = False
            self._record(operation, "success")
        except Exception:  # noqa: BLE001
            self._record(operation, "error")
            LOG.exception("asynchronous %s failed for node=%s", operation, self.node.name)
        finally:
            self._future = None
            self._future_operation = ""
            self._future_state = _REFRESH_STATE
            self._future_network_boot = False
            self.metrics.provisioning.labels(self.node.name).set(0)
        return True

    def _start_async(
        self,
        operation: str,
        action: Callable[..., Any],
        target_state: bool,
        *args: object,
    ) -> int | None:
        with self._lock:
            if not self._finish_future_locked():
                self._record(operation, "busy")
                return IPMI_NODE_BUSY
            if self._power_state == target_state and operation in {"power_on", "power_off"}:
                self._record(operation, "success")
                return None
            self._future = self.executor.submit(action, self.node, *args)
            self._future_operation = operation
            self._future_state = target_state
            self.metrics.provisioning.labels(self.node.name).set(1)
            self._record(operation, "busy")
            return IPMI_NODE_BUSY

    def power_on(self) -> int | None:
        with self._lock:
            if not self._finish_future_locked():
                self._record("power_on", "busy")
                return IPMI_NODE_BUSY
            if self._power_state and not self._prepare_for_network_boot:
                self._record("power_on", "success")
                return None
            prepare_for_network_boot = self._prepare_for_network_boot
            self._future = self.executor.submit(
                self.backend.ensure_power_on,
                self.node,
                self.profile,
                prepare_for_network_boot,
            )
            self._future_operation = "power_on"
            self._future_state = True
            self._future_network_boot = prepare_for_network_boot
            self.metrics.provisioning.labels(self.node.name).set(1)
            self._record("power_on", "busy")
            return IPMI_NODE_BUSY

    def power_off(self) -> int | None:
        return self._start_async("power_off", self.backend.power_off, False)

    def power_reset(self) -> int | None:
        with self._lock:
            prepare_for_network_boot = self._prepare_for_network_boot
        if prepare_for_network_boot:
            return self.power_on()
        if self.power_state() == 0:
            return self.power_on()
        return self._start_async("power_reset", self.backend.power_reset, True)

    def shutdown(self) -> int | None:
        return self._start_async("shutdown", self.backend.shutdown, False)

    def get_boot_device(self) -> str:
        with self._lock:
            return self._boot_device

    def set_boot_device(self, device: str) -> int | None:
        if device not in {"network", "hd", "optical", "default"}:
            self._record("set_boot_device", "invalid")
            return IPMI_INVALID_DATA
        with self._lock:
            self._boot_device = device
            self._prepare_for_network_boot = device == "network"
        self._record("set_boot_device", "success")
        LOG.info(
            "stored boot device node=%s device=%s prepare_for_network_boot=%s",
            self.node.name,
            device,
            self._prepare_for_network_boot,
        )
        return None

    def provisioning(self) -> bool:
        with self._lock:
            self._finish_future_locked()
            return self._future is not None

