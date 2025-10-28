#!/usr/bin/env python3
"""
Интеграция базы данных с Modbus сервером
Автоматическое сохранение и загрузка регистров
"""

import time
import threading
from typing import Dict, Any
from modbus_database import ModbusDatabase


class ModbusDatabaseIntegration:
    """Класс для интеграции базы данных с Modbus сервером"""
    
    def __init__(self, modbus_server, db_path: str = "modbus_registers.db"):
        """
        Инициализация интеграции
        
        Args:
            modbus_server: Экземпляр Modbus сервера
            db_path: Путь к файле базы данных
        """
        self.modbus_server = modbus_server
        self.db = ModbusDatabase(db_path)
        self.is_monitoring = False
        self.monitor_thread = None
        
        # DoubleWord пары FLOAT (адрес младшего регистра, описание)
        self.holding_doubleword_pairs = [
            (40002, "Эталонная толщина стенки"),
            (40004, "Эталонная толщина дна"),
            (40006, "Эталонный диаметр"),
            (40008, "Эталонная высота"),
            (40010, "Расстояние датчики 1,2"),
            (40012, "Расстояние датчики 1,3"),
            (40014, "Расстояние датчик 4"),
            (40016, "Расстояние датчик 1-центр"),
            (40018, "Расстояние датчик 2-центр"),
            (40055, "Дистанция до плоскости"),
            (40057, "Измеренная высота заготовки"),
        ]
        
        # DoubleWord пары INTEGER (большие числа, не float)
        self.holding_doubleword_int_pairs = [
            # (40052, "Количество шагов"),  # Не сохраняем в БД - это временные данные
        ]
        
        # Одиночные регистры для мониторинга
        self.holding_registers_to_monitor = [
            # Регистры для измерения высоты
            # (54, "Импульсов на 1 мм"),  # Не сохраняем в БД - это временные данные
            # Режимы проверки качества
            (49, "Режим проверки качества"),
            (50, "Допуск условно-годных"),
            (51, "Допуск негодных"),
            # НЕ сохраняются: CMD (40001), Смена (40100), Номер изделия (40101)
            # Пороговые значения для верхней стенки (40352-40357)
            (352, "Верхняя стенка базовое (мл)"),
            (353, "Верхняя стенка базовое (ст)"),
            (354, "Верхняя стенка условно-год (мл)"),
            (355, "Верхняя стенка условно-год (ст)"),
            (356, "Верхняя стенка негод (мл)"),
            (357, "Верхняя стенка негод (ст)"),
            # Пороговые значения для нижней стенки (40358-40363)
            (358, "Нижняя стенка базовое (мл)"),
            (359, "Нижняя стенка базовое (ст)"),
            (360, "Нижняя стенка условно-год (мл)"),
            (361, "Нижняя стенка условно-год (ст)"),
            (362, "Нижняя стенка негод (мл)"),
            (363, "Нижняя стенка негод (ст)"),
            # Пороговые значения для дна (40364-40369)
            (364, "Дно базовое (мл)"),
            (365, "Дно базовое (ст)"),
            (366, "Дно условно-год (мл)"),
            (367, "Дно условно-год (ст)"),
            (368, "Дно негод (мл)"),
            (369, "Дно негод (ст)"),
            # Пороговые значения для фланца (40370-40375)
            (370, "Фланец базовое (мл)"),
            (371, "Фланец базовое (ст)"),
            (372, "Фланец условно-год (мл)"),
            (373, "Фланец условно-год (ст)"),
            (374, "Фланец негод (мл)"),
            (375, "Фланец негод (ст)"),
            # Пороговые значения для высоты (40376-40381)
            (376, "Высота базовое (мл)"),
            (377, "Высота базовое (ст)"),
            (378, "Высота условно-год (мл)"),
            (379, "Высота условно-год (ст)"),
            (380, "Высота негод (мл)"),
            (381, "Высота негод (ст)"),
            # Пороговые значения для диаметра корпуса (40382-40387)
            (382, "Диаметр корпуса базовое (мл)"),
            (383, "Диаметр корпуса базовое (ст)"),
            (384, "Диаметр корпуса условно-год (мл)"),
            (385, "Диаметр корпуса условно-год (ст)"),
            (386, "Диаметр корпуса негод (мл)"),
            (387, "Диаметр корпуса негод (ст)"),
            # Пороговые значения для диаметра фланца (40388-40393)
            (388, "Диаметр фланца базовое (мл)"),
            (389, "Диаметр фланца базовое (ст)"),
            (390, "Диаметр фланца условно-год (мл)"),
            (391, "Диаметр фланца условно-год (ст)"),
            (392, "Диаметр фланца негод (мл)"),
            (393, "Диаметр фланца негод (ст)"),
            # Положительные погрешности (40400-40403)
            (400, "Дно положит погрешность (мл)"),
            (401, "Дно положит погрешность (ст)"),
            (402, "Нижняя стенка положит погрешность (мл)"),
            (403, "Нижняя стенка положит погрешность (ст)"),
        ]
        
        self.input_registers_to_monitor = [
            (1, "Датчик 1 расстояние (мл)"),
            (2, "Датчик 1 расстояние (ст)"),
            (3, "Датчик 2 расстояние (мл)"),
            (4, "Датчик 2 расстояние (ст)"),
            (5, "Датчик 3 расстояние (мл)"),
            (6, "Датчик 3 расстояние (ст)"),
            (7, "Датчик 4 расстояние (мл)"),
            (8, "Датчик 4 расстояние (ст)"),
            # НЕ сохраняется: Флаг цикла (30009)
            (10, "Толщина стенки (мл)"),
            (11, "Толщина стенки (ст)"),
            (12, "Толщина фланца (мл)"),
            (13, "Толщина фланца (ст)"),
            (14, "Толщина дна (мл)"),
            (15, "Толщина дна (ст)"),
            # Результаты измерений верхней стенки (30016-30021)
            (16, "Верхняя стенка макс (мл)"),
            (17, "Верхняя стенка макс (ст)"),
            (18, "Верхняя стенка сред (мл)"),
            (19, "Верхняя стенка сред (ст)"),
            (20, "Верхняя стенка мин (мл)"),
            (21, "Верхняя стенка мин (ст)"),
            # Результаты измерений нижней стенки (30022-30027)
            (22, "Нижняя стенка макс (мл)"),
            (23, "Нижняя стенка макс (ст)"),
            (24, "Нижняя стенка сред (мл)"),
            (25, "Нижняя стенка сред (ст)"),
            (26, "Нижняя стенка мин (мл)"),
            (27, "Нижняя стенка мин (ст)"),
            # Результаты измерений дна (30028-30033)
            (28, "Дно макс (мл)"),
            (29, "Дно макс (ст)"),
            (30, "Дно сред (мл)"),
            (31, "Дно сред (ст)"),
            (32, "Дно мин (мл)"),
            (33, "Дно мин (ст)"),
            # Результаты измерений толщины фланца (30034-30039)
            (34, "Толщина фланца макс (мл)"),
            (35, "Толщина фланца макс (ст)"),
            (36, "Толщина фланца сред (мл)"),
            (37, "Толщина фланца сред (ст)"),
            (38, "Толщина фланца мин (мл)"),
            (39, "Толщина фланца мин (ст)"),
            # Результаты измерений высоты (30040-30045)
            (40, "Высота макс (мл)"),
            (41, "Высота макс (ст)"),
            (42, "Высота сред (мл)"),
            (43, "Высота сред (ст)"),
            (44, "Высота мин (мл)"),
            (45, "Высота мин (ст)"),
            # Результаты измерений диаметра корпуса (30046-30051)
            (46, "Диаметр корпуса макс (мл)"),
            (47, "Диаметр корпуса макс (ст)"),
            (48, "Диаметр корпуса сред (мл)"),
            (49, "Диаметр корпуса сред (ст)"),
            (50, "Диаметр корпуса мин (мл)"),
            (51, "Диаметр корпуса мин (ст)"),
            # Результаты измерений диаметра фланца (30052-30057)
            (52, "Диаметр фланца макс (мл)"),
            (53, "Диаметр фланца макс (ст)"),
            (54, "Диаметр фланца сред (мл)"),
            (55, "Диаметр фланца сред (ст)"),
            (56, "Диаметр фланца мин (мл)"),
            (57, "Диаметр фланца мин (ст)"),
            # Счетчики изделий (30101-30104)
            (101, "Количество изделий за смену"),
            (102, "Годные изделия"),
            (103, "Условно-негодные изделия"),
            (104, "Негодные изделия"),
        ]
    
    def start_monitoring(self, interval: float = 1.0):
        """
        Запуск мониторинга изменений регистров
        
        Args:
            interval: Интервал проверки в секундах
        """
        if self.is_monitoring:
            return
        
        self.is_monitoring = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_registers,
            args=(interval,),
            daemon=True
        )
        self.monitor_thread.start()
        print(f"Мониторинг регистров запущен (интервал: {interval}с)")
    
    def stop_monitoring(self):
        """Остановка мониторинга"""
        self.is_monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join()
        print("Мониторинг регистров остановлен")
    
    def _monitor_registers(self, interval: float):
        """Мониторинг изменений регистров"""
        previous_values = {}
        
        while self.is_monitoring:
            try:
                # Мониторим DoubleWord FLOAT пары Holding Registers
                for address, description in self.holding_doubleword_pairs:
                    try:
                        addr_idx = address - 40000
                        values = self.modbus_server.slave_context.getValues(3, addr_idx, 2)
                        if values and len(values) == 2:
                            low_word = int(values[0])
                            high_word = int(values[1])
                            key = f"holding_dw_{address}"
                            
                            # Проверяем изменилось ли значение
                            current_pair = (low_word, high_word)
                            if key not in previous_values or previous_values[key] != current_pair:
                                # Конвертируем в float
                                import struct
                                combined = (high_word << 16) | low_word
                                packed = struct.pack('>I', combined)
                                float_value = struct.unpack('>f', packed)[0]
                                
                                # Сохраняем как DoubleWord
                                self.db.save_doubleword_register(
                                    address, 'holding', float_value, description
                                )
                                print(f"Сохранен Holding DoubleWord Float {address}-{address+1}: {float_value:.3f}мм")
                                previous_values[key] = current_pair
                    except Exception as e:
                        pass  # Игнорируем ошибки отдельных регистров
                
                # Мониторим DoubleWord INTEGER пары Holding Registers
                for address, description in self.holding_doubleword_int_pairs:
                    try:
                        addr_idx = address - 40000
                        values = self.modbus_server.slave_context.getValues(3, addr_idx, 2)
                        if values and len(values) == 2:
                            low_word = int(values[0])
                            high_word = int(values[1])
                            key = f"holding_dwi_{address}"
                            
                            # Проверяем изменилось ли значение
                            current_pair = (low_word, high_word)
                            if key not in previous_values or previous_values[key] != current_pair:
                                # Объединяем в 32-битное целое число
                                int_value = (high_word << 16) | low_word
                                
                                # Сохраняем как два регистра с описанием (используем специальный флаг)
                                self.db.save_single_register(
                                    address, 'holding', low_word, f"{description} (младший)"
                                )
                                self.db.save_single_register(
                                    address + 1, 'holding', high_word, f"{description} (старший)"
                                )
                                print(f"Сохранен Holding DoubleWord Integer {address}-{address+1}: {int_value}")
                                previous_values[key] = current_pair
                    except Exception as e:
                        pass  # Игнорируем ошибки отдельных регистров
                
                # Мониторим одиночные Holding Registers
                for addr, description in self.holding_registers_to_monitor:
                    try:
                        values = self.modbus_server.slave_context.getValues(3, addr, 1)
                        if values:
                            current_value = int(values[0])
                            key = f"holding_{addr}"
                            
                            if key not in previous_values or previous_values[key] != current_value:
                                # Значение изменилось, сохраняем в БД
                                self.db.save_single_register(
                                    40000 + addr, 'holding', current_value, description
                                )
                                print(f"Сохранен Holding Register {40000 + addr}: {current_value}")
                                previous_values[key] = current_value
                    except Exception as e:
                        pass  # Игнорируем ошибки отдельных регистров
                
                # Мониторим Input Registers
                for addr, description in self.input_registers_to_monitor:
                    try:
                        values = self.modbus_server.slave_context.getValues(4, addr, 1)
                        if values:
                            current_value = int(values[0])
                            key = f"input_{addr}"
                            
                            if key not in previous_values or previous_values[key] != current_value:
                                # Значение изменилось, сохраняем в БД
                                self.db.save_single_register(
                                    30000 + addr, 'input', current_value, description
                                )
                                print(f"Сохранен Input Register {30000 + addr}: {current_value}")
                                previous_values[key] = current_value
                    except Exception as e:
                        pass  # Игнорируем ошибки отдельных регистров
                
                time.sleep(interval)
                
            except Exception as e:
                print(f"Ошибка мониторинга: {e}")
                time.sleep(interval)
    
    def load_all_registers_from_db(self):
        """Загрузка всех регистров из базы данных в Modbus сервер"""
        print("Загрузка регистров из базы данных...")
        
        # Загружаем Holding Registers
        holding_registers = self.db.load_all_registers('holding')
        loaded_addresses = set()  # Отслеживаем загруженные адреса, чтобы не загружать дважды
        
        for reg in holding_registers:
            try:
                address = reg['address']
                if address in loaded_addresses:
                    continue  # Уже загружен как часть DoubleWord пары
                
                addr = address - 40000  # Конвертируем в индекс pymodbus
                value_low = reg['value_low']
                value_high = reg['value_high']
                
                # Проверяем, является ли это DoubleWord регистром по наличию float_value или is_float_display
                is_doubleword = reg.get('is_float_display', 0) == 1 or reg.get('float_value') is not None
                
                if is_doubleword and value_high is not None:
                    # DoubleWord регистр - загружаем оба слова
                    self.modbus_server.slave_context.setValues(3, addr, [value_low])
                    self.modbus_server.slave_context.setValues(3, addr + 1, [value_high])
                    loaded_addresses.add(address)
                    loaded_addresses.add(address + 1)
                    print(f"Загружен Holding DoubleWord {address}-{address+1}: low={value_low}, high={value_high}, float={reg.get('float_value', 'N/A')}")
                else:
                    # Обычный регистр
                    self.modbus_server.slave_context.setValues(3, addr, [value_low])
                    loaded_addresses.add(address)
                    print(f"Загружен Holding Register {address}: {value_low}")
            except Exception as e:
                print(f"Ошибка загрузки Holding Register {reg.get('address', '?')}: {e}")
        
        # Загружаем Input Registers
        input_registers = self.db.load_all_registers('input')
        loaded_addresses = set()
        
        for reg in input_registers:
            try:
                address = reg['address']
                if address in loaded_addresses:
                    continue
                
                addr = address - 30000  # Конвертируем в индекс pymodbus
                value_low = reg['value_low']
                value_high = reg['value_high']
                
                is_doubleword = reg.get('is_float_display', 0) == 1 or reg.get('float_value') is not None
                
                if is_doubleword and value_high is not None:
                    # DoubleWord регистр
                    self.modbus_server.slave_context.setValues(4, addr, [value_low])
                    self.modbus_server.slave_context.setValues(4, addr + 1, [value_high])
                    loaded_addresses.add(address)
                    loaded_addresses.add(address + 1)
                    print(f"Загружен Input DoubleWord {address}-{address+1}: low={value_low}, high={value_high}, float={reg.get('float_value', 'N/A')}")
                else:
                    # Обычный регистр
                    self.modbus_server.slave_context.setValues(4, addr, [value_low])
                    loaded_addresses.add(address)
                    print(f"Загружен Input Register {address}: {value_low}")
            except Exception as e:
                print(f"Ошибка загрузки Input Register {reg.get('address', '?')}: {e}")
        
        print("Загрузка регистров завершена")
    
    def save_doubleword_register(self, address: int, register_type: str, 
                                float_value: float, description: str = ""):
        """
        Сохранение DoubleWord регистра в базу данных
        
        Args:
            address: Адрес младшего регистра (40002, 40010, etc.)
            register_type: Тип регистра ('holding' или 'input')
            float_value: Float значение
            description: Описание регистра
        """
        self.db.save_doubleword_register(address, register_type, float_value, description)
        print(f"Сохранен DoubleWord регистр {address}-{address+1}: {float_value:.3f}")
    
    def save_single_register(self, address: int, register_type: str, 
                           value: int, description: str = ""):
        """
        Сохранение одиночного регистра в базу данных
        
        Args:
            address: Адрес регистра
            register_type: Тип регистра ('holding' или 'input')
            value: Значение регистра
            description: Описание регистра
        """
        self.db.save_single_register(address, register_type, value, description)
        print(f"Сохранен регистр {address}: {value}")
    
    def get_database_stats(self) -> Dict[str, Any]:
        """Получение статистики базы данных"""
        return self.db.get_database_stats()
    
    def backup_database(self, backup_path: str = None) -> str:
        """Создание резервной копии базы данных"""
        return self.db.backup_database(backup_path)
    
    def restore_database(self, backup_path: str):
        """Восстановление базы данных из резервной копии"""
        self.db.restore_database(backup_path)
        # Перезагружаем регистры после восстановления
        self.load_all_registers_from_db()
    
    def clear_database(self):
        """Очистка базы данных"""
        self.db.clear_all_registers()
        print("База данных очищена")
    
    def add_register_to_monitor(self, address: int, register_type: str, description: str):
        """
        Добавление регистра для мониторинга
        
        Args:
            address: Адрес регистра
            register_type: Тип регистра ('holding' или 'input')
            description: Описание регистра
        """
        if register_type == 'holding':
            self.holding_registers_to_monitor.append((address - 40000, description))
        elif register_type == 'input':
            self.input_registers_to_monitor.append((address - 30000, description))
        
        print(f"Добавлен для мониторинга: {address} ({register_type})")


def main():
    """Тестирование интеграции"""
    print("Тестирование интеграции Modbus Database")
    
    # Создаем тестовый Modbus сервер
    from modbus_slave_server import ModbusSlaveServer
    
    modbus_server = ModbusSlaveServer(enable_gui=False)
    modbus_server.start_modbus_server()
    
    # Создаем интеграцию
    integration = ModbusDatabaseIntegration(modbus_server, "test_integration.db")
    
    # Загружаем регистры из БД
    integration.load_all_registers_from_db()
    
    # Запускаем мониторинг
    integration.start_monitoring(0.5)
    
    # Тестируем сохранение
    print("\nТестирование сохранения...")
    integration.save_doubleword_register(40002, 'holding', 5.0, 'Тестовая толщина')
    integration.save_single_register(40001, 'holding', 100, 'Тестовая команда')
    
    # Ждем немного
    time.sleep(2)
    
    # Статистика
    stats = integration.get_database_stats()
    print(f"\nСтатистика: {stats}")
    
    # Останавливаем мониторинг
    integration.stop_monitoring()
    
    # Останавливаем сервер
    modbus_server.stop_modbus_server()
    
    print("Тестирование завершено!")


if __name__ == "__main__":
    main()
