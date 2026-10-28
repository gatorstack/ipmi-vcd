# Требования ipmi-vcd

## Цель

Дать двум учебным регионам OpenStack `ru-msk` и `ru-spb` по десять стабильных IPMI
endpoint, за которыми лениво создаются VM в VCD. OpenStack использует штатный
Ironic hardware type `ipmi` и не знает о VCD.

## Контракт

1. Конфигурация описывает профили CPU/RAM и конечный пул узлов.
2. Имя, профиль и UDP-порт узла неизменны между рестартами сервиса.
3. Первый успешный `power on` гарантирует существование включённой VCD VM с
   детерминированным именем `ironic-<node>`.
4. Повторный `power on`, `power off` и `shutdown` идемпотентны.
5. Временная длительная операция возвращает IPMI completion code `0xc0` и может
   быть безопасно повторена клиентом.
6. Выключение не удаляет VM и диск.
7. VCD и IPMI credentials никогда не входят в YAML, образ, Helm values, логи или
   метрики.
8. Один endpoint принадлежит одному региону Ironic. Одновременная регистрация
   одного endpoint в двух регионах не поддерживается.
9. `set boot device network` делает следующий `power on` одноразовой подготовкой
   PXE: системный диск выключенной существующей VM заменяется пустым диском из
   template; остальные power-операции содержимое диска не меняют.
10. Каждый endpoint имеет заранее назначенный уникальный MAC. Ironic port и
    DHCP allowlist поэтому создаются до VCD VM, а адаптер применяет этот MAC
    после ленивого клонирования и до первого power-on.

## Профили и пул

- десять узлов профиля `8-16`: по пять в каждом регионе, 8 vCPU и 16 GiB RAM;
- десять узлов профиля `16-32`: по пять в каждом регионе, 16 vCPU и 32 GiB RAM;
- десять endpoint выделено `ru-msk`, десять — `ru-spb`.

Размер пустого локального диска наследуется от VCD template. При первом deploy
он создаётся вместе с VM, а перед повторным deploy заменяет диск предыдущего
пользователя. Это оставляет размер диска единым свойством подготовленного
bare-metal template и гарантирует PXE fallback без удаления самой VM.

## Не входит в первую версию

- динамическое выделение endpoint из Ironic;
- удаление VCD VM через IPMI;
- SOL, sensor data, chassis inventory и virtual media;
- изменение boot order через VCD API;
- автоматическое создание provisioning network или VCD template;
- production HA.

## Приёмка

- unit tests проверяют YAML, уникальность портов, асинхронный power-on и
  одноразовую подготовку PXE;
- Helm chart рендерит двадцать уникальных UDP Service ports;
- контейнер запускается без root и не содержит credentials;
- `ipmitool` получает power state от развернутого endpoint;
- медленный запрос power state к VCD не задерживает IPMI status других endpoint;
- в каждом регионе Ironic создаётся хотя бы один узел, driver validation видит
  IPMI endpoint и power operation проходит через адаптер;
- DHCP relay VCD передаёт PXE только региональным dnsmasq, которые отвечают
  зарегистрированным MAC из allowlist;
- тестовый Nova server проходит `available → deploying → active`, а записанная
  на пустой диск ОС загружается с IP, совпадающим с Neutron port.
- после удаления и повторного заказа тот же endpoint снова проходит
  `available → deploying → active`, а предыдущая ОС не загружается вместо IPA.
- повторная подготовка диска переиспользует автоматически созданные VCD
  controllers и не создаёт конфликтующих `InstanceID`.

