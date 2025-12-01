#!/bin/bash
# Скрипт для установки capabilities на Python бинарник для работы с портом 502 (Modbus TCP)

# Определяем путь к проекту динамически (текущая директория скрипта)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python3"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "Ошибка: не найден Python бинарник в виртуальном окружении: $VENV_PYTHON"
    echo "Убедитесь, что виртуальное окружение создано:"
    echo "  cd $PROJECT_DIR"
    echo "  python3 -m venv .venv"
    exit 1
fi

# Находим реальный бинарник Python (разрешаем симлинк)
PYTHON_BIN=$(readlink -f "$VENV_PYTHON")

if [ -z "$PYTHON_BIN" ] || [ ! -f "$PYTHON_BIN" ]; then
    echo "Ошибка: не удалось найти реальный Python бинарник"
    exit 1
fi

echo "Виртуальное окружение Python: $VENV_PYTHON"
echo "Реальный Python бинарник: $PYTHON_BIN"

# Проверяем текущие capabilities
CURRENT_CAPS=$(getcap "$PYTHON_BIN" 2>/dev/null)
if [ -n "$CURRENT_CAPS" ]; then
    echo "Текущие capabilities: $CURRENT_CAPS"
else
    echo "Capabilities не установлены"
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



