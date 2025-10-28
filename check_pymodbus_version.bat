@echo off
echo ========================================
echo Проверка версии pymodbus
echo ========================================
echo.

echo 1. Проверка через pip show:
python -m pip show pymodbus | findstr /C:"Version:"
echo.

echo 2. Проверка через import:
python -c "import pymodbus; print('Версия:', pymodbus.__version__)" 2>nul
if errorlevel 1 (
    echo ОШИБКА: pymodbus не установлен или не может быть импортирован
    echo.
    echo Установите pymodbus:
    echo python -m pip install pymodbus>=3.6.0,^<4.0.0
)
echo.

echo 3. Проверка расположения пакета:
python -m pip show pymodbus | findstr /C:"Location:"
echo.

pause

