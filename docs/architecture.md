# Архитектура

```text
Ironic conductor (ru-msk / ru-spb)
        |
        | RMCP+ / IPMI 2.0, UDP 6230..6244
        v
ipmi-vcd in Kubernetes study (10.10.0.16)
        |
        | VMware Cloud Director API
        v
deterministic vApp + one VM per endpoint
```

Каждый `pyghmi.Bmc` слушает собственный UDP-порт, но все экземпляры работают в
одном процессе. `NodeController` отделяет IPMI semantics от VCD SDK. Длительное
клонирование выполняется в thread pool, чтобы RMCP session не зависала на время
VCD task.

Состояние VM не хранится локально: источником истины служит детерминированное имя
vApp `ironic-<node>` и VM `<node>`. После рестарта сервис находит существующую VM
в VDC. Локально хранится только advisory boot-device; после рестарта он снова
равен `network`, что соответствует обязательному PXE-first template.

HTTP API предназначен для probes, метрик и ручного запуска ensure. Он не
публикуется наружу кластера. IPMI Service публикует только UDP-порты.

## Границы сетевой модели

IPMI management traffic идёт по сети study `10.10.0.0/24`. PXE/DHCP — отдельный
data-plane и должен быть спроектирован независимо. Простая регистрация и
проверка питания не доказывают, что образ может быть развернут. Для полного
deploy VCD VM и Ironic conductor должны иметь общий L2 provisioning segment без
конфликтующего DHCP.

