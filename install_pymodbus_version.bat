@echo off
echo ========================================
echo Установка конкретной версии pymodbus
echo ========================================
echo.

if "%1"=="" (
    echo Использование:
    echo   install_pymodbus_version.bat [версия]
    echo.
    echo Примеры:
    echo   install_pymodbus_version.bat 3.6.0
    echo   install_pymodbus_version.bat 3.7.0
    echo   install_pymodbus_version.bat "^>=3.6.0,^<4.0.0"
    echo.
    echo Текущая версия:
    python -m pip show pymodbus | findstr /C:"Version:"
    pause
    exit /b
)

echo Установка pymodbus версии: %1
echo.

echo 1. Удаление старой версии...
python -m pip uninstall pymodbus -y
echo.

echo 2. Установка новой версии...
python -m pip install pymodbus%1
echo.

echo 3. Проверка установки...
python -c "import pymodbus; print('OK! Установлена версия:', pymodbus.__version__)"
if errorlevel 1 (
    echo ОШИБКА! Установка не удалась.
    pause
    exit /b 1
)

echo.
echo ========================================
echo Установка завершена успешно!
echo ========================================
pause

