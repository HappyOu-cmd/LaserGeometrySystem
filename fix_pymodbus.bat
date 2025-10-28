@echo off
echo ========================================
echo Установка pymodbus для Laser Geometry System
echo ========================================
echo.

echo 1. Проверка виртуального окружения...
python -c "import sys; print('Python:', sys.executable)"
echo.

echo 2. Деактивация pymodbus (если установлен)...
python -m pip uninstall pymodbus -y
echo.

echo 3. Обновление pip...
python -m pip install --upgrade pip
echo.

echo 4. Установка pymodbus 3.6.0...
python -m pip install pymodbus>=3.6.0,<4.0.0
echo.

echo 5. Проверка установки...
python -c "import pymodbus; print('OK! pymodbus', pymodbus.__version__, 'установлен успешно')"
if errorlevel 1 (
    echo ОШИБКА! pymodbus не установлен.
    echo.
    echo Попробуйте:
    echo 1. Убедитесь что venv активирован: venv\Scripts\activate
    echo 2. Запустите: python fix_pymodbus.bat
    pause
    exit /b 1
)

echo.
echo ========================================
echo Установка завершена успешно!
echo ========================================
pause

