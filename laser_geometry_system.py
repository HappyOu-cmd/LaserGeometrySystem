#!/usr/bin/env python3
"""
 ОСНОВНАЯ СИСТЕМА ЛАЗЕРНОЙ ГЕОМЕТРИИ
Интеграция датчиков РФ602, Modbus сервера и автомата состояний
"""

import time
import threading
import struct
import os
import ctypes
from enum import Enum
from typing import Optional, Tuple, List
from collections import deque

# Проверяем доступность psutil
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# Импорты из существующих модулей
from main import HighSpeedRiftekSensor, apply_system_optimizations
from modbus_slave_server import ModbusSlaveServer
from modbus_debug_gui import ModbusDebugGUI
from modbus_database_integration import ModbusDatabaseIntegration


def apply_laser_system_optimizations():
    """
    Применение системных оптимизаций для лазерной системы (без pyftdi)
    """
    print("[SYSTEM] ПРИМЕНЕНИЕ СИСТЕМНЫХ ОПТИМИЗАЦИЙ...")
    
    # 1. Высокий приоритет процесса
    if HAS_PSUTIL:
        try:
            p = psutil.Process(os.getpid())
            p.nice(psutil.HIGH_PRIORITY_CLASS)
            print("[OK] Установлен высокий приоритет процесса")
        except Exception as e:
            print(f"[WARNING] Не удалось установить приоритет: {e}")
    else:
        print("[WARNING] psutil не установлен - приоритет не изменен")
    
    # 2. Высокое разрешение таймера Windows
    print("[TIMER] Настройка точности таймера Windows...")
    try:
        # Устанавливаем минимальное разрешение таймера (1 мс)
        result = ctypes.windll.winmm.timeBeginPeriod(1)
        if result == 0:  # TIMERR_NOERROR
            print("[OK] Установлено высокое разрешение таймера (1 мс)")
        else:
            print(f"[WARNING] Ошибка установки таймера: {result}")
    except Exception as e:
        print(f"[WARNING] Не удалось настроить таймер: {e}")
    
    # 3. Отключение спящего режима
    try:
        # Отключаем спящий режим для текущего процесса
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        print("[OK] Отключен спящий режим системы")
    except Exception as e:
        print(f"[WARNING] Не удалось отключить спящий режим: {e}")
    
    print("[SYSTEM] Системные оптимизации применены\n")


def cleanup_laser_system_optimizations():
    """
    Очистка системных оптимизаций при завершении
    """
    try:
        # Восстанавливаем обычное разрешение таймера
        ctypes.windll.winmm.timeEndPeriod(1)
        print("🔧 Восстановлено стандартное разрешение таймера")
    except:
        pass
    
    try:
        # Восстанавливаем обычное управление питанием
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)  # ES_CONTINUOUS
        print("🔧 Восстановлено управление питанием")
    except:
        pass


class SystemState(Enum):
    """Состояния системы"""
    IDLE = "IDLE"
    
    # Калибровки
    CALIBRATE_WALL = "CALIBRATE_WALL"
    CALIBRATE_BOTTOM = "CALIBRATE_BOTTOM"
    CALIBRATE_FLANGE = "CALIBRATE_FLANGE"
    CALIBRATE_HEIGHT = "CALIBRATE_HEIGHT"
    DEBUG_REGISTERS = "DEBUG_REGISTERS"
    
    # Измерение высоты
    MEASURE_HEIGHT_PROCESS = "MEASURE_HEIGHT_PROCESS"      # CMD=9: поиск препятствия и сбор данных
    
    # Основной цикл измерения - верхняя стенка
    MEASURE_WALL_PROCESS = "MEASURE_WALL_PROCESS"      # CMD=10: сбор данных
    MEASURE_WALL_CALCULATE = "MEASURE_WALL_CALCULATE"  # CMD=11: подсчёт результатов
    
    # Основной цикл измерения - фланец
    MEASURE_FLANGE_PROCESS = "MEASURE_FLANGE_PROCESS"      # CMD=12: сбор данных
    MEASURE_FLANGE_CALCULATE = "MEASURE_FLANGE_CALCULATE"  # CMD=13: подсчёт результатов
    
    # Основной цикл измерения - нижняя стенка
    MEASURE_BOTTOM_PROCESS = "MEASURE_BOTTOM_PROCESS"      # CMD=14: сбор данных
    MEASURE_BOTTOM_CALCULATE = "MEASURE_BOTTOM_CALCULATE"  # CMD=15: подсчёт результатов
    
    # Оценка качества изделия
    QUALITY_EVALUATION = "QUALITY_EVALUATION"  # CMD=16: оценка качества
    
    # Потоковые режимы
    STREAM_SENSOR1 = "STREAM_SENSOR1"
    STREAM_SENSOR2 = "STREAM_SENSOR2"
    STREAM_SENSOR3 = "STREAM_SENSOR3"
    STREAM_SENSOR4 = "STREAM_SENSOR4"
    
    ERROR = "ERROR"


class LaserGeometrySystem:
    """Основная система лазерной геометрии"""
    
    def __init__(self, port: str = 'COM11', baudrate: int = 921600, modbus_port: int = 502, 
                 test_mode: bool = False, enable_debug_gui: bool = False):
        """
        Инициализация системы
        
        Args:
            port: COM порт для датчиков
            baudrate: Скорость передачи данных
            modbus_port: Порт Modbus сервера
            test_mode: Режим тестирования без реальных датчиков
            enable_debug_gui: Включить GUI для отладки Modbus
        """
        # Настройки датчиков
        self.port = port
        self.baudrate = baudrate
        self.test_mode = test_mode
        self.enable_debug_gui = enable_debug_gui
        
        # Компоненты системы
        self.sensors = None
        self.modbus_server = None
        self.debug_gui = None
        self.db_integration = None
        
        # Состояние системы
        self.current_state = SystemState.IDLE
        self.previous_cmd = 0
        
        # Данные калибровки
        self.calibration_data = {
            'wall_distance_1_2': 0.0,  # Расстояние между датчиками 1,2
            'wall_distance_1_3': 0.0,  # Расстояние между датчиками 1,3
            'bottom_distance_4': 0.0,  # Расстояние датчик 4 до поверхности
            'flange_distance_1_center': 0.0,  # Расстояние датчик 1 до центра
        }
        
        # Буферы для усреднения
        self.measurement_buffer = {
            'sensor1': deque(maxlen=1000),
            'sensor2': deque(maxlen=1000),
            'sensor3': deque(maxlen=1000),
            'sensor4': deque(maxlen=1000),
        }
        
        
        # Параметры измерений
        self.measurement_duration = 4.0  # секунд для калибровки
        self.sensor_range_mm = 25.0      # диапазон датчиков
        self.base_distance_mm = 20.0     # базовое расстояние
        
        # Флаги
        self.is_running = False
        self.calibration_in_progress = False
        self.stream_active_sensor1 = False
        self.stream_active_sensor2 = False
        self.stream_active_sensor3 = False
        self.stream_active_sensor4 = False
        self.height_calibration_nonzero_count = 0  # Счетчик ненулевых показаний для CMD=103
        self.distance_to_plane_calculated = False  # Флаг завершения расчёта дистанции (CMD=103)
        self.recent_measurements = []  # Буфер последних измерений для CMD=103
        
        # Автоматическое переподключение датчиков
        self.last_reconnect_attempt = 0  # Время последней попытки переподключения
        self.reconnect_interval = 5.0  # Интервал попыток переподключения (секунды)
        
        # Счетчики для потокового режима
        self.stream_measurement_count = 0
        self.stream_start_time = None
        self.stream_measurements_buffer = []  # Буфер измерений за секунду
        
        # Буферы для основного цикла измерения
        self.sensor1_measurements = []  # Буфер усредненных измерений датчика 1
        self.sensor2_measurements = []  # Буфер усредненных измерений датчика 2
        self.wall_thickness_buffer = []  # Буфер рассчитанной толщины стенки
        self.measurement_cycle_active = False  # Флаг активного цикла измерения
        
        # Флаги выполнения расчётов (чтобы не выполнять многократно)
        self.wall_calculated = False
        self.flange_calculated = False
        self.bottom_calculated = False
        self.quality_evaluated = False
        
        # Мониторинг в состоянии ожидания (IDLE)
        self.idle_monitor_last_time = 0.0

        # Номер смены (для сброса счётчиков при смене)
        self.last_shift_number = None
        
        # Кеш калиброванных расстояний (для ускорения циклов измерения)
        self.cached_distance_1_2 = None
        self.cached_distance_to_center = None
        self.cached_distance_1_3 = None
        self.cached_distance_sensor4 = None
        
        # Отслеживание смены для сброса счётчиков
        self.current_shift_number = 1  # Текущая смена
        
        # Мониторинг частоты опроса
        self.frequency_counter = 0
        self.frequency_start_time = None
        self.last_frequency_display = 0
        
        # Буферы для измерения высоты (команда 9)
        self.height_measurements = []  # Буфер измерений высоты
        self.obstacle_detected = False  # Флаг обнаружения препятствия
        self.obstacle_filter_count = 0  # Счетчик для фильтрации препятствий
        self.height_calculated = False  # Флаг завершения расчета высоты
        
        # Временные буферы для усреднения (команда 10)
        self.temp_sensor1_buffer = []  # Временный буфер для 10 измерений датчика 1
        self.temp_sensor2_buffer = []  # Временный буфер для 10 измерений датчика 2
        
        # Буферы для команды 11 (измерение фланца)
        self.sensor1_flange_measurements = []  # Буфер усредненных измерений датчика 1 для команды 11
        self.sensor3_measurements = []  # Буфер усредненных измерений датчика 3
        self.sensor4_measurements = []  # Буфер усредненных измерений датчика 4
        
        # Временные буферы для усреднения (команда 11)
        self.temp_sensor1_flange_buffer = []  # Временный буфер для 10 измерений датчика 1 (команда 11)
        self.temp_sensor3_buffer = []  # Временный буфер для 10 измерений датчика 3
        self.temp_sensor4_buffer = []  # Временный буфер для 10 измерений датчика 4
        
        # Расчетные буферы для команды 11
        self.body_diameter_buffer = []    # Буфер диаметра корпуса (датчик 1)
        self.flange_diameter_buffer = []  # Буфер диаметра фланца (датчик 3)
        self.flange_thickness_buffer = [] # Буфер толщины фланца (датчики 1,3)
        self.bottom_thickness_buffer = [] # Буфер толщины дна (датчик 4)
        
        # Буферы для команды 12 (измерение нижней стенки)
        self.sensor1_bottom_measurements = []  # Буфер усредненных измерений датчика 1 для команды 12
        self.sensor2_bottom_measurements = []  # Буфер усредненных измерений датчика 2 для команды 12
        
        # Временные буферы для усреднения (команда 12)
        self.temp_sensor1_bottom_buffer = []  # Временный буфер для 10 измерений датчика 1 (команда 12)
        self.temp_sensor2_bottom_buffer = []  # Временный буфер для 10 измерений датчика 2 (команда 12)
        
        # Расчетный буфер для команды 12
        self.bottom_wall_thickness_buffer = []  # Буфер толщины нижней стенки
        
    def start_system(self):
        """Запуск системы"""
        print("ЗАПУСК СИСТЕМЫ ЛАЗЕРНОЙ ГЕОМЕТРИИ")
        print("=" * 50)
        
        # Применяем системные оптимизации для лазерной системы
        apply_laser_system_optimizations()
        
        # Инициализация датчиков
        sensors_connected = False
        if self.test_mode:
            print("ТЕСТОВЫЙ РЕЖИМ - датчики не подключены")
            self.sensors = None
        else:
            print(f"Подключение к датчикам на порту {self.port}...")
            self.sensors = HighSpeedRiftekSensor(self.port, self.baudrate, timeout=0.002)
            
            if not self.sensors.connect():
                print(" ВНИМАНИЕ: Ошибка подключения к датчикам!")
                print(" Программа продолжит работу, но измерения будут недоступны.")
                self.sensors = None  # Сбрасываем указатель на датчики
                sensors_connected = False
            else:
                print("OK Датчики подключены")
                sensors_connected = True
        
        # Инициализация Modbus сервера (без GUI, так как у нас есть Debug GUI)
        self.modbus_server = ModbusSlaveServer(enable_gui=False)
        
        # Запускаем Modbus сервер без GUI
        try:
            self.modbus_server.start_modbus_server()
            print("OK Modbus сервер запущен")
        except Exception as e:
            print(f"Ошибка запуска Modbus сервера: {e}")
            return False
        
        # Теперь можем установить бит ошибки после инициализации Modbus сервера
        if not sensors_connected:
            self.set_error_bit(0, True)  # Устанавливаем бит 0 - ошибка подключения датчиков
        else:
            self.set_error_bit(0, False)  # Сбрасываем бит ошибки если подключение успешно

        # Инициализируем номер смены для детектора смены
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # 40100 -> индекс 99 в Holding регистрах
                current_shift = self.modbus_server.slave_context.getValues(3, 99, 1)[0]
                self.last_shift_number = int(current_shift)
                print(f" [SHIFT] Текущая смена: {self.last_shift_number}")
        except Exception as e:
            print(f" [SHIFT] Ошибка чтения текущей смены при старте: {e}")
        
        # Инициализация интеграции с базой данных
        self.db_integration = ModbusDatabaseIntegration(self.modbus_server)
        
        # Загружаем сохраненные регистры из базы данных
        self.db_integration.load_all_registers_from_db()
        
        # Запускаем мониторинг изменений регистров
        self.db_integration.start_monitoring(interval=1.0)
        print("OK Интеграция с базой данных запущена")
        
        # Инициализация Debug GUI (если включен)
        if self.enable_debug_gui:
            # Передаем Modbus сервер с уже настроенной интеграцией БД
            self.modbus_server.db_integration = self.db_integration
            self.debug_gui = ModbusDebugGUI(self.modbus_server)
            print("OK Debug GUI инициализирован с интеграцией БД")
        
        # Запуск основного цикла в отдельном потоке
        self.is_running = True
        main_thread = threading.Thread(target=self.main_loop, daemon=True)
        main_thread.start()
        print("OK Основной цикл запущен")
        
        # Запуск Debug GUI в главном потоке (если включен)
        if self.enable_debug_gui and self.debug_gui:
            print("OK Debug GUI запущен")
            self.debug_gui.start()  # Блокирующий вызов в главном потоке
        else:
            # Если GUI не нужен, просто ждем завершения основного потока
            main_thread.join()
        
        return True
    
    def stop_system(self):
        """Остановка системы"""
        print("\n ОСТАНОВКА СИСТЕМЫ")
        self.is_running = False
        
        # Остановка всех потоковых режимов
        if self.sensors and self.stream_active_sensor1:
            try:
                self.sensors.stop_stream_mode(1)
                self.stream_active_sensor1 = False
                print(" Остановлен потоковый режим датчика 1")
            except Exception as e:
                print(f" Ошибка остановки потокового режима датчика 1: {e}")
        
        if self.sensors and self.stream_active_sensor2:
            try:
                self.sensors.stop_stream_mode(2)
                self.stream_active_sensor2 = False
                print(" Остановлен потоковый режим датчика 2")
            except Exception as e:
                print(f" Ошибка остановки потокового режима датчика 2: {e}")
        
        if self.sensors and self.stream_active_sensor3:
            try:
                self.sensors.stop_stream_mode(3)
                self.stream_active_sensor3 = False
                print(" Остановлен потоковый режим датчика 3")
            except Exception as e:
                print(f" Ошибка остановки потокового режима датчика 3: {e}")
        
        if self.sensors and self.stream_active_sensor4:
            try:
                self.sensors.stop_stream_mode(4)
                self.stream_active_sensor4 = False
                print(" Остановлен потоковый режим датчика 4")
            except Exception as e:
                print(f" Ошибка остановки потокового режима датчика 4: {e}")
        
        if self.sensors:
            try:
                self.sensors.disconnect()
                print(" Датчики отключены")
            except Exception as e:
                print(f" Ошибка отключения датчиков: {e}")
            finally:
                self.sensors = None
            
        if self.db_integration:
            try:
                self.db_integration.stop_monitoring()
            except Exception as e:
                print(f"Ошибка остановки мониторинга БД: {e}")
            
        if self.modbus_server:
            try:
                self.modbus_server.stop_modbus_server()
            except Exception as e:
                print(f"Ошибка остановки Modbus сервера: {e}")
            
        if self.debug_gui:
            self.debug_gui.close_gui()
        
        # Очищаем системные оптимизации
        cleanup_laser_system_optimizations()
            
        print(" Система остановлена")
    
    def main_loop(self):
        """Основной цикл системы"""
        print(" Запуск основного цикла...")
        
        try:
            while self.is_running:
                # ПРОВЕРКА СБРОСА ОШИБОК (регистр 40024)
                try:
                    reset_flag = self.modbus_server.slave_context.getValues(3, 23, 1)[0]  # 40024 -> index 23
                    if reset_flag == 1:
                        print(" [RESET] Получен запрос на сброс ошибок (40024=1)")
                        # Сбрасываем статус в 0
                        self.modbus_server.slave_context.setValues(4, 8, [0])  # 30009 -> index 8
                        # Сбрасываем флаг сброса обратно в 0
                        self.modbus_server.slave_context.setValues(3, 23, [0])  # 40024 -> index 23
                        print(" [RESET] Статус 30009 сброшен в 0, флаг 40024 сброшен")
                except Exception as e:
                    print(f" Ошибка проверки регистра сброса 40024: {e}")
                
                # АВТОМАТИЧЕСКОЕ ПЕРЕПОДКЛЮЧЕНИЕ ДАТЧИКОВ (если не подключены)
                if not self.test_mode:
                    self.check_and_reconnect_sensors()
                
                # Проверяем команду от Modbus
                current_cmd = self.get_current_command()
                
                if current_cmd != self.previous_cmd:
                    print(f"📨 Получена команда: {current_cmd}")
                    self.handle_command(current_cmd)
                    self.previous_cmd = current_cmd

                # Детектор смены: при изменении 40100 сбрасываем счётчики 30101-30104
                try:
                    if self.modbus_server and self.modbus_server.slave_context:
                        current_shift = int(self.modbus_server.slave_context.getValues(3, 99, 1)[0])  # 40100
                        if self.last_shift_number is None:
                            self.last_shift_number = current_shift
                        elif current_shift != self.last_shift_number:
                            print(f" [SHIFT] Смена изменилась: {self.last_shift_number} → {current_shift}. Сбрасываем счётчики за смену...")
                            # 30101..30104 → индексы 100..103 в Input регистрах
                            self.modbus_server.slave_context.setValues(4, 100, [0])  # всего
                            self.modbus_server.slave_context.setValues(4, 101, [0])  # годных
                            self.modbus_server.slave_context.setValues(4, 102, [0])  # условно-негодных
                            self.modbus_server.slave_context.setValues(4, 103, [0])  # негодных
                            self.last_shift_number = current_shift
                except Exception as e:
                    print(f" [SHIFT] Ошибка проверки/сброса счётчиков при смене: {e}")
                
                # Выполняем действия в зависимости от состояния
                self.execute_state_actions()
                
                # Пауза только если НЕ потоковый режим (иначе тормозит поток!)
                if self.current_state not in [SystemState.STREAM_SENSOR1, SystemState.STREAM_SENSOR2, 
                                             SystemState.STREAM_SENSOR3, SystemState.STREAM_SENSOR4, 
                                             SystemState.MEASURE_HEIGHT_PROCESS, SystemState.MEASURE_WALL_PROCESS, 
                                             SystemState.MEASURE_FLANGE_PROCESS, SystemState.MEASURE_BOTTOM_PROCESS,
                                             SystemState.CALIBRATE_HEIGHT]:
                    time.sleep(0.1)
                
        except KeyboardInterrupt:
            print("\n Остановка по запросу пользователя")
        except Exception as e:
            print(f" Ошибка в основном цикле: {e}")
            self.current_state = SystemState.ERROR
        finally:
            self.stop_system()
    
    def get_current_command(self) -> int:
        """Получение текущей команды из Modbus регистра 40001"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                values = self.modbus_server.slave_context.getValues(3, 1, 1)  # Holding Register 40001
                if values:
                    return int(values[0])
        except Exception as e:
            print(f" Ошибка чтения команды: {e}")
        return 0
    
    def handle_command(self, cmd: int):
        """Обработка команды и переход в соответствующее состояние"""
        # Останавливаем все активные потоки при смене команды
        self.stop_all_streams()
        
        # Управление флагом цикла измерения (регистр 30009)
        self.manage_measurement_cycle_flag(cmd)
        
        if cmd == 0:
            self.current_state = SystemState.IDLE
            
        # Калибровки
        elif cmd == 100:
            self.current_state = SystemState.CALIBRATE_WALL
        elif cmd == 101:
            self.current_state = SystemState.CALIBRATE_BOTTOM
        elif cmd == 102:
            self.current_state = SystemState.CALIBRATE_FLANGE
        elif cmd == 103:
            self.current_state = SystemState.CALIBRATE_HEIGHT
        elif cmd == 104:
            self.current_state = SystemState.DEBUG_REGISTERS
            
        # Измерение верхней стенки
        elif cmd == 10:
            self.current_state = SystemState.MEASURE_WALL_PROCESS
            
        # Потоковые режимы
        elif cmd == 200:
            self.current_state = SystemState.STREAM_SENSOR1
        elif cmd == 201:
            self.current_state = SystemState.STREAM_SENSOR2
        elif cmd == 202:
            self.current_state = SystemState.STREAM_SENSOR3
        elif cmd == 203:
            self.current_state = SystemState.STREAM_SENSOR4
            
        # Основной цикл измерения - подсчёт верхней стенки
        elif cmd == 11:
            self.current_state = SystemState.MEASURE_WALL_CALCULATE
            
        # Основной цикл измерения - фланец
        elif cmd == 12:
            self.current_state = SystemState.MEASURE_FLANGE_PROCESS
        elif cmd == 13:
            self.current_state = SystemState.MEASURE_FLANGE_CALCULATE
            
        # Основной цикл измерения - нижняя стенка
        elif cmd == 14:
            self.current_state = SystemState.MEASURE_BOTTOM_PROCESS
        elif cmd == 15:
            self.current_state = SystemState.MEASURE_BOTTOM_CALCULATE
            
        # Оценка качества изделия
        elif cmd == 16:
            self.current_state = SystemState.QUALITY_EVALUATION
            
        else:
            print(f" Неизвестная команда: {cmd}")
            self.current_state = SystemState.ERROR
        
        print(f" Переход в состояние: {self.current_state.value}")
    
    def manage_measurement_cycle_flag(self, new_cmd: int):
        """
        Управление флагом цикла измерения в регистре 30009
        
        Новая логика статусов:
        0   - готов к следующей команде
        10  - измерение верхней стенки
        11  - подсчёт верхней стенки
        110 - подсчёт завершён, готов к CMD=12
        12  - измерение фланца
        13  - подсчёт фланца
        112 - подсчёт завершён, готов к CMD=14
        14  - измерение нижней стенки
        15  - подсчёт нижней стенки
        114 - подсчёт завершён, готов к CMD=16
        16  - оценка качества
        116 - оценка завершена, готов к CMD=0
        -1  - ошибка
        """
        try:
            if not self.modbus_server or not self.modbus_server.slave_context:
                return
                
            current_state_value = self.current_state.value if hasattr(self.current_state, 'value') else str(self.current_state)
            
            # === КАЛИБРОВКА ВЫСОТЫ (CMD=103) ===
            if current_state_value == "IDLE" and new_cmd == 103:
                # 0 → 103: начало калибровки высоты (поиск 3 ненулевых показаний датчика 1)
                self.write_cycle_flag(103)
                self.clear_measurement_buffers()
                self.height_calibration_nonzero_count = 0
                self.distance_to_plane_calculated = False
                self.recent_measurements = []
                print(" [0→103] Начало калибровки высоты: поиск 3 ненулевых показаний датчика 1")
            
            # === КАЛИБРОВКИ СТЕНКИ/ДНА/ФЛАНЦА (100/101/102) ===
            elif current_state_value == "IDLE" and new_cmd in [100, 101, 102]:
                # Устанавливаем статус равным номеру команды
                self.write_cycle_flag(new_cmd)
                self.clear_measurement_buffers()
                print(f" [0→{new_cmd}] Начало калибровки")
            
            # === НАЧАЛО ЦИКЛА ИЗМЕРЕНИЙ ===
            elif current_state_value == "IDLE" and new_cmd == 10:
                # 0 → 10: начало цикла измерения верхней стенки
                self.write_cycle_flag(10)
                self.measurement_cycle_active = True
                self.clear_measurement_buffers()
                # Сбрасываем флаги выполнения расчётов
                self.wall_calculated = False
                self.flange_calculated = False
                self.bottom_calculated = False
                self.quality_evaluated = False
                # Очищаем кеш калиброванных расстояний
                self.cached_distance_1_2 = None
                self.cached_distance_to_center = None
                self.cached_distance_1_3 = None
                self.cached_distance_sensor4 = None
                # Сбрасываем счетчики частоты
                self.frequency_counter = 0
                self.frequency_start_time = None
                self.last_frequency_display = 0
                print(" [0→10] Начало цикла: измерение верхней стенки")
            
            # === ВЕРХНЯЯ СТЕНКА ===
            elif current_state_value == "MEASURE_WALL_PROCESS" and new_cmd == 11:
                # 10 → 11: команда на подсчёт верхней стенки
                self.write_cycle_flag(11)
                print(" [10→11] Подсчёт результатов верхней стенки...")
                
            elif current_state_value == "MEASURE_WALL_CALCULATE" and new_cmd == 12:
                # После завершения подсчёта, HMI отправляет CMD=12
                self.write_cycle_flag(12)
                # Сбрасываем флаг расчёта фланца для следующего этапа
                self.flange_calculated = False
                # Сбрасываем счетчики частоты для нового этапа
                self.frequency_counter = 0
                self.frequency_start_time = None
                self.last_frequency_display = 0
                # Очищаем кеш калиброванных расстояний для перезагрузки свежих значений
                self.cached_distance_to_center = None
                self.cached_distance_1_3 = None
                self.cached_distance_sensor4 = None
                print(" [11→12] Подсчёт завершён, начало измерения фланца, кеш очищен")
            
            # === ФЛАНЕЦ ===
            elif current_state_value == "MEASURE_FLANGE_PROCESS" and new_cmd == 13:
                # 12 → 13: команда на подсчёт фланца
                self.write_cycle_flag(13)
                print(" [12→13] Подсчёт результатов фланца...")
                
            elif current_state_value == "MEASURE_FLANGE_CALCULATE" and new_cmd == 14:
                # После завершения подсчёта, HMI отправляет CMD=14
                self.write_cycle_flag(14)
                # Сбрасываем флаг расчёта нижней стенки для следующего этапа
                self.bottom_calculated = False
                # Сбрасываем счетчики частоты для нового этапа
                self.frequency_counter = 0
                self.frequency_start_time = None
                self.last_frequency_display = 0
                print(" [13→14] Подсчёт завершён, начало измерения нижней стенки")
            
            # === НИЖНЯЯ СТЕНКА ===
            elif current_state_value == "MEASURE_BOTTOM_PROCESS" and new_cmd == 15:
                # 14 → 15: команда на подсчёт нижней стенки
                self.write_cycle_flag(15)
                print(" [14→15] Подсчёт результатов нижней стенки...")
                
            elif current_state_value == "MEASURE_BOTTOM_CALCULATE" and new_cmd == 16:
                # После завершения подсчёта, HMI отправляет CMD=16
                self.write_cycle_flag(16)
                # Сбрасываем флаг оценки качества для следующего этапа
                self.quality_evaluated = False
                print(" [15→16] Подсчёт завершён, начало оценки качества")
            
            # === ОЦЕНКА КАЧЕСТВА ===
            elif current_state_value == "QUALITY_EVALUATION" and new_cmd == 0:
                # 16 → 0: завершение цикла и возврат в IDLE
                self.write_cycle_flag(0)
                self.measurement_cycle_active = False
                self.clear_measurement_buffers()
                print(" [16→0] Оценка завершена, цикл завершён, возврат в IDLE")

            # === ЗАВЕРШЕНИЕ КАЛИБРОВКИ ВЫСОТЫ ===
            elif current_state_value == "CALIBRATE_HEIGHT" and new_cmd == 0:
                # 103 → 0: завершение калибровки высоты
                self.write_cycle_flag(0)
                self.clear_measurement_buffers()
                print(" [103→0] Калибровка высоты завершена, возврат в IDLE")
            
            # === ЗАВЕРШЕНИЕ ОТЛАДКИ РЕГИСТРОВ ===
            elif current_state_value == "DEBUG_REGISTERS" and new_cmd == 0:
                # 104 → 0: завершение отладки
                self.write_cycle_flag(0)
                self.clear_measurement_buffers()
                # Очищаем атрибуты отладки
                if hasattr(self, 'debug_start_time'):
                    delattr(self, 'debug_start_time')
                if hasattr(self, 'debug_last_display'):
                    delattr(self, 'debug_last_display')
                print(" [104→0] Отладка регистров завершена, возврат в IDLE")
            
            # === ЗАВЕРШЕНИЕ КАЛИБРОВОК 100/101/102 ===
            elif current_state_value in ["CALIBRATE_WALL", "CALIBRATE_BOTTOM", "CALIBRATE_FLANGE"] and new_cmd == 0:
                self.write_cycle_flag(0)
                self.clear_measurement_buffers()
                print(f" [{current_state_value}→0] Калибровка завершена, возврат в IDLE")
            
            # === ПРЕРЫВАНИЕ ЦИКЛА (ОШИБКИ) ===
            elif new_cmd == 0 and self.measurement_cycle_active:
                # Любой переход в 0 во время активного цикла (кроме успешного 16→0) = прерывание
                self.write_cycle_flag(-1)
                self.measurement_cycle_active = False
                self.clear_measurement_buffers()
                print(f" [{current_state_value}→0] Цикл прерван! Ошибка.")

            # === ПОТОКОВЫЕ РЕЖИМЫ (200/201/202/203) ===
            elif current_state_value == "IDLE" and new_cmd in [200, 201, 202, 203]:
                # Устанавливаем статус равным номеру команды
                self.write_cycle_flag(new_cmd)
                self.clear_measurement_buffers()
                print(f" [0→{new_cmd}] Начало потокового режима")
            elif current_state_value in ["STREAM_SENSOR1", "STREAM_SENSOR2", "STREAM_SENSOR3", "STREAM_SENSOR4"] and new_cmd == 0:
                # Выход из потокового режима → 0
                self.write_cycle_flag(0)
                self.clear_measurement_buffers()
                print(f" [{current_state_value}→0] Выход из потокового режима")
                
        except Exception as e:
            print(f" Ошибка управления флагом цикла: {e}")
    
    def write_cycle_flag(self, flag_value: int):
        """Запись флага цикла в регистр 30009"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # 30009 -> индекс 8 в pymodbus (см. modbus_slave_server.py init_registers)
                # Для отрицательных значений используем дополнительный код (two's complement)
                if flag_value < 0:
                    register_value = 65536 + flag_value  # Для -1 получится 65535
                else:
                    register_value = flag_value
                
                # Индекс 8 (как определено в modbus_slave_server.py)
                self.modbus_server.slave_context.setValues(4, 8, [int(register_value)])
                
                # Проверка записи
                verify = self.modbus_server.slave_context.getValues(4, 8, 1)
                print(f" [ФЛАГ 30009] Записано: {flag_value} | Проверка: {verify[0] if verify else 'ERROR'}")
        except Exception as e:
            print(f" [ФЛАГ 30009] ОШИБКА записи: {e}")
            import traceback
            traceback.print_exc()
    
    def set_error_bit(self, bit_number: int, value: bool):
        """
        Установка состояния ошибки в регистре 30058 как целого значения:
        1 = есть ошибка, 0 = нет ошибки. bit_number игнорируется.
        """
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Инвертируем: 1 = ПОДКЛЮЧЕНО (нет ошибки), 0 = ОТКЛЮЧЕНО (ошибка)
                new_value = 0 if value else 1
                # 30058 -> адрес 58 в блоке Input (старт с 1)
                self.modbus_server.slave_context.setValues(4, 58, [int(new_value)])
                print(f" [СОСТОЯНИЕ ДАТЧИКОВ] 30058 = {new_value} ({'OK' if new_value == 1 else 'НЕТ'})")
        except Exception as e:
            print(f" [ОШИБКА] Ошибка записи регистра 30058: {e}")
    
    def get_error_bit(self, bit_number: int) -> bool:
        """
        Возвращает True, если регистр 30058 равен 1 (есть ошибка), иначе False.
        bit_number игнорируется.
        """
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # 30058 -> адрес 58 в блоке Input (старт с 1)
                current_value = self.modbus_server.slave_context.getValues(4, 58, 1)[0]
                # Возвращаем True, если ЕСТЬ ОШИБКА (для совместимости с названием функции)
                # Ошибка теперь когда 30058 == 0
                return int(current_value) == 0
        except Exception as e:
            print(f" [ОШИБКА] Ошибка чтения регистра 30058: {e}")
        return False
    
    def _is_port_available(self, port_name: str) -> bool:
        """
        Проверяет наличие COM-порта в системе через pyserial.tools.list_ports.
        Возвращает True, если порт есть в списке доступных.
        """
        try:
            import serial.tools.list_ports as list_ports
            ports = [p.device.upper() for p in list_ports.comports()]
            return port_name.upper() in ports
        except Exception:
            return True  # если не удалось проверить — не блокируем логику
    
    def _is_sensor_connection_alive(self) -> bool:
        """
        Возвращает True, если объект датчиков есть, порт существует в системе,
        и (если доступно) serial.is_open == True.
        """
        if self.sensors is None:
            return False
        if not self._is_port_available(self.port):
            return False
        ser = getattr(self.sensors, 'ser', None)
        if ser is None:
            return False
        if hasattr(ser, 'is_open'):
            try:
                return bool(ser.is_open)
            except Exception:
                return False
        return True
    
    def check_and_reconnect_sensors(self):
        """
        Проверка и автоматическое переподключение датчиков, если они не подключены
        Вызывается периодически в основном цикле
        """
        try:
            # Проверяем, что Modbus сервер инициализирован
            if not self.modbus_server or not self.modbus_server.slave_context:
                return  # Modbus сервер еще не готов
            
            # Проверяем, нужно ли пытаться переподключиться
            if self.sensors is not None:
                # Комплексная проверка соединения
                if not self._is_sensor_connection_alive():
                    print(" [ПОДКЛЮЧЕНИЕ] Соединение с датчиками потеряно. Помечаем как отключенные.")
                    self.sensors = None
                    self.set_error_bit(0, True)
                    self.last_reconnect_attempt = 0  # разрешить немедленную попытку
                else:
                    if self.get_error_bit(0):
                        self.set_error_bit(0, False)
                    return
            
            # Датчики не подключены - проверяем интервал перед следующей попыткой
            current_time = time.time()
            time_since_last_attempt = current_time - self.last_reconnect_attempt
            
            # Попытки переподключения делаем по таймеру, даже если бит ошибки не установлен
            should_attempt = (self.last_reconnect_attempt == 0) or (time_since_last_attempt >= self.reconnect_interval)
            
            # Отладочное сообщение для диагностики
            if should_attempt:
                current_err = None
                try:
                    current_err = self.get_error_bit(0)
                except Exception:
                    current_err = None
                print(f" [ПЕРЕПОДКЛЮЧЕНИЕ] Проверка: датчики=None, бит_ошибки={current_err}, время_с_последней_попытки={time_since_last_attempt:.1f}с")
            
            if should_attempt:
                # Пытаемся переподключиться
                self.last_reconnect_attempt = current_time
                print(f" [ПЕРЕПОДКЛЮЧЕНИЕ] Попытка переподключения датчиков на порту {self.port}...")
                
                self.sensors = HighSpeedRiftekSensor(self.port, self.baudrate, timeout=0.002)
                
                if self.sensors.connect():
                    print(" [ПЕРЕПОДКЛЮЧЕНИЕ] ✅ Датчики успешно переподключены!")
                    self.set_error_bit(0, False)  # Сбрасываем бит ошибки
                else:
                    print(f" [ПЕРЕПОДКЛЮЧЕНИЕ] ❌ Ошибка переподключения, повторим через {self.reconnect_interval:.0f} сек")
                    self.sensors = None  # Сбрасываем указатель
                    self.set_error_bit(0, True)  # Устанавливаем бит ошибки
            # else: не прошло достаточно времени с последней попытки - ничего не делаем
                
        except Exception as e:
            print(f" [ПЕРЕПОДКЛЮЧЕНИЕ] Ошибка при проверке/переподключении датчиков: {e}")
            import traceback
            traceback.print_exc()
            self.sensors = None
            try:
                self.set_error_bit(0, True)
            except:
                pass  # Если Modbus сервер еще не готов, игнорируем ошибку
    
    def increment_product_number(self):
        """Увеличение номера изделия при успешном завершении цикла"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # 40101 -> индекс 100 в Holding регистрах (40001=индекс 0, 40101=индекс 100)
                current_number = self.modbus_server.slave_context.getValues(3, 100, 1)[0]
                new_number = current_number + 1
                self.modbus_server.slave_context.setValues(3, 100, [new_number])
                print(f" Номер изделия увеличен: {current_number} → {new_number}")
                
        except Exception as e:
            print(f" Ошибка увеличения номера изделия: {e}")
    
    def evaluate_product_quality(self) -> str:
        """
        Оценка качества изделия после завершения цикла измерения
        
        Returns:
            "GOOD", "CONDITIONALLY_GOOD" или "BAD"
        """
        try:
            if not self.modbus_server or not self.modbus_server.slave_context:
                return "BAD"
            
            # Читаем настройки проверки из регистров 40049-40051
            check_mode = self.modbus_server.slave_context.getValues(3, 48, 1)[0]  # 40049
            allowed_conditionally_bad = self.modbus_server.slave_context.getValues(3, 49, 1)[0]  # 40050
            allowed_bad = self.modbus_server.slave_context.getValues(3, 50, 1)[0]  # 40051
            
            # Читаем номер смены и изделия
            shift_number = self.modbus_server.slave_context.getValues(3, 99, 1)[0]  # 40100
            product_number = self.modbus_server.slave_context.getValues(3, 100, 1)[0]  # 40101
            
            # Счётчики ошибок
            conditionally_bad_count = 0
            bad_count = 0
            
            # Структура для хранения результатов проверки
            measurement_data = {
                'shift_number': shift_number,
                'product_number': product_number,
                'check_mode': check_mode,
                'allowed_conditionally_bad': allowed_conditionally_bad,
                'allowed_bad': allowed_bad
            }
            
            # Определяем какие значения проверять
            check_indices = []
            if check_mode == 0:  # все значения (мин, сред, макс)
                check_indices = [0, 1, 2]  # макс, сред, мин
            elif check_mode == 1:  # только среднее
                check_indices = [1]
            elif check_mode == 2:  # макс + сред
                check_indices = [0, 1]
            elif check_mode == 3:  # мин + сред
                check_indices = [1, 2]
            
            # Проверяем 7 параметров (добавлены: высота, нижняя стенка, дно)
            parameters = [
                {
                    'name': 'height',
                    'measured_regs': [(30040, 30041), (30042, 30043), (30044, 30045)],  # макс, сред, мин
                    'base_regs': (40376, 40377),  # базовое значение
                    'cond_bad_regs': (40378, 40379),  # условно-негодная погрешность
                    'bad_regs': (40380, 40381),  # негодная погрешность
                    'check_type': 'one_sided'  # односторонняя проверка
                },
                {
                    'name': 'upper_wall',
                    'measured_regs': [(30016, 30017), (30018, 30019), (30020, 30021)],  # макс, сред, мин
                    'base_regs': (40352, 40353),  # базовое значение
                    'cond_bad_regs': (40354, 40355),  # условно-негодная погрешность
                    'bad_regs': (40356, 40357),  # негодная погрешность
                    'check_type': 'one_sided'  # односторонняя проверка
                },
                {
                    'name': 'flange_thickness',
                    'measured_regs': [(30034, 30035), (30036, 30037), (30038, 30039)],
                    'base_regs': (40370, 40371),
                    'cond_bad_regs': (40372, 40373),
                    'bad_regs': (40374, 40375),
                    'check_type': 'one_sided'  # односторонняя проверка
                },
                {
                    'name': 'body_diameter',
                    'measured_regs': [(30046, 30047), (30048, 30049), (30050, 30051)],
                    'base_regs': (40382, 40383),
                    'cond_bad_regs': (40384, 40385),
                    'bad_regs': (40386, 40387),
                    'check_type': 'one_sided'  # односторонняя проверка
                },
                {
                    'name': 'flange_diameter',
                    'measured_regs': [(30052, 30053), (30054, 30055), (30056, 30057)],
                    'base_regs': (40388, 40389),
                    'cond_bad_regs': (40390, 40391),
                    'bad_regs': (40392, 40393),
                    'check_type': 'one_sided'  # односторонняя проверка
                },
                {
                    'name': 'bottom_wall',
                    'measured_regs': [(30022, 30023), (30024, 30025), (30026, 30027)],  # макс, сред, мин
                    'base_regs': (40358, 40359),  # базовое значение
                    'cond_bad_regs': (40360, 40361),  # условно-негодная погрешность
                    'bad_regs': (40362, 40363),  # негодная погрешность
                    'positive_bad_regs': (40402, 40403),  # положительная негодная погрешность
                    'check_type': 'two_sided'  # двусторонняя проверка
                },
                {
                    'name': 'bottom',
                    'measured_regs': [(30028, 30029), (30030, 30031), (30032, 30033)],  # макс, сред, мин
                    'base_regs': (40364, 40365),  # базовое значение
                    'cond_bad_regs': (40366, 40367),  # условно-негодная погрешность
                    'bad_regs': (40368, 40369),  # негодная погрешность
                    'positive_bad_regs': (40400, 40401),  # положительная негодная погрешность
                    'check_type': 'two_sided'  # двусторонняя проверка
                }
            ]
            
            # Проверяем каждый параметр
            for param in parameters:
                # Читаем базовое значение и погрешности
                base_value = self.read_float_from_registers(param['base_regs'], 'holding')
                cond_bad_error = self.read_float_from_registers(param['cond_bad_regs'], 'holding')  # отрицательное
                bad_error = self.read_float_from_registers(param['bad_regs'], 'holding')  # отрицательное
                
                # Для двусторонней проверки читаем положительную погрешность
                positive_bad_error = None
                if param['check_type'] == 'two_sided':
                    positive_bad_error = self.read_float_from_registers(param['positive_bad_regs'], 'holding')
                
                # Читаем измеренные значения
                measured_values = []
                for reg_pair in param['measured_regs']:
                    value = self.read_float_from_registers(reg_pair, 'input')
                    measured_values.append(value)
                
                # Сохраняем значения в measurement_data
                measurement_data[f"{param['name']}_max"] = measured_values[0]
                measurement_data[f"{param['name']}_avg"] = measured_values[1]
                measurement_data[f"{param['name']}_min"] = measured_values[2]
                
                # Выводим проверяемый параметр
                print(f"\n === {param['name'].upper()} ({param['check_type']}) ===")
                
                # Проверяем только выбранные значения
                param_errors = []
                value_names = ['МАКС', 'СРЕД', 'МИН']
                for idx in check_indices:
                    measured = measured_values[idx]
                    
                    # Выбираем метод проверки в зависимости от типа
                    if param['check_type'] == 'two_sided':
                        status = self.check_single_value_with_upper_limit(
                            measured, base_value, cond_bad_error, bad_error, positive_bad_error
                        )
                    else:  # one_sided
                        status = self.check_single_value(measured, base_value, cond_bad_error, bad_error)
                    
                    param_errors.append(status)
                    print(f" [{value_names[idx]}] {measured:.3f} → {status}")
                
                # Определяем статус параметра (худший из проверенных)
                if "BAD" in param_errors:
                    param_status = "BAD"
                    bad_count += 1
                elif "CONDITIONALLY_GOOD" in param_errors:
                    param_status = "CONDITIONALLY_GOOD"
                    conditionally_bad_count += 1
                else:
                    param_status = "GOOD"
                
                print(f" ИТОГ: {param_status}")
                measurement_data[f"{param['name']}_status"] = param_status
            
            # Сохраняем счётчики ошибок
            measurement_data['conditionally_bad_count'] = conditionally_bad_count
            measurement_data['bad_count'] = bad_count
            
            # Определяем итоговый результат
            if bad_count > allowed_bad:
                result = "BAD"
            elif conditionally_bad_count > allowed_conditionally_bad:
                result = "CONDITIONALLY_GOOD"
            else:
                result = "GOOD"
            
            print(f"\n ИТОГ: {result} | Смена: {shift_number} | Изделие: {product_number}")
            
            measurement_data['result'] = result
            
            # Сохраняем в БД
            if self.modbus_server.db_integration:
                self.modbus_server.db_integration.db.save_measurement_record(measurement_data)
            
            return result
            
        except Exception as e:
            print(f" Ошибка оценки качества: {e}")
            return "BAD"
    
    def check_single_value(self, measured: float, base: float, cond_bad_error: float, bad_error: float) -> str:
        """
        Проверка одного измеренного значения (односторонняя - только нижние границы)
        
        Args:
            measured: Измеренное значение
            base: Базовое (эталонное) значение
            cond_bad_error: Условно-негодная погрешность (отрицательная)
            bad_error: Негодная погрешность (отрицательная, меньше cond_bad_error)
        
        Returns:
            "GOOD", "CONDITIONALLY_GOOD" или "BAD"
        """
        # Диапазоны:
        # ГОДНАЯ: [base + cond_bad_error, base]
        # УСЛОВНО-ГОДНАЯ: [base + bad_error, base + cond_bad_error)
        # НЕГОДНАЯ: < base + bad_error или > base
        
        if measured > base:
            return "BAD"  # Больше базового - негодная
        elif measured >= base + cond_bad_error:
            return "GOOD"  # В диапазоне годных
        elif measured >= base + bad_error:
            return "CONDITIONALLY_GOOD"  # В диапазоне условно-годных
        else:
            return "BAD"  # Меньше минимума - негодная
    
    def check_single_value_with_upper_limit(self, measured: float, base: float, 
                                           cond_bad_error: float, bad_error: float, 
                                           positive_bad_error: float) -> str:
        """
        Проверка одного измеренного значения (двусторонняя - нижние и верхняя границы)
        Используется для нижней стенки и дна
        
        Args:
            measured: Измеренное значение
            base: Базовое (эталонное) значение
            cond_bad_error: Условно-негодная погрешность (отрицательная)
            bad_error: Негодная погрешность (отрицательная, меньше cond_bad_error)
            positive_bad_error: Положительная негодная погрешность (верхняя граница)
        
        Returns:
            "GOOD", "CONDITIONALLY_GOOD" или "BAD"
        """
        # Диапазоны:
        # НЕГОДНАЯ: measured < (base + bad_error)
        # УСЛОВНО-ГОДНАЯ: (base + bad_error) <= measured < (base + cond_bad_error)
        # ГОДНАЯ: (base + cond_bad_error) <= measured <= (base + positive_bad_error)
        # НЕГОДНАЯ: measured > (base + positive_bad_error)
        
        if measured > base + positive_bad_error:
            return "BAD"  # Больше верхней границы - негодная
        elif measured >= base + cond_bad_error:
            return "GOOD"  # В диапазоне годных
        elif measured >= base + bad_error:
            return "CONDITIONALLY_GOOD"  # В диапазоне условно-годных
        else:
            return "BAD"  # Меньше нижней границы - негодная
    
    def read_float_from_registers(self, reg_pair: Tuple[int, int], reg_type: str = 'holding') -> float:
        """
        Чтение float значения из пары регистров
        
        Args:
            reg_pair: Кортеж (low_register, high_register)
            reg_type: 'holding' или 'input'
        
        Returns:
            float значение
        """
        try:
            # Определяем тип регистров
            if reg_type == 'holding':
                function_code = 3
                base_offset = 40000
            else:  # input
                function_code = 4
                base_offset = 30000
            
            # Вычисляем индексы (ВАЖНО: 40001 = индекс 1, 30001 = индекс 1 в pymodbus)
            first_idx = reg_pair[0] - base_offset
            second_idx = reg_pair[1] - base_offset
            
            # Читаем значения (В HMI: первый регистр = СТАРШЕЕ слово, второй = МЛАДШЕЕ)
            high_word = self.modbus_server.slave_context.getValues(function_code, first_idx, 1)[0]
            low_word = self.modbus_server.slave_context.getValues(function_code, second_idx, 1)[0]
            
            # Преобразуем в float
            combined = (high_word << 16) | low_word
            float_value = struct.unpack('!f', struct.pack('!I', combined))[0]
            
            return float_value
            
        except Exception as e:
            print(f" Ошибка чтения float из регистров {reg_pair}: {e}")
            return 0.0
    
    def update_product_counters(self, result: str):
        """
        Обновление счётчиков изделий
        
        Args:
            result: "GOOD", "CONDITIONALLY_GOOD" или "BAD"
        """
        try:
            if not self.modbus_server or not self.modbus_server.slave_context:
                return
            
            # Проверяем смену смены
            self.check_shift_change()
            
            # 30101 (индекс 100) - общее количество
            total = self.modbus_server.slave_context.getValues(4, 100, 1)[0]
            self.modbus_server.slave_context.setValues(4, 100, [total + 1])
            
            # Обновляем соответствующий счётчик
            if result == "GOOD":
                # 30102 (индекс 101) - годные
                good = self.modbus_server.slave_context.getValues(4, 101, 1)[0]
                self.modbus_server.slave_context.setValues(4, 101, [good + 1])
            elif result == "CONDITIONALLY_GOOD":
                # 30103 (индекс 102) - условно-годные
                cond_good = self.modbus_server.slave_context.getValues(4, 102, 1)[0]
                self.modbus_server.slave_context.setValues(4, 102, [cond_good + 1])
            else:  # BAD
                # 30104 (индекс 103) - негодные
                bad = self.modbus_server.slave_context.getValues(4, 103, 1)[0]
                self.modbus_server.slave_context.setValues(4, 103, [bad + 1])
            
            print(f" Счётчики обновлены: Всего={total + 1}, Результат={result}")
            
        except Exception as e:
            print(f" Ошибка обновления счётчиков: {e}")
    
    def check_shift_change(self):
        """
        Проверка смены смены и сброс счётчиков при необходимости
        """
        try:
            if not self.modbus_server or not self.modbus_server.slave_context:
                return
            
            # Читаем текущее значение смены из регистра 40100
            new_shift_number = self.modbus_server.slave_context.getValues(3, 99, 1)[0]
            
            # Если смена изменилась
            if new_shift_number != self.current_shift_number:
                print(f" Обнаружена смена смены: {self.current_shift_number} -> {new_shift_number}")
                
                # Сбрасываем все счётчики изделий
                self.reset_product_counters()
                
                # Обновляем текущую смену
                self.current_shift_number = new_shift_number
                
        except Exception as e:
            print(f" Ошибка проверки смены: {e}")
    
    def reset_product_counters(self):
        """
        Сброс всех счётчиков изделий при смене смены
        """
        try:
            if not self.modbus_server or not self.modbus_server.slave_context:
                return
            
            # Сбрасываем счётчики в Input регистрах 30101-30104
            self.modbus_server.slave_context.setValues(4, 100, [0])  # 30101 - всего
            self.modbus_server.slave_context.setValues(4, 101, [0])  # 30102 - годных
            self.modbus_server.slave_context.setValues(4, 102, [0])  # 30103 - условно-годных
            self.modbus_server.slave_context.setValues(4, 103, [0])  # 30104 - негодных
            
            print(" Счётчики изделий сброшены для новой смены")
            
        except Exception as e:
            print(f" Ошибка сброса счётчиков: {e}")
    
    def clear_measurement_buffers(self):
        """Очистка буферов измерений"""
        # Очищаем основной буфер калибровок (measurement_buffer)
        self.measurement_buffer['sensor1'].clear()
        self.measurement_buffer['sensor2'].clear()
        self.measurement_buffer['sensor3'].clear()
        self.measurement_buffer['sensor4'].clear()
        
        # Буферы команды 9 (высота)
        self.height_measurements = []
        self.obstacle_detected = False
        self.obstacle_filter_count = 0
        self.height_calculated = False
        # Буферы и флаги команды 103 (калибровка высоты)
        self.height_calibration_nonzero_count = 0
        self.distance_to_plane_calculated = False
        self.recent_measurements = []
        
        # Буферы команды 10
        self.sensor1_measurements = []
        self.sensor2_measurements = []
        self.wall_thickness_buffer = []
        self.temp_sensor1_buffer = []
        self.temp_sensor2_buffer = []
        
        # Буферы команды 11
        self.sensor1_flange_measurements = []
        self.sensor3_measurements = []
        self.sensor4_measurements = []
        self.temp_sensor1_flange_buffer = []
        self.temp_sensor3_buffer = []
        self.temp_sensor4_buffer = []
        self.body_diameter_buffer = []
        self.flange_diameter_buffer = []
        self.flange_thickness_buffer = []
        self.bottom_thickness_buffer = []
        
        # Буферы команды 12
        self.sensor1_bottom_measurements = []
        self.sensor2_bottom_measurements = []
        self.temp_sensor1_bottom_buffer = []
        self.temp_sensor2_bottom_buffer = []
        self.bottom_wall_thickness_buffer = []
        
        print(" Буферы измерений очищены")
    
    def stop_all_streams(self):
        """Остановка всех активных потоковых режимов"""
        if self.sensors:
            if self.stream_active_sensor1:
                try:
                    self.sensors.stop_stream_mode(1)
                    self.stream_active_sensor1 = False
                    print(" Остановлен поток датчика 1")
                except Exception as e:
                    print(f" Ошибка остановки потока датчика 1: {e}")
            
            if self.stream_active_sensor2:
                try:
                    self.sensors.stop_stream_mode(2)
                    self.stream_active_sensor2 = False
                    print(" Остановлен поток датчика 2")
                except Exception as e:
                    print(f" Ошибка остановки потока датчика 2: {e}")
            
            if self.stream_active_sensor3:
                try:
                    self.sensors.stop_stream_mode(3)
                    self.stream_active_sensor3 = False
                    print(" Остановлен поток датчика 3")
                except Exception as e:
                    print(f" Ошибка остановки потока датчика 3: {e}")
            
            if self.stream_active_sensor4:
                try:
                    self.sensors.stop_stream_mode(4)
                    self.stream_active_sensor4 = False
                    print(" Остановлен поток датчика 4")
                except Exception as e:
                    print(f" Ошибка остановки потока датчика 4: {e}")
    
    def execute_state_actions(self):
        """Выполнение действий в зависимости от текущего состояния"""
        if self.current_state == SystemState.IDLE:
            self.handle_idle_state()
            
        # Калибровки
        elif self.current_state == SystemState.CALIBRATE_WALL:
            self.handle_calibrate_wall_state()
        elif self.current_state == SystemState.CALIBRATE_BOTTOM:
            self.handle_calibrate_bottom_state()
        elif self.current_state == SystemState.CALIBRATE_FLANGE:
            self.handle_calibrate_flange_state()
        elif self.current_state == SystemState.CALIBRATE_HEIGHT:
            self.handle_calibrate_height_state()
        elif self.current_state == SystemState.DEBUG_REGISTERS:
            self.handle_debug_registers_state()
            
        # Измерение высоты
        elif self.current_state == SystemState.MEASURE_HEIGHT_PROCESS:
            self.handle_measure_height_process_state()
            
        # Основной цикл измерения - верхняя стенка
        elif self.current_state == SystemState.MEASURE_WALL_PROCESS:
            self.handle_measure_wall_process_state()
        elif self.current_state == SystemState.MEASURE_WALL_CALCULATE:
            self.handle_calculate_wall_state()
            
        # Основной цикл измерения - фланец
        elif self.current_state == SystemState.MEASURE_FLANGE_PROCESS:
            self.handle_measure_flange_process_state()
        elif self.current_state == SystemState.MEASURE_FLANGE_CALCULATE:
            self.handle_calculate_flange_state()
            
        # Основной цикл измерения - нижняя стенка
        elif self.current_state == SystemState.MEASURE_BOTTOM_PROCESS:
            self.handle_measure_bottom_process_state()
        elif self.current_state == SystemState.MEASURE_BOTTOM_CALCULATE:
            self.handle_calculate_bottom_state()
            
        # Оценка качества изделия
        elif self.current_state == SystemState.QUALITY_EVALUATION:
            self.handle_quality_evaluation_state()
            
        # Потоковые режимы
        elif self.current_state == SystemState.STREAM_SENSOR1:
            self.handle_stream_sensor1_state()
        elif self.current_state == SystemState.STREAM_SENSOR2:
            self.handle_stream_sensor2_state()
        elif self.current_state == SystemState.STREAM_SENSOR3:
            self.handle_stream_sensor3_state()
        elif self.current_state == SystemState.STREAM_SENSOR4:
            self.handle_stream_sensor4_state()
            
        elif self.current_state == SystemState.ERROR:
            self.handle_error_state()
    
    def handle_calibrate_height_state(self):
        """
        CMD=103: Калибровка высоты
        - Ожидаем 3 последовательных ненулевых измерения с датчика 1
        - Читаем шаги (40052-40053) и импульсы на мм (40054)
        - Читаем эталонную высоту (40008-40009)
        - Вычисляем дистанцию до начала плоскости и записываем в 40055-40056
        - Обновляем статус: 103 (поиск) → 931 (рассчитано), ожидаем CMD=0
        """
        if not self.sensors:
            print(" Ошибка: датчики не подключены!")
            self.current_state = SystemState.ERROR
            return
        
        
        try:
            # Инициализация таймера частоты при первом измерении
            if self.frequency_start_time is None:
                self.frequency_start_time = time.time()
                self.last_frequency_display = self.frequency_start_time
            
            # Читаем только датчик 1
            sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.sensors.perform_quad_sensor_measurement(
                self.sensor_range_mm, self.sensor_range_mm, self.sensor_range_mm, self.sensor_range_mm
            )
            
            # Отладочная информация о сырых показаниях (только первые несколько измерений)
            if self.frequency_counter <= 5:
                print(f" [CMD=103] Отладка #{self.frequency_counter}: sensor1_mm={sensor1_mm}")
            
            
            # Увеличиваем счетчик измерений и выводим частоту раз в секунду
            self.frequency_counter += 1
            current_time = time.time()
            if current_time - self.last_frequency_display >= 1.0:
                elapsed = current_time - self.frequency_start_time
                if elapsed > 0:
                    instant_freq = self.frequency_counter / elapsed
                    status = "Поиск 3 ненулевых показаний" if not self.distance_to_plane_calculated else "Рассчитано"
                    print(f" [CMD=103] {status}: {instant_freq:.1f} Гц | Измерений: {self.frequency_counter}")
                self.last_frequency_display = current_time
            
            # Логика поиска 3 ненулевых показаний из последних 5 измерений
            # Добавляем текущее измерение в буфер (храним последние 5)
            self.recent_measurements.append(sensor1_mm)
            if len(self.recent_measurements) > 5:
                self.recent_measurements.pop(0)  # Удаляем самое старое
            
            # Подсчитываем ненулевые показания в буфере
            nonzero_count = sum(1 for m in self.recent_measurements if m is not None and m > 0)
            
            # Выводим только при изменении количества ненулевых в буфере
            if hasattr(self, '_last_nonzero_count') and self._last_nonzero_count != nonzero_count:
                if sensor1_mm is not None and sensor1_mm > 0:
                    print(f" [CMD=103] Ненулевое показание: {sensor1_mm:.3f}мм | Ненулевых в буфере: {nonzero_count}/5")
                else:
                    print(f" [CMD=103] Нулевое показание: {sensor1_mm} | Ненулевых в буфере: {nonzero_count}/5")
            self._last_nonzero_count = nonzero_count
            
            # Если найдено 3+ ненулевых показания в буфере и еще не рассчитано
            if nonzero_count >= 3 and not self.distance_to_plane_calculated:
                print(f" [CMD=103] Найдено {nonzero_count} ненулевых показаний в буфере! Начинаем расчет...")
                # Читаем регистры
                steps = self.read_register_40020()          # 40052-40053
                pulses_per_mm = self.read_register_40021()  # 40054
                reference_height = self.read_reference_height()  # 40008-40009
                
                print(f" [CMD=103] Данные для расчета: шаги={steps}, импульсы/мм={pulses_per_mm}, эталонная_высота={reference_height}")
                
                if steps is not None and pulses_per_mm is not None and pulses_per_mm > 0 and reference_height is not None:
                    # Дистанция до начала плоскости = шаги/импульсы_на_мм + эталонная высота
                    distance_to_plane = (steps / float(pulses_per_mm)) + float(reference_height)
                    self.write_distance_to_plane(distance_to_plane)
                    self.write_cycle_flag(931)  # Статус: рассчитано
                    # Выводим только когда флаг переходит из False в True
                    if not self.distance_to_plane_calculated:
                        print(f" [CMD=103] ФЛАГ ИЗМЕНИЛСЯ: distance_to_plane_calculated False → True")
                        print(f" [CMD=103] Дистанция до плоскости рассчитана: {distance_to_plane:.3f}мм")
                        print(f" [CMD=103] Ожидаем CMD=0 для завершения калибровки")
                    self.distance_to_plane_calculated = True
                else:
                    print(f" [CMD=103] ОШИБКА: Не удалось прочитать данные для расчета!")
            elif nonzero_count >= 3:
                pass  # Уже рассчитано, ничего не выводим
        except Exception as e:
            print(f" Ошибка калибровки высоты (CMD=103): {e}")
            self.current_state = SystemState.ERROR

    def handle_debug_registers_state(self):
        """CMD=104: Отладка регистров - показывает данные раз в секунду"""
        try:
            # Инициализация таймера при первом вызове
            if not hasattr(self, 'debug_start_time'):
                self.debug_start_time = time.time()
                self.debug_last_display = self.debug_start_time
                print(" [CMD=104] Начало отладки регистров...")
            
            current_time = time.time()
            # Выводим данные раз в секунду
            if current_time - self.debug_last_display >= 1.0:
                # Читаем регистр статуса (30009)
                try:
                    status_values = self.modbus_server.slave_context.getValues(4, 8, 1)  # 30009 -> index 8
                    status = status_values[0] if status_values else "None"
                except:
                    status = "Ошибка чтения"
                
                # Читаем все регистры
                steps_raw = self.read_register_40020_raw()
                pulses_per_mm_raw = self.read_register_40021_raw()
                reference_height_raw = self.read_reference_height_raw()
                
                print(f" [CMD=104] === ОТЛАДКА РЕГИСТРОВ ===")
                print(f" [CMD=104] Статус (30009): {status}")
                print(f" [CMD=104] 40052-40053 (шаги): {steps_raw}")
                print(f" [CMD=104] 40054 (импульсы/мм): {pulses_per_mm_raw}")
                print(f" [CMD=104] 40008-40009 (эталонная высота): {reference_height_raw}")
                print(f" [CMD=104] =========================")
                
                self.debug_last_display = current_time
                
        except Exception as e:
            print(f" Ошибка отладки регистров (CMD=104): {e}")
            self.current_state = SystemState.ERROR

    def read_register_40020_raw(self):
        """Чтение сырых и обработанных данных регистров 40052-40053"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Сырые данные
                raw_values = self.modbus_server.slave_context.getValues(3, 52, 2)
                # Для 32-bit integer НЕ переворачиваем как float
                if raw_values and len(raw_values) == 2:
                    # Правильное объединение: младшее слово (низкие биты) + старшее слово (высокие биты)
                    steps = (int(raw_values[0]) << 16) | int(raw_values[1])
                    return f"сырые [52-53]: {raw_values} → 32-bit int: {steps}"
        except Exception as e:
            return f"Ошибка: {e}"

    def read_register_40021_raw(self):
        """Чтение сырых и обработанных данных регистра 40054"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Сырые данные - читаем правильный регистр
                raw_value = self.modbus_server.slave_context.getValues(3, 54, 1)
                if raw_value:
                    pulses = int(raw_value[0])
                    return f"сырые [54]: {raw_value} → 16-bit int: {pulses}"
        except Exception as e:
            return f"Ошибка: {e}"

    def read_reference_height_raw(self):
        """Чтение сырых и обработанных данных регистров 40008-40009"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Сырые данные
                raw_values = self.modbus_server.slave_context.getValues(3, 8, 2)
                # Обработанные данные
                height = self.read_reference_height()
                return f"сырые [7-8]: {raw_values} → float мм: {height:.3f}"
        except Exception as e:
            return f"Ошибка: {e}"

    def read_reference_height(self) -> float:
        """Чтение эталонной высоты из регистров 40008, 40009"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # HMI: старшее слово в 40008, младшее в 40009
                values = self.modbus_server.slave_context.getValues(3, 8, 2)
                if values and len(values) >= 2:
                    high_word = int(values[0])  # 40008 - старший
                    low_word = int(values[1])   # 40009 - младший
                    height = self.doubleword_to_float(low_word, high_word)
                    return height
        except Exception as e:
            print(f" Ошибка чтения эталонной высоты: {e}")
        return 0.0

    def write_distance_to_plane(self, distance: float):
        """Запись дистанции до начала плоскости в регистры 40055, 40056"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                low_word, high_word = self.float_to_doubleword(distance)
                # HMI читает: старшее слово из 40055, младшее из 40056
                self.modbus_server.slave_context.setValues(3, 55, [int(high_word)])  # 40055 - старший
                self.modbus_server.slave_context.setValues(3, 56, [int(low_word)])   # 40056 - младший
                # Сохранение в БД
                if self.db_integration:
                    self.db_integration.save_doubleword_register(40055, 'holding', distance, 'Дистанция до плоскости')
                print(f" Записана дистанция до плоскости 40055-40056: {distance:.3f} мм (high: {int(high_word)}, low: {int(low_word)})")
        except Exception as e:
            print(f" Ошибка записи дистанции до плоскости: {e}")
    
    def handle_idle_state(self):
        """Обработка состояния ожидания"""
        try:
            # Раз в секунду мониторим подключение датчиков и выполняем авто-переподключение
            current_time = time.time()
            if current_time - self.idle_monitor_last_time >= 1.0:
                self.idle_monitor_last_time = current_time
                if not self.test_mode:
                    self.check_and_reconnect_sensors()
                    # Однострочный статус без спама
                    connected = self._is_sensor_connection_alive()
                    print(f" [IDLE] Мониторинг подключения датчиков: {'OK' if connected else 'НЕТ'}")
                # Убедимся, что статус ожидания команды остаётся 30009=0
                if self.modbus_server and self.modbus_server.slave_context:
                    self.modbus_server.slave_context.setValues(4, 8, [0])  # 30009 -> index 8
        except Exception as e:
            print(f" [IDLE] Ошибка мониторинга: {e}")
    
    def handle_calibrate_wall_state(self):
        """Обработка калибровки стенки (CMD = 100)"""
        if self.calibration_in_progress:
            return
            
        print("🔧 НАЧАЛО КАЛИБРОВКИ СТЕНКИ")
        self.calibration_in_progress = True
        
        try:
            # 1. Читаем толщину эталона из регистров 40002, 40003
            reference_thickness = self.read_reference_thickness()
            print(f" Толщина эталона: {reference_thickness:.3f} мм")
            
            # 2. Измеряем датчиками 1,2 не менее 4 секунд
            print(" Измерение датчиками 1,2 в течение 4 секунд...")
            self.measure_sensors_for_calibration()
            
            # 3. Усредняем измерения
            avg_sensor1, avg_sensor2, avg_sensor3 = self.calculate_averages()
            print(f" Средние значения: Д1={avg_sensor1:.3f}мм, Д2={avg_sensor2:.3f}мм, Д3={avg_sensor3:.3f}мм")
            
            # 4. Вычисляем расстояние между датчиками 1,2
            distance_1_2 = avg_sensor1 + avg_sensor2 + reference_thickness
            print(f" Расстояние между датчиками 1,2: {distance_1_2:.3f} мм")
            
            # 5. Вычисляем расстояние между датчиками 1,3
            distance_1_3 = avg_sensor1 - avg_sensor3
            print(f" Расстояние между датчиками 1,3: {distance_1_3:.3f} мм")
            
            # 6. Записываем результат 1,2 в регистры 40010, 40011
            self.write_calibration_result_1_2(distance_1_2)
            
            # 7. Записываем результат 1,3 в регистры 40012, 40013
            self.write_calibration_result_1_3(distance_1_3)
            
            # 8. Сохраняем в локальных данных
            self.calibration_data['wall_distance_1_2'] = distance_1_2
            self.calibration_data['wall_distance_1_3'] = distance_1_3
            
            # 9. Сбрасываем команду в 0, чтобы избежать повторного запуска
            self.reset_command()
            
            print(" КАЛИБРОВКА СТЕНКИ ЗАВЕРШЕНА")
            
        except Exception as e:
            print(f" Ошибка калибровки: {e}")
            self.current_state = SystemState.ERROR
                            # Сбрасываем команду даже при ошибке
            self.reset_command()
        finally:
            self.calibration_in_progress = False
    
    def handle_calibrate_bottom_state(self):
        """Обработка калибровки дна (CMD = 101)"""
        if self.calibration_in_progress:
            return
            
        print("🔧 НАЧАЛО КАЛИБРОВКИ ДНА")
        self.calibration_in_progress = True
        
        try:
            # 1. Читаем эталонную толщину дна из регистров 40004, 40005
            reference_bottom_thickness = self.read_reference_bottom_thickness()
            print(f" Эталонная толщина дна: {reference_bottom_thickness:.3f} мм")
            
            # 2. Измеряем датчиком 4 не менее 4 секунд
            print(" Измерение датчиком 4 в течение 4 секунд...")
            self.measure_sensor4_for_calibration()
            
            # 3. Усредняем измерения датчика 4
            avg_sensor4 = self.calculate_sensor4_average()
            print(f" Среднее значение датчика 4: {avg_sensor4:.3f} мм")
            
            # 4. Вычисляем расстояние от датчика 4 до поверхности
            # Формула: Расстояние 4 - поверхность = Измеренное расстояние датчиком 4 + эталонная толщина дна
            distance_4_surface = avg_sensor4 + reference_bottom_thickness
            print(f" Расстояние от датчика 4 до поверхности: {distance_4_surface:.3f} мм")
            
            # 5. Записываем результат в регистры 40014, 40015
            self.write_calibration_result_4_surface(distance_4_surface)
            
            # 6. Сохраняем в локальных данных
            self.calibration_data['bottom_distance_4'] = distance_4_surface
            
            # 7. Сбрасываем команду в 0, чтобы избежать повторного запуска
            self.reset_command()
            
            print(" КАЛИБРОВКА ДНА ЗАВЕРШЕНА")
            
        except Exception as e:
            print(f" Ошибка калибровки дна: {e}")
            self.current_state = SystemState.ERROR
            # Сбрасываем команду даже при ошибке
            self.reset_command()
        finally:
            self.calibration_in_progress = False
    
    def handle_calibrate_flange_state(self):
        """Обработка калибровки фланца (CMD = 102)"""
        if self.calibration_in_progress:
            return
            
        print("🔧 НАЧАЛО КАЛИБРОВКИ ФЛАНЦА")
        self.calibration_in_progress = True
        
        try:
            # 1. Читаем эталонный диаметр из регистров 40006, 40007
            reference_diameter = self.read_reference_diameter()
            print(f" Эталонный диаметр: {reference_diameter:.3f} мм")
            
            # 2. Измеряем датчиком 1 не менее 4 секунд
            print(" Измерение датчиком 1 в течение 4 секунд...")
            self.measure_sensor1_for_calibration()
            
            # 3. Усредняем измерения датчика 1
            avg_sensor1 = self.calculate_sensor1_average()
            print(f" Среднее значение датчика 1: {avg_sensor1:.3f} мм")
            
            # 4. Вычисляем расстояние от датчика 1 до центра
            # Формула: Расстояние датчик 1 - центр = Эталонный диаметр + измерение датчика 1
            distance_1_center = reference_diameter + avg_sensor1
            print(f" Расстояние от датчика 1 до центра: {distance_1_center:.3f} мм")
            
            # 5. Записываем результат в регистры 40016, 40017
            self.write_calibration_result_1_center(distance_1_center)
            
            # 6. Сохраняем в локальных данных
            self.calibration_data['flange_distance_1_center'] = distance_1_center
            
            # 7. Сбрасываем команду в 0, чтобы избежать повторного запуска
            self.reset_command()
            
            print(" КАЛИБРОВКА ФЛАНЦА ЗАВЕРШЕНА")
            
        except Exception as e:
            print(f" Ошибка калибровки фланца: {e}")
            self.current_state = SystemState.ERROR
            # Сбрасываем команду даже при ошибке
            self.reset_command()
        finally:
            self.calibration_in_progress = False
    
    def read_reference_thickness(self) -> float:
        """Чтение толщины эталона из регистров 40002, 40003"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Читаем два регистра (40002, 40003) - HMI отправляет в обратном порядке
                values = self.modbus_server.slave_context.getValues(3, 2, 2)
                if values and len(values) >= 2:
                    # HMI отправляет: старший регистр в 40002, младший в 40003
                    high_word = int(values[0])  # 40002 - старший регистр от HMI
                    low_word = int(values[1])   # 40003 - младший регистр от HMI
                    
                    # Конвертируем из двух 16-битных регистров в float
                    thickness_float = self.doubleword_to_float(low_word, high_word)
                    print(f" Прочитана толщина эталона: {high_word}, {low_word} -> {thickness_float:.3f} мм")
                    return thickness_float
        except Exception as e:
            print(f" Ошибка чтения эталона: {e}")
        return 0.0
    
    def measure_sensors_for_calibration(self):
        """Измерение датчиков для калибровки в течение 4 секунд"""
        if not self.sensors:
            print(" Ошибка: датчики не подключены!")
            return
        
        # Очищаем буферы перед началом измерений
        self.measurement_buffer['sensor1'].clear()
        self.measurement_buffer['sensor2'].clear()
        self.measurement_buffer['sensor3'].clear()
        self.measurement_buffer['sensor4'].clear()
            
        start_time = time.time()
        measurement_count = 0
        
        print(" Начало измерений...")
        
        while (time.time() - start_time) < self.measurement_duration:
            try:
                # Выполняем QUAD измерение (как в main.py - с параметрами диапазонов)
                sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.sensors.perform_quad_sensor_measurement(
                    self.sensor_range_mm, self.sensor_range_mm, self.sensor_range_mm, self.sensor_range_mm
                )
                
                # Сохраняем только валидные измерения
                if all(v is not None for v in [sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm]):
                    self.measurement_buffer['sensor1'].append(sensor1_mm)
                    self.measurement_buffer['sensor2'].append(sensor2_mm)
                    self.measurement_buffer['sensor3'].append(sensor3_mm)
                    self.measurement_buffer['sensor4'].append(sensor4_mm)
                    measurement_count += 1
                
                # Показываем прогресс каждую секунду
                elapsed = time.time() - start_time
                if int(elapsed) != int(elapsed - 0.1):  # Каждую секунду
                    print(f" Время: {elapsed:.1f}с, Измерений: {measurement_count}")
                    
            except Exception as e:
                print(f" Ошибка измерения: {e}")
                # Убран sleep для ускорения
        
        print(f" Измерения завершены. Всего: {measurement_count}")
    
    def calculate_averages(self) -> Tuple[float, float, float]:
        """Вычисление средних значений для датчиков 1, 2 и 3"""
        if (len(self.measurement_buffer['sensor1']) == 0 or 
            len(self.measurement_buffer['sensor2']) == 0 or 
            len(self.measurement_buffer['sensor3']) == 0):
            raise ValueError("Недостаточно данных для усреднения")
        
        avg_sensor1 = sum(self.measurement_buffer['sensor1']) / len(self.measurement_buffer['sensor1'])
        avg_sensor2 = sum(self.measurement_buffer['sensor2']) / len(self.measurement_buffer['sensor2'])
        avg_sensor3 = sum(self.measurement_buffer['sensor3']) / len(self.measurement_buffer['sensor3'])
        
        return avg_sensor1, avg_sensor2, avg_sensor3
    
    def write_calibration_result_1_2(self, distance: float):
        """Запись результата калибровки расстояния 1,2 в регистры 40010, 40011"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Конвертируем float в два 16-битных регистра
                low_word, high_word = self.float_to_doubleword(distance)
                
                # HMI читает: старший регистр из 40010, младший из 40011
                # Поэтому записываем в обратном порядке
                self.modbus_server.slave_context.setValues(3, 10, [int(high_word)])  # 40010 - старший регистр
                self.modbus_server.slave_context.setValues(3, 11, [int(low_word)])   # 40011 - младший регистр
                
                # Сохраняем в базу данных
                if self.db_integration:
                    self.db_integration.save_doubleword_register(
                        40010, 'holding', distance, 'Расстояние между датчиками 1,2'
                    )
                
                print(f" Результат 1,2 записан в регистры 40010, 40011: {distance:.3f} мм (high: {int(high_word)}, low: {int(low_word)})")
        except Exception as e:
            print(f" Ошибка записи результата 1,2: {e}")
    
    def write_calibration_result_1_3(self, distance: float):
        """Запись результата калибровки расстояния 1,3 в регистры 40012, 40013"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Конвертируем float в два 16-битных регистра
                low_word, high_word = self.float_to_doubleword(distance)
                
                # HMI читает: старший регистр из 40012, младший из 40013
                # Поэтому записываем в обратном порядке
                self.modbus_server.slave_context.setValues(3, 12, [int(high_word)])  # 40012 - старший регистр
                self.modbus_server.slave_context.setValues(3, 13, [int(low_word)])   # 40013 - младший регистр
                
                # Сохраняем в базу данных
                if self.db_integration:
                    self.db_integration.save_doubleword_register(
                        40012, 'holding', distance, 'Расстояние между датчиками 1,3'
                    )
                
                print(f" Результат 1,3 записан в регистры 40012, 40013: {distance:.3f} мм (high: {int(high_word)}, low: {int(low_word)})")
        except Exception as e:
            print(f" Ошибка записи результата 1,3: {e}")
    
    def read_reference_bottom_thickness(self) -> float:
        """Чтение эталонной толщины дна из регистров 40004, 40005"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Читаем два регистра (40004, 40005) - HMI отправляет в обратном порядке
                values = self.modbus_server.slave_context.getValues(3, 4, 2)
                if values and len(values) >= 2:
                    # HMI: старший регистр в 40004, младший в 40005
                    high_word = int(values[0])  # 40004
                    low_word = int(values[1])   # 40005
                    
                    # Конвертируем в float
                    thickness = self.doubleword_to_float(low_word, high_word)
                    return thickness
            return 0.0
        except Exception as e:
            print(f" Ошибка чтения эталонной толщины дна: {e}")
            return 0.0
    
    def measure_sensor4_for_calibration(self):
        """Измерение датчика 4 для калибровки в течение 4 секунд"""
        if not self.sensors:
            print(" Ошибка: датчики не подключены!")
            return
            
        start_time = time.time()
        measurement_count = 0
        
        print(" Начало измерений датчика 4...")
        
        # Очищаем буфер датчика 4
        self.measurement_buffer['sensor4'].clear()
        
        while (time.time() - start_time) < self.measurement_duration:
            try:
                # Выполняем QUAD измерение (как в команде 100)
                sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.sensors.perform_quad_sensor_measurement(
                    self.sensor_range_mm, self.sensor_range_mm, self.sensor_range_mm, self.sensor_range_mm
                )
                
                # Сохраняем только измерения датчика 4
                if sensor4_mm is not None:
                    self.measurement_buffer['sensor4'].append(sensor4_mm)
                    measurement_count += 1
                
                # Показываем прогресс каждую секунду
                elapsed = time.time() - start_time
                if int(elapsed) != int(elapsed - 0.1):  # Каждую секунду
                    print(f" Время: {elapsed:.1f}с, Измерений датчика 4: {measurement_count}")
                    
            except Exception as e:
                print(f" Ошибка измерения датчика 4: {e}")
                # Убран sleep для ускорения
        
        print(f" Измерения датчика 4 завершены. Всего: {measurement_count}")
    
    def calculate_sensor4_average(self) -> float:
        """Вычисление среднего значения для датчика 4"""
        if len(self.measurement_buffer['sensor4']) == 0:
            raise ValueError("Недостаточно данных для усреднения датчика 4")
        
        avg_sensor4 = sum(self.measurement_buffer['sensor4']) / len(self.measurement_buffer['sensor4'])
        return round(avg_sensor4, 3)
    
    def write_calibration_result_4_surface(self, distance: float):
        """Запись результата калибровки расстояния датчика 4 до поверхности в регистры 40014, 40015"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Конвертируем float в два 16-битных регистра
                low_word, high_word = self.float_to_doubleword(distance)
                
                # HMI читает: старший регистр из 40014, младший из 40015
                # Поэтому записываем в обратном порядке
                self.modbus_server.slave_context.setValues(3, 14, [int(high_word)])  # 40014 - старший регистр
                self.modbus_server.slave_context.setValues(3, 15, [int(low_word)])   # 40015 - младший регистр
                
                # Сохраняем в базу данных
                if self.db_integration:
                    self.db_integration.save_doubleword_register(
                        40014, 'holding', distance, 'Расстояние датчика 4 до поверхности'
                    )
                
                print(f" Результат датчика 4 записан в регистры 40014, 40015: {distance:.3f} мм (high: {int(high_word)}, low: {int(low_word)})")
        except Exception as e:
            print(f" Ошибка записи результата датчика 4: {e}")
    
    def read_reference_diameter(self) -> float:
        """Чтение эталонного диаметра из регистров 40006, 40007"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Читаем два регистра (40006, 40007) - HMI отправляет в обратном порядке
                values = self.modbus_server.slave_context.getValues(3, 6, 2)
                if values and len(values) >= 2:
                    # HMI: старший регистр в 40006, младший в 40007
                    high_word = int(values[0])  # 40006
                    low_word = int(values[1])   # 40007
                    
                    # Конвертируем в float
                    diameter = self.doubleword_to_float(low_word, high_word)
                    return diameter
            return 0.0
        except Exception as e:
            print(f" Ошибка чтения эталонного диаметра: {e}")
            return 0.0
    
    def measure_sensor1_for_calibration(self):
        """Измерение датчика 1 для калибровки в течение 4 секунд"""
        if not self.sensors:
            print(" Ошибка: датчики не подключены!")
            return
            
        start_time = time.time()
        measurement_count = 0
        
        print(" Начало измерений датчика 1...")
        
        # Очищаем буфер датчика 1
        self.measurement_buffer['sensor1'].clear()
        
        while (time.time() - start_time) < self.measurement_duration:
            try:
                # Выполняем QUAD измерение (как в команде 100)
                sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.sensors.perform_quad_sensor_measurement(
                    self.sensor_range_mm, self.sensor_range_mm, self.sensor_range_mm, self.sensor_range_mm
                )
                
                # Сохраняем только измерения датчика 1
                if sensor1_mm is not None:
                    self.measurement_buffer['sensor1'].append(sensor1_mm)
                    measurement_count += 1
                
                # Показываем прогресс каждую секунду
                elapsed = time.time() - start_time
                if int(elapsed) != int(elapsed - 0.1):  # Каждую секунду
                    print(f" Время: {elapsed:.1f}с, Измерений датчика 1: {measurement_count}")
                    
            except Exception as e:
                print(f" Ошибка измерения датчика 1: {e}")
                # Убран sleep для ускорения
        
        print(f" Измерения датчика 1 завершены. Всего: {measurement_count}")
    
    def calculate_sensor1_average(self) -> float:
        """Вычисление среднего значения для датчика 1"""
        if len(self.measurement_buffer['sensor1']) == 0:
            raise ValueError("Недостаточно данных для усреднения датчика 1")
        
        avg_sensor1 = sum(self.measurement_buffer['sensor1']) / len(self.measurement_buffer['sensor1'])
        return round(avg_sensor1, 3)
    
    def write_calibration_result_1_center(self, distance: float):
        """Запись результата калибровки расстояния датчика 1 до центра в регистры 40016, 40017"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Конвертируем float в два 16-битных регистра
                low_word, high_word = self.float_to_doubleword(distance)
                
                # HMI читает: старший регистр из 40016, младший из 40017
                # Поэтому записываем в обратном порядке
                self.modbus_server.slave_context.setValues(3, 16, [int(high_word)])  # 40016 - старший регистр
                self.modbus_server.slave_context.setValues(3, 17, [int(low_word)])   # 40017 - младший регистр
                
                # Сохраняем в базу данных
                if self.db_integration:
                    self.db_integration.save_doubleword_register(
                        40016, 'holding', distance, 'Расстояние датчика 1 до центра'
                    )
                
                print(f" Результат датчика 1 записан в регистры 40016, 40017: {distance:.3f} мм (high: {int(high_word)}, low: {int(low_word)})")
        except Exception as e:
            print(f" Ошибка записи результата датчика 1: {e}")
    
    def float_to_doubleword(self, value: float) -> Tuple[int, int]:
        """Конвертация float в два 16-битных регистра"""
        packed = struct.pack('>f', value)  # Big-endian float
        high_word, low_word = struct.unpack('>HH', packed)
        return low_word, high_word  # Младшее слово первое
    
    def doubleword_to_float(self, low_word: int, high_word: int) -> float:
        """Конвертация двух 16-битных регистров в float"""
        packed = struct.pack('>HH', high_word, low_word)
        return struct.unpack('>f', packed)[0]
    
    def reset_command(self):
        """Сброс команды в регистр 40001 в 0"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                self.modbus_server.slave_context.setValues(3, 1, [0])  # 40001 = 0
                print(" Команда сброшена в 0")
        except Exception as e:
            print(f" Ошибка сброса команды: {e}")
    
    def handle_measure_wall_state(self):
        """Обработка измерения верхней стенки (CMD = 10) - ТОЛЬКО СБОР ДАННЫХ"""
        if not self.sensors:
            print(" Ошибка: датчики не подключены!")
            self.current_state = SystemState.ERROR
            return
        
        # Убрана проверка get_current_command() для ускорения
        # Команда меняется через handle_command(), который уже меняет current_state
        
        try:
            # Инициализация таймера частоты при первом измерении
            if self.frequency_start_time is None:
                self.frequency_start_time = time.time()
                self.last_frequency_display = self.frequency_start_time
            
            # Статус уже установлен в manage_measurement_cycle_flag
            # Просто продолжаем сбор данных
            
            # Выполняем QUAD измерение датчиков 1 и 2 (как в main.py)
            sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.sensors.perform_quad_sensor_measurement(
                self.sensor_range_mm, self.sensor_range_mm, self.sensor_range_mm, self.sensor_range_mm
            )
            
            # Увеличиваем счетчик измерений
            self.frequency_counter += 1
            
            # Выводим частоту каждую секунду
            current_time = time.time()
            if current_time - self.last_frequency_display >= 1.0:
                elapsed = current_time - self.frequency_start_time
                if elapsed > 0:
                    instant_freq = self.frequency_counter / elapsed
                    print(f" [CMD=10] Частота опроса: {instant_freq:.1f} Гц | Измерений: {self.frequency_counter}")
                self.last_frequency_display = current_time
            
            # Проверяем что получили данные от датчиков 1 и 2
            if sensor1_mm is not None and sensor2_mm is not None:
                # Добавляем в временные буферы для усреднения
                self.temp_sensor1_buffer.append(sensor1_mm)
                self.temp_sensor2_buffer.append(sensor2_mm)
                
                # Когда накопилось 10 измерений - усредняем и записываем
                if len(self.temp_sensor1_buffer) >= 10:
                    # Вычисляем средние значения
                    avg_sensor1 = sum(self.temp_sensor1_buffer) / len(self.temp_sensor1_buffer)
                    avg_sensor2 = sum(self.temp_sensor2_buffer) / len(self.temp_sensor2_buffer)
                    
                    # Добавляем усредненные значения в основные буферы
                    self.sensor1_measurements.append(avg_sensor1)
                    self.sensor2_measurements.append(avg_sensor2)
                    
                    # Используем кешированное расстояние (вместо чтения из Modbus)
                    distance_1_2 = self.cached_distance_1_2
                    
                    if distance_1_2 is not None:
                        # Вычисляем толщину стенки по формуле (используем усредненные значения)
                        wall_thickness = distance_1_2 - avg_sensor1 - avg_sensor2
                        self.wall_thickness_buffer.append(wall_thickness)
                        
                        # Выводим текущие значения каждые 100 усредненных измерений (уменьшена частота для ускорения)
                        if len(self.wall_thickness_buffer) % 100 == 0:
                            print(f" Усредненное измерение #{len(self.wall_thickness_buffer)}: "
                                  f"Датчик1={avg_sensor1:.3f}мм, Датчик2={avg_sensor2:.3f}мм, "
                                  f"Толщина={wall_thickness:.3f}мм")
                    else:
                        print(" Ошибка: не удалось прочитать калиброванное расстояние 1,2")
                    
                    # Очищаем временные буферы для следующих 10 измерений
                    self.temp_sensor1_buffer = []
                    self.temp_sensor2_buffer = []
            else:
                print(f" Ошибка измерения: датчик1={sensor1_mm}, датчик2={sensor2_mm}")
                
        except Exception as e:
            print(f" Ошибка измерения верхней стенки: {e}")
            self.current_state = SystemState.ERROR
    
    def read_calibrated_distance_1_2(self) -> float:
        """Чтение калиброванного расстояния между датчиками 1,2 из регистров 40010-40011"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Читаем два регистра (HMI: старший в 40010, младший в 40011)
                values = self.modbus_server.slave_context.getValues(3, 10, 2)  # 40010-40011
                if values and len(values) == 2:
                    high_word = int(values[0])  # 40010 - старший
                    low_word = int(values[1])   # 40011 - младший
                    distance = self.doubleword_to_float(low_word, high_word)
                    return distance
        except Exception as e:
            print(f" Ошибка чтения калиброванного расстояния 1,2: {e}")
        return None
    
    def read_calibrated_distance_to_center(self) -> float:
        """Чтение расстояния до центра из регистров 40016-40017"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Читаем два регистра (HMI: старший в 40016, младший в 40017)
                values = self.modbus_server.slave_context.getValues(3, 16, 2)  # 40016-40017
                if values and len(values) == 2:
                    high_word = int(values[0])  # 40016 - старший
                    low_word = int(values[1])   # 40017 - младший
                    distance = self.doubleword_to_float(low_word, high_word)
                    return distance
        except Exception as e:
            print(f" Ошибка чтения расстояния до центра: {e}")
        return None
    
    def read_calibrated_distance_1_3(self) -> float:
        """Чтение расстояния между датчиками 1,3 из регистров 40012-40013"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Читаем два регистра (HMI: старший в 40012, младший в 40013)
                values = self.modbus_server.slave_context.getValues(3, 12, 2)  # 40012-40013
                if values and len(values) == 2:
                    high_word = int(values[0])  # 40012 - старший
                    low_word = int(values[1])   # 40013 - младший
                    distance = self.doubleword_to_float(low_word, high_word)
                    return distance
        except Exception as e:
            print(f" Ошибка чтения расстояния 1,3: {e}")
        return None
    
    def read_calibrated_distance_sensor4(self) -> float:
        """Чтение расстояния датчика 4 из регистров 40014-40015"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Читаем два регистра (HMI: старший в 40014, младший в 40015)
                values = self.modbus_server.slave_context.getValues(3, 14, 2)  # 40014-40015
                if values and len(values) == 2:
                    high_word = int(values[0])  # 40014 - старший
                    low_word = int(values[1])   # 40015 - младший
                    distance = self.doubleword_to_float(low_word, high_word)
                    return distance
        except Exception as e:
            print(f" Ошибка чтения расстояния датчика 4: {e}")
        return None
    
    def process_wall_measurement_results(self):
        """Обработка результатов измерения стенки при переходе 10→11"""
        try:
            if len(self.wall_thickness_buffer) == 0:
                print(" Ошибка: нет данных измерений для обработки")
                return
            
            # Вычисляем статистику
            max_thickness = max(self.wall_thickness_buffer)
            min_thickness = min(self.wall_thickness_buffer)
            avg_thickness = sum(self.wall_thickness_buffer) / len(self.wall_thickness_buffer)
            
            print(f" Результаты измерения верхней стенки:")
            print(f"   Измерений: {len(self.wall_thickness_buffer)}")
            print(f"   Максимум: {max_thickness:.3f}мм")
            print(f"   Среднее:  {avg_thickness:.3f}мм")
            print(f"   Минимум:  {min_thickness:.3f}мм")
            
            # Записываем результаты в регистры
            self.write_wall_measurement_results(max_thickness, avg_thickness, min_thickness)
            
        except Exception as e:
            print(f" Ошибка обработки результатов измерения стенки: {e}")
    
    def write_wall_measurement_results(self, max_val: float, avg_val: float, min_val: float):
        """Запись результатов измерения стенки в регистры"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Максимальная толщина → 30016-30017
                self.write_stream_result_to_input_registers(max_val, 30016)
                
                # Средняя толщина → 30018-30019
                self.write_stream_result_to_input_registers(avg_val, 30018)
                
                # Минимальная толщина → 30020-30021
                self.write_stream_result_to_input_registers(min_val, 30020)
                
                print(f" Результаты записаны: макс={max_val:.3f}, сред={avg_val:.3f}, мин={min_val:.3f}")
                
        except Exception as e:
            print(f" Ошибка записи результатов измерения стенки: {e}")
    
    def process_flange_measurement_results(self):
        """Обработка результатов измерения фланца при переходе 11→12"""
        try:
            if (len(self.body_diameter_buffer) == 0 or len(self.flange_diameter_buffer) == 0 or
                len(self.flange_thickness_buffer) == 0 or len(self.bottom_thickness_buffer) == 0):
                print(" Ошибка: нет данных измерений фланца для обработки")
                return
            
            # ДИАГНОСТИКА: Показываем содержимое буферов (первые 5 и последние 5 значений)
            print(f"\n [БУФЕР ДИАМЕТР КОРПУСА] Размер: {len(self.body_diameter_buffer)}")
            print(f"   Первые 5: {[f'{x:.3f}' for x in list(self.body_diameter_buffer)[:5]]}")
            print(f"   Последние 5: {[f'{x:.3f}' for x in list(self.body_diameter_buffer)[-5:]]}")
            
            print(f"\n [БУФЕР ТОЛЩИНА ДНА] Размер: {len(self.bottom_thickness_buffer)}")
            print(f"   Первые 5: {[f'{x:.3f}' for x in list(self.bottom_thickness_buffer)[:5]]}")
            print(f"   Последние 5: {[f'{x:.3f}' for x in list(self.bottom_thickness_buffer)[-5:]]}\n")
            
            # Вычисляем статистику для диаметра корпуса
            max_body_diameter = max(self.body_diameter_buffer)
            min_body_diameter = min(self.body_diameter_buffer)
            avg_body_diameter = sum(self.body_diameter_buffer) / len(self.body_diameter_buffer)
            
            # Вычисляем статистику для диаметра фланца
            max_flange_diameter = max(self.flange_diameter_buffer)
            min_flange_diameter = min(self.flange_diameter_buffer)
            avg_flange_diameter = sum(self.flange_diameter_buffer) / len(self.flange_diameter_buffer)
            
            # Вычисляем статистику для толщины фланца
            max_flange_thickness = max(self.flange_thickness_buffer)
            min_flange_thickness = min(self.flange_thickness_buffer)
            avg_flange_thickness = sum(self.flange_thickness_buffer) / len(self.flange_thickness_buffer)
            
            # Вычисляем статистику для толщины дна
            max_bottom_thickness = max(self.bottom_thickness_buffer)
            min_bottom_thickness = min(self.bottom_thickness_buffer)
            avg_bottom_thickness = sum(self.bottom_thickness_buffer) / len(self.bottom_thickness_buffer)
            
            print(f" Результаты измерения фланца:")
            print(f"   Измерений: {len(self.body_diameter_buffer)}")
            print(f"   Диаметр корпуса: макс={max_body_diameter:.3f}мм, сред={avg_body_diameter:.3f}мм, мин={min_body_diameter:.3f}мм")
            print(f"   Диаметр фланца: макс={max_flange_diameter:.3f}мм, сред={avg_flange_diameter:.3f}мм, мин={min_flange_diameter:.3f}мм")
            print(f"   Толщина фланца: макс={max_flange_thickness:.3f}мм, сред={avg_flange_thickness:.3f}мм, мин={min_flange_thickness:.3f}мм")
            print(f"   Толщина дна: макс={max_bottom_thickness:.3f}мм, сред={avg_bottom_thickness:.3f}мм, мин={min_bottom_thickness:.3f}мм")
            
            # Записываем результаты в регистры
            self.write_flange_measurement_results(
                max_body_diameter, avg_body_diameter, min_body_diameter,
                max_flange_diameter, avg_flange_diameter, min_flange_diameter,
                max_flange_thickness, avg_flange_thickness, min_flange_thickness,
                max_bottom_thickness, avg_bottom_thickness, min_bottom_thickness
            )
            
        except Exception as e:
            print(f" Ошибка обработки результатов измерения фланца: {e}")
    
    def write_flange_measurement_results(self, 
                                       max_body_diameter: float, avg_body_diameter: float, min_body_diameter: float,
                                       max_flange_diameter: float, avg_flange_diameter: float, min_flange_diameter: float,
                                       max_flange_thickness: float, avg_flange_thickness: float, min_flange_thickness: float,
                                       max_bottom_thickness: float, avg_bottom_thickness: float, min_bottom_thickness: float):
        """Запись результатов измерения фланца в регистры"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Диаметр корпуса → 30046-30051
                self.write_stream_result_to_input_registers(max_body_diameter, 30046)   # Максимальное
                self.write_stream_result_to_input_registers(avg_body_diameter, 30048)   # Среднее
                self.write_stream_result_to_input_registers(min_body_diameter, 30050)   # Минимальное
                
                # Диаметр фланца → 30052-30057
                self.write_stream_result_to_input_registers(max_flange_diameter, 30052) # Максимальное
                self.write_stream_result_to_input_registers(avg_flange_diameter, 30054) # Среднее
                self.write_stream_result_to_input_registers(min_flange_diameter, 30056) # Минимальное
                
                # Толщина фланца → 30034-30039
                self.write_stream_result_to_input_registers(max_flange_thickness, 30034) # Максимальное
                self.write_stream_result_to_input_registers(avg_flange_thickness, 30036) # Среднее
                self.write_stream_result_to_input_registers(min_flange_thickness, 30038) # Минимальное
                
                # Толщина дна → 30028-30033
                self.write_stream_result_to_input_registers(max_bottom_thickness, 30028) # Максимальное
                self.write_stream_result_to_input_registers(avg_bottom_thickness, 30030) # Среднее
                self.write_stream_result_to_input_registers(min_bottom_thickness, 30032) # Минимальное
                
                print(f" Результаты фланца записаны в регистры")
                
        except Exception as e:
            print(f" Ошибка записи результатов измерения фланца: {e}")
    
    def process_bottom_wall_measurement_results(self):
        """Обработка результатов измерения нижней стенки при переходе 12→0"""
        try:
            if len(self.bottom_wall_thickness_buffer) == 0:
                print(" Ошибка: нет данных измерений нижней стенки для обработки")
                return
            
            # Вычисляем статистику для толщины нижней стенки
            max_bottom_wall_thickness = max(self.bottom_wall_thickness_buffer)
            min_bottom_wall_thickness = min(self.bottom_wall_thickness_buffer)
            avg_bottom_wall_thickness = sum(self.bottom_wall_thickness_buffer) / len(self.bottom_wall_thickness_buffer)
            
            print(f" Результаты измерения нижней стенки:")
            print(f"   Измерений: {len(self.bottom_wall_thickness_buffer)}")
            print(f"   Максимум: {max_bottom_wall_thickness:.3f}мм")
            print(f"   Среднее:  {avg_bottom_wall_thickness:.3f}мм")
            print(f"   Минимум:  {min_bottom_wall_thickness:.3f}мм")
            
            # Записываем результаты в регистры
            self.write_bottom_wall_measurement_results(max_bottom_wall_thickness, avg_bottom_wall_thickness, min_bottom_wall_thickness)
            
        except Exception as e:
            print(f" Ошибка обработки результатов измерения нижней стенки: {e}")
    
    def write_bottom_wall_measurement_results(self, max_val: float, avg_val: float, min_val: float):
        """Запись результатов измерения нижней стенки в регистры"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Максимальная толщина нижней стенки → 30022-30023
                self.write_stream_result_to_input_registers(max_val, 30022)
                
                # Средняя толщина нижней стенки → 30024-30025
                self.write_stream_result_to_input_registers(avg_val, 30024)
                
                # Минимальная толщина нижней стенки → 30026-30027
                self.write_stream_result_to_input_registers(min_val, 30026)
                
                print(f" Результаты нижней стенки записаны: макс={max_val:.3f}, сред={avg_val:.3f}, мин={min_val:.3f}")
                
        except Exception as e:
            print(f" Ошибка записи результатов измерения нижней стенки: {e}")
    
    def write_doubleword_to_input_registers(self, value: float, base_address: int):
        """Запись DoubleWord float в Input регистры"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Конвертируем в DoubleWord
                low_word, high_word = self.float_to_doubleword(value)
                
                # Вычисляем индексы (младший в base_address, старший в base_address+1)
                reg_index_low = base_address - 30000       # Младший регистр (base_address)
                reg_index_high = base_address - 30000 + 1  # Старший регистр (base_address+1)
                
                # Записываем (младший в первый регистр, старший во второй)
                self.modbus_server.slave_context.setValues(4, reg_index_low, [int(low_word)])
                self.modbus_server.slave_context.setValues(4, reg_index_high, [int(high_word)])
                
                print(f" Записано в {base_address}-{base_address+1}: {value:.3f}мм (low={low_word}, high={high_word})")
                
        except Exception as e:
            print(f" Ошибка записи DoubleWord в регистры {base_address}-{base_address+1}: {e}")
    
    def handle_measure_flange_state(self):
        """Обработка измерения фланца (CMD = 12) - ТОЛЬКО СБОР ДАННЫХ"""
        if not self.sensors:
            print(" Ошибка: датчики не подключены!")
            self.current_state = SystemState.ERROR
            return
        
        # Убрана проверка get_current_command() для ускорения
        # Команда меняется через handle_command(), который уже меняет current_state
        
        try:
            # Инициализация таймера частоты при первом измерении фланца
            if self.frequency_start_time is None:
                self.frequency_start_time = time.time()
                self.last_frequency_display = self.frequency_start_time
                self.frequency_counter = 0
            
            # Статус уже установлен в manage_measurement_cycle_flag
            # Просто продолжаем сбор данных
            
            # Выполняем QUAD измерение датчиков 1, 3 и 4 (как в main.py)
            sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.sensors.perform_quad_sensor_measurement(
                self.sensor_range_mm, self.sensor_range_mm, self.sensor_range_mm, self.sensor_range_mm
            )
            
            # Увеличиваем счетчик измерений
            self.frequency_counter += 1
            
            # Выводим частоту каждую секунду
            current_time = time.time()
            if current_time - self.last_frequency_display >= 1.0:
                elapsed = current_time - self.frequency_start_time
                if elapsed > 0:
                    instant_freq = self.frequency_counter / elapsed
                    print(f" [CMD=12] Частота опроса: {instant_freq:.1f} Гц | Измерений: {self.frequency_counter}")
                self.last_frequency_display = current_time
            
            # Проверяем что получили данные от датчиков 1, 3 и 4
            if (sensor1_mm is not None and sensor3_mm is not None and sensor4_mm is not None):
                # Добавляем в временные буферы для усреднения
                self.temp_sensor1_flange_buffer.append(sensor1_mm)
                self.temp_sensor3_buffer.append(sensor3_mm)
                self.temp_sensor4_buffer.append(sensor4_mm)
                
                # Когда накопилось 10 измерений - усредняем и записываем
                if len(self.temp_sensor1_flange_buffer) >= 10:
                    # Вычисляем средние значения
                    avg_sensor1 = sum(self.temp_sensor1_flange_buffer) / len(self.temp_sensor1_flange_buffer)
                    avg_sensor3 = sum(self.temp_sensor3_buffer) / len(self.temp_sensor3_buffer)
                    avg_sensor4 = sum(self.temp_sensor4_buffer) / len(self.temp_sensor4_buffer)
                    
                    # Добавляем усредненные значения в основные буферы
                    self.sensor1_flange_measurements.append(avg_sensor1)
                    self.sensor3_measurements.append(avg_sensor3)
                    self.sensor4_measurements.append(avg_sensor4)
                    
                    # Используем кешированные калиброванные значения
                    distance_to_center = self.cached_distance_to_center
                    distance_1_3 = self.cached_distance_1_3
                    distance_sensor4 = self.cached_distance_sensor4
                    
                    if (distance_to_center is not None and distance_1_3 is not None and 
                        distance_sensor4 is not None):
                        
                        # 1) Диаметр корпуса (Датчик 1)
                        body_diameter = distance_to_center - avg_sensor1
                        self.body_diameter_buffer.append(body_diameter)
                        
                        # 2) Диаметр фланца (Датчик 3)
                        flange_diameter = distance_to_center + distance_1_3 - avg_sensor3
                        self.flange_diameter_buffer.append(flange_diameter)
                        
                        # 3) Толщина фланца (Датчики 1,3)
                        flange_thickness = avg_sensor1 - avg_sensor3 + distance_1_3
                        self.flange_thickness_buffer.append(flange_thickness)
                        
                        # 4) Толщина дна (Датчик 4)
                        bottom_thickness = distance_sensor4 - avg_sensor4
                        self.bottom_thickness_buffer.append(bottom_thickness)
                        
                        # Выводим прогресс каждые 100 усредненных измерений
                        if len(self.body_diameter_buffer) % 100 == 0:
                            print(f" [CMD=12] Собрано: {len(self.body_diameter_buffer)} измерений")
                            print(f"   Диаметр корпуса={body_diameter:.3f}мм, Диаметр фланца={flange_diameter:.3f}мм")
                            print(f"   Толщина фланца={flange_thickness:.3f}мм, Толщина дна={bottom_thickness:.3f}мм")
                    else:
                        print(" Ошибка: не удалось прочитать калиброванные значения")
                    
                    # Очищаем временные буферы для следующих 10 измерений
                    self.temp_sensor1_flange_buffer = []
                    self.temp_sensor3_buffer = []
                    self.temp_sensor4_buffer = []
            else:
                print(f" Ошибка измерения: датчик1={sensor1_mm}, датчик3={sensor3_mm}, датчик4={sensor4_mm}")
                
        except Exception as e:
            print(f" Ошибка измерения фланца: {e}")
            self.current_state = SystemState.ERROR
    
    def handle_measure_bottom_state(self):
        """Обработка измерения нижней стенки (CMD = 14) - ТОЛЬКО СБОР ДАННЫХ"""
        if not self.sensors:
            print(" Ошибка: датчики не подключены!")
            self.current_state = SystemState.ERROR
            return
        
        # Убрана проверка get_current_command() для ускорения
        # Команда меняется через handle_command(), который уже меняет current_state
        
        try:
            # Инициализация таймера частоты при первом измерении дна
            if self.frequency_start_time is None:
                self.frequency_start_time = time.time()
                self.last_frequency_display = self.frequency_start_time
                self.frequency_counter = 0
            
            # Статус уже установлен в manage_measurement_cycle_flag
            # Просто продолжаем сбор данных
            
            # Выполняем QUAD измерение датчиков 1 и 2 (как в main.py)
            sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.sensors.perform_quad_sensor_measurement(
                self.sensor_range_mm, self.sensor_range_mm, self.sensor_range_mm, self.sensor_range_mm
            )
            
            # Увеличиваем счетчик измерений
            self.frequency_counter += 1
            
            # Выводим частоту каждую секунду
            current_time = time.time()
            if current_time - self.last_frequency_display >= 1.0:
                elapsed = current_time - self.frequency_start_time
                if elapsed > 0:
                    instant_freq = self.frequency_counter / elapsed
                    print(f" [CMD=14] Частота опроса: {instant_freq:.1f} Гц | Измерений: {self.frequency_counter}")
                self.last_frequency_display = current_time
            
            # Проверяем что получили данные от датчиков 1 и 2
            if sensor1_mm is not None and sensor2_mm is not None:
                # Добавляем в временные буферы для усреднения
                self.temp_sensor1_bottom_buffer.append(sensor1_mm)
                self.temp_sensor2_bottom_buffer.append(sensor2_mm)
                
                # Когда накопилось 10 измерений - усредняем и записываем
                if len(self.temp_sensor1_bottom_buffer) >= 10:
                    # Вычисляем средние значения
                    avg_sensor1 = sum(self.temp_sensor1_bottom_buffer) / len(self.temp_sensor1_bottom_buffer)
                    avg_sensor2 = sum(self.temp_sensor2_bottom_buffer) / len(self.temp_sensor2_bottom_buffer)
                    
                    # Добавляем усредненные значения в основные буферы
                    self.sensor1_bottom_measurements.append(avg_sensor1)
                    self.sensor2_bottom_measurements.append(avg_sensor2)
                    
                    # Используем кешированное расстояние (вместо чтения из Modbus)
                    distance_1_2 = self.cached_distance_1_2
                    
                    if distance_1_2 is not None:
                        # Вычисляем толщину нижней стенки по формуле (используем усредненные значения)
                        bottom_wall_thickness = distance_1_2 - avg_sensor1 - avg_sensor2
                        self.bottom_wall_thickness_buffer.append(bottom_wall_thickness)
                        
                        # Выводим текущие значения каждые 100 усредненных измерений (уменьшена частота для ускорения)
                        if len(self.bottom_wall_thickness_buffer) % 100 == 0:
                            print(f" Усредненное измерение #{len(self.bottom_wall_thickness_buffer)}: "
                                  f"Датчик1={avg_sensor1:.3f}мм, Датчик2={avg_sensor2:.3f}мм, "
                                  f"Толщина нижней стенки={bottom_wall_thickness:.3f}мм")
                    else:
                        print(" Ошибка: не удалось прочитать калиброванное расстояние 1,2")
                    
                    # Очищаем временные буферы для следующих 10 измерений
                    self.temp_sensor1_bottom_buffer = []
                    self.temp_sensor2_bottom_buffer = []
            else:
                print(f" Ошибка измерения: датчик1={sensor1_mm}, датчик2={sensor2_mm}")
                
        except Exception as e:
            print(f" Ошибка измерения нижней стенки: {e}")
            self.current_state = SystemState.ERROR
    
    # ===== НОВЫЕ МЕТОДЫ ДЛЯ РАЗДЕЛЁННОГО ЦИКЛА ИЗМЕРЕНИЯ =====
    
    def handle_measure_height_process_state(self):
        """
        CMD=9: Измерение высоты - поиск препятствия и сбор данных
        """
        if not self.sensors:
            print(" Ошибка: датчики не подключены!")
            self.current_state = SystemState.ERROR
            return
        
        try:
            # Инициализация таймера частоты при первом измерении
            if self.frequency_start_time is None:
                self.frequency_start_time = time.time()
                self.last_frequency_display = self.frequency_start_time
                self.frequency_counter = 0
                self.obstacle_detected = False
                self.obstacle_filter_count = 0
                self.height_measurements = []
                print(" [CMD=9] Начало поиска препятствия...")
            
            # Читаем только датчик 1 для поиска препятствия
            sensor1_mm, _, _, _ = self.sensors.perform_quad_sensor_measurement(
                self.sensor_range_mm, self.sensor_range_mm, self.sensor_range_mm, self.sensor_range_mm
            )
            
            # Увеличиваем счетчик измерений
            self.frequency_counter += 1
            
            # Выводим частоту каждую секунду
            current_time = time.time()
            if current_time - self.last_frequency_display >= 1.0:
                elapsed = current_time - self.frequency_start_time
                if elapsed > 0:
                    instant_freq = self.frequency_counter / elapsed
                    status = "Поиск препятствия" if not self.obstacle_detected else "Сбор данных высоты"
                    print(f" [CMD=9] {status}: {instant_freq:.1f} Гц | Измерений: {self.frequency_counter}")
                self.last_frequency_display = current_time
            
            # ПРАВИЛЬНАЯ ЛОГИКА: 
            # Пока датчик = 0 → ищем препятствие
            # Как только датчик != 0 (5 раз подряд) → нашли препятствие, считаем высоту
            
            if sensor1_mm is not None and sensor1_mm > 0.0:
                # Датчик показывает ненулевое значение - есть препятствие!
                self.obstacle_filter_count += 1
                
                if not self.obstacle_detected and self.obstacle_filter_count >= 5:
                    # 5 ненулевых показаний подряд - препятствие подтверждено!
                    self.obstacle_detected = True
                    self.write_cycle_flag(90)  # Статус: препятствие найдено
                    print(f" [CMD=9] Препятствие найдено! Датчик 1 = {sensor1_mm:.3f}мм (5 показаний подряд)")
                
                if self.obstacle_detected:
                    # Собираем данные для расчета высоты
                    self.collect_height_data()
            else:
                # Датчик = 0 или None → сбрасываем счетчик, продолжаем поиск
                self.obstacle_filter_count = 0
                
        except Exception as e:
            print(f" Ошибка измерения высоты: {e}")
            self.current_state = SystemState.ERROR
    
    def collect_height_data(self):
        """Сбор данных для расчета высоты"""
        try:
            # Читаем регистры как в командах 103 и 104
            steps = self.read_register_40020()  # Количество шагов (40052-40053)
            pulses_per_mm = self.read_register_40021()  # Импульсов на 1 мм (40054)
            distance_to_plane = self.read_register_40022_40023()  # Дистанция до плоскости (40055-40056)
            
            # Проверяем что все значения валидны (не None и не 0 для pulses_per_mm)
            if steps is not None and pulses_per_mm is not None and pulses_per_mm > 0 and distance_to_plane is not None:
                # Рассчитываем высоту по формуле: height = distance_to_plane - (steps/pulses)
                height = distance_to_plane - (steps / pulses_per_mm)
                self.height_measurements.append(height)
                
                # Выводим прогресс каждые 10 измерений с отладочной информацией
                if len(self.height_measurements) % 10 == 0:
                    # Читаем сырые данные шагов для отладки
                    try:
                        raw_steps_values = self.modbus_server.slave_context.getValues(3, 52, 2)
                        raw_steps_str = f"сырые [52-53]: {raw_steps_values}" if raw_steps_values else "None"
                    except:
                        raw_steps_str = "Ошибка чтения"
                    
                    print(f" [CMD=9] Собрано: {len(self.height_measurements)} | "
                          f"Шаги: {raw_steps_str} → {steps}, Имп/мм={pulses_per_mm}, Дист={distance_to_plane:.3f}мм → Высота={height:.3f}мм")
                
                # Если собрали достаточно данных (например, 50 измерений) И флаг не установлен
                if len(self.height_measurements) >= 50 and not self.height_calculated:
                    self.calculate_and_save_height()
                    
        except Exception as e:
            # Не выводим ошибку если просто нулевые значения
            if "division by zero" not in str(e):
                print(f" [CMD=9] Ошибка сбора данных высоты: {e}")
    
    def calculate_and_save_height(self):
        """Расчет и сохранение результатов измерения высоты"""
        try:
            if len(self.height_measurements) == 0:
                print(" Ошибка: нет данных для расчета высоты")
                return
            
            # Вычисляем статистику
            max_height = max(self.height_measurements)
            min_height = min(self.height_measurements)
            avg_height = sum(self.height_measurements) / len(self.height_measurements)
            
            # Записываем результаты в регистры
            self.write_height_measurement_results(max_height, avg_height, min_height)
            
            # Устанавливаем статус завершения
            self.write_cycle_flag(91)  # Статус: высота рассчитана
            self.height_calculated = True
            
            # Выводим формулу расчета высоты
            print(f" [CMD=9] ФОРМУЛА: height = distance_to_plane - (steps / pulses_per_mm)")
            print(f" [CMD=9] Где: distance_to_plane={self.read_register_40022_40023():.3f}мм (40055-40056)")
            print(f" [CMD=9]       steps={self.read_register_40020()} (40052-40053)")
            print(f" [CMD=9]       pulses_per_mm={self.read_register_40021()} (40054)")
            print(f" [CMD=9] Высота рассчитана: макс={max_height:.3f}мм, "
                  f"сред={avg_height:.3f}мм, мин={min_height:.3f}мм")
            print(f" [CMD=9] Готов к следующей команде (CMD=10)")
            
        except Exception as e:
            print(f" Ошибка расчета высоты: {e}")
    
    def read_register_40020(self) -> int:
        """Чтение регистров 40052-40053 (количество шагов - DoubleWord integer)"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                values = self.modbus_server.slave_context.getValues(3, 52, 2)  # 40052-40053
                if values and len(values) == 2:
                    # Правильное объединение 32-bit integer: старшее слово + младшее слово
                    steps = (int(values[0]) << 16) | int(values[1])
                    return steps
        except Exception as e:
            print(f" Ошибка чтения регистров 40052-40053: {e}")
        return None
    
    def read_register_40021(self) -> int:
        """Чтение регистра 40054 (импульсов на 1 мм)"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                value = self.modbus_server.slave_context.getValues(3, 54, 1)[0]  # 40054
                return int(value)
        except Exception as e:
            print(f" Ошибка чтения регистра 40054: {e}")
        return None
    
    def read_register_40022_40023(self) -> float:
        """Чтение регистров 40055-40056 (дистанция до плоскости)"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                values = self.modbus_server.slave_context.getValues(3, 55, 2)  # 40055-40056
                if values and len(values) == 2:
                    high_word = int(values[0])  # 40055 - старший
                    low_word = int(values[1])   # 40056 - младший
                    distance = self.doubleword_to_float(low_word, high_word)
                    return distance
        except Exception as e:
            print(f" Ошибка чтения регистров 40055-40056: {e}")
        return None
    
    def read_measured_height(self) -> float:
        """Чтение регистров 40057-40058 (измеренная высота заготовки)"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                values = self.modbus_server.slave_context.getValues(3, 57, 2)  # 40057-40058
                if values and len(values) == 2:
                    high_word = int(values[0])  # 40057 - старший
                    low_word = int(values[1])   # 40058 - младший
                    height = self.doubleword_to_float(low_word, high_word)
                    return height
        except Exception as e:
            print(f" Ошибка чтения регистров 40057-40058: {e}")
        return None
    
    def write_height_measurement_results(self, max_val: float, avg_val: float, min_val: float):
        """Запись результатов измерения высоты в регистры"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Максимальная высота → 30040-30041
                self.write_stream_result_to_input_registers(max_val, 30040)
                
                # Средняя высота → 30042-30043
                self.write_stream_result_to_input_registers(avg_val, 30042)
                
                # Минимальная высота → 30044-30045
                self.write_stream_result_to_input_registers(min_val, 30044)
                
                print(f" Результаты высоты записаны: макс={max_val:.3f}, сред={avg_val:.3f}, мин={min_val:.3f}")
                
        except Exception as e:
            print(f" Ошибка записи результатов измерения высоты: {e}")
    
    def handle_measure_wall_process_state(self):
        """
        CMD=10: Сбор данных измерения верхней стенки
        Просто собираем данные, не делаем подсчёт
        """
        # Загружаем калиброванное расстояние в кеш (один раз при входе в состояние)
        if self.cached_distance_1_2 is None:
            self.cached_distance_1_2 = self.read_calibrated_distance_1_2()
            print(f" Загружено расстояние 1-2: {self.cached_distance_1_2:.3f}мм")
        
        # Перенаправляем на существующий метод (он уже только собирает данные)
        self.handle_measure_wall_state()
    
    def handle_calculate_wall_state(self):
        """
        CMD=11: Подсчёт результатов верхней стенки
        """
        try:
            # Выполняем подсчёт только один раз
            if not self.wall_calculated:
                print(" [CMD=11] Подсчёт результатов верхней стенки...")
                
                # Вызываем существующий метод подсчёта
                self.process_wall_measurement_results()
                
                # Устанавливаем статус "готово к следующей команде"
                self.write_cycle_flag(110)
                print(" [STATUS=110] Подсчёт завершён, готов к CMD=12")
                
                # Отмечаем что расчёт выполнен
                self.wall_calculated = True
            
        except Exception as e:
            print(f" Ошибка подсчёта результатов стенки: {e}")
            self.current_state = SystemState.ERROR
    
    def handle_measure_flange_process_state(self):
        """
        CMD=12: Сбор данных измерения фланца
        Просто собираем данные, не делаем подсчёт
        """
        # Загружаем калиброванные расстояния в кеш (один раз при входе в состояние)
        if self.cached_distance_to_center is None:
            self.cached_distance_to_center = self.read_calibrated_distance_to_center()
            self.cached_distance_1_3 = self.read_calibrated_distance_1_3()
            self.cached_distance_sensor4 = self.read_calibrated_distance_sensor4()
            print(f" Загружены расстояния: центр={self.cached_distance_to_center:.3f}мм, "
                  f"1-3={self.cached_distance_1_3:.3f}мм, sensor4={self.cached_distance_sensor4:.3f}мм")
        
        # Перенаправляем на существующий метод
        self.handle_measure_flange_state()
    
    def handle_calculate_flange_state(self):
        """
        CMD=13: Подсчёт результатов фланца
        """
        try:
            # Выполняем подсчёт только один раз
            if not self.flange_calculated:
                print(" [CMD=13] Подсчёт результатов фланца...")
                
                # Вызываем существующий метод подсчёта
                self.process_flange_measurement_results()
                
                # Устанавливаем статус "готово к следующей команде"
                self.write_cycle_flag(112)
                print(" [STATUS=112] Подсчёт завершён, готов к CMD=14")
                
                # Отмечаем что расчёт выполнен
                self.flange_calculated = True
            
        except Exception as e:
            print(f" Ошибка подсчёта результатов фланца: {e}")
            self.current_state = SystemState.ERROR
    
    def handle_measure_bottom_process_state(self):
        """
        CMD=14: Сбор данных измерения нижней стенки
        Просто собираем данные, не делаем подсчёт
        """
        # Загружаем калиброванное расстояние в кеш (один раз при входе в состояние)
        # Используем тот же кеш что и для CMD=10, т.к. это то же расстояние 1-2
        if self.cached_distance_1_2 is None:
            self.cached_distance_1_2 = self.read_calibrated_distance_1_2()
            print(f" Загружено расстояние 1-2: {self.cached_distance_1_2:.3f}мм")
        
        # Перенаправляем на существующий метод
        self.handle_measure_bottom_state()
    
    def handle_calculate_bottom_state(self):
        """
        CMD=15: Подсчёт результатов нижней стенки
        """
        try:
            # Выполняем подсчёт только один раз
            if not self.bottom_calculated:
                print(" [CMD=15] Подсчёт результатов нижней стенки...")
                
                # Вызываем существующий метод подсчёта
                self.process_bottom_wall_measurement_results()
                
                # Устанавливаем статус "готово к следующей команде"
                self.write_cycle_flag(114)
                print(" [STATUS=114] Подсчёт завершён, готов к CMD=16")
                
                # Отмечаем что расчёт выполнен
                self.bottom_calculated = True
            
        except Exception as e:
            print(f" Ошибка подсчёта результатов нижней стенки: {e}")
            self.current_state = SystemState.ERROR
    
    def handle_quality_evaluation_state(self):
        """
        CMD=16: Оценка качества изделия
        """
        try:
            # Выполняем оценку только один раз
            if not self.quality_evaluated:
                print(" [CMD=16] Оценка качества изделия...")
                
                # Читаем измеренную высоту заготовки
                measured_height = self.read_measured_height()
                if measured_height is not None:
                    print(f" [CMD=16] Измеренная высота заготовки (40057-40058): {measured_height:.3f}мм")
                else:
                    print(f" [CMD=16] ОШИБКА: Не удалось прочитать измеренную высоту заготовки!")
                
                # Оценка качества изделия
                quality_result = self.evaluate_product_quality()
                
                # Обновление счётчиков изделий
                self.update_product_counters(quality_result)
                
                # Увеличиваем номер изделия
                self.increment_product_number()
                
                # Устанавливаем статус "готово к завершению"
                self.write_cycle_flag(116)
                print(f" [STATUS=116] Оценка завершена ({quality_result}), готов к CMD=0")
                
                # Отмечаем что оценка выполнена
                self.quality_evaluated = True
            
        except Exception as e:
            print(f" Ошибка оценки качества: {e}")
            self.current_state = SystemState.ERROR
    
    # ===== КОНЕЦ НОВЫХ МЕТОДОВ =====
    
    def handle_stream_sensor1_state(self):
        """Потоковый режим датчика 1 (CMD=200)"""
        if not self.sensors:
            print(" Ошибка: датчики не подключены для датчика 1!")
            self.current_state = SystemState.ERROR
            return
        
        # Проверяем смену команды
        current_cmd = self.get_current_command()
        if current_cmd != 200:
            print(f" Команда изменилась с 200 на {current_cmd}. Выходим из потокового режима датчика 1")
            self.handle_command(current_cmd)
            return
        
        try:
            # Запускаем потоковый режим если еще не запущен
            if not self.stream_active_sensor1:
                if self.sensors.start_stream_mode(1):
                    self.stream_active_sensor1 = True
                    self.stream_measurement_count = 0
                    self.stream_start_time = time.time()
                    self.stream_measurements_buffer = []  # Очищаем буфер
                    print(" Запущен потоковый режим для датчика 1")
                else:
                    print(" Ошибка запуска потокового режима для датчика 1")
                    self.current_state = SystemState.ERROR
                    return
            
            # Читаем одно измерение из потока (как в main.py)
            measurement = self.sensors.read_stream_data(self.sensor_range_mm)
            self.stream_measurement_count += 1
            
            # Если получили валидное измерение, добавляем в буфер для усреднения
            if measurement is not None:
                # НЕ ОКРУГЛЯЕМ - сохраняем максимальную точность!
                self.stream_measurements_buffer.append(measurement)
                
                # ОТЛАДКА: показываем размер буфера каждые 50 измерений
                if len(self.stream_measurements_buffer) % 50 == 0:
                    print(f" ОТЛАДКА: В буфере {len(self.stream_measurements_buffer)} измерений, последнее: {measurement:.6f}мм")
                
                # Если накопилось >100 измерений - усредняем и отправляем в регистры!
                if len(self.stream_measurements_buffer) > 100:
                    avg_measurement = sum(self.stream_measurements_buffer) / len(self.stream_measurements_buffer)
                    # НЕ ОКРУГЛЯЕМ СРЕДНЕЕ - максимальная точность!
                    
                    print(f" УСРЕДНЕНИЕ: {len(self.stream_measurements_buffer)} измерений -> {avg_measurement:.6f}мм")
                    
                    # Отправляем в Input Register 30001-30002 (по аналогии с калибровкой)
                    try:
                        self.write_stream_result_to_input_registers(avg_measurement, 30001)
                        print(f" ЗАПИСЬ В РЕГИСТРЫ: {avg_measurement:.6f}мм -> 30001-30002 УСПЕШНО")
                    except Exception as e:
                        print(f" ОШИБКА ЗАПИСИ В РЕГИСТРЫ: {e}")
                    
                    # Выводим результат
                    current_time = time.time()
                    elapsed = current_time - self.stream_start_time
                    frequency = self.stream_measurement_count / elapsed if elapsed > 0 else 0
                    valid_count = len(self.stream_measurements_buffer)
                    
                    print(f" Поток датчика 1: {elapsed:5.1f}с | Измерений: {self.stream_measurement_count:6d} | "
                          f"Частота: {frequency:7.1f} Гц | Усреднено {valid_count} измерений | "
                          f"Среднее: {avg_measurement:.6f}мм -> Регистры 30001-30002")
                    
                    # Очищаем буфер для следующих 100 измерений
                    self.stream_measurements_buffer = []
                    print(f" БУФЕР ОЧИЩЕН, размер: {len(self.stream_measurements_buffer)}")
            else:
                # ОТЛАДКА: показываем когда измерение None
                if self.stream_measurement_count % 1000 == 0:
                    print(f" ОТЛАДКА: Измерение #{self.stream_measurement_count} = None")
                
            # Диагностика раз в секунду (без усреднения)
            current_time = time.time()
            if hasattr(self, '_last_stream_print_1'):
                if current_time - self._last_stream_print_1 > 1.0:  # Раз в секунду
                    elapsed = current_time - self.stream_start_time
                    frequency = self.stream_measurement_count / elapsed if elapsed > 0 else 0
                    buffer_size = len(self.stream_measurements_buffer)
                    
                    print(f" Диагностика: {elapsed:5.1f}с | Измерений: {self.stream_measurement_count:6d} | "
                          f"Частота: {frequency:7.1f} Гц | В буфере: {buffer_size}")
                    
                    self._last_stream_print_1 = current_time
            else:
                self._last_stream_print_1 = current_time
            
        except Exception as e:
            print(f" Ошибка потокового режима датчика 1: {e}")
            # Останавливаем потоковый режим при ошибке
            if self.stream_active_sensor1:
                self.sensors.stop_stream_mode(1)
                self.stream_active_sensor1 = False
            self.current_state = SystemState.ERROR
    
    def handle_stream_sensor2_state(self):
        """Потоковый режим датчика 2 (CMD=201)"""
        if not self.sensors:
            print(" Ошибка: датчики не подключены для датчика 2!")
            self.current_state = SystemState.ERROR
            return
        
        # Проверяем смену команды
        current_cmd = self.get_current_command()
        if current_cmd != 201:
            print(f" Команда изменилась с 201 на {current_cmd}. Выходим из потокового режима датчика 2")
            self.handle_command(current_cmd)
            return
        
        try:
            # Запускаем потоковый режим если еще не запущен
            if not self.stream_active_sensor2:
                if self.sensors.start_stream_mode(2):
                    self.stream_active_sensor2 = True
                    self.stream_measurement_count = 0
                    self.stream_start_time = time.time()
                    self.stream_measurements_buffer = []
                    print(" Запущен потоковый режим для датчика 2")
                else:
                    print(" Ошибка запуска потокового режима для датчика 2")
                    self.current_state = SystemState.ERROR
                    return
            
            # Читаем одно измерение из потока
            measurement = self.sensors.read_stream_data(self.sensor_range_mm)
            self.stream_measurement_count += 1
            
            # Если получили валидное измерение, добавляем в буфер
            if measurement is not None:
                self.stream_measurements_buffer.append(measurement)
                
                # Если накопилось >100 измерений - усредняем и отправляем в регистры!
                if len(self.stream_measurements_buffer) > 100:
                    avg_measurement = sum(self.stream_measurements_buffer) / len(self.stream_measurements_buffer)
                    
                    # Отправляем в Input Register 30003-30004
                    self.write_stream_result_to_input_registers(avg_measurement, 30003)
                    
                    # Выводим результат
                    current_time = time.time()
                    elapsed = current_time - self.stream_start_time
                    frequency = self.stream_measurement_count / elapsed if elapsed > 0 else 0
                    valid_count = len(self.stream_measurements_buffer)
                    
                    print(f" Поток датчика 2: {elapsed:5.1f}с | Измерений: {self.stream_measurement_count:6d} | "
                          f"Частота: {frequency:7.1f} Гц | Усреднено {valid_count} измерений | "
                          f"Среднее: {avg_measurement:.3f}мм")
                    
                    # Очищаем буфер для следующих 100 измерений
                    self.stream_measurements_buffer = []
                
        except Exception as e:
            print(f" Ошибка потокового режима датчика 2: {e}")
            if self.stream_active_sensor2:
                self.sensors.stop_stream_mode(2)
                self.stream_active_sensor2 = False
            self.current_state = SystemState.ERROR
    
    def handle_stream_sensor3_state(self):
        """Потоковый режим датчика 3 (CMD=202)"""
        if not self.sensors:
            print(" Ошибка: датчики не подключены для датчика 3!")
            self.current_state = SystemState.ERROR
            return
        
        # Проверяем смену команды
        current_cmd = self.get_current_command()
        if current_cmd != 202:
            print(f" Команда изменилась с 202 на {current_cmd}. Выходим из потокового режима датчика 3")
            self.handle_command(current_cmd)
            return
        
        try:
            # Запускаем потоковый режим если еще не запущен
            if not self.stream_active_sensor3:
                if self.sensors.start_stream_mode(3):
                    self.stream_active_sensor3 = True
                    self.stream_measurement_count = 0
                    self.stream_start_time = time.time()
                    self.stream_measurements_buffer = []
                    print(" Запущен потоковый режим для датчика 3")
                else:
                    print(" Ошибка запуска потокового режима для датчика 3")
                    self.current_state = SystemState.ERROR
                    return
            
            # Читаем одно измерение из потока
            measurement = self.sensors.read_stream_data(self.sensor_range_mm)
            self.stream_measurement_count += 1
            
            # Если получили валидное измерение, добавляем в буфер
            if measurement is not None:
                self.stream_measurements_buffer.append(measurement)
                
                # Если накопилось >100 измерений - усредняем и отправляем в регистры!
                if len(self.stream_measurements_buffer) > 100:
                    avg_measurement = sum(self.stream_measurements_buffer) / len(self.stream_measurements_buffer)
                    
                    # Отправляем в Input Register 30005-30006
                    self.write_stream_result_to_input_registers(avg_measurement, 30005)
                    
                    # Выводим результат
                    current_time = time.time()
                    elapsed = current_time - self.stream_start_time
                    frequency = self.stream_measurement_count / elapsed if elapsed > 0 else 0
                    valid_count = len(self.stream_measurements_buffer)
                    
                    print(f" Поток датчика 3: {elapsed:5.1f}с | Измерений: {self.stream_measurement_count:6d} | "
                          f"Частота: {frequency:7.1f} Гц | Усреднено {valid_count} измерений | "
                          f"Среднее: {avg_measurement:.3f}мм")
                    
                    # Очищаем буфер для следующих 100 измерений
                    self.stream_measurements_buffer = []
                
        except Exception as e:
            print(f" Ошибка потокового режима датчика 3: {e}")
            if self.stream_active_sensor3:
                self.sensors.stop_stream_mode(3)
                self.stream_active_sensor3 = False
            self.current_state = SystemState.ERROR
    
    def handle_stream_sensor4_state(self):
        """Потоковый режим датчика 4 (CMD=203)"""
        if not self.sensors:
            print(" Ошибка: датчики не подключены для датчика 4!")
            self.current_state = SystemState.ERROR
            return
        
        # Проверяем смену команды
        current_cmd = self.get_current_command()
        if current_cmd != 203:
            print(f" Команда изменилась с 203 на {current_cmd}. Выходим из потокового режима датчика 4")
            self.handle_command(current_cmd)
            return
        
        try:
            # Запускаем потоковый режим если еще не запущен
            if not self.stream_active_sensor4:
                if self.sensors.start_stream_mode(4):
                    self.stream_active_sensor4 = True
                    self.stream_measurement_count = 0
                    self.stream_start_time = time.time()
                    self.stream_measurements_buffer = []
                    print(" Запущен потоковый режим для датчика 4")
                else:
                    print(" Ошибка запуска потокового режима для датчика 4")
                    self.current_state = SystemState.ERROR
                    return
            
            # Читаем одно измерение из потока
            measurement = self.sensors.read_stream_data(self.sensor_range_mm)
            self.stream_measurement_count += 1
            
            # Если получили валидное измерение, добавляем в буфер
            if measurement is not None:
                self.stream_measurements_buffer.append(measurement)
                
                # Если накопилось >100 измерений - усредняем и отправляем в регистры!
                if len(self.stream_measurements_buffer) > 100:
                    avg_measurement = sum(self.stream_measurements_buffer) / len(self.stream_measurements_buffer)
                    
                    # Отправляем в Input Register 30007-30008
                    self.write_stream_result_to_input_registers(avg_measurement, 30007)
                    
                    # Выводим результат
                    current_time = time.time()
                    elapsed = current_time - self.stream_start_time
                    frequency = self.stream_measurement_count / elapsed if elapsed > 0 else 0
                    valid_count = len(self.stream_measurements_buffer)
                    
                    print(f" Поток датчика 4: {elapsed:5.1f}с | Измерений: {self.stream_measurement_count:6d} | "
                          f"Частота: {frequency:7.1f} Гц | Усреднено {valid_count} измерений | "
                          f"Среднее: {avg_measurement:.3f}мм")
                    
                    # Очищаем буфер для следующих 100 измерений
                    self.stream_measurements_buffer = []
                
        except Exception as e:
            print(f" Ошибка потокового режима датчика 4: {e}")
            if self.stream_active_sensor4:
                self.sensors.stop_stream_mode(4)
                self.stream_active_sensor4 = False
            self.current_state = SystemState.ERROR
    
    
    def write_stream_result_to_input_registers(self, value: float, base_address: int):
        """Запись результата потокового измерения в Input регистры"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Конвертируем float в два 16-битных регистра
                low_word, high_word = self.float_to_doubleword(value)
                
                # Вычисляем индексы регистров
                reg_index_high = base_address - 30000      # Старший регистр
                reg_index_low = base_address - 30000 + 1   # Младший регистр
                
                # Записываем в Input регистры (функция 4)
                self.modbus_server.slave_context.setValues(4, reg_index_high, [int(high_word)])  # Старший
                self.modbus_server.slave_context.setValues(4, reg_index_low, [int(low_word)])    # Младший
                
        except Exception as e:
            print(f" ОШИБКА ЗАПИСИ В INPUT РЕГИСТРЫ {base_address}-{base_address+1}: {e}")
            import traceback
            traceback.print_exc()
    
    def handle_error_state(self):
        """Обработка состояния ошибки"""
        print(" Состояние ошибки. Проверьте систему.")


def main():
    """Главная функция"""
    print("СИСТЕМА ЛАЗЕРНОЙ ГЕОМЕТРИИ")
    print("Интеграция датчиков РФ602 + Modbus + Автомат состояний")
    print("=" * 60)
    
    # Настройки системы
    PORT = 'COM11'  # Попробуйте другой порт если COM7 занят
    BAUDRATE = 921600
    MODBUS_PORT = 502
    ENABLE_DEBUG_GUI = True  # Включить GUI для отладки Modbus
    TEST_MODE = False  # Режим с реальными датчиками
    
    # Создание и запуск системы
    system = LaserGeometrySystem(PORT, BAUDRATE, MODBUS_PORT, test_mode=TEST_MODE, enable_debug_gui=ENABLE_DEBUG_GUI)
    
    try:
        system.start_system()
    except KeyboardInterrupt:
        print("\n Остановка по запросу пользователя")
    except Exception as e:
        print(f"Критическая ошибка: {e}")
    finally:
        try:
            system.stop_system()
        except:
            pass
        # Финальная очистка оптимизаций
        cleanup_laser_system_optimizations()


if __name__ == "__main__":
    main()
 