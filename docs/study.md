# Study: установка и проверка

## 1. Подготовить VCD template

Template содержит одну выключенную x86_64 VM с VMXNET3, локальным диском и
статическим порядком PXE → disk. Запишите точные имена catalog и template в
Helm values. Десять VM не создаются при установке сервиса: каждая появляется
только после power-on своего endpoint.

В текущей организации обнаружены catalog `Public-VM` и template
`Ubuntu 24.04 LTS Server cloudimg`; они заданы в chart для проверки clone/power.
Перед проверкой PXE замените template на отдельную подготовленную копию с
сетевой загрузкой первой.

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
отдельный `nova-compute-ironic`. Не привязывайте dnsmasq к management interface
`ens192`: это общая сеть действующих VM. Для лабораторной регистрации используйте
изолированный provisioning interface. Полный PXE потребует отдельного VCD L2
segment и второго NIC у controller и template.

После Kolla deploy проверьте service catalog и conductor:

```sh
openstack baremetal driver list
openstack baremetal conductor list
openstack endpoint list --service ironic
```

## 4. Зарегистрировать тестовый узел

В `ru-msk` используйте endpoint `ru-msk-bm-01:6230`, в `ru-spb` —
`ru-spb-bm-01:6240`. При создании передайте `--management-interface noop` и
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

Добавлять MAC и переводить узел в `available` нужно только после готовности
общего provisioning L2. Регистрация BMC сама по себе этого не требует.

