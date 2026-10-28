from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from typing import Protocol

import pyvcloud.vcd.client as vcd_client
from pyvcloud.vcd.client import (
    NSMAP,
    BasicLoginCredentials,
    Client,
    E,
    EntityType,
    RelationType,
)
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

    def ensure_power_on(
        self,
        node: NodeConfig,
        profile: HardwareProfile,
        prepare_for_network_boot: bool = False,
    ) -> None: ...

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
        kwargs = {
            "verify_ssl_certs": self.config.verify_tls,
            # Client creates its own file handler even when request logging is
            # disabled. /tmp is an emptyDir in the read-only-root deployment.
            "log_file": "/tmp/vcd_pysdk.log",
            "log_requests": False,
            "log_headers": False,
            "log_bodies": False,
        }
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

    def _find_vapp(self, client: Client, node: NodeConfig) -> VApp | None:
        vdc = self._vdc(client)
        try:
            vapp_resource = vdc.get_vapp(self._vapp_name(node))
        except EntityNotFoundException:
            return None
        return VApp(client, resource=vapp_resource)

    def _find_vm(self, client: Client, node: NodeConfig) -> VM | None:
        vapp = self._find_vapp(client, node)
        if vapp is None:
            return None
        try:
            vm_resource = vapp.get_vm(node.name)
        except EntityNotFoundException:
            vm_names = [str(vm.get("name")) for vm in vapp.get_all_vms()]
            if vm_names:
                raise BackendError(
                    f"vApp {self._vapp_name(node)!r} contains unexpected VMs: "
                    f"{', '.join(vm_names)}"
                ) from None
            LOG.info("managed vApp is empty node=%s", node.name)
            return None
        return VM(client, resource=vm_resource)

    def _delete_empty_vapp(self, client: Client, node: NodeConfig) -> None:
        """Remove an empty managed vApp so lazy VM creation can reuse its name."""
        vapp = self._find_vapp(client, node)
        if vapp is None:
            return
        vm_names = [str(vm.get("name")) for vm in vapp.get_all_vms()]
        if vm_names:
            raise BackendError(
                f"refusing to replace non-empty vApp {self._vapp_name(node)!r}: "
                f"{', '.join(vm_names)}"
            )
        LOG.info("deleting empty managed vApp before recreation node=%s", node.name)
        self._wait(
            client,
            self._vdc(client).delete_vapp(self._vapp_name(node), force=True),
        )

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

    @staticmethod
    def _hard_disk_count(disk_section: object) -> int:
        resource_type = f"{{{NSMAP['rasd']}}}ResourceType"
        return sum(
            1
            for item in getattr(disk_section, "Item", [])
            if str(item[resource_type]) == "17"
        )

    @staticmethod
    def _rasd_value(item: object, name: str) -> str | None:
        try:
            return str(item[f"{{{NSMAP['rasd']}}}{name}"])
        except (AttributeError, KeyError):
            return None

    @staticmethod
    def _set_rasd_value(item: object, name: str, value: str) -> None:
        item[f"{{{NSMAP['rasd']}}}{name}"]._setText(value)

    def _merge_template_disk_items(
        self, current_disks: object, template_disks: object
    ) -> None:
        """Merge the pristine disk while reusing controllers VCD recreated."""
        current_items = list(getattr(current_disks, "Item", []))
        used_ids = {
            value
            for item in current_items
            if (value := self._rasd_value(item, "InstanceID")) is not None
        }

        def controller_key(item: object) -> tuple[str | None, str | None, str | None]:
            return (
                self._rasd_value(item, "ResourceType"),
                self._rasd_value(item, "ResourceSubType"),
                self._rasd_value(item, "Address"),
            )

        existing_controllers = {
            controller_key(item): self._rasd_value(item, "InstanceID")
            for item in current_items
            if self._rasd_value(item, "ResourceType") != "17"
        }
        controller_ids: dict[str, str] = {}

        def next_id() -> str:
            candidate = max(
                (int(value) for value in used_ids if value.isdigit()), default=0
            ) + 1
            while str(candidate) in used_ids:
                candidate += 1
            value = str(candidate)
            used_ids.add(value)
            return value

        template_items = list(getattr(template_disks, "Item", []))
        for item in template_items:
            if self._rasd_value(item, "ResourceType") == "17":
                continue
            source_id = self._rasd_value(item, "InstanceID")
            if source_id is None:
                raise BackendError("VCD template controller has no InstanceID")
            existing_id = existing_controllers.get(controller_key(item))
            if existing_id is not None:
                controller_ids[source_id] = existing_id
                continue
            controller = deepcopy(item)
            target_id = source_id if source_id not in used_ids else next_id()
            used_ids.add(target_id)
            self._set_rasd_value(controller, "InstanceID", target_id)
            current_disks.append(controller)
            controller_ids[source_id] = target_id

        for item in template_items:
            if self._rasd_value(item, "ResourceType") != "17":
                continue
            disk = deepcopy(item)
            source_parent = self._rasd_value(disk, "Parent")
            if source_parent not in controller_ids:
                raise BackendError(
                    f"VCD template disk references unknown controller {source_parent!r}"
                )
            self._set_rasd_value(disk, "Parent", controller_ids[source_parent])
            source_id = self._rasd_value(disk, "InstanceID")
            if source_id is None or source_id in used_ids:
                self._set_rasd_value(disk, "InstanceID", next_id())
            else:
                used_ids.add(source_id)
            current_disks.append(disk)

    def _template_disk_section(self, client: Client) -> object:
        org = Org(client, resource=client.get_org())
        template_href = org.get_vapp_template_href(
            self.config.catalog, self.config.template
        )
        template = client.get_resource(template_href)
        children = getattr(template, "Children", None)
        vms = [
            child
            for child in getattr(children, "iterchildren", lambda: [])()
            if str(child.tag).rsplit("}", 1)[-1] == "Vm"
        ]
        if len(vms) != 1:
            raise BackendError(
                f"VCD template {self.config.template!r} must contain exactly one VM"
            )
        return client.get_resource(
            f"{vms[0].get('href')}/virtualHardwareSection/disks"
        )

    def _replace_system_disk_from_template(self, client: Client, vm: VM) -> None:
        """Replace the powered-off VM disk with the pristine template disk."""
        template_disks = self._template_disk_section(client)
        current_uri = f"{vm.href}/virtualHardwareSection/disks"
        current_disks = client.get_resource(current_uri)
        if self._hard_disk_count(template_disks) != 1:
            raise BackendError(
                f"VCD template {self.config.template!r} must contain one hard disk"
            )
        current_disk_count = self._hard_disk_count(current_disks)
        if current_disk_count > 1:
            raise BackendError(
                "VCD VM must contain at most one hard disk before PXE preparation"
            )

        # VCD removes an empty SCSI controller automatically. Submit the empty
        # section first, refetch it, and then restore both the pristine
        # controller and disk from the template in one update.
        current_items = list(getattr(current_disks, "Item", []))
        if current_items:
            for item in current_items:
                current_disks.remove(item)
            self._wait(
                client,
                client.put_resource(
                    current_uri, current_disks, EntityType.RASD_ITEMS_LIST.value
                ),
            )

        empty_disks = client.get_resource(current_uri)
        self._merge_template_disk_items(empty_disks, template_disks)
        self._wait(
            client,
            client.put_resource(
                current_uri, empty_disks, EntityType.RASD_ITEMS_LIST.value
            ),
        )

    def _ensure_mac_address(self, client: Client, vm: VM, node: NodeConfig) -> None:
        """Apply the declarative NIC MAC before the first VM power-on."""
        if node.mac_address is None:
            return
        vm_resource = vm.get_resource()
        section = vm_resource.NetworkConnectionSection
        primary_index = str(section.PrimaryNetworkConnectionIndex)
        connections = [
            connection
            for connection in section.NetworkConnection
            if str(connection.NetworkConnectionIndex) == primary_index
        ]
        if len(connections) != 1:
            raise BackendError(f"VCD VM {node.name} must contain one primary NIC")
        connection = connections[0]
        current = str(connection.MACAddress).lower()
        if current == node.mac_address:
            return
        if vm.is_powered_on():
            raise BackendError(
                f"VCD VM {node.name} is powered on with MAC {current}, "
                f"expected {node.mac_address}"
            )
        connection.MACAddress = E.MACAddress(node.mac_address)
        self._wait(
            client,
            client.put_linked_resource(
                section,
                RelationType.EDIT,
                EntityType.NETWORK_CONNECTION_SECTION.value,
                section,
            ),
        )
        vm.reload()

    def ensure_power_on(
        self,
        node: NodeConfig,
        profile: HardwareProfile,
        prepare_for_network_boot: bool = False,
    ) -> None:
        with self._node_lock(node.name):
            try:
                with self._session() as client:
                    vm = self._find_vm(client, node)
                    created = vm is None
                    if vm is None:
                        self._delete_empty_vapp(client, node)
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
                    self._ensure_mac_address(client, vm, node)
                    if prepare_for_network_boot and not created:
                        if vm.is_powered_on():
                            self._wait(client, vm.power_off())
                        LOG.info(
                            "replacing system disk from template before PXE node=%s",
                            node.name,
                        )
                        self._replace_system_disk_from_template(client, vm)
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

