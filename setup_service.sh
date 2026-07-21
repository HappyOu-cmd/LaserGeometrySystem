#!/usr/bin/env bash
# Установка Laser Geometry System как системного сервиса systemd.
# Сервис работает от обычного пользователя, а право на порт 502 получает от systemd.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="laser_geometry.service"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME"

# Скрипт можно запускать как обычным пользователем, так и через sudo.
if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
    TARGET_USER="$SUDO_USER"
else
    TARGET_USER="$(id -un)"
fi
TARGET_GROUP="$(id -gn "$TARGET_USER")"

PYTHON_BIN="$PROJECT_DIR/.venv/bin/python3"
MAIN_SCRIPT="$PROJECT_DIR/laser_geometry_system.py"

echo "=========================================="
echo "Установка Laser Geometry System"
echo "=========================================="
echo "Путь к проекту: $PROJECT_DIR"
echo "Пользователь:   $TARGET_USER"
echo "Группа:         $TARGET_GROUP"
echo "Сервис:         $SERVICE_FILE"
echo

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "ОШИБКА: Python виртуального окружения не найден: $PYTHON_BIN" >&2
    echo "Сначала создайте .venv и установите зависимости." >&2
    exit 1
fi

if [[ ! -f "$MAIN_SCRIPT" ]]; then
    echo "ОШИБКА: Основной файл не найден: $MAIN_SCRIPT" >&2
    exit 1
fi

# Не допускаем одновременный запуск старого user-unit и нового system-unit.
if [[ "$TARGET_USER" == "$(id -un)" ]]; then
    systemctl --user disable --now "$SERVICE_NAME" >/dev/null 2>&1 || true
fi

UNIT_TMP="$(mktemp)"
trap 'rm -f "$UNIT_TMP"' EXIT

cat > "$UNIT_TMP" <<EOF
[Unit]
Description=Laser Geometry System
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$TARGET_USER
Group=$TARGET_GROUP
WorkingDirectory=$PROJECT_DIR
ExecStart=$PYTHON_BIN -u $MAIN_SCRIPT

# Разрешает непривилегированному Python слушать стандартный Modbus TCP порт 502.
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true

Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "Установка systemd unit (sudo потребуется только при развёртывании)..."
sudo install -o root -g root -m 0644 "$UNIT_TMP" "$SERVICE_FILE"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

echo
echo "=========================================="
echo "Установка завершена"
echo "=========================================="
echo "Статус:       sudo systemctl status $SERVICE_NAME"
echo "Логи:         sudo journalctl -u $SERVICE_NAME -n 100 -f"
echo "Перезапуск:   sudo systemctl restart $SERVICE_NAME"
echo "Остановка:    sudo systemctl stop $SERVICE_NAME"
echo "Автозапуск:   sudo systemctl enable $SERVICE_NAME"
echo "Отключение:   sudo systemctl disable --now $SERVICE_NAME"
echo
echo "setup_capabilities.sh для этого сервиса запускать не требуется."
