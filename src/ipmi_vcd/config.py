from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_MAC_RE = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")


class ConfigError(ValueError):
    """Raised when the service configuration is invalid."""


@dataclass(frozen=True)
class ServerConfig:
    ipmi_address: str
    http_address: str
    http_port: int


@dataclass(frozen=True)
class VcdConfig:
    url: str
    org: str
    vdc: str
    catalog: str
    template: str
    network: str
    storage_profile: str | None
    api_version: str | None
    verify_tls: bool
    ip_allocation_mode: str
    network_adapter_type: str
    name_prefix: str


@dataclass(frozen=True)
class HardwareProfile:
    name: str
    cpu: int
    memory_mib: int


@dataclass(frozen=True)
class NodeConfig:
    name: str
    profile: str
    port: int
    mac_address: str | None = None


@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig
    vcd: VcdConfig
    profiles: dict[str, HardwareProfile]
    nodes: tuple[NodeConfig, ...]


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be a mapping")
    return value


def _required_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{path} must be a positive integer")
    return value


def _port(value: Any, path: str) -> int:
    port = _positive_int(value, path)
    if port > 65535:
        raise ConfigError(f"{path} must be between 1 and 65535")
    return port


def _mac_address(value: Any, path: str) -> str:
    mac = _required_string(value, path).lower().replace("-", ":")
    if not _MAC_RE.fullmatch(mac):
        raise ConfigError(f"{path} must be a valid MAC address")
    first_octet = int(mac[:2], 16)
    if first_octet & 1:
        raise ConfigError(f"{path} must be a unicast MAC address")
    return mac


def load_config(path: str | Path) -> AppConfig:
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {source}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {source}: {exc}") from exc

    root = _mapping(raw, "config")
    server_raw = _mapping(root.get("server", {}), "server")
    server = ServerConfig(
        ipmi_address=str(server_raw.get("ipmiAddress", "0.0.0.0")),
        http_address=str(server_raw.get("httpAddress", "0.0.0.0")),
        http_port=_port(server_raw.get("httpPort", 8080), "server.httpPort"),
    )

    vcd_raw = _mapping(root.get("vcd"), "vcd")
    verify_tls = vcd_raw.get("verifyTLS", True)
    if not isinstance(verify_tls, bool):
        raise ConfigError("vcd.verifyTLS must be a boolean")
    allocation_mode = str(vcd_raw.get("ipAllocationMode", "dhcp")).lower()
    if allocation_mode not in {"dhcp", "pool", "manual"}:
        raise ConfigError("vcd.ipAllocationMode must be dhcp, pool, or manual")
    api_version = vcd_raw.get("apiVersion")
    if api_version is not None:
        api_version = _required_string(api_version, "vcd.apiVersion")
    storage_profile = vcd_raw.get("storageProfile")
    if storage_profile is not None:
        storage_profile = _required_string(storage_profile, "vcd.storageProfile")
    vcd = VcdConfig(
        url=_required_string(vcd_raw.get("url"), "vcd.url").removesuffix("/api").rstrip("/"),
        org=_required_string(vcd_raw.get("org"), "vcd.org"),
        vdc=_required_string(vcd_raw.get("vdc"), "vcd.vdc"),
        catalog=_required_string(vcd_raw.get("catalog"), "vcd.catalog"),
        template=_required_string(vcd_raw.get("template"), "vcd.template"),
        network=_required_string(vcd_raw.get("network"), "vcd.network"),
        storage_profile=storage_profile,
        api_version=api_version,
        verify_tls=verify_tls,
        ip_allocation_mode=allocation_mode,
        network_adapter_type=str(vcd_raw.get("networkAdapterType", "VMXNET3")),
        name_prefix=str(vcd_raw.get("namePrefix", "ironic")),
    )

    profiles_raw = _mapping(root.get("profiles"), "profiles")
    if not profiles_raw:
        raise ConfigError("profiles must contain at least one profile")
    profiles: dict[str, HardwareProfile] = {}
    for name, value in profiles_raw.items():
        profile_name = _required_string(name, "profiles key")
        profile_raw = _mapping(value, f"profiles.{profile_name}")
        profiles[profile_name] = HardwareProfile(
            name=profile_name,
            cpu=_positive_int(profile_raw.get("cpu"), f"profiles.{profile_name}.cpu"),
            memory_mib=_positive_int(
                profile_raw.get("memoryMiB"), f"profiles.{profile_name}.memoryMiB"
            ),
        )

    nodes_raw = root.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise ConfigError("nodes must contain at least one node")
    nodes: list[NodeConfig] = []
    names: set[str] = set()
    ports: set[int] = set()
    mac_addresses: set[str] = set()
    for index, value in enumerate(nodes_raw):
        node_raw = _mapping(value, f"nodes[{index}]")
        name = _required_string(node_raw.get("name"), f"nodes[{index}].name")
        if not _NAME_RE.fullmatch(name):
            raise ConfigError(f"nodes[{index}].name is not a valid lowercase DNS name")
        profile = _required_string(node_raw.get("profile"), f"nodes[{index}].profile")
        port = _port(node_raw.get("port"), f"nodes[{index}].port")
        mac_address = None
        if node_raw.get("macAddress") is not None:
            mac_address = _mac_address(
                node_raw.get("macAddress"), f"nodes[{index}].macAddress"
            )
        if profile not in profiles:
            raise ConfigError(f"nodes[{index}].profile references unknown profile {profile!r}")
        if name in names:
            raise ConfigError(f"duplicate node name {name!r}")
        if port in ports:
            raise ConfigError(f"duplicate IPMI port {port}")
        if mac_address is not None and mac_address in mac_addresses:
            raise ConfigError(f"duplicate MAC address {mac_address!r}")
        names.add(name)
        ports.add(port)
        if mac_address is not None:
            mac_addresses.add(mac_address)
        nodes.append(
            NodeConfig(
                name=name,
                profile=profile,
                port=port,
                mac_address=mac_address,
            )
        )

    return AppConfig(server=server, vcd=vcd, profiles=profiles, nodes=tuple(nodes))

