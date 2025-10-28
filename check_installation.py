#!/usr/bin/env python3
"""
Скрипт для проверки установки всех зависимостей проекта
"""

import sys

def check_package(package_name, import_name=None):
    """Проверка установки пакета"""
    if import_name is None:
        import_name = package_name
    
    try:
        __import__(import_name)
        print(f"✅ {package_name} - установлен")
        return True
    except ImportError:
        print(f"❌ {package_name} - НЕ установлен")
        return False

def main():
    print("=" * 50)
    print("Проверка установки зависимостей проекта")
    print("=" * 50)
    print()
    
    # Проверяем Python версию
    print(f"Python версия: {sys.version}")
    print(f"Путь к Python: {sys.executable}")
    print()
    
    # Проверяем, в виртуальном окружении ли мы
    in_venv = hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)
    if in_venv:
        print("✅ Работаем в виртуальном окружении")
    else:
        print("⚠️  ВНИМАНИЕ: НЕ в виртуальном окружении!")
        print("   Рекомендуется использовать виртуальное окружение!")
    print()
    
    # Проверяем основные пакеты
    packages = [
        ("pyserial", "serial"),
        ("pymodbus", "pymodbus"),
        ("psutil", "psutil"),
    ]
    
    all_ok = True
    for package, import_name in packages:
        if not check_package(package, import_name):
            all_ok = False
    
    print()
    print("=" * 50)
    if all_ok:
        print("✅ Все зависимости установлены правильно!")
    else:
        print("❌ Некоторые зависимости отсутствуют!")
        print()
        print("Решения:")
        print("1. Убедитесь, что виртуальное окружение активировано:")
        print("   Windows: venv\\Scripts\\activate")
        print("   Linux/Mac: source venv/bin/activate")
        print()
        print("2. Установите недостающие пакеты:")
        print("   pip install -r requirements.txt")
        print()
        print("3. Если проблема сохраняется, попробуйте:")
        print("   pip install --upgrade pip")
        print("   pip install --force-reinstall pymodbus")
    print("=" * 50)
    
    return 0 if all_ok else 1

if __name__ == "__main__":
    sys.exit(main())

