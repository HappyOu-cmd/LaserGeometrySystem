# Управление версией pymodbus

## Текущая версия в проекте

**Установленная версия: 3.5.4** (на момент последней проверки)

**Требования:** pymodbus>=3.5.4,<4.0.0

## Как проверить установленную версию

### Способ 1: Через pip
```bash
python -m pip show pymodbus
```

Или короче:
```bash
pip show pymodbus | findstr "Version"
```

### Способ 2: Через Python
```bash
python -c "import pymodbus; print(pymodbus.__version__)"
```

### Способ 3: Через скрипт
```bash
python check_pymodbus_version.bat
```

## Как установить конкретную версию

### Установка точной версии
```bash
python -m pip install pymodbus==3.6.0
```

### Установка последней версии 3.x
```bash
python -m pip install "pymodbus>=3.6.0,<4.0.0"
```

### Установка последней версии (включая 4.x)
```bash
python -m pip install --upgrade pymodbus
```

### Через скрипт
```bash
install_pymodbus_version.bat 3.6.0
```

## Проверенные версии

- ✅ **3.5.4** - работает корректно
- ✅ **3.6.0** - рекомендуется для новых установок
- ✅ **3.7.0+** - должны работать (требуется проверка)

## Проблемы с версиями

### Если версия < 3.5.4
```bash
python -m pip install --upgrade "pymodbus>=3.5.4,<4.0.0"
```

### Если версия >= 4.0.0 (несовместимо)
```bash
python -m pip install "pymodbus>=3.6.0,<4.0.0"
```

### Переустановка с нуля
```bash
python -m pip uninstall pymodbus -y
python -m pip install "pymodbus>=3.6.0,<4.0.0"
python -c "import pymodbus; print('OK:', pymodbus.__version__)"
```

## Совместимость кода

Проект использует API pymodbus 3.x:

```python
# Для pymodbus 3.x (работает)
from pymodbus.server import StartTcpServer
from pymodbus.device import ModbusDeviceIdentification
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext, ModbusServerContext
```

Код автоматически определяет версию и выбирает правильный импорт (с fallback на 2.x).

## Откат на предыдущую версию

```bash
# Например, вернуться на 3.5.4
python -m pip install pymodbus==3.5.4
```

