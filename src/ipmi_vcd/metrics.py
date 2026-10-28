from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.operations = Counter(
            "ipmi_vcd_operations_total",
            "IPMI operations handled by the service",
            ("node", "operation", "result"),
            registry=self.registry,
        )
        self.provisioning = Gauge(
            "ipmi_vcd_provisioning",
            "Whether a VCD ensure/power-on operation is running",
            ("node",),
            registry=self.registry,
        )

