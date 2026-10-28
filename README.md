# ipmi-vcd

`ipmi-vcd` — лабораторный IPMI 2.0 facade для VMware Cloud Director. Ironic
работает с обычным драйвером `ipmi`, а сервис переводит команды питания в
операции над VM в VCD.

Сервис предназначен для стенда study. Он не моделирует физические отказы,
датчики, консоль BMC или полноценную прошивку сервера и не должен использоваться
как production BMC.

## Как устроен пул

Ironic не выбирает профиль и не создаёт IPMI endpoint динамически. Оператор
заранее регистрирует узел с адресом сервиса и выделенным UDP-портом. Endpoint
уже связан с одним профилем:

| Endpoint | Регион | Порт | Профиль |
|---|---|---:|---|
| `ru-msk-bm-01` | ru-msk | 6230 | 8 vCPU / 16 GiB |
| `ru-msk-bm-02` | ru-msk | 6231 | 8 vCPU / 16 GiB |
| `ru-msk-bm-03` | ru-msk | 6232 | 8 vCPU / 16 GiB |
| `ru-msk-bm-04` | ru-msk | 6233 | 16 vCPU / 32 GiB |
| `ru-msk-bm-05` | ru-msk | 6234 | 16 vCPU / 32 GiB |
| `ru-spb-bm-01` | ru-spb | 6240 | 8 vCPU / 16 GiB |
| `ru-spb-bm-02` | ru-spb | 6241 | 8 vCPU / 16 GiB |
| `ru-spb-bm-03` | ru-spb | 6242 | 16 vCPU / 32 GiB |
| `ru-spb-bm-04` | ru-spb | 6243 | 16 vCPU / 32 GiB |
| `ru-spb-bm-05` | ru-spb | 6244 | 16 vCPU / 32 GiB |

При первом `power on` сервис клонирует VCD template, задаёт CPU/RAM профиля и
включает VM. Пока длится клонирование, IPMI возвращает `Node Busy`; ipmitool и
Ironic повторяют команду. `power off`, `reset` и `soft off` управляют уже
созданной VM. VM сохраняется после выключения и используется следующей
операцией Ironic.

## Ограничение boot device

Cloud Director tenant API не предоставляет сервису надёжного изменения BIOS/UEFI
boot order. Поэтому команда IPMI `set boot device` принимается и запоминается,
но фактический порядок задаёт VCD template. Для развертывания ОС template должен
иметь сетевую загрузку первой, а локальный диск второй. В Ironic для этих узлов
следует использовать management interface `noop`, чтобы статический порядок
оставался источником истины.

## Конфигурация

Несекретная конфигурация находится в YAML, пример — `config/example.yaml`.
Обязательные секреты передаются только через environment:

- `IPMI_USERNAME`, `IPMI_PASSWORD` — общие credentials тестового BMC;
- `VCD_USER`, `VCD_PASSWORD` — basic login в VCD; либо `VCD_TOKEN` и
  `VCD_AUTH_TYPE=jwt`.

VCD template должен содержать ровно одну VM, один сетевой адаптер и локальный
диск достаточного размера. Guest customization в template должен быть выключен.

Локальный запуск:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pip install --no-deps -e .
IPMI_VCD_CONFIG=config/example.yaml python -m ipmi_vcd
```

## Kubernetes

Создайте Secrets без сохранения значений в values или Git:

```sh
kubectl -n ipmi-vcd create secret generic ipmi-vcd-vcd \
  --from-literal=VCD_USER="$VCD_USER" \
  --from-literal=VCD_PASSWORD="$VCD_PASSWORD"
kubectl -n ipmi-vcd create secret generic ipmi-vcd-ipmi \
  --from-literal=IPMI_USERNAME="$IPMI_USERNAME" \
  --from-literal=IPMI_PASSWORD="$IPMI_PASSWORD"
helm upgrade --install ipmi-vcd charts/ipmi-vcd -n ipmi-vcd --create-namespace
```

Один LoadBalancer публикует десять UDP-портов. HTTP API остаётся ClusterIP:

- `GET /healthz`, `GET /readyz`;
- `GET /metrics`;
- `GET /v1/nodes`;
- `POST /v1/nodes/{name}/power-on`.

Deployment намеренно имеет одну реплику: один IPMI UDP-порт нельзя одновременно
обслуживать несколькими независимыми BMC-процессами.

## Регистрация в Ironic

Пример для endpoint `ru-msk-bm-01`, если service IP равен `10.10.0.16`:

```sh
openstack baremetal node create \
  --name ru-msk-bm-01 \
  --driver ipmi \
  --management-interface noop \
  --resource-class gator_8_16 \
  --driver-info ipmi_address=10.10.0.16 \
  --driver-info ipmi_port=6230 \
  --driver-info ipmi_username="$IPMI_USERNAME" \
  --driver-info ipmi_password="$IPMI_PASSWORD" \
  --property cpus=8 \
  --property memory_mb=16384 \
  --property cpu_arch=x86_64
```

MAC-адрес порта берётся из VM/template и добавляется отдельной командой
`openstack baremetal port create`. Полный порядок для study описан в
[`docs/study.md`](docs/study.md).

## Проверки

```sh
make check
make docker
```

