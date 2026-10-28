# Требования ipmi-vcd

## Цель

Дать двум учебным регионам OpenStack `ru-msk` и `ru-spb` по пять стабильных IPMI
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

## Профили и пул

- пять узлов профиля `8-16`: 8 vCPU, 16 GiB RAM;
- пять узлов профиля `16-32`: 16 vCPU, 32 GiB RAM;
- пять endpoint выделено `ru-msk`, пять — `ru-spb`.

Размер локального диска наследуется от VCD template. Это предотвращает запуск
guest customization при клонировании и оставляет размер диска единым свойством
подготовленного bare-metal template.

## Не входит в первую версию

- динамическое выделение endpoint из Ironic;
- удаление VCD VM через IPMI;
- SOL, sensor data, chassis inventory и virtual media;
- изменение boot order через VCD API;
- автоматическое создание provisioning network или VCD template;
- production HA.

## Приёмка

- unit tests проверяют YAML, уникальность портов и асинхронный power-on;
- Helm chart рендерит десять уникальных UDP Service ports;
- контейнер запускается без root и не содержит credentials;
- `ipmitool` получает power state от развернутого endpoint;
- в каждом регионе Ironic создаётся хотя бы один узел, driver validation видит
  IPMI endpoint и power operation проходит через адаптер.

