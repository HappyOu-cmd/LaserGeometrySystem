#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Диагностика записи в Input регистры
"""

from modbus_slave_server import ModbusSlaveServer
from modbus_database_integration import ModbusDatabaseIntegration
import struct

def debug_register_write():
    print("Диагностика записи в Input регистры...")
    
    # Создаем Modbus сервер
    modbus_server = ModbusSlaveServer(enable_gui=False)
    modbus_server.start_modbus_server()
    
    # Создаем интеграцию с базой данных
    db_integration = ModbusDatabaseIntegration(modbus_server)
    modbus_server.db_integration = db_integration
    
    try:
        print("\n1. Тестируем функцию float_to_doubleword...")
        
        # Тестовые значения
        test_values = [0.0, 1.234, 12.345, 123.456, 16.816]
        
        for test_val in test_values:
            # Используем метод из базы данных
            low_word, high_word = db_integration.db.float_to_doubleword(test_val)
            
            print(f"   {test_val:.3f} -> low_word: {low_word}, high_word: {high_word}")
            print(f"      low_word в hex: 0x{low_word:04X}, high_word в hex: 0x{high_word:04X}")
            
            # Проверяем диапазон
            if low_word > 65535 or high_word > 65535:
                print(f"      ПРОБЛЕМА: Значения превышают 16-битный диапазон!")
            
            if low_word < 0 or high_word < 0:
                print(f"      ПРОБЛЕМА: Отрицательные значения!")
            
            # Конвертируем обратно
            restored = db_integration.db.doubleword_to_float(low_word, high_word)
            print(f"      Восстановлено: {restored:.3f}, разница: {abs(test_val - restored):.6f}")
            print()
        
        print("\n2. Тестируем запись в регистры...")
        
        test_value = 16.816
        low_word, high_word = db_integration.db.float_to_doubleword(test_value)
        
        print(f"Записываем {test_value:.3f}:")
        print(f"   low_word: {low_word} (0x{low_word:04X})")
        print(f"   high_word: {high_word} (0x{high_word:04X})")
        
        # Записываем как в системе
        modbus_server.slave_context.setValues(4, 0, [int(high_word)])  # 30001
        modbus_server.slave_context.setValues(4, 1, [int(low_word)])   # 30002
        
        # Читаем обратно
        read_values = modbus_server.slave_context.getValues(4, 0, 2)
        print(f"   Прочитано из регистров: {read_values}")
        print(f"   30001: {read_values[0]} (ожидалось: {int(high_word)})")
        print(f"   30002: {read_values[1]} (ожидалось: {int(low_word)})")
        
        # Восстанавливаем float
        if len(read_values) >= 2:
            restored_float = db_integration.db.doubleword_to_float(read_values[1], read_values[0])
            print(f"   Восстановленный float: {restored_float:.3f}")
            print(f"   Разница: {abs(test_value - restored_float):.6f}")
        
        print("\n3. Проверяем проблему с 6816 вместо 16816...")
        
        # Если в регистре 6816 вместо 16816, возможно проблема в усечении
        problem_value = 6816
        expected_value = 16816
        
        print(f"Проблемное значение: {problem_value}")
        print(f"Ожидаемое значение: {expected_value}")
        print(f"Разность: {expected_value - problem_value} = {expected_value - problem_value}")
        print(f"В hex: проблемное 0x{problem_value:04X}, ожидаемое 0x{expected_value:04X}")
        
        # Проверяем битовые операции
        print(f"Проблемное в бинарном: {bin(problem_value)}")
        print(f"Ожидаемое в бинарном: {bin(expected_value)}")
        
        # Проверяем, не происходит ли усечение до 16 бит
        if expected_value > 65535:
            truncated = expected_value & 0xFFFF
            print(f"Усечение до 16 бит: {truncated}")
        
        print("\nДиагностика завершена!")
        
    except Exception as e:
        print(f"Ошибка диагностики: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Остановка
        db_integration.stop_monitoring()
        modbus_server.stop_modbus_server()

if __name__ == "__main__":
    debug_register_write()
