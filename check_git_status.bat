@echo off
echo ========================================
echo Проверка соответствия файлов GitHub
echo ========================================
echo.

echo 1. Получение последних данных с GitHub...
git fetch origin
echo.

echo 2. Проверка статуса локальных файлов...
git status
echo.

echo 3. Сравнение с GitHub (main ветка)...
echo.
echo --- Файлы, которые отличаются от GitHub: ---
git diff origin/main --name-status
echo.

echo --- Детальные различия: ---
git diff origin/main
echo.

echo 4. Коммиты, которые есть на GitHub, но отсутствуют локально:
git log HEAD..origin/main --oneline
if %errorlevel% neq 0 (
    echo Все коммиты синхронизированы
)
echo.

echo 5. Коммиты, которые есть локально, но отсутствуют на GitHub:
git log origin/main..HEAD --oneline
if %errorlevel% neq 0 (
    echo Все коммиты синхронизированы
)
echo.

echo 6. Хеш последнего коммита локально:
git rev-parse HEAD
echo.

echo 7. Хеш последнего коммита на GitHub:
git rev-parse origin/main
echo.

echo ========================================
echo Если различий нет, файлы полностью соответствуют GitHub
echo ========================================
pause

