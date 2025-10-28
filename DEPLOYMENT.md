# 🚀 Развертывание Laser Geometry System

## Быстрая инструкция для GitHub

### На вашем текущем компьютере:

1. **Инициализируйте Git репозиторий (если еще не сделано):**
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   ```

2. **Создайте репозиторий на GitHub:**
   - Зайдите на https://github.com
   - Создайте новый репозиторий (например: `LaserGeometry`)
   - НЕ добавляйте README, .gitignore или лицензию (они уже есть)

3. **Подключите локальный репозиторий к GitHub:**
   ```bash
   git remote add origin https://github.com/ваш-юзернейм/LaserGeometry.git
   git branch -M main
   git push -u origin main
   ```

### На новом компьютере (чистый Windows):

1. **Установите Python 3.10+** с https://www.python.org/downloads/
   - ✅ Важно: отметьте "Add Python to PATH"

2. **Установите Git** с https://git-scm.com/download/win

3. **Склонируйте проект:**
   ```bash
   git clone https://github.com/ваш-юзернейм/LaserGeometry.git
   cd LaserGeometry
   ```

4. **Создайте виртуальное окружение:**
   ```bash
   python -m venv venv
   venv\Scripts\activate
   ```

5. **Установите зависимости:**
   ```bash
   pip install -r requirements.txt
   ```

6. **Настройте COM порт в `laser_geometry_system.py`**

7. **Запустите:**
   ```bash
   python laser_geometry_system.py
   ```

## Структура файлов для GitHub

### ✅ Включить в репозиторий:
- Все `.py` файлы
- `requirements.txt`
- `README.md`
- `.gitignore`
- `SETUP_GUIDE.md`
- `DEPLOYMENT.md`

### ❌ НЕ включать (уже в .gitignore):
- `__pycache__/`
- `*.db` (базы данных)
- `venv/`
- `.idea/`
- `Backup/`

## Что проверить перед push в GitHub

1. ✅ Все зависимости в `requirements.txt`
2. ✅ `.gitignore` настроен правильно
3. ✅ README.md обновлён
4. ✅ Нет паролей/секретов в коде
5. ✅ База данных не включена в репозиторий

## После развертывания

- База данных `modbus_registers.db` создастся автоматически при первом запуске
- Все настройки сохраняются в БД автоматически
- При необходимости перенесите БД с калибровками вручную (если нужно)

