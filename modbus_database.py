#!/usr/bin/env python3
"""
Модуль для работы с базой данных Modbus регистров
Автоматическое сохранение и загрузка всех регистров
"""

import sqlite3
import time
import struct
from typing import Dict, List, Tuple, Optional
from pathlib import Path


class ModbusDatabase:
    """Класс для работы с базой данных Modbus регистров"""
    
    def __init__(self, db_path: str = "modbus_registers.db"):
        """
        Инициализация базы данных
        
        Args:
            db_path: Путь к файлу базы данных
        """
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Инициализация структуры базы данных"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Создаем таблицу для всех регистров (базовая версия)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS modbus_registers (
                    register_address INTEGER PRIMARY KEY,
                    register_type TEXT NOT NULL,  -- 'holding' или 'input'
                    value_high INTEGER,           -- старший регистр (для DoubleWord)
                    value_low INTEGER,            -- младший регистр (для DoubleWord)
                    float_value REAL,             -- float значение (для удобства)
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    description TEXT              -- описание регистра
                )
            ''')
            
            # Создаем таблицу истории измерений
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS measurement_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shift_number INTEGER,
                    product_number INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    result TEXT,
                    
                    upper_wall_max REAL,
                    upper_wall_avg REAL,
                    upper_wall_min REAL,
                    upper_wall_status TEXT,
                    
                    flange_thickness_max REAL,
                    flange_thickness_avg REAL,
                    flange_thickness_min REAL,
                    flange_thickness_status TEXT,
                    
                    body_diameter_max REAL,
                    body_diameter_avg REAL,
                    body_diameter_min REAL,
                    body_diameter_status TEXT,
                    
                    flange_diameter_max REAL,
                    flange_diameter_avg REAL,
                    flange_diameter_min REAL,
                    flange_diameter_status TEXT,
                    
                    conditionally_bad_count INTEGER,
                    bad_count INTEGER,
                    
                    check_mode INTEGER,
                    allowed_conditionally_bad INTEGER,
                    allowed_bad INTEGER
                )
            ''')
            
            # Создаем индекс для быстрого поиска по смене
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_shift_number 
                ON measurement_history(shift_number)
            ''')
            
            # Создаем индекс для поиска по времени
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_timestamp 
                ON measurement_history(timestamp)
            ''')
            
            # Создаем таблицу для хранения средних значений измерений текущей смены
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS quality_measurements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shift_number INTEGER NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    height_avg REAL,
                    upper_wall_avg REAL,
                    body_diameter_avg REAL,
                    flange_diameter_avg REAL,
                    bottom_wall_avg REAL,
                    flange_thickness_avg REAL,
                    bottom_avg REAL
                )
            ''')
            
            # Создаем индекс для быстрого поиска по смене
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_quality_shift_number 
                ON quality_measurements(shift_number)
            ''')
            
            # Выполняем миграцию для добавления новых полей
            self._migrate_database(cursor)
            
            # Создаем индексы для быстрого поиска
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_register_type 
                ON modbus_registers(register_type)
            ''')
            
            try:
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_float_display 
                    ON modbus_registers(is_float_display)
                ''')
            except sqlite3.OperationalError:
                # Поле еще не существует, пропускаем
                pass
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_last_updated 
                ON modbus_registers(last_updated)
            ''')
            
            conn.commit()
    
    def _migrate_database(self, cursor):
        """Миграция базы данных для добавления новых полей"""
        # Проверяем, существуют ли новые поля в modbus_registers
        cursor.execute("PRAGMA table_info(modbus_registers)")
        columns = [column[1] for column in cursor.fetchall()]
        
        # Добавляем новые поля если их нет
        if 'is_float_display' not in columns:
            cursor.execute('ALTER TABLE modbus_registers ADD COLUMN is_float_display INTEGER DEFAULT 0')
            print("Добавлено поле is_float_display")
        
        if 'float_word_type' not in columns:
            cursor.execute('ALTER TABLE modbus_registers ADD COLUMN float_word_type TEXT DEFAULT "single"')
            print("Добавлено поле float_word_type")
        
        if 'float_pair_address' not in columns:
            cursor.execute('ALTER TABLE modbus_registers ADD COLUMN float_pair_address INTEGER')
            print("Добавлено поле float_pair_address")
        
        if 'float_name' not in columns:
            cursor.execute('ALTER TABLE modbus_registers ADD COLUMN float_name TEXT')
            print("Добавлено поле float_name")
        
        if 'float_units' not in columns:
            cursor.execute('ALTER TABLE modbus_registers ADD COLUMN float_units TEXT DEFAULT "мм"')
            print("Добавлено поле float_units")
        
        # Проверяем, существуют ли новые поля в measurement_history
        cursor.execute("PRAGMA table_info(measurement_history)")
        measurement_columns = [column[1] for column in cursor.fetchall()]
        
        # Добавляем поля для высоты
        if 'height_max' not in measurement_columns:
            cursor.execute('ALTER TABLE measurement_history ADD COLUMN height_max REAL')
            cursor.execute('ALTER TABLE measurement_history ADD COLUMN height_avg REAL')
            cursor.execute('ALTER TABLE measurement_history ADD COLUMN height_min REAL')
            cursor.execute('ALTER TABLE measurement_history ADD COLUMN height_status TEXT')
            print("Добавлены поля для высоты в measurement_history")
        
        # Добавляем поля для нижней стенки
        if 'bottom_wall_max' not in measurement_columns:
            cursor.execute('ALTER TABLE measurement_history ADD COLUMN bottom_wall_max REAL')
            cursor.execute('ALTER TABLE measurement_history ADD COLUMN bottom_wall_avg REAL')
            cursor.execute('ALTER TABLE measurement_history ADD COLUMN bottom_wall_min REAL')
            cursor.execute('ALTER TABLE measurement_history ADD COLUMN bottom_wall_status TEXT')
            print("Добавлены поля для нижней стенки в measurement_history")
        
        # Добавляем поля для дна
        if 'bottom_max' not in measurement_columns:
            cursor.execute('ALTER TABLE measurement_history ADD COLUMN bottom_max REAL')
            cursor.execute('ALTER TABLE measurement_history ADD COLUMN bottom_avg REAL')
            cursor.execute('ALTER TABLE measurement_history ADD COLUMN bottom_min REAL')
            cursor.execute('ALTER TABLE measurement_history ADD COLUMN bottom_status TEXT')
            print("Добавлены поля для дна в measurement_history")
    
    def save_register(self, address: int, register_type: str, value_high: int = 0, 
                     value_low: int = 0, float_value: float = 0.0, description: str = "",
                     is_float_display: bool = False, float_word_type: str = 'single',
                     float_pair_address: int = None, float_name: str = "", 
                     float_units: str = "мм"):
        """
        Сохранение регистра в базу данных
        
        Args:
            address: Адрес регистра (40001, 40002, etc.)
            register_type: Тип регистра ('holding' или 'input')
            value_high: Старший регистр (для DoubleWord)
            value_low: Младший регистр (для DoubleWord)
            float_value: Float значение
            description: Описание регистра
            is_float_display: Отображать в Float Values
            float_word_type: Тип слова ('single', 'low', 'high')
            float_pair_address: Адрес парного регистра
            float_name: Название для Float Values
            float_units: Единицы измерения
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO modbus_registers 
                (register_address, register_type, value_high, value_low, float_value, 
                 last_updated, description, is_float_display, float_word_type,
                 float_pair_address, float_name, float_units)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?)
            ''', (address, register_type, value_high, value_low, float_value, description,
                  1 if is_float_display else 0, float_word_type, float_pair_address, 
                  float_name, float_units))
            
            conn.commit()
    
    def save_doubleword_register(self, address: int, register_type: str, 
                                float_value: float, description: str = ""):
        """
        Сохранение DoubleWord регистра (два соседних регистра)
        
        Args:
            address: Адрес младшего регистра (40002, 40010, etc.)
            register_type: Тип регистра ('holding' или 'input')
            float_value: Float значение
            description: Описание регистра
        
        ВАЖНО: При записи в регистры используется порядок:
        - setValues(3, addr_idx, [high_word]) - регистр address (40010) содержит high_word
        - setValues(3, addr_idx+1, [low_word]) - регистр address+1 (40011) содержит low_word
        Поэтому при сохранении в БД:
        - address (40010) должен содержать high_word (value_high)
        - address+1 (40011) должен содержать low_word (value_low)
        """
        # Конвертируем float в два 16-битных регистра
        low_word, high_word = self.float_to_doubleword(float_value)
        
        # ВАЖНО: При записи в регистры используется порядок:
        # - Регистр address (40010) содержит high_word
        # - Регистр address+1 (40011) содержит low_word
        # Поэтому сохраняем в БД в том же порядке:
        # Регистр address (40010) сохраняется с high_word (value_high) и low_word (value_low) для восстановления float
        # Это позволяет восстановить float из пары регистров при загрузке
        self.save_register(address, register_type, high_word, low_word, float_value, 
                          f"{description} (старший)")
        
        # Регистр address+1 (40011) сохраняется с low_word (value_low) и high_word (value_high) для восстановления float
        # Оба регистра сохраняют полную информацию (high_word, low_word) для восстановления float
        self.save_register(address + 1, register_type, high_word, low_word, float_value, 
                          f"{description} (младший)")
    
    def save_float_pair(self, address_low: int, address_high: int, register_type: str,
                       float_value: float, float_name: str, description: str = "",
                       float_units: str = "мм"):
        """
        Сохранение Float пары с флагами для отображения в Float Values
        
        Args:
            address_low: Адрес младшего регистра
            address_high: Адрес старшего регистра  
            register_type: Тип регистра ('holding' или 'input')
            float_value: Float значение
            float_name: Название для Float Values
            description: Описание регистра
            float_units: Единицы измерения
        """
        # Конвертируем float в два 16-битных регистра
        low_word, high_word = self.float_to_doubleword(float_value)
        
        # Сохраняем младший регистр с флагами Float
        self.save_register(
            address_low, register_type, 0, low_word, float_value, 
            f"{description} (младший)", 
            is_float_display=True, float_word_type='low', 
            float_pair_address=address_high, float_name=float_name, 
            float_units=float_units
        )
        
        # Сохраняем старший регистр с флагами Float
        self.save_register(
            address_high, register_type, high_word, 0, 0.0,
            f"{description} (старший)",
            is_float_display=True, float_word_type='high',
            float_pair_address=address_low, float_name=float_name,
            float_units=float_units
        )
    
    def save_single_register(self, address: int, register_type: str, 
                           value: int, description: str = ""):
        """
        Сохранение одиночного регистра
        
        Args:
            address: Адрес регистра
            register_type: Тип регистра ('holding' или 'input')
            value: Значение регистра
            description: Описание регистра
        """
        self.save_register(address, register_type, 0, value, float(value), description)
    
    def load_register(self, address: int, register_type: str) -> Optional[Dict]:
        """
        Загрузка регистра из базы данных
        
        Args:
            address: Адрес регистра
            register_type: Тип регистра
            
        Returns:
            Словарь с данными регистра или None
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT register_address, register_type, value_high, value_low, 
                       float_value, last_updated, description, is_float_display,
                       float_word_type, float_pair_address, float_name, float_units
                FROM modbus_registers 
                WHERE register_address = ? AND register_type = ?
            ''', (address, register_type))
            
            row = cursor.fetchone()
            if row:
                return {
                    'address': row[0],
                    'type': row[1],
                    'value_high': row[2],
                    'value_low': row[3],
                    'float_value': row[4],
                    'last_updated': row[5],
                    'description': row[6],
                    'is_float_display': bool(row[7]),
                    'float_word_type': row[8],
                    'float_pair_address': row[9],
                    'float_name': row[10],
                    'float_units': row[11]
                }
            return None
    
    def load_all_registers(self, register_type: str = None) -> List[Dict]:
        """
        Загрузка всех регистров из базы данных
        
        Args:
            register_type: Тип регистра ('holding', 'input' или None для всех)
            
        Returns:
            Список словарей с данными регистров
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            if register_type:
                cursor.execute('''
                    SELECT register_address, register_type, value_high, value_low, 
                           float_value, last_updated, description, is_float_display,
                           float_word_type, float_pair_address, float_name, float_units
                    FROM modbus_registers 
                    WHERE register_type = ?
                    ORDER BY register_address
                ''', (register_type,))
            else:
                cursor.execute('''
                    SELECT register_address, register_type, value_high, value_low, 
                           float_value, last_updated, description, is_float_display,
                           float_word_type, float_pair_address, float_name, float_units
                    FROM modbus_registers 
                    ORDER BY register_type, register_address
                ''')
            
            rows = cursor.fetchall()
            return [{
                'address': row[0],
                'type': row[1],
                'value_high': row[2],
                'value_low': row[3],
                'float_value': row[4],
                'last_updated': row[5],
                'description': row[6],
                'is_float_display': bool(row[7]),
                'float_word_type': row[8],
                'float_pair_address': row[9],
                'float_name': row[10],
                'float_units': row[11]
            } for row in rows]
    
    def load_doubleword_register(self, address: int, register_type: str) -> Optional[float]:
        """
        Загрузка DoubleWord регистра из базы данных
        
        Args:
            address: Адрес младшего регистра
            register_type: Тип регистра
            
        Returns:
            Float значение или None
        """
        low_reg = self.load_register(address, register_type)
        high_reg = self.load_register(address + 1, register_type)
        
        if low_reg and high_reg:
            return self.doubleword_to_float(low_reg['value_low'], high_reg['value_low'])
        return None
    
    def load_float_pairs(self, register_type: str = None) -> List[Dict]:
        """
        Загрузка Float пар для отображения в Float Values
        
        Args:
            register_type: Тип регистра ('holding', 'input' или None для всех)
            
        Returns:
            Список Float пар с данными для отображения
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Загружаем только регистры с флагом Float и типом 'low' (младшие)
            if register_type:
                cursor.execute('''
                    SELECT register_address, register_type, float_value, float_name, 
                           float_units, description, float_pair_address
                    FROM modbus_registers 
                    WHERE register_type = ? AND is_float_display = 1 AND float_word_type = 'low'
                    ORDER BY register_address
                ''', (register_type,))
            else:
                cursor.execute('''
                    SELECT register_address, register_type, float_value, float_name, 
                           float_units, description, float_pair_address
                    FROM modbus_registers 
                    WHERE is_float_display = 1 AND float_word_type = 'low'
                    ORDER BY register_type, register_address
                ''')
            
            rows = cursor.fetchall()
            float_pairs = []
            
            for row in rows:
                address_low = row[0]
                register_type = row[1]
                float_value = row[2]
                float_name = row[3] or f"Регистр {address_low}"
                float_units = row[4] or "мм"
                description = row[5] or ""
                address_high = row[6]
                
                # Формируем строку адресов (младший-старший)
                if address_high:
                    min_addr = min(address_low, address_high)
                    max_addr = max(address_low, address_high)
                    registers_str = f"{min_addr}-{max_addr}"
                else:
                    registers_str = str(address_low)
                
                float_pairs.append({
                    'registers': registers_str,
                    'name': float_name,
                    'value': f"{float_value:.3f}",
                    'units': float_units,
                    'description': description,
                    'address_low': address_low,
                    'address_high': address_high,
                    'register_type': register_type
                })
            
            return float_pairs
    
    def delete_register(self, address: int, register_type: str) -> bool:
        """
        Удаление регистра из базы данных
        
        Args:
            address: Адрес регистра
            register_type: Тип регистра ('holding' или 'input')
            
        Returns:
            True если регистр удален, False если не найден
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                DELETE FROM modbus_registers 
                WHERE register_address = ? AND register_type = ?
            ''', (address, register_type))
            
            return cursor.rowcount > 0
    
    def delete_doubleword_register(self, address: int, register_type: str) -> bool:
        """
        Удаление DoubleWord регистра (два последовательных регистра)
        
        Args:
            address: Адрес младшего регистра
            register_type: Тип регистра ('holding' или 'input')
            
        Returns:
            True если регистры удалены
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                DELETE FROM modbus_registers 
                WHERE register_address IN (?, ?) AND register_type = ?
            ''', (address, address + 1, register_type))
            
            return cursor.rowcount > 0
    
    def remove_float_display_flag(self, address: int, register_type: str) -> bool:
        """
        Убрать флаг отображения в Float Values для пары регистров
        
        Args:
            address: Адрес младшего регистра
            register_type: Тип регистра ('holding' или 'input')
            
        Returns:
            True если обновлен успешно
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Убираем флаг is_float_display для обоих регистров пары
            cursor.execute('''
                UPDATE modbus_registers 
                SET is_float_display = 0, 
                    float_word_type = 'single',
                    float_pair_address = NULL,
                    float_name = '',
                    float_units = ''
                WHERE register_address IN (?, ?) AND register_type = ?
            ''', (address, address + 1, register_type))
            
            return cursor.rowcount > 0
    
    def clear_all_registers(self, register_type: str = None):
        """
        Очистка всех регистров из базы данных
        
        Args:
            register_type: Тип регистра ('holding', 'input' или None для всех)
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            if register_type:
                cursor.execute('DELETE FROM modbus_registers WHERE register_type = ?', 
                             (register_type,))
            else:
                cursor.execute('DELETE FROM modbus_registers')
            
            conn.commit()
    
    def get_database_stats(self) -> Dict:
        """
        Получение статистики базы данных
        
        Returns:
            Словарь со статистикой
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Общее количество регистров
            cursor.execute('SELECT COUNT(*) FROM modbus_registers')
            total_registers = cursor.fetchone()[0]
            
            # Количество по типам
            cursor.execute('''
                SELECT register_type, COUNT(*) 
                FROM modbus_registers 
                GROUP BY register_type
            ''')
            by_type = dict(cursor.fetchall())
            
            # Последнее обновление
            cursor.execute('''
                SELECT MAX(last_updated) 
                FROM modbus_registers
            ''')
            last_update = cursor.fetchone()[0]
            
            return {
                'total_registers': total_registers,
                'by_type': by_type,
                'last_update': last_update,
                'database_path': self.db_path
            }
    
    def float_to_doubleword(self, value: float) -> Tuple[int, int]:
        """Конвертация float в два 16-битных регистра"""
        packed = struct.pack('>f', value)  # Big-endian float
        high_word, low_word = struct.unpack('>HH', packed)
        return low_word, high_word  # Младшее слово первое
    
    def doubleword_to_float(self, low_word: int, high_word: int) -> float:
        """Конвертация двух 16-битных регистров в float"""
        packed = struct.pack('>HH', high_word, low_word)
        return struct.unpack('>f', packed)[0]
    
    def backup_database(self, backup_path: str = None):
        """
        Создание резервной копии базы данных
        
        Args:
            backup_path: Путь для резервной копии
        """
        if backup_path is None:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            backup_path = f"modbus_registers_backup_{timestamp}.db"
        
        # Копируем файл базы данных
        import shutil
        shutil.copy2(self.db_path, backup_path)
        return backup_path
    
    def restore_database(self, backup_path: str):
        """
        Восстановление базы данных из резервной копии
        
        Args:
            backup_path: Путь к резервной копии
        """
        import shutil
        shutil.copy2(backup_path, self.db_path)
        print(f"База данных восстановлена из {backup_path}")
    
    def save_measurement_record(self, measurement_data: Dict):
        """
        Сохранение записи измерения в историю
        
        Args:
            measurement_data: Словарь с данными измерения
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO measurement_history (
                    shift_number, product_number, result,
                    upper_wall_max, upper_wall_avg, upper_wall_min, upper_wall_status,
                    flange_thickness_max, flange_thickness_avg, flange_thickness_min, flange_thickness_status,
                    body_diameter_max, body_diameter_avg, body_diameter_min, body_diameter_status,
                    flange_diameter_max, flange_diameter_avg, flange_diameter_min, flange_diameter_status,
                    conditionally_bad_count, bad_count,
                    check_mode, allowed_conditionally_bad, allowed_bad
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                measurement_data.get('shift_number'),
                measurement_data.get('product_number'),
                measurement_data.get('result'),
                measurement_data.get('upper_wall_max'),
                measurement_data.get('upper_wall_avg'),
                measurement_data.get('upper_wall_min'),
                measurement_data.get('upper_wall_status'),
                measurement_data.get('flange_thickness_max'),
                measurement_data.get('flange_thickness_avg'),
                measurement_data.get('flange_thickness_min'),
                measurement_data.get('flange_thickness_status'),
                measurement_data.get('body_diameter_max'),
                measurement_data.get('body_diameter_avg'),
                measurement_data.get('body_diameter_min'),
                measurement_data.get('body_diameter_status'),
                measurement_data.get('flange_diameter_max'),
                measurement_data.get('flange_diameter_avg'),
                measurement_data.get('flange_diameter_min'),
                measurement_data.get('flange_diameter_status'),
                measurement_data.get('conditionally_bad_count'),
                measurement_data.get('bad_count'),
                measurement_data.get('check_mode'),
                measurement_data.get('allowed_conditionally_bad'),
                measurement_data.get('allowed_bad')
            ))
            conn.commit()
    
    def get_shift_statistics(self, shift_number: int) -> Dict:
        """
        Получение статистики по смене
        
        Args:
            shift_number: Номер смены
            
        Returns:
            Словарь со статистикой смены
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN result = 'GOOD' THEN 1 ELSE 0 END) as good,
                    SUM(CASE WHEN result = 'CONDITIONALLY_GOOD' THEN 1 ELSE 0 END) as conditionally_good,
                    SUM(CASE WHEN result = 'BAD' THEN 1 ELSE 0 END) as bad
                FROM measurement_history
                WHERE shift_number = ?
            ''', (shift_number,))
            
            row = cursor.fetchone()
            return {
                'total': row[0] or 0,
                'good': row[1] or 0,
                'conditionally_good': row[2] or 0,
                'bad': row[3] or 0
            }
    
    def save_quality_measurement(self, shift_number: int, measurement_data: Dict):
        """
        Сохранение средних значений измерения в таблицу quality_measurements
        
        Args:
            shift_number: Номер смены
            measurement_data: Словарь с ключами height_avg, upper_wall_avg, и т.д.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO quality_measurements (
                    shift_number,
                    height_avg,
                    upper_wall_avg,
                    body_diameter_avg,
                    flange_diameter_avg,
                    bottom_wall_avg,
                    flange_thickness_avg,
                    bottom_avg
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                shift_number,
                measurement_data.get('height_avg'),
                measurement_data.get('upper_wall_avg'),
                measurement_data.get('body_diameter_avg'),
                measurement_data.get('flange_diameter_avg'),
                measurement_data.get('bottom_wall_avg'),
                measurement_data.get('flange_thickness_avg'),
                measurement_data.get('bottom_avg')
            ))
            conn.commit()
    
    def get_shift_measurements(self, shift_number: int) -> List[Dict]:
        """
        Получение всех измерений для смены
        
        Args:
            shift_number: Номер смены
            
        Returns:
            Список словарей с измерениями
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 
                    id,
                    height_avg,
                    upper_wall_avg,
                    body_diameter_avg,
                    flange_diameter_avg,
                    bottom_wall_avg,
                    flange_thickness_avg,
                    bottom_avg
                FROM quality_measurements
                WHERE shift_number = ?
                ORDER BY id
            ''', (shift_number,))
            
            rows = cursor.fetchall()
            return [{
                'id': row[0],
                'height_avg': row[1],
                'upper_wall_avg': row[2],
                'body_diameter_avg': row[3],
                'flange_diameter_avg': row[4],
                'bottom_wall_avg': row[5],
                'flange_thickness_avg': row[6],
                'bottom_avg': row[7]
            } for row in rows]
    
    def clear_shift_measurements(self, shift_number: int):
        """
        Очистка измерений для смены
        
        Args:
            shift_number: Номер смены
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM quality_measurements
                WHERE shift_number = ?
            ''', (shift_number,))
            conn.commit()


def main():
    """Тестирование модуля базы данных"""
    print("Тестирование модуля Modbus Database")
    
    # Создаем экземпляр базы данных
    db = ModbusDatabase("test_modbus.db")
    
    # Тестируем сохранение регистров
    print("\n1. Сохранение регистров...")
    
    # Сохраняем одиночные регистры
    db.save_single_register(40001, 'holding', 0, 'CMD команда')
    db.save_single_register(30001, 'input', 12500, 'Датчик 1')
    
    # Сохраняем DoubleWord регистры
    db.save_doubleword_register(40002, 'holding', 5.0, 'Эталонная толщина стенки')
    db.save_doubleword_register(40010, 'holding', 50.5, 'Расстояние датчики 1,2')
    
    # Тестируем загрузку
    print("\n2. Загрузка регистров...")
    
    cmd_reg = db.load_register(40001, 'holding')
    print(f"CMD регистр: {cmd_reg}")
    
    thickness = db.load_doubleword_register(40002, 'holding')
    print(f"Эталонная толщина: {thickness}")
    
    # Тестируем загрузку всех регистров
    print("\n3. Все регистры:")
    all_registers = db.load_all_registers()
    for reg in all_registers:
        print(f"  {reg['address']} ({reg['type']}): {reg['float_value']}")
    
    # Статистика
    print("\n4. Статистика базы данных:")
    stats = db.get_database_stats()
    print(f"  Всего регистров: {stats['total_registers']}")
    print(f"  По типам: {stats['by_type']}")
    print(f"  Последнее обновление: {stats['last_update']}")
    
    print("\nТестирование завершено!")


if __name__ == "__main__":
    main()
