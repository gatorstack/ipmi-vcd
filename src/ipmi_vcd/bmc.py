from __future__ import annotations

import uuid

from pyghmi.ipmi.bmc import Bmc

from ipmi_vcd.controller import IPMI_INVALID_DATA, NodeController


class VcdBmc(Bmc):
    def __init__(
        self,
        controller: NodeController,
        username: str,
        password: str,
        address: str,
    ) -> None:
        self.controller = controller
        super().__init__({username: password}, port=controller.node.port, address=address)

    def get_system_guid(self) -> uuid.UUID:
        return uuid.uuid5(uuid.NAMESPACE_DNS, f"ipmi-vcd:{self.controller.node.name}")

    def cold_reset(self) -> int | None:
        return None

    def get_power_state(self) -> int:
        return self.controller.power_state()

    def is_active(self) -> bool:
        return self.controller.power_state() == 1

    def power_on(self) -> int | None:
        return self.controller.power_on()

    def power_off(self) -> int | None:
        return self.controller.power_off()

    def power_cycle(self) -> int | None:
        return self.controller.power_reset()

    def power_reset(self) -> int | None:
        return self.controller.power_reset()

    def power_shutdown(self) -> int | None:
        return self.controller.shutdown()

    def pulse_diag(self) -> int:
        return IPMI_INVALID_DATA

    def get_boot_device(self) -> str:
        return self.controller.get_boot_device()

    def set_boot_device(
        self,
        bootdevice: str,
        persist: bool = False,
        uefiboot: bool = False,
    ) -> int | None:
        del persist, uefiboot
        return self.controller.set_boot_device(bootdevice)

