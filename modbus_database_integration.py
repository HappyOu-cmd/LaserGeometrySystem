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
        # Сохраняем калибровочные величины:
        self.holding_doubleword_pairs = [
            (40002, "Эталонная толщина стенки"),           # 1) Толщина эталона
            (40004, "Эталонная толщина дна"),               # 2) Толщина дна для калибровки
            (40006, "Эталонный диаметр корпуса"),          # 3) Диаметр эталона корпуса
            (40010, "Расстояние датчики 1,2"),             # 4) Расстояние 1-2
            (40012, "Расстояние датчики 1,3"),             # 5) Расстояние 1-3
            (40014, "Расстояние датчик 4"),                # 6) Расстояние-4 поверхность
            (40016, "Расстояние датчик 1-центр"),         # 7) Расстояние 1 - до центра
            (40030, "Эталонный диаметр фланца"),           # 8) Диаметр эталона фланца
            (40032, "Расстояние датчик 3 - центр"),        # 9) Расстояние 3 - до центра
            (40500, "Коэффициент смещения толщины верхней стенки"),
            (40502, "Коэффициент смещения толщины нижней стенки"),
            (40504, "Коэффициент смещения диаметра корпуса"),
            (40506, "Коэффициент смещения диаметра фланца"),
            (40508, "Коэффициент смещения толщины дна"),
            (40511, "Коэффициент экстраполяции толщины верхней стенки"),
            (40513, "Коэффициент экстраполяции толщины нижней стенки"),
            (40515, "Коэффициент экстраполяции диаметра корпуса"),
            (40517, "Коэффициент экстраполяции диаметра фланца"),
            (40519, "Коэффициент экстраполяции толщины дна"),
            (40404, "Начало диапазона для дискретного сигнала"),
            (40406, "Конец диапазона для дискретного сигнала"),
        ]
        
        # Отдельно фиксируем регистры экстраполяции, т.к. их адреса в словаре offset на 1 меньше реального
        self.extrapolation_registers = {
            40511, 40512, 40513, 40514, 40515, 40516, 40517, 40518, 40519, 40520
        }
        
        # DoubleWord пары INTEGER (большие числа, не float)
        # Отключено: сохраняем только указанные величины
        self.holding_doubleword_int_pairs = []
        
        # Одиночные регистры для мониторинга
        # Сохраняем только: номер смены (40100) и номер изделия (40101)
        self.holding_registers_to_monitor = [
            (100, "Номер смены"),      # 40100
            (101, "Номер изделия"),    # 40101
        ]
        
        self.input_registers_to_monitor = [
            # Счетчики изделий (30101-30104)
            # Сохраняем только: количество изделий за смену (30101), условно-негодные (30103), негодные (30104)
            (101, "Количество изделий за смену"),    # 30101
            (103, "Условно-негодные изделия"),        # 30103
            (104, "Негодные изделия"),                # 30104
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
                        # Для регистров экстраполяции используем addr_idx = address - 40001 (ModbusSequentialDataBlock начинается с 1)
                        if address in self.extrapolation_registers:
                            addr_idx = address - 40001
                        else:
                            addr_idx = address - 40000
                        values = self.modbus_server.slave_context.getValues(3, addr_idx, 2)
                        if values and len(values) == 2:
                            # ВАЖНО: При записи в регистры используется порядок:
                            # setValues(3, addr_idx, [high_word]) - регистр address (40010) содержит high_word
                            # setValues(3, addr_idx+1, [low_word]) - регистр address+1 (40011) содержит low_word
                            # Поэтому при чтении:
                            high_word = int(values[0])  # values[0] - это значение из регистра addr_idx (address)
                            low_word = int(values[1])   # values[1] - это значение из регистра addr_idx+1 (address+1)
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
                
                # Исключаем регистры 40057-40060 из загрузки (записывается только ПЛК/HMI)
                if address == 40057 or address == 40058 or address == 40059 or address == 40060:
                    continue  # Пропускаем регистр измеренной высоты - он записывается только ПЛК
                
                # Проверяем, является ли это DoubleWord регистром по наличию float_value или is_float_display
                is_doubleword = reg.get('is_float_display', 0) == 1 or reg.get('float_value') is not None
                
                if is_doubleword and reg.get('value_high') is not None:
                    # DoubleWord регистр - загружаем оба слова
                    # Проверяем, является ли это первым регистром пары (address, а не address+1)
                    # Для этого проверяем, есть ли address в списке holding_doubleword_pairs
                    is_first_register = any(pair[0] == address for pair in self.holding_doubleword_pairs)
                    
                    if not is_first_register:
                        # Это второй регистр пары (address+1), пропускаем - он будет загружен вместе с первым
                        continue
                    
                    if address in self.extrapolation_registers:
                        addr = address - 40001  # Для регистров экстраполяции
                    else:
                        addr = address - 40000  # Для остальных регистров
                    value_low = reg['value_low']
                    value_high = reg['value_high']
                    
                    # ВАЖНО: При записи результатов калибровки используется порядок:
                    # setValues(3, addr_idx, [high_word]) - регистр address (40010) содержит high_word
                    # setValues(3, addr_idx+1, [low_word]) - регистр address+1 (40011) содержит low_word
                    # Поэтому при загрузке из БД:
                    # value_high из БД должен быть записан в регистр address (addr_idx)
                    # value_low из БД должен быть записан в регистр address+1 (addr_idx+1)
                    self.modbus_server.slave_context.setValues(3, addr, [value_high])  # Регистр address содержит high_word
                    self.modbus_server.slave_context.setValues(3, addr + 1, [value_low])  # Регистр address+1 содержит low_word
                    loaded_addresses.add(address)
                    loaded_addresses.add(address + 1)
                    print(f"Загружен Holding DoubleWord {address}-{address+1}: high={value_high}, low={value_low}, float={reg.get('float_value', 'N/A')}")
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
