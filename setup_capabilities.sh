#!/bin/bash
# Скрипт для установки capabilities на Python бинарник для работы с портом 502 (Modbus TCP)

PYTHON_BIN=$(readlink -f /home/stend_1/Laser/LaserGeometrySystem/.venv/bin/python3)

if [ -z "$PYTHON_BIN" ]; then
    echo "Ошибка: не найден Python бинарник в виртуальном окружении"
    exit 1
fi

echo "Найден Python бинарник: $PYTHON_BIN"

# Проверяем текущие capabilities
CURRENT_CAPS=$(getcap "$PYTHON_BIN" 2>/dev/null)
if [ -n "$CURRENT_CAPS" ]; then
    echo "Текущие capabilities: $CURRENT_CAPS"
fi

# Устанавливаем capability для работы с портами < 1024 (включая 502)
echo "Установка capability CAP_NET_BIND_SERVICE на $PYTHON_BIN..."
sudo setcap 'cap_net_bind_service=+ep' "$PYTHON_BIN"

if [ $? -eq 0 ]; then
    echo "✓ Capability успешно установлена"
    getcap "$PYTHON_BIN"
else
    echo "✗ Ошибка установки capability"
    exit 1
fi



