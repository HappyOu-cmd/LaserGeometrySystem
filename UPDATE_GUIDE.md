# Обновление Laser Geometry System на промышленном компьютере

## 1. Подготовка и резервная копия

Откройте терминал и перейдите в каталог проекта:

```bash
cd /home/stend_1/Laser/LaserGeometrySystem
```

Если на другом компьютере проект расположен в другом каталоге, используйте его фактический путь. Новый установщик сервиса определяет путь автоматически.

Остановите старые варианты сервиса и создайте резервную копию базы данных:

```bash
systemctl --user disable --now laser_geometry.service 2>/dev/null || true
sudo systemctl stop laser_geometry.service 2>/dev/null || true
cp -a modbus_registers.db "modbus_registers.db.backup-$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
```

## 2. Получение изменений

Убедитесь, что локальные файлы не содержат незавершённых изменений:

```bash
git status
```

Затем загрузите изменения ветки `main`:

```bash
git pull --ff-only origin main
```

Не применяйте `git reset --hard`, если на компьютере есть локальные настройки или изменения, которые требуется сохранить.

## 3. Виртуальное окружение и зависимости

Создайте окружение, если его ещё нет:

```bash
python3 -m venv .venv
```

Установите зависимости и принудительно зафиксируйте проверенную версию `pymodbus`:

```bash
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m pip install -r requirements_modbus.txt
./.venv/bin/python -m pip install 'pymodbus==3.5.4'
```

Проверка версии:

```bash
./.venv/bin/python -c "import pymodbus; print(pymodbus.__version__)"
```

Ожидаемый результат: `3.5.4`.

## 4. Установка системного сервиса

Запустите установщик обычным пользователем:

```bash
chmod +x setup_service.sh
./setup_service.sh
```

Установщик запросит пароль `sudo`, создаст системный сервис, выдаст ему только право открытия порта 502, включит автозапуск и запустит приложение.

`setup_capabilities.sh` запускать не требуется. Право на порт 502 теперь выдаётся непосредственно сервису через systemd и не зависит от файла Python или виртуального окружения.

## 5. Проверка запуска

```bash
sudo systemctl status laser_geometry.service
ss -ltn | grep ':502'
sudo journalctl -u laser_geometry.service -n 100 --no-pager
```

Непрерывный просмотр логов:

```bash
sudo journalctl -u laser_geometry.service -n 100 -f
```

Остановка просмотра логов: `Ctrl+C`. Сам сервис продолжит работать.

## 6. Новые коэффициенты min/max в HMI

Каждый коэффициент занимает два Holding-регистра и хранится как `DoubleWord float`.

| Параметр | Смещение минимума | Смещение максимума |
|---|---:|---:|
| Толщина верхней стенки | 40560–40561 | 40562–40563 |
| Толщина нижней стенки | 40564–40565 | 40566–40567 |
| Диаметр корпуса | 40568–40569 | 40570–40571 |
| Диаметр фланца | 40572–40573 | 40574–40575 |
| Толщина дна | 40576–40577 | 40578–40579 |
| Диаметр корпуса 2 | 40580–40581 | 40582–40583 |

Формулы:

```text
итоговый минимум = найденный минимум + коэффициент минимума
итоговый максимум = найденный максимум + коэффициент максимума
```

Среднее значение не изменяется. Коэффициенты применяются после обычного смещения, экстраполяции, фильтрации и окончательного определения min/max.

Все новые коэффициенты автоматически сохраняются в `modbus_registers.db` при изменении и восстанавливаются после перезапуска. Начальное значение каждого коэффициента — `0.0`.

## 7. Проверка сохранения коэффициентов

1. Запишите тестовое значение в один из новых регистров через HMI.
2. Подождите не менее двух секунд для цикла сохранения БД.
3. Перезапустите сервис:

```bash
sudo systemctl restart laser_geometry.service
```

4. Убедитесь через HMI, что значение восстановилось.
5. Верните рабочее значение коэффициента.

## 8. Управление сервисом

```bash
sudo systemctl start laser_geometry.service
sudo systemctl stop laser_geometry.service
sudo systemctl restart laser_geometry.service
sudo systemctl enable laser_geometry.service
sudo systemctl disable --now laser_geometry.service
```

Для системного сервиса команды выполняются без параметра `--user`.
