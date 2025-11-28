# Инструкция по настройке на новом компьютере

## Шаг 1: Клонирование репозитория

```bash
cd ~
git clone https://github.com/HappyOu-cmd/LaserGeometrySystem.git
cd LaserGeometrySystem
```

## Шаг 2: Создание виртуального окружения и установка зависимостей

```bash
# Создаем виртуальное окружение
python3 -m venv .venv

# Активируем виртуальное окружение
source .venv/bin/activate

# Обновляем pip
pip install --upgrade pip

# Устанавливаем зависимости
pip install -r requirements.txt

# Устанавливаем опциональные зависимости
pip install pyftdi
```

## Шаг 3: Установка capabilities для работы с портом 502

Для работы с портом 502 (Modbus TCP) необходимо установить capabilities на Python бинарник:

```bash
cd ~/LaserGeometrySystem
bash setup_capabilities.sh
```

**Примечание:** Скрипт запросит пароль sudo. Capability будет установлена на системный Python бинарник.

## Шаг 4: Настройка автозапуска через systemd

Запустите скрипт настройки сервиса:

```bash
cd ~/LaserGeometrySystem
bash setup_service.sh
```

Скрипт автоматически:
- Создаст файл сервиса в `~/.config/systemd/user/laser_geometry.service`
- Настроит правильные пути к проекту
- Включит автозапуск при входе пользователя

## Шаг 5: Запуск сервиса

```bash
# Запустить сервис
systemctl --user start laser_geometry.service

# Проверить статус
systemctl --user status laser_geometry.service

# Просмотр логов
journalctl --user -u laser_geometry.service -f
```

## Полезные команды

### Управление сервисом:
```bash
# Запустить
systemctl --user start laser_geometry.service

# Остановить
systemctl --user stop laser_geometry.service

# Перезапустить
systemctl --user restart laser_geometry.service

# Статус
systemctl --user status laser_geometry.service

# Включить автозапуск
systemctl --user enable laser_geometry.service

# Отключить автозапуск
systemctl --user disable laser_geometry.service
```

### Просмотр логов:
```bash
# Последние 50 строк
journalctl --user -u laser_geometry.service -n 50

# Логи в реальном времени
journalctl --user -u laser_geometry.service -f

# Логи за последний час
journalctl --user -u laser_geometry.service --since "1 hour ago"
```

### Проверка порта 502:
```bash
# Проверить, слушает ли сервис на порту 502
sudo ss -tlnp | grep 502

# Или
sudo netstat -tlnp | grep 502
```

## Проверка установки capabilities

```bash
# Проверить установленные capabilities
getcap $(readlink -f ~/LaserGeometrySystem/.venv/bin/python3)
```

Должно вывести что-то вроде:
```
/usr/bin/python3.12 cap_net_bind_service=ep
```

## Устранение проблем

### Сервис не запускается:
1. Проверьте логи: `journalctl --user -u laser_geometry.service -n 50`
2. Проверьте, что виртуальное окружение создано: `ls -la ~/LaserGeometrySystem/.venv`
3. Проверьте, что зависимости установлены: `source .venv/bin/activate && pip list`

### Ошибка "permission denied" на порту 502:
1. Убедитесь, что capabilities установлены: `getcap $(readlink -f ~/LaserGeometrySystem/.venv/bin/python3)`
2. Если capabilities не установлены, запустите: `bash setup_capabilities.sh`
3. Перезапустите сервис: `systemctl --user restart laser_geometry.service`

### Сервис не запускается автоматически:
1. Проверьте, что автозапуск включен: `systemctl --user is-enabled laser_geometry.service`
2. Если не включен: `systemctl --user enable laser_geometry.service`
3. Убедитесь, что systemd user session запущен: `systemctl --user status`

## Примечания

- Сервис запускается от имени текущего пользователя (user service)
- Для работы требуется активная сессия пользователя (systemd user session)
- Если нужно запускать без активной сессии, используйте system service (требует root)
- Capabilities устанавливаются на системный Python, поэтому они будут работать для всех виртуальных окружений, использующих этот Python

