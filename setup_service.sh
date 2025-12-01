#!/bin/bash
# Скрипт для настройки автозапуска Laser Geometry System через systemd

# Определяем путь к проекту (текущая директория)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="laser_geometry.service"
SERVICE_DIR="$HOME/.config/systemd/user"

echo "=========================================="
echo "Настройка автозапуска Laser Geometry System"
echo "=========================================="
echo "Путь к проекту: $PROJECT_DIR"
echo "Пользователь: $(whoami)"
echo ""

# Проверяем наличие виртуального окружения
if [ ! -d "$PROJECT_DIR/.venv" ]; then
    echo "ОШИБКА: Виртуальное окружение не найдено!"
    echo "Создайте виртуальное окружение:"
    echo "  cd $PROJECT_DIR"
    echo "  python3 -m venv .venv"
    echo "  source .venv/bin/activate"
    echo "  pip install -r requirements.txt"
    exit 1
fi

# Проверяем наличие основного скрипта
if [ ! -f "$PROJECT_DIR/laser_geometry_system.py" ]; then
    echo "ОШИБКА: Файл laser_geometry_system.py не найден!"
    exit 1
fi

# Создаем директорию для сервисов пользователя, если её нет
mkdir -p "$SERVICE_DIR"

# Создаем файл сервиса
SERVICE_FILE="$SERVICE_DIR/$SERVICE_NAME"
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Laser Geometry System
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/.venv/bin/python3 $PROJECT_DIR/laser_geometry_system.py
Restart=always
RestartSec=10
# Лимит перезапусков: максимум 5 раз за 5 минут
StartLimitIntervalSec=300
StartLimitBurst=5
StandardOutput=journal
StandardError=journal

# Переменные окружения
Environment="PATH=$PROJECT_DIR/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Убираем проверку групп, которая может вызывать ошибки
SupplementaryGroups=

[Install]
WantedBy=default.target
EOF

echo "✓ Файл сервиса создан: $SERVICE_FILE"
echo ""

# Проверяем и включаем linger для автозапуска без активной сессии
echo "Проверка linger для автозапуска без активной сессии..."
if ! loginctl show-user "$(whoami)" | grep -q "Linger=yes"; then
    echo "Включение linger (требуются права sudo)..."
    sudo loginctl enable-linger "$(whoami)"
    if [ $? -eq 0 ]; then
        echo "✓ Linger включен"
    else
        echo "⚠ Предупреждение: не удалось включить linger. Сервис будет запускаться только при активной сессии пользователя."
    fi
else
    echo "✓ Linger уже включен"
fi

# Перезагружаем конфигурацию systemd
echo ""
echo "Перезагрузка конфигурации systemd..."
systemctl --user daemon-reload

if [ $? -eq 0 ]; then
    echo "✓ Конфигурация systemd перезагружена"
else
    echo "✗ Ошибка перезагрузки конфигурации systemd"
    exit 1
fi

# Включаем автозапуск
echo ""
echo "Включение автозапуска..."
systemctl --user enable "$SERVICE_NAME"

if [ $? -eq 0 ]; then
    echo "✓ Автозапуск включен"
else
    echo "✗ Ошибка включения автозапуска"
    exit 1
fi

echo ""
echo "=========================================="
echo "Настройка завершена!"
echo "=========================================="
echo ""
echo "Полезные команды:"
echo "  Запустить сервис:    systemctl --user start $SERVICE_NAME"
echo "  Остановить сервис:   systemctl --user stop $SERVICE_NAME"
echo "  Перезапустить:       systemctl --user restart $SERVICE_NAME"
echo "  Статус:             systemctl --user status $SERVICE_NAME"
echo "  Логи:               journalctl --user -u $SERVICE_NAME -f"
echo "  Отключить автозапуск: systemctl --user disable $SERVICE_NAME"
echo ""
echo "ВАЖНО: Не забудьте запустить setup_capabilities.sh для установки прав на порт 502!"
echo "  cd $PROJECT_DIR"
echo "  bash setup_capabilities.sh"
echo ""

