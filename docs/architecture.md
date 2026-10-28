# Архитектура

```text
Ironic conductor (ru-msk / ru-spb)
        |                                      VCD Edge DHCP forwarder
        | RMCP+ / IPMI 2.0                     10.10.0.17, 10.10.0.20
        v                                                |
ipmi-vcd in Kubernetes study (10.10.0.16)                |
        |                                                v
        | VMware Cloud Director API       PXE VM on wireguard-net
        v                                      |
deterministic vApp + one VM per endpoint       +--> regional Ironic HTTP/API
```

Каждый `pyghmi.Bmc` слушает собственный UDP-порт, но все экземпляры работают в
одном процессе. `NodeController` отделяет IPMI semantics от VCD SDK. Длительное
клонирование выполняется в thread pool, чтобы RMCP session не зависала на время
VCD task.

Состояние VM не хранится локально: источником истины служит детерминированное имя
vApp `ironic-<node>` и VM `<node>`. После рестарта сервис находит существующую VM
в VDC. Локально хранится одноразовый флаг подготовки PXE: его устанавливает IPMI
`set boot device network`, а успешный `power on` сбрасывает. Подготовка заменяет
системный диск содержимым pristine template, не меняя vApp, VM, MAC, CPU и RAM.
После рестарта флаг равен `false`; Ironic с management interface `ipmitool`
повторяет boot-device command в рамках deploy.

HTTP API предназначен для probes, метрик и ручного запуска ensure. Он не
публикуется наружу кластера. IPMI Service публикует только UDP-порты.

## PXE в study

IPMI management traffic и лабораторный PXE используют общий VCD L2
`wireguard-net` (`10.10.0.0/24`). NSX не пропускает ответы произвольного DHCP
server с VM-порта, поэтому сеть работает в DHCP mode `RELAY`, а Edge DHCP
forwarder передаёт запросы на controllers `10.10.0.17` и `10.10.0.20`.

Региональные dnsmasq разделяют диапазоны: ru-msk `.90–.94`, ru-spb `.95–.99`.
В каждом включён общий deny для неизвестных MAC и отдельные разрешения для
зарегистрированных Ironic ports, поэтому controller другого региона и обычные
VM ответа не получают. Первый iPXE script должен chain-load `boot.ipxe`: он
выбирает сгенерированный Ironic per-node config с `ipa-api-url`, а общий
inspection-only `ipa.ipxe` для deploy не подходит.

Neutron cleaning network использует тот же CIDR только как адресный реестр с
выключенным Neutron DHCP. Fixed IP Nova port должен совпадать с записью dnsmasq;
пакеты идут по VCD L2, а не через Neutron VXLAN.

