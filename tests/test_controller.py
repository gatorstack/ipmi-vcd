import time
from concurrent.futures import ThreadPoolExecutor

from ipmi_vcd.config import HardwareProfile, NodeConfig
from ipmi_vcd.controller import IPMI_INVALID_DATA, IPMI_NODE_BUSY, NodeController
from ipmi_vcd.metrics import Metrics


class FakeBackend:
    def __init__(self) -> None:
        self.powered = False
        self.ensure_calls = 0
        self.prepare_calls: list[bool] = []
        self.power_off_calls = 0

    def get_power_state(self, node: NodeConfig) -> bool:
        return self.powered

    def ensure_power_on(
        self,
        node: NodeConfig,
        profile: HardwareProfile,
        prepare_for_network_boot: bool = False,
    ) -> None:
        self.ensure_calls += 1
        self.prepare_calls.append(prepare_for_network_boot)
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
        assert backend.prepare_calls == [False]
        assert item.power_state() == 1


def test_power_off_is_idempotent() -> None:
    backend = FakeBackend()
    backend.powered = True
    with ThreadPoolExecutor(max_workers=1) as executor:
        item = controller(backend, executor)
        for _ in range(100):
            result = item.power_off()
            if result is None:
                break
            time.sleep(0.01)
        assert result is None
        assert backend.power_off_calls == 1
        assert item.power_state() == 0


def test_slow_power_refresh_never_blocks_ipmi_status() -> None:
    class SlowBackend(FakeBackend):
        def __init__(self) -> None:
            super().__init__()
            import threading

            self.release = threading.Event()

        def get_power_state(self, node: NodeConfig) -> bool:
            self.release.wait(1)
            return self.powered

    backend = SlowBackend()
    with ThreadPoolExecutor(max_workers=1) as executor:
        item = controller(backend, executor)
        started = time.monotonic()
        assert item.power_state() == 0
        assert time.monotonic() - started < 0.1
        backend.release.set()


def test_boot_device_is_validated_and_network_preparation_is_one_shot() -> None:
    backend = FakeBackend()
    with ThreadPoolExecutor(max_workers=1) as executor:
        item = controller(backend, executor)
        assert item.get_boot_device() == "default"
        assert item.set_boot_device("network") is None
        assert item.get_boot_device() == "network"
        assert item.power_on() == IPMI_NODE_BUSY
        for _ in range(100):
            result = item.power_on()
            if result is None:
                break
            time.sleep(0.01)
        assert result is None
        assert backend.prepare_calls == [True]

        backend.powered = False
        item.refresh_power_state()
        for _ in range(100):
            if not item.provisioning():
                break
            time.sleep(0.01)
        assert item.power_on() == IPMI_NODE_BUSY
        for _ in range(100):
            result = item.power_on()
            if result is None:
                break
            time.sleep(0.01)
        assert result is None
        assert backend.prepare_calls == [True, False]

        assert item.set_boot_device("hd") is None
        assert item.get_boot_device() == "hd"
        assert item.set_boot_device("floppy") == IPMI_INVALID_DATA


def test_network_boot_turns_reset_into_prepared_power_on() -> None:
    backend = FakeBackend()
    backend.powered = True
    with ThreadPoolExecutor(max_workers=1) as executor:
        item = controller(backend, executor)
        assert item.set_boot_device("network") is None
        assert item.power_reset() == IPMI_NODE_BUSY
        for _ in range(100):
            result = item.power_on()
            if result is None:
                break
            time.sleep(0.01)
        assert result is None
        assert backend.prepare_calls == [True]


def test_failed_power_on_preserves_network_boot_preparation() -> None:
    class FailingBackend(FakeBackend):
        def ensure_power_on(
            self,
            node: NodeConfig,
            profile: HardwareProfile,
            prepare_for_network_boot: bool = False,
        ) -> None:
            self.ensure_calls += 1
            self.prepare_calls.append(prepare_for_network_boot)
            if self.ensure_calls == 1:
                raise RuntimeError("temporary VCD error")
            self.powered = True

    backend = FailingBackend()
    with ThreadPoolExecutor(max_workers=1) as executor:
        item = controller(backend, executor)
        assert item.set_boot_device("network") is None
        assert item.power_on() == IPMI_NODE_BUSY
        for _ in range(100):
            time.sleep(0.01)
            item.power_on()
            if backend.ensure_calls == 2:
                break
        for _ in range(100):
            result = item.power_on()
            if result is None:
                break
            time.sleep(0.01)
        assert result is None
        assert backend.prepare_calls == [True, True]


def test_completed_power_on_rechecks_external_power_state() -> None:
    backend = FakeBackend()
    with ThreadPoolExecutor(max_workers=1) as executor:
        item = controller(backend, executor)
        assert item.set_boot_device("network") is None
        assert item.power_on() == IPMI_NODE_BUSY
        for _ in range(100):
            if not item.provisioning():
                break
            time.sleep(0.01)

        backend.powered = False
        item.refresh_power_state()
        for _ in range(100):
            if not item.provisioning():
                break
            time.sleep(0.01)
        assert item.power_on() == IPMI_NODE_BUSY
        for _ in range(100):
            result = item.power_on()
            if result is None:
                break
            time.sleep(0.01)
        assert result is None
        assert backend.prepare_calls == [True, False]

