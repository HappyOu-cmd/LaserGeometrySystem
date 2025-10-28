#!/usr/bin/env python3
"""
Диагностический скрипт для проблемы с pymodbus
"""

import sys
import subprocess

def run_command(cmd):
    """Выполнить команду и получить результат"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except Exception as e:
        return "", str(e), 1

def main():
    print("=" * 60)
    print("ДИАГНОСТИКА ПРОБЛЕМЫ С PYMODBUS")
    print("=" * 60)
    print()
    
    # 1. Проверка Python
    print("1. Информация о Python:")
    print(f"   Версия: {sys.version}")
    print(f"   Исполняемый файл: {sys.executable}")
    print()
    
    # 2. Проверка виртуального окружения
    in_venv = hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)
    if in_venv:
        print("   ✅ Работаем в виртуальном окружении")
        print(f"   Путь к venv: {sys.prefix}")
    else:
        print("   ❌ НЕ в виртуальном окружении!")
    print()
    
    # 3. Проверка pip
    print("2. Информация о pip:")
    stdout, stderr, code = run_command(f'"{sys.executable}" -m pip --version')
    if code == 0:
        print(f"   ✅ {stdout}")
    else:
        print(f"   ❌ Ошибка: {stderr}")
    print()
    
    # 4. Проверка установленных пакетов
    print("3. Установленные версии пакетов:")
    packages = ['pymodbus', 'pyserial', 'psutil']
    for package in packages:
        stdout, stderr, code = run_command(f'"{sys.executable}" -m pip show {package}')
        if code == 0:
            # Извлекаем версию
            for line in stdout.split('\n'):
                if line.startswith('Version:'):
                    version = line.split(':', 1)[1].strip()
                    location_line = [l for l in stdout.split('\n') if l.startswith('Location:')]
                    location = location_line[0].split(':', 1)[1].strip() if location_line else 'unknown'
                    print(f"   ✅ {package}: {version}")
                    print(f"      Расположение: {location}")
                    break
        else:
            print(f"   ❌ {package}: НЕ установлен")
    print()
    
    # 5. Проверка импорта
    print("4. Попытка импорта pymodbus:")
    try:
        import pymodbus
        print(f"   ✅ Успешный импорт pymodbus")
        print(f"   Версия: {getattr(pymodbus, '__version__', 'unknown')}")
        
        # Проверяем специфичные импорты
        print()
        print("5. Проверка специфичных импортов:")
        try:
            from pymodbus.server import StartTcpServer
            print("   ✅ from pymodbus.server import StartTcpServer")
        except ImportError as e:
            print(f"   ❌ from pymodbus.server import StartTcpServer: {e}")
        
        try:
            from pymodbus.device import ModbusDeviceIdentification
            print("   ✅ from pymodbus.device import ModbusDeviceIdentification")
        except ImportError as e:
            print(f"   ❌ from pymodbus.device import ModbusDeviceIdentification: {e}")
        
        try:
            from pymodbus.datastore import ModbusSequentialDataBlock
            print("   ✅ from pymodbus.datastore import ModbusSequentialDataBlock")
        except ImportError as e:
            print(f"   ❌ from pymodbus.datastore import ModbusSequentialDataBlock: {e}")
            
    except ImportError as e:
        print(f"   ❌ Ошибка импорта: {e}")
        print()
        print("   РЕШЕНИЕ:")
        print(f"   Запустите: \"{sys.executable}\" -m pip install pymodbus>=3.6.0")
    
    print()
    print("=" * 60)
    print("РЕКОМЕНДАЦИИ:")
    print("=" * 60)
    
    if not in_venv:
        print("1. Активируйте виртуальное окружение:")
        print("   venv\\Scripts\\activate")
        print()
    
    print("2. Переустановите pymodbus:")
    print(f"   \"{sys.executable}\" -m pip uninstall pymodbus -y")
    print(f"   \"{sys.executable}\" -m pip install --upgrade pip")
    print(f"   \"{sys.executable}\" -m pip install pymodbus>=3.6.0")
    print()
    
    print("3. Проверьте после установки:")
    print(f"   \"{sys.executable}\" -c \"import pymodbus; print('OK:', pymodbus.__version__)\"")
    print()
    print("=" * 60)

if __name__ == "__main__":
    main()

