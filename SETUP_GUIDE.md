# Инструкция по развертыванию на чистом Windows

## Шаг 1: Установка Python

1. Скачайте Python 3.10 или 3.11 с официального сайта:
   - https://www.python.org/downloads/
   
2. При установке **ВАЖНО**: отметьте галочку **"Add Python to PATH"**

3. Проверьте установку:
   ```bash
   python --version
   ```

## Шаг 2: Установка Git

1. Скачайте Git для Windows:
   - https://git-scm.com/download/win

2. Установите с настройками по умолчанию

3. Проверьте установку:
   ```bash
   git --version
   ```

## Шаг 3: Клонирование проекта

1. Откройте командную строку (cmd) или PowerShell

2. Перейдите в нужную папку:
   ```bash
   cd C:\Projects
   ```

3. Склонируйте репозиторий:
   ```bash
   git clone https://github.com/ваш-юзернейм/LaserGeometry.git
   cd LaserGeometry
   ```

## Шаг 4: Создание виртуального окружения

```bash
python -m venv venv
venv\Scripts\activate
```

Вы должны увидеть `(venv)` в начале строки.

## Шаг 5: Установка зависимостей

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## Шаг 6: Настройка COM порта

1. Определите COM порт вашего адаптера:
   - Откройте "Диспетчер устройств"
   - Найдите "Порты (COM и LPT)"
   - Запомните номер COM порта (например, COM7)

2. Откройте `laser_geometry_system.py` в текстовом редакторе

3. Найдите строку с инициализацией датчиков и измените порт:
   ```python
   sensors = HighSpeedRiftekSensor(port='COM7')  # Ваш COM порт
   ```

## Шаг 7: Запуск системы

```bash
python laser_geometry_system.py
```

## Возможные проблемы

### Проблема: "python не является внутренней командой"
**Решение:** Переустановите Python с галочкой "Add Python to PATH"

### Проблема: "pip не найден"
**Решение:** 
```bash
python -m ensurepip --upgrade
```

### Проблема: "Permission denied" при установке пакетов
**Решение:** Запустите командную строку от имени администратора

### Проблема: "No module named 'pymodbus'" (хотя установлен)
**Это самая частая проблема!** Обычно означает, что пакет установлен не в то окружение.

**Решения:**

1. **Убедитесь, что виртуальное окружение активировано:**
   ```bash
   # В начале строки должно быть (venv)
   (venv) C:\Projects\LaserGeometry>
   ```

2. **Проверьте, какой Python используется:**
   ```bash
   python --version
   where python
   # Должен показывать путь к venv\Scripts\python.exe
   ```

3. **Переустановите пакеты в виртуальном окружении:**
   ```bash
   pip uninstall pymodbus -y
   pip install pymodbus>=3.6.0
   ```

4. **Проверьте установку:**
   ```bash
   python -c "import pymodbus; print(pymodbus.__version__)"
   ```

5. **Если ничего не помогает - пересоздайте окружение:**
   ```bash
   deactivate
   rmdir /s venv
   python -m venv venv
   venv\Scripts\activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

6. **Используйте скрипт проверки:**
   ```bash
   python check_installation.py
   ```

### Проблема: COM порт занят
**Решение:** 
1. Закройте все программы, использующие COM порт
2. Перезагрузите компьютер
3. Проверьте настройки COM порта в диспетчере устройств

## Проверка работы

1. Запустите систему
2. Должно появиться сообщение: "Modbus TCP сервер запущен на порту 5020"
3. Запустите GUI отладки (опционально):
   ```bash
   python modbus_debug_gui.py
   ```

## Дополнительная информация

- База данных создаётся автоматически: `modbus_registers.db`
- Все логи выводятся в консоль
- Modbus TCP сервер работает на порту 5020 (можно изменить)

