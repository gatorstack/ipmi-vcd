from pathlib import Path

import pytest

from ipmi_vcd.config import ConfigError, load_config

EXAMPLE = Path(__file__).parents[1] / "config" / "example.yaml"


def test_example_has_ten_nodes_and_two_profiles() -> None:
    config = load_config(EXAMPLE)

    assert len(config.nodes) == 10
    assert {profile.name for profile in config.profiles.values()} == {"8-16", "16-32"}
    assert len({node.port for node in config.nodes}) == 10
    assert sum(node.profile == "8-16" for node in config.nodes) == 5
    assert sum(node.profile == "16-32" for node in config.nodes) == 5


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

