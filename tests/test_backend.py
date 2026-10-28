from lxml import objectify

from ipmi_vcd.backend import NSMAP, VcdBackend


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
