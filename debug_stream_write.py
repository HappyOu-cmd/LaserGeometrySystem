#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Диагностика записи в потоковом режиме
"""

from laser_geometry_system import LaserGeometrySystem
import time

def debug_stream_write():
    print("Диагностика записи в потоковом режиме...")
    
    # Создаем систему без датчиков (тестовый режим)
    system = LaserGeometrySystem(port="COM3", enable_debug_gui=False)
    
    try:
        print("\n1. Запускаем систему...")
        system.start_system()
        time.sleep(1)
        
        print("\n2. Тестируем метод write_stream_result_to_input_registers...")
        
        # Тестовые значения
        test_values = [16.816, 6.816, 10.000, 25.000]
        
        for test_val in test_values:
            print(f"\nТестируем значение: {test_val:.3f}")
            
            # Вызываем метод записи
            system.write_stream_result_to_input_registers(test_val, 30001)
            
            # Читаем что записалось
            if system.modbus_server and system.modbus_server.slave_context:
                values = system.modbus_server.slave_context.getValues(4, 0, 2)  # 30001-30002
                print(f"   Записано в регистры: {values}")
                print(f"   30001: {values[0]}")
                print(f"   30002: {values[1]}")
                
                # Восстанавливаем float
                if len(values) >= 2:
                    restored = system.doubleword_to_float(values[1], values[0])
                    print(f"   Восстановленный float: {restored:.3f}")
                    print(f"   Разница: {abs(test_val - restored):.6f}")
        
        print("\n3. Проверяем метод float_to_doubleword в системе...")
        
        test_val = 16.816
        low_word, high_word = system.float_to_doubleword(test_val)
        print(f"Система: {test_val:.3f} -> low: {low_word}, high: {high_word}")
        
        # Сравниваем с методом из БД
        if system.db_integration:
            db_low, db_high = system.db_integration.db.float_to_doubleword(test_val)
            print(f"БД:      {test_val:.3f} -> low: {db_low}, high: {db_high}")
            
            if low_word != db_low or high_word != db_high:
                print("   ПРОБЛЕМА: Разные результаты в системе и БД!")
            else:
                print("   OK: Результаты совпадают")
        
        print("\n4. Проверяем прямую запись проблемного значения...")
        
        # Если проблема в том, что пишется 6816 вместо 16816
        # Попробуем записать 16816 напрямую
        direct_value = 16816
        print(f"Записываем напрямую: {direct_value}")
        
        system.modbus_server.slave_context.setValues(4, 0, [direct_value])
        read_back = system.modbus_server.slave_context.getValues(4, 0, 1)
        print(f"Прочитано обратно: {read_back[0]}")
        
        if read_back[0] != direct_value:
            print(f"ПРОБЛЕМА: Записали {direct_value}, прочитали {read_back[0]}")
            print(f"Разность: {direct_value - read_back[0]}")
        else:
            print("OK: Прямая запись работает корректно")
        
        print("\nДиагностика завершена!")
        
    except Exception as e:
        print(f"Ошибка диагностики: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\nОстанавливаем систему...")
        system.stop_system()

if __name__ == "__main__":
    debug_stream_write()
