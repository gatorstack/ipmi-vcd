import time
from concurrent.futures import ThreadPoolExecutor

from ipmi_vcd.config import HardwareProfile, NodeConfig
from ipmi_vcd.controller import IPMI_INVALID_DATA, IPMI_NODE_BUSY, NodeController
from ipmi_vcd.metrics import Metrics


class FakeBackend:
    def __init__(self) -> None:
        self.powered = False
        self.ensure_calls = 0
        self.power_off_calls = 0

    def get_power_state(self, node: NodeConfig) -> bool:
        return self.powered

    def ensure_power_on(self, node: NodeConfig, profile: HardwareProfile) -> None:
        self.ensure_calls += 1
        self.powered = True

    def power_off(self, node: NodeConfig) -> None:
        self.power_off_calls += 1
        self.powered = False

    def power_reset(self, node: NodeConfig) -> None:
        self.powered = True

    def shutdown(self, node: NodeConfig) -> None:
        self.powered = False


def controller(backend: FakeBackend, executor: ThreadPoolExecutor) -> NodeController:
    return NodeController(
        NodeConfig("ru-msk-bm-01", "8-16", 6230),
        HardwareProfile("8-16", 8, 16384),
        backend,
        executor,
        Metrics(),
    )


def test_first_power_on_is_async_and_retries_complete() -> None:
    backend = FakeBackend()
    with ThreadPoolExecutor(max_workers=1) as executor:
        item = controller(backend, executor)
        assert item.power_on() == IPMI_NODE_BUSY
        for _ in range(100):
            result = item.power_on()
            if result is None:
                break
            time.sleep(0.01)
        assert result is None
        assert backend.ensure_calls == 1
        assert item.power_state() == 1


def test_power_off_is_idempotent() -> None:
    backend = FakeBackend()
    backend.powered = True
    with ThreadPoolExecutor(max_workers=1) as executor:
        item = controller(backend, executor)
        assert item.power_off() is None
        assert backend.power_off_calls == 1
        assert item.power_state() == 0


def test_boot_device_is_advisory_but_validated() -> None:
    backend = FakeBackend()
    with ThreadPoolExecutor(max_workers=1) as executor:
        item = controller(backend, executor)
        assert item.get_boot_device() == "network"
        assert item.set_boot_device("hd") is None
        assert item.get_boot_device() == "hd"
        assert item.set_boot_device("floppy") == IPMI_INVALID_DATA

