from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

import pyvcloud.vcd.client as vcd_client
from pyvcloud.vcd.client import BasicLoginCredentials, Client
from pyvcloud.vcd.exceptions import EntityNotFoundException

from ipmi_vcd.config import HardwareProfile, NodeConfig, VcdConfig

# pyvcloud creates a debug RotatingFileHandler at import time in every high-level
# module. The service runs with a read-only root filesystem and must not persist
# request headers or credentials in an SDK log file.
vcd_client.get_logger = lambda *args, **kwargs: logging.getLogger("pyvcloud")

from pyvcloud.vcd.org import Org  # noqa: E402
from pyvcloud.vcd.vapp import VApp  # noqa: E402
from pyvcloud.vcd.vdc import VDC  # noqa: E402
from pyvcloud.vcd.vm import VM  # noqa: E402

LOG = logging.getLogger(__name__)


class BackendError(RuntimeError):
    """Raised when an operation against Cloud Director fails."""


class Backend(Protocol):
    def get_power_state(self, node: NodeConfig) -> bool | None: ...

    def ensure_power_on(self, node: NodeConfig, profile: HardwareProfile) -> None: ...

    def power_off(self, node: NodeConfig) -> None: ...

    def power_reset(self, node: NodeConfig) -> None: ...

    def shutdown(self, node: NodeConfig) -> None: ...


class VcdBackend:
    def __init__(self, config: VcdConfig) -> None:
        self.config = config
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _node_lock(self, node_name: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(node_name, threading.Lock())

    @contextmanager
    def _session(self) -> Iterator[Client]:
        kwargs = {"verify_ssl_certs": self.config.verify_tls}
        if self.config.api_version:
            kwargs["api_version"] = self.config.api_version
        client = Client(self.config.url, **kwargs)
        token = os.getenv("VCD_TOKEN")
        if token:
            auth_type = os.getenv("VCD_AUTH_TYPE", "jwt").lower()
            client.rehydrate_from_token(token, is_jwt_token=auth_type == "jwt")
        else:
            username = os.getenv("VCD_USER")
            password = os.getenv("VCD_PASSWORD")
            if not username or not password:
                raise BackendError("VCD_USER and VCD_PASSWORD, or VCD_TOKEN, must be set")
            client.set_credentials(BasicLoginCredentials(username, self.config.org, password))
        try:
            yield client
        finally:
            try:
                client.logout()
            except Exception:  # noqa: BLE001
                LOG.debug("Cloud Director logout failed", exc_info=True)

    def _vdc(self, client: Client) -> VDC:
        org = Org(client, resource=client.get_org())
        return VDC(client, resource=org.get_vdc(self.config.vdc))

    def _vapp_name(self, node: NodeConfig) -> str:
        return f"{self.config.name_prefix}-{node.name}"

    def _find_vm(self, client: Client, node: NodeConfig) -> VM | None:
        vdc = self._vdc(client)
        try:
            vapp_resource = vdc.get_vapp(self._vapp_name(node))
        except EntityNotFoundException:
            return None
        vapp = VApp(client, resource=vapp_resource)
        try:
            vm_resource = vapp.get_vm(node.name)
        except EntityNotFoundException as exc:
            raise BackendError(
                f"vApp {self._vapp_name(node)!r} exists but VM {node.name!r} is missing"
            ) from exc
        return VM(client, resource=vm_resource)

    @staticmethod
    def _wait(client: Client, task: object | None) -> None:
        if task is not None:
            client.get_task_monitor().wait_for_success(task)

    @staticmethod
    def _creation_task(resource: object) -> object | None:
        tasks = getattr(resource, "Tasks", None)
        task_list = getattr(tasks, "Task", None)
        return task_list[0] if task_list else None

    def get_power_state(self, node: NodeConfig) -> bool | None:
        try:
            with self._session() as client:
                vm = self._find_vm(client, node)
                return None if vm is None else bool(vm.is_powered_on())
        except BackendError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BackendError(f"cannot read VCD power state for {node.name}: {exc}") from exc

    def ensure_power_on(self, node: NodeConfig, profile: HardwareProfile) -> None:
        with self._node_lock(node.name):
            try:
                with self._session() as client:
                    vm = self._find_vm(client, node)
                    if vm is None:
                        LOG.info(
                            "creating VCD VM node=%s profile=%s cpu=%d memoryMiB=%d",
                            node.name,
                            profile.name,
                            profile.cpu,
                            profile.memory_mib,
                        )
                        vdc = self._vdc(client)
                        resource = vdc.instantiate_vapp(
                            name=self._vapp_name(node),
                            catalog=self.config.catalog,
                            template=self.config.template,
                            description=(
                                f"Managed by ipmi-vcd; node={node.name}; profile={profile.name}"
                            ),
                            network=self.config.network,
                            ip_allocation_mode=self.config.ip_allocation_mode,
                            deploy=True,
                            power_on=False,
                            memory=profile.memory_mib,
                            cpu=profile.cpu,
                            vm_name=node.name,
                            storage_profile=self.config.storage_profile,
                            network_adapter_type=self.config.network_adapter_type,
                            accept_all_eulas=True,
                        )
                        self._wait(client, self._creation_task(resource))
                        vm = self._find_vm(client, node)
                        if vm is None:
                            raise BackendError(f"VCD created no VM for node {node.name}")
                    if not vm.is_powered_on():
                        self._wait(client, vm.power_on())
            except BackendError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise BackendError(f"cannot ensure VCD VM for {node.name}: {exc}") from exc

    def power_off(self, node: NodeConfig) -> None:
        self._power_action(node, "power off", lambda vm: vm.power_off())

    def power_reset(self, node: NodeConfig) -> None:
        self._power_action(node, "reset", lambda vm: vm.power_reset(), require_vm=True)

    def shutdown(self, node: NodeConfig) -> None:
        self._power_action(node, "shutdown", lambda vm: vm.shutdown())

    def _power_action(
        self,
        node: NodeConfig,
        operation: str,
        action: object,
        *,
        require_vm: bool = False,
    ) -> None:
        with self._node_lock(node.name):
            try:
                with self._session() as client:
                    vm = self._find_vm(client, node)
                    if vm is None:
                        if require_vm:
                            raise BackendError(f"node {node.name} has no VCD VM")
                        return
                    if operation in {"power off", "shutdown"} and not vm.is_powered_on():
                        return
                    self._wait(client, action(vm))
            except BackendError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise BackendError(f"cannot {operation} VCD VM for {node.name}: {exc}") from exc

