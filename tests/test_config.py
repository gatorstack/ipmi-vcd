from pathlib import Path

import pytest

from ipmi_vcd.config import ConfigError, load_config

EXAMPLE = Path(__file__).parents[1] / "config" / "example.yaml"


def test_example_has_twenty_nodes_and_two_profiles() -> None:
    config = load_config(EXAMPLE)

    assert len(config.nodes) == 20
    assert {profile.name for profile in config.profiles.values()} == {"8-16", "16-32"}
    assert len({node.port for node in config.nodes}) == 20
    assert len({node.mac_address for node in config.nodes}) == 20
    assert sum(node.profile == "8-16" for node in config.nodes) == 10
    assert sum(node.profile == "16-32" for node in config.nodes) == 10


def test_unknown_profile_is_rejected(tmp_path: Path) -> None:
    text = EXAMPLE.read_text().replace("profile: 8-16", "profile: missing", 1)
    source = tmp_path / "config.yaml"
    source.write_text(text)

    with pytest.raises(ConfigError, match="unknown profile"):
        load_config(source)


def test_duplicate_port_is_rejected(tmp_path: Path) -> None:
    text = EXAMPLE.read_text().replace("port: 6231", "port: 6230", 1)
    source = tmp_path / "config.yaml"
    source.write_text(text)

    with pytest.raises(ConfigError, match="duplicate IPMI port"):
        load_config(source)


def test_duplicate_mac_is_rejected(tmp_path: Path) -> None:
    text = EXAMPLE.read_text().replace(
        "macAddress: 00:50:56:10:00:02",
        "macAddress: 00:50:56:05:e9:2c",
        1,
    )
    source = tmp_path / "config.yaml"
    source.write_text(text)

    with pytest.raises(ConfigError, match="duplicate MAC address"):
        load_config(source)

