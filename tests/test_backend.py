from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from lxml import objectify

import ipmi_vcd.backend as backend_module
from ipmi_vcd.backend import NSMAP, BackendError, VcdBackend
from ipmi_vcd.config import NodeConfig


def disk_section(items: str) -> object:
    return objectify.fromstring(
        f'<RasdItemsList xmlns:rasd="{NSMAP["rasd"]}">{items}</RasdItemsList>'.encode()
    )


def item(instance_id: str, resource_type: str, *, parent: str = "", subtype: str = "") -> str:
    parent_xml = f"<rasd:Parent>{parent}</rasd:Parent>" if parent else ""
    subtype_xml = f"<rasd:ResourceSubType>{subtype}</rasd:ResourceSubType>" if subtype else ""
    return (
        "<Item>"
        "<rasd:Address>0</rasd:Address>"
        f"<rasd:InstanceID>{instance_id}</rasd:InstanceID>"
        f"{parent_xml}{subtype_xml}"
        f"<rasd:ResourceType>{resource_type}</rasd:ResourceType>"
        "</Item>"
    )


def value(resource: object, name: str) -> str:
    return str(resource[f"{{{NSMAP['rasd']}}}{name}"])


def test_template_disk_merge_remaps_controller_recreated_by_vcd() -> None:
    current = disk_section(item("2", "5"))
    template = disk_section(
        item("2", "6", subtype="VirtualSCSI")
        + item("2000", "17", parent="2")
        + item("3", "5")
    )

    backend = object.__new__(VcdBackend)
    backend._merge_template_disk_items(current, template)

    items = list(current.Item)
    assert [value(entry, "ResourceType") for entry in items] == ["5", "6", "17"]
    assert [value(entry, "InstanceID") for entry in items] == ["2", "3", "2000"]
    assert value(items[-1], "Parent") == "3"


def test_empty_managed_vapp_is_deleted_before_recreation(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = object.__new__(VcdBackend)
    backend.config = SimpleNamespace(name_prefix="ironic")
    backend._wait = Mock()
    vdc = Mock()
    vdc.get_vapp.return_value = object()
    vdc.delete_vapp.return_value = "delete-task"
    backend._vdc = Mock(return_value=vdc)
    vapp = Mock()
    vapp.get_all_vms.return_value = []
    monkeypatch.setattr(backend_module, "VApp", Mock(return_value=vapp))

    node = NodeConfig("ru-msk-bm-01", "8-16", 6230)
    client = object()
    backend._delete_empty_vapp(client, node)

    vdc.delete_vapp.assert_called_once_with("ironic-ru-msk-bm-01", force=True)
    backend._wait.assert_called_once_with(client, "delete-task")


def test_nonempty_managed_vapp_is_never_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = object.__new__(VcdBackend)
    backend.config = SimpleNamespace(name_prefix="ironic")
    vdc = Mock()
    vdc.get_vapp.return_value = object()
    backend._vdc = Mock(return_value=vdc)
    vapp = Mock()
    vapp.get_all_vms.return_value = [SimpleNamespace(get=lambda key: "unexpected-vm")]
    monkeypatch.setattr(backend_module, "VApp", Mock(return_value=vapp))

    node = NodeConfig("ru-msk-bm-01", "8-16", 6230)
    with pytest.raises(BackendError, match="refusing to replace non-empty vApp"):
        backend._delete_empty_vapp(object(), node)
    vdc.delete_vapp.assert_not_called()
