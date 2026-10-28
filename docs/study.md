# Study: установка и проверка

## 1. Подготовить VCD template

Template `Ironic PXE blank 20GiB` в catalog `ipmi-vcd` содержит одну выключенную
x86_64 VM, один VMXNET3 на `wireguard-net` и пустой локальный диск 20 GiB.
До deploy BIOS не находит ОС на диске и переходит к PXE; после записи образа
загружается локальный диск. Перед повторным deploy адаптер заменяет этот диск
пустым диском из template по команде Ironic `set boot device network`. Десять VM
не создаются при установке сервиса: каждая появляется только после power-on
своего endpoint.

## 2. Развернуть сервис

Используйте явный kubeconfig study и Kubernetes Secrets. Не добавляйте значения
credentials в shell history или Git. После установки проверьте:

```sh
kubectl --kubeconfig "$STUDY_KUBECONFIG" -n ipmi-vcd rollout status deploy/ipmi-vcd
kubectl --kubeconfig "$STUDY_KUBECONFIG" -n ipmi-vcd get svc ipmi-vcd-ipmi
ipmitool -I lanplus -H 10.10.0.16 -p 6230 \
  -U "$IPMI_USERNAME" -P "$IPMI_PASSWORD" chassis power status
```

Ожидаемый начальный state — `off`; запрос state не создаёт VM.

## 3. Установить Ironic в регионы

Kolla в каждом регионе включает Ironic API, conductor, HTTP/TFTP/dnsmasq и
отдельный `nova-compute-ironic`. В этой лаборатории dnsmasq привязан к `ens192`,
но не обслуживает общую сеть напрямую: VCD сеть `wireguard-net` использует DHCP
mode `RELAY`, а Edge DHCP forwarder направляет запросы только на controllers:

| Регион | Controller | DHCP pool | MAC allowlist |
|---|---|---|---|
| ru-msk | `10.10.0.17` | `10.10.0.90–10.10.0.94` | `ironic_dhcp_hosts` |
| ru-spb | `10.10.0.20` | `10.10.0.95–10.10.0.99` | `ironic_dhcp_hosts` |

В каждом `ironic_dhcp_hosts` обязательна wildcard-запись `ignore` и точная
запись `MAC,IP` для каждого зарегистрированного узла. Неизвестные VM и MAC
другого региона игнорируются. Neutron network `ironic-cleaning` использует
`10.10.0.0/24`, соответствующий региональный pool, gateway `10.10.0.1` и
выключенный DHCP.

Kolla по умолчанию направляет второй этап iPXE на inspection script. Для deploy
создайте `/etc/kolla/config/ironic/ipa.ipxe`:

```ipxe
#!ipxe
chain http://CONTROLLER_IP:8089/boot.ipxe
```

`boot.ipxe` выбирает per-node config и передаёт IPA правильный
`ipa-api-url=http://CONTROLLER_IP:6385`.

После Kolla deploy проверьте service catalog и conductor:

```sh
openstack baremetal driver list
openstack baremetal conductor list
openstack endpoint list --service ironic
```

## 4. Зарегистрировать тестовый узел

В `ru-msk` используйте endpoint `ru-msk-bm-01:6230`, в `ru-spb` —
`ru-spb-bm-01:6240`. При создании передайте `--management-interface ipmitool` и
resource class соответствующего профиля.

Проверка без PXE:

```sh
openstack baremetal node validate <node>
openstack baremetal node power on <node>
openstack baremetal node show <node> -f yaml
```

Первый power-on может несколько раз вернуть busy, пока VCD клонирует template.
Успех подтверждается одновременно состоянием `power on` в Ironic, созданной VM
в VCD и метрикой `ipmi_vcd_operations_total`.

После первого power-on получите MAC созданной VCD VM, создайте Ironic port и
добавьте тот же MAC с выбранным IP в allowlist регионального dnsmasq. Fixed IP
Nova/Neutron port должен совпадать с этой записью. Перед выдачей пользователю
проверьте `DHCPACK`, heartbeat IPA, состояние Ironic `active`, Nova `ACTIVE` и
загрузку ОС с локального диска.

Для проверки повторного заказа удалите тестовый Nova server, дождитесь состояния
Ironic `available`, затем создайте новый server на том же flavor. В логах
`ipmi-vcd` должна появиться запись `replacing system disk from template before
PXE`; прежняя ОС не должна получить DHCP до запуска IPA.

