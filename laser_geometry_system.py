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
import math
import contextlib
import fcntl
import sys
import shutil
from datetime import datetime
from queue import Queue, Empty
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
import sys

try:
    from main import HighSpeedRiftekSensor, apply_system_optimizations
except ImportError:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    from main import HighSpeedRiftekSensor, apply_system_optimizations
from modbus_slave_server import ModbusSlaveServer
from modbus_database_integration import ModbusDatabaseIntegration

try:
    from docx import Document
    from docx.shared import Pt
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False


def apply_laser_system_optimizations():
    """
    Применение системных оптимизаций для лазерной системы (без pyftdi)
    """
    print("[SYSTEM] ПРИМЕНЕНИЕ СИСТЕМНЫХ ОПТИМИЗАЦИЙ...")
    
    # 1. Высокий приоритет процесса (REALTIME_PRIORITY_CLASS для максимальной производительности)
    if HAS_PSUTIL:
        try:
            p = psutil.Process(os.getpid())
            # Используем REALTIME_PRIORITY_CLASS для максимальной производительности
            # Это критично для стабильной работы на слабых процессорах
            try:
                p.nice(psutil.REALTIME_PRIORITY_CLASS)
                print("[OK] Установлен приоритет REALTIME (максимальный)")
            except:
                # Если REALTIME недоступен, используем HIGH_PRIORITY
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
    CALIBRATE_FLANGE_DIAMETER = "CALIBRATE_FLANGE_DIAMETER"
    CALIBRATE_BODY_DIAMETER_SEPARATE = "CALIBRATE_BODY_DIAMETER_SEPARATE"  # CMD=107
    CALIBRATE_BODY2_DIAMETER = "CALIBRATE_BODY2_DIAMETER"  # CMD=108
    DEBUG_REGISTERS = "DEBUG_REGISTERS"
    CONFIGURE_SENSOR3_RANGE = "CONFIGURE_SENSOR3_RANGE"  # CMD=106: настройка диапазонов датчика 3
    
    # Измерение высоты
    MEASURE_HEIGHT_PROCESS = "MEASURE_HEIGHT_PROCESS"      # CMD=9: поиск препятствия и сбор данных
    
    # Основной цикл измерения - верхняя стенка
    MEASURE_WALL_PROCESS = "MEASURE_WALL_PROCESS"      # CMD=10: сбор данных
    MEASURE_WALL_CALCULATE = "MEASURE_WALL_CALCULATE"  # CMD=11: подсчёт результатов
    
    # Основной цикл измерения - фланец
    MEASURE_FLANGE_PROCESS = "MEASURE_FLANGE_PROCESS"      # CMD=12: сбор данных
    MEASURE_FLANGE_CALCULATE = "MEASURE_FLANGE_CALCULATE"  # CMD=13: подсчёт результатов
    MEASURE_FLANGE_ONLY_PROCESS = "MEASURE_FLANGE_ONLY_PROCESS"      # CMD=20
    MEASURE_FLANGE_ONLY_CALCULATE = "MEASURE_FLANGE_ONLY_CALCULATE"  # CMD=21
    MEASURE_BODY_ONLY_PROCESS = "MEASURE_BODY_ONLY_PROCESS"      # CMD=30
    MEASURE_BODY_ONLY_CALCULATE = "MEASURE_BODY_ONLY_CALCULATE"  # CMD=31
    MEASURE_BODY2_PROCESS = "MEASURE_BODY2_PROCESS"      # CMD=40
    MEASURE_BODY2_CALCULATE = "MEASURE_BODY2_CALCULATE"  # CMD=41
    
    # Основной цикл измерения - нижняя стенка
    MEASURE_BOTTOM_PROCESS = "MEASURE_BOTTOM_PROCESS"      # CMD=14: сбор данных
    MEASURE_BOTTOM_CALCULATE = "MEASURE_BOTTOM_CALCULATE"  # CMD=15: подсчёт результатов
    
    # Оценка качества изделия
    QUALITY_EVALUATION = "QUALITY_EVALUATION"  # CMD=16: оценка качества
    
    # Потоковый режим (QUAD - все 4 датчика)
    STREAM_QUAD = "STREAM_QUAD"  # CMD=200: QUAD режим всех датчиков
    
    ERROR = "ERROR"


class LaserGeometrySystem:
    """Основная система лазерной геометрии"""
    
    def __init__(self, port: str = '/dev/ttyUSB0', baudrate: int = 921600, modbus_port: int = 502, 
                 test_mode: bool = False):
        """
        Инициализация системы
        
        Args:
            port: COM порт для датчиков
            baudrate: Скорость передачи данных
            modbus_port: Порт Modbus сервера
            test_mode: Режим тестирования без реальных датчиков
        """
        # Настройки датчиков
        self.port = port
        self.baudrate = baudrate
        self.test_mode = test_mode
        
        # Компоненты системы
        self.sensors = None
        self.modbus_server = None
        self.db_integration = None
        self.data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        try:
            os.makedirs(self.data_dir, exist_ok=True)
        except Exception as e:
            print(f" [REPORT] Не удалось создать папку для отчётов {self.data_dir}: {e}")
        
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
        self.stream_active_quad = False  # Флаг активного QUAD режима
        
        # Потоки для разделения чтения датчиков и обработки данных
        self.sensor_reading_thread = None
        self.sensor_data_queue = Queue(maxsize=1000)  # Очередь для передачи данных от датчиков
        self.sensor_reading_active = False  # Флаг активности потока чтения датчиков
        self.sensor_reading_lock = threading.Lock()  # Блокировка для синхронизации доступа к датчикам
        self.height_calibration_nonzero_count = 0  # Счетчик ненулевых показаний для CMD=103
        self.distance_to_plane_calculated = False  # Флаг завершения расчёта дистанции (CMD=103)
        self.recent_measurements = []  # Буфер последних измерений для CMD=103
        
        # Автоматическое переподключение датчиков
        self.last_reconnect_attempt = 0  # Время последней попытки переподключения
        self.reconnect_interval = 5.0  # Интервал попыток переподключения (секунды)
        
        # Счетчики для потокового режима QUAD
        self.stream_measurement_count = 0
        self.stream_start_time = None
        # Буферы для усреднения по 10 измерениям для каждого датчика
        self.stream_temp_sensor1_buffer = []
        self.stream_temp_sensor2_buffer = []
        self.stream_temp_sensor3_buffer = []
        self.stream_temp_sensor4_buffer = []
        
        # Буферы для основного цикла измерения
        self.sensor1_measurements = []  # Буфер усредненных измерений датчика 1
        self.sensor2_measurements = []  # Буфер усредненных измерений датчика 2
        self.wall_thickness_buffer = []  # Буфер рассчитанной толщины стенки
        self.measurement_cycle_active = False  # Флаг активного цикла измерения
        
        # Флаги выполнения расчётов (чтобы не выполнять многократно)
        self.wall_calculated = False
        self.flange_calculated = False
        self.bottom_calculated = False
        self.flange_only_calculated = False
        self.body_only_calculated = False
        self.body2_calculated = False
        self.quality_evaluated = False
        self.body2_quality_required = False
        
        # Мониторинг в состоянии ожидания (IDLE)
        self.idle_monitor_last_time = 0.0

        # Номер смены (для сброса счётчиков при смене)
        self.last_shift_number = None
        
        # Кеш калиброванных расстояний (для ускорения циклов измерения)
        self.cached_distance_1_2 = None
        self.cached_distance_to_center = None
        self.cached_distance_1_3 = None
        self.cached_distance_sensor4 = None
        self.cached_distance_sensor3_to_center = None
        self.cached_distance_sensor3_to_center_body = None
        self.cached_distance_sensor3_to_center_body2 = None
        
        # Отслеживание смены для сброса счётчиков
        self.current_shift_number = 1  # Текущая смена
        self.shift_initialized = False  # Флаг, чтобы не сбрасывать при первом запуске
        
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
        self.temp_sensor1_buffer = []  # Временный буфер сглаживания датчика 1
        self.temp_sensor2_buffer = []  # Временный буфер сглаживания датчика 2
        
        # Буферы для команды 11 (измерение фланца)
        self.sensor1_flange_measurements = []  # Буфер усредненных измерений датчика 1 для команды 11
        self.sensor3_measurements = []  # Буфер усредненных измерений датчика 3
        self.sensor4_measurements = []  # Буфер усредненных измерений датчика 4
        
        # Временные буферы для усреднения (команда 11)
        self.temp_sensor1_flange_buffer = []  # Временный буфер сглаживания датчика 1 (команда 11)
        self.temp_sensor3_buffer = []  # Временный буфер сглаживания датчика 3
        self.temp_sensor4_buffer = []  # Временный буфер сглаживания датчика 4
        
        # Расчетные буферы для команды 11
        self.body_diameter_buffer = []    # Буфер диаметра корпуса (датчик 1)
        self.flange_diameter_buffer = []  # Буфер диаметра фланца (датчик 3)
        self.bottom_thickness_buffer = [] # Буфер толщины дна (датчик 4)
        
        # Буферы для команды 12 (измерение нижней стенки)
        self.sensor1_bottom_measurements = []  # Буфер усредненных измерений датчика 1 для команды 12
        self.sensor2_bottom_measurements = []  # Буфер усредненных измерений датчика 2 для команды 12
        
        # Временные буферы для усреднения (команда 12)
        self.temp_sensor1_bottom_buffer = []  # Временный буфер сглаживания датчика 1 (команда 12)
        self.temp_sensor2_bottom_buffer = []  # Временный буфер сглаживания датчика 2 (команда 12)
        
        # Расчетный буфер для команды 12
        self.bottom_wall_thickness_buffer = []  # Буфер толщины нижней стенки

        # Буферы для раздельных команд диаметров (20/30/40)
        self.sensor3_flange_only_measurements = []
        self.sensor3_body_only_measurements = []
        self.sensor3_body2_measurements = []
        self.temp_sensor3_flange_only_buffer = []
        self.temp_sensor3_body_only_buffer = []
        self.temp_sensor3_body2_buffer = []
        self.body_only_diameter_buffer = []
        self.body2_diameter_buffer = []
        
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
                # Очищаем буферы серийного порта после подключения
                self.clear_serial_buffers()
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
        
        # После загрузки БД повторно синхронизируем номер смены,
        # чтобы избежать ложного срабатывания детектора смены
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                current_shift = self.modbus_server.slave_context.getValues(3, 99, 1)[0]
                self.last_shift_number = int(current_shift)
                print(f" [SHIFT] Смены синхронизированы после загрузки БД: {self.last_shift_number}")
        except Exception as e:
            print(f" [SHIFT] Ошибка повторной синхронизации смены: {e}")
        
        # Запускаем мониторинг изменений регистров
        self.db_integration.start_monitoring(interval=1.0)
        print("OK Интеграция с базой данных запущена")
        
        # Флаг работы системы должен быть поднят ДО запуска вспомогательных потоков,
        # иначе поток чтения датчиков завершится, не войдя в цикл
        self.is_running = True

        # Запуск потока чтения датчиков для доставки данных через очередь
        if self.sensors and not self.test_mode and not self.sensor_reading_active:
            self.sensor_reading_active = True
            self.sensor_reading_thread = threading.Thread(target=self.sensor_reading_loop, daemon=True)
            self.sensor_reading_thread.start()
            print("OK Поток чтения датчиков запущен")
        
        # Запуск основного цикла в отдельном потоке
        main_thread = threading.Thread(target=self.main_loop, daemon=True)
        main_thread.start()
        print("OK Основной цикл запущен")
        
        # Ждем завершения основного потока
        main_thread.join()
        
        return True
    
    def stop_system(self):
        """Остановка системы"""
        print("\n ОСТАНОВКА СИСТЕМЫ")
        self.is_running = False
        
        # Остановка потока чтения датчиков
        if self.sensor_reading_thread:
            try:
                self.sensor_reading_active = False
                # Ждем завершения потока (максимум 1 секунда)
                self.sensor_reading_thread.join(timeout=1.0)
                print(" Остановлен поток чтения датчиков")
            except Exception as e:
                print(f" Ошибка остановки потока чтения датчиков: {e}")
        
        # Остановка QUAD потокового режима
        if self.sensors and self.stream_active_quad:
            try:
                self.stream_active_quad = False
                print(" Остановлен QUAD потоковый режим")
            except Exception as e:
                print(f" Ошибка остановки QUAD режима: {e}")
        
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
            
        # Очищаем системные оптимизации
        cleanup_laser_system_optimizations()
            
        print(" Система остановлена")
    
    def sensor_reading_loop(self):
        """
        Отдельный поток для чтения датчиков с высоким приоритетом
        Только чтение данных, без обработки и записи в Modbus
        """
        print(" Запуск потока чтения датчиков...")
        
        # Устанавливаем высокий приоритет для потока чтения датчиков
        if HAS_PSUTIL:
            try:
                thread_id = threading.current_thread().ident
                if thread_id:
                    # Устанавливаем высокий приоритет для текущего потока
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    THREAD_PRIORITY_HIGHEST = 2
                    kernel32.SetThreadPriority(kernel32.OpenThread(0x1F03FF, False, thread_id), THREAD_PRIORITY_HIGHEST)
                    print(" [SENSOR THREAD] Установлен высокий приоритет для потока чтения датчиков")
            except Exception as e:
                print(f" [SENSOR THREAD] Не удалось установить приоритет: {e}")
        
        try:
            while self.sensor_reading_active and self.is_running:
                if not self.sensors:
                    time.sleep(0.01)  # Небольшая пауза если датчики не подключены
                    continue
                
                # Читаем данные с датчиков (только чтение, без обработки)
                try:
                    with self.sensor_reading_lock:
                        sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.sensors.perform_quad_sensor_measurement(
                            self.sensor_range_mm, self.sensor_range_mm, 
                            self.sensor_range_mm, self.sensor_range_mm
                        )
                    
                    # Помещаем данные в очередь (неблокирующая запись)
                    try:
                        self.sensor_data_queue.put_nowait({
                            'sensor1': sensor1_mm,
                            'sensor2': sensor2_mm,
                            'sensor3': sensor3_mm,
                            'sensor4': sensor4_mm,
                            'timestamp': time.time()
                        })
                    except:
                        # Очередь переполнена - удаляем старые данные и добавляем новые
                        try:
                            self.sensor_data_queue.get_nowait()
                            self.sensor_data_queue.put_nowait({
                                'sensor1': sensor1_mm,
                                'sensor2': sensor2_mm,
                                'sensor3': sensor3_mm,
                                'sensor4': sensor4_mm,
                                'timestamp': time.time()
                            })
                        except:
                            pass  # Игнорируем ошибки при переполнении очереди
                            
                except Exception as e:
                    # Ошибка чтения датчиков - небольшая пауза и продолжаем
                    if self.sensor_reading_active:
                        time.sleep(0.001)  # Минимальная пауза при ошибке
                    continue
                    
        except Exception as e:
            print(f" [SENSOR THREAD] Критическая ошибка в потоке чтения датчиков: {e}")
            import traceback
            traceback.print_exc()
        finally:
            print(" [SENSOR THREAD] Поток чтения датчиков завершен")
    
    def get_sensor_data(self, timeout=0.001):
        """
        Получение данных с датчиков из очереди (неблокирующее)
        
        Args:
            timeout: Таймаут ожидания данных (секунды)
            
        Returns:
            dict с данными датчиков или None если данных нет
        """
        try:
            return self.sensor_data_queue.get(timeout=timeout)
        except Empty:
            return None
    
    def read_sensors_safe(self):
        """
        Безопасное чтение датчиков с блокировкой
        Предотвращает конфликты при одновременном чтении из разных потоков
        
        Returns:
            Tuple[sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm]
        """
        if not self.sensors:
            return None, None, None, None

        # Если работает поток чтения, пробуем взять свежие данные из очереди
        if self.sensor_reading_active and self.sensor_reading_thread and self.sensor_reading_thread.is_alive():
            attempts = 0
            while attempts < 5:
                try:
                    data = self.sensor_data_queue.get(timeout=0.01)
                    return (
                        data.get('sensor1'),
                        data.get('sensor2'),
                        data.get('sensor3'),
                        data.get('sensor4'),
                    )
                except Empty:
                    attempts += 1

        # Данных в очереди нет — возвращаем None, чтобы вызывающий код повторил попытку
        return None, None, None, None
    
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

                # Проверяем смену смены в основном цикле
                self.check_shift_change()
                
                # Выполняем действия в зависимости от состояния
                self.execute_state_actions()
                
                # Пауза только если НЕ потоковый режим (иначе тормозит поток!)
                if self.current_state not in [SystemState.STREAM_QUAD, 
                                             SystemState.MEASURE_HEIGHT_PROCESS, SystemState.MEASURE_WALL_PROCESS, 
                                             SystemState.MEASURE_FLANGE_PROCESS, SystemState.MEASURE_FLANGE_ONLY_PROCESS,
                                             SystemState.MEASURE_BODY_ONLY_PROCESS, SystemState.MEASURE_BODY2_PROCESS,
                                             SystemState.MEASURE_BOTTOM_PROCESS,
                                             SystemState.CALIBRATE_HEIGHT,SystemState.CALIBRATE_WALL,SystemState.CALIBRATE_FLANGE,
                                             SystemState.CALIBRATE_FLANGE_DIAMETER, SystemState.CALIBRATE_BODY_DIAMETER_SEPARATE,
                                             SystemState.CALIBRATE_BODY2_DIAMETER, SystemState.CALIBRATE_BOTTOM]:
                    time.sleep(0.1)
                elif self.current_state == SystemState.STREAM_QUAD:
                    # Микро-пауза в QUAD режиме для снижения нагрузки на CPU (5 мс)
                    # Это поможет избежать переполнения буферов на слабых процессорах
                    time.sleep(0.005)
                
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
            # Очищаем буферы серийного порта при переходе в IDLE
            self.clear_serial_buffers()
            
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
        elif cmd == 105:
            self.current_state = SystemState.CALIBRATE_FLANGE_DIAMETER
        elif cmd == 106:
            # Команда 106: Настройка диапазонов для дискретного сигнала датчика 3
            self.current_state = SystemState.CONFIGURE_SENSOR3_RANGE
        elif cmd == 107:
            self.current_state = SystemState.CALIBRATE_BODY_DIAMETER_SEPARATE
        elif cmd == 108:
            self.current_state = SystemState.CALIBRATE_BODY2_DIAMETER
            
        # Измерение верхней стенки
        elif cmd == 10:
            self.current_state = SystemState.MEASURE_WALL_PROCESS
            
        # Потоковый режим (QUAD - все 4 датчика)
        elif cmd == 200:
            self.current_state = SystemState.STREAM_QUAD
            # Очищаем буферы серийного порта при переходе в QUAD режим
            self.clear_serial_buffers()
            
        # Основной цикл измерения - подсчёт верхней стенки
        elif cmd == 11:
            self.current_state = SystemState.MEASURE_WALL_CALCULATE
            
        # Основной цикл измерения - фланец
        elif cmd == 12:
            self.current_state = SystemState.MEASURE_FLANGE_PROCESS
        elif cmd == 13:
            self.current_state = SystemState.MEASURE_FLANGE_CALCULATE
        elif cmd == 20:
            self.current_state = SystemState.MEASURE_FLANGE_ONLY_PROCESS
        elif cmd == 21:
            self.current_state = SystemState.MEASURE_FLANGE_ONLY_CALCULATE
        elif cmd == 30:
            self.current_state = SystemState.MEASURE_BODY_ONLY_PROCESS
        elif cmd == 31:
            self.current_state = SystemState.MEASURE_BODY_ONLY_CALCULATE
        elif cmd == 40:
            self.current_state = SystemState.MEASURE_BODY2_PROCESS
            self.body2_quality_required = True
        elif cmd == 41:
            self.current_state = SystemState.MEASURE_BODY2_CALCULATE
            
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
        20  - раздельное измерение фланца
        21  - раздельный подсчёт фланца
        212 - раздельный подсчёт фланца завершён
        30  - раздельное измерение диаметра корпуса
        31  - раздельный подсчёт диаметра корпуса
        312 - раздельный подсчёт диаметра корпуса завершён
        40  - измерение диаметра корпуса 2
        41  - подсчёт диаметра корпуса 2
        412 - подсчёт диаметра корпуса 2 завершён
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
                self.reset_measurement_result_registers()
                # Сбрасываем флаги выполнения расчётов
                self.wall_calculated = False
                self.flange_calculated = False
                self.bottom_calculated = False
                self.flange_only_calculated = False
                self.body_only_calculated = False
                self.body2_calculated = False
                self.quality_evaluated = False
                self.body2_quality_required = False
                # Очищаем кеш калиброванных расстояний
                self.cached_distance_1_2 = None
                self.cached_distance_to_center = None
                self.cached_distance_1_3 = None
                self.cached_distance_sensor4 = None
                self.cached_distance_sensor3_to_center = None
                self.cached_distance_sensor3_to_center_body = None
                self.cached_distance_sensor3_to_center_body2 = None
                # Сбрасываем счетчики частоты
                self.frequency_counter = 0
                self.frequency_start_time = None
                self.last_frequency_display = 0
                print(" [0→10] Начало цикла: измерение верхней стенки")
            
            # === ВЕРХНЯЯ СТЕНКА ===
            elif current_state_value == "MEASURE_WALL_PROCESS" and new_cmd == 11:
                # 10 → 11: команда на подсчёт верхней стенки
                self.write_cycle_flag(11)
                # Очищаем флаг начала измерения для возможности повторного запуска
                if hasattr(self, '_wall_measurement_started'):
                    delattr(self, '_wall_measurement_started')
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
                # ВАЖНО: Очищаем ВСЕ буферы измерения фланца перед началом нового измерения
                print(" [11→12] Подсчёт завершён, начало измерения фланца, кеш очищен")
                print(" [11→12] ОЧИСТКА БУФЕРОВ ИЗМЕРЕНИЯ ФЛАНЦА ПЕРЕД НОВЫМ ИЗМЕРЕНИЕМ")
                # Очищаем буферы усредненных значений датчиков для фланца
                self.sensor1_flange_measurements = []
                self.sensor3_measurements = []
                self.sensor4_measurements = []
                self.temp_sensor1_flange_buffer = []
                self.temp_sensor3_buffer = []
                self.temp_sensor4_buffer = []
                # Очищаем буферы рассчитанных значений для фланца
                self.body_diameter_buffer = []
                self.flange_diameter_buffer = []
                self.bottom_thickness_buffer = []
                print(" [11→12] Все буферы измерения фланца очищены")
            elif current_state_value == "MEASURE_WALL_CALCULATE" and new_cmd == 20:
                self.write_cycle_flag(20)
                self.flange_only_calculated = False
                self.frequency_counter = 0
                self.frequency_start_time = None
                self.last_frequency_display = 0
                self.cached_distance_sensor3_to_center = None
                self.sensor3_flange_only_measurements = []
                self.temp_sensor3_flange_only_buffer = []
                self.flange_diameter_buffer = []
                print(" [11→20] Начало раздельного измерения фланца")
            elif current_state_value == "MEASURE_WALL_CALCULATE" and new_cmd == 30:
                self.write_cycle_flag(30)
                self.body_only_calculated = False
                self.frequency_counter = 0
                self.frequency_start_time = None
                self.last_frequency_display = 0
                self.cached_distance_sensor3_to_center_body = None
                self.sensor3_body_only_measurements = []
                self.temp_sensor3_body_only_buffer = []
                self.body_only_diameter_buffer = []
                print(" [11→30] Начало раздельного измерения диаметра корпуса")
            elif current_state_value == "MEASURE_WALL_CALCULATE" and new_cmd == 40:
                self.write_cycle_flag(40)
                self.body2_calculated = False
                self.body2_quality_required = True
                self.frequency_counter = 0
                self.frequency_start_time = None
                self.last_frequency_display = 0
                self.cached_distance_sensor3_to_center_body2 = None
                self.sensor3_body2_measurements = []
                self.temp_sensor3_body2_buffer = []
                self.body2_diameter_buffer = []
                print(" [11→40] Начало измерения диаметра корпуса 2")
            
            # === ФЛАНЕЦ ===
            elif current_state_value == "MEASURE_FLANGE_PROCESS" and new_cmd == 13:
                # 12 → 13: команда на подсчёт фланца
                self.write_cycle_flag(13)
                # Очищаем флаг начала измерения для возможности повторного запуска
                if hasattr(self, '_flange_measurement_started'):
                    delattr(self, '_flange_measurement_started')
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
                # Сбрасываем флаг инициализации частоты для нижней стенки
                if hasattr(self, '_bottom_frequency_initialized'):
                    delattr(self, '_bottom_frequency_initialized')
                print(" [13→14] Подсчёт завершён, начало измерения нижней стенки")
            elif current_state_value == "MEASURE_FLANGE_CALCULATE" and new_cmd == 30:
                self.write_cycle_flag(30)
                self.body_only_calculated = False
                self.frequency_counter = 0
                self.frequency_start_time = None
                self.last_frequency_display = 0
                self.cached_distance_sensor3_to_center_body = None
                self.sensor3_body_only_measurements = []
                self.temp_sensor3_body_only_buffer = []
                self.body_only_diameter_buffer = []
                print(" [13→30] Начало раздельного измерения диаметра корпуса")
            elif current_state_value == "MEASURE_FLANGE_CALCULATE" and new_cmd == 40:
                self.write_cycle_flag(40)
                self.body2_calculated = False
                self.body2_quality_required = True
                self.frequency_counter = 0
                self.frequency_start_time = None
                self.last_frequency_display = 0
                self.cached_distance_sensor3_to_center_body2 = None
                self.sensor3_body2_measurements = []
                self.temp_sensor3_body2_buffer = []
                self.body2_diameter_buffer = []
                print(" [13→40] Начало измерения диаметра корпуса 2")

            # === РАЗДЕЛЬНЫЙ ФЛАНЕЦ ===
            elif current_state_value == "MEASURE_FLANGE_ONLY_PROCESS" and new_cmd == 21:
                self.write_cycle_flag(21)
                if hasattr(self, '_flange_only_measurement_started'):
                    delattr(self, '_flange_only_measurement_started')
                print(" [20→21] Подсчёт результатов раздельного фланца...")
            elif current_state_value == "MEASURE_FLANGE_ONLY_CALCULATE" and new_cmd == 14:
                self.write_cycle_flag(14)
                self.bottom_calculated = False
                self.frequency_counter = 0
                self.frequency_start_time = None
                self.last_frequency_display = 0
                if hasattr(self, '_bottom_frequency_initialized'):
                    delattr(self, '_bottom_frequency_initialized')
                print(" [21→14] Подсчёт раздельного фланца завершён, начало измерения нижней стенки")
            elif current_state_value == "MEASURE_FLANGE_ONLY_CALCULATE" and new_cmd == 30:
                self.write_cycle_flag(30)
                self.body_only_calculated = False
                self.frequency_counter = 0
                self.frequency_start_time = None
                self.last_frequency_display = 0
                self.cached_distance_sensor3_to_center_body = None
                self.sensor3_body_only_measurements = []
                self.temp_sensor3_body_only_buffer = []
                self.body_only_diameter_buffer = []
                print(" [21→30] Начало раздельного измерения диаметра корпуса")
            elif current_state_value == "MEASURE_FLANGE_ONLY_CALCULATE" and new_cmd == 40:
                self.write_cycle_flag(40)
                self.body2_calculated = False
                self.body2_quality_required = True
                self.frequency_counter = 0
                self.frequency_start_time = None
                self.last_frequency_display = 0
                self.cached_distance_sensor3_to_center_body2 = None
                self.sensor3_body2_measurements = []
                self.temp_sensor3_body2_buffer = []
                self.body2_diameter_buffer = []
                print(" [21→40] Начало измерения диаметра корпуса 2")

            # === РАЗДЕЛЬНЫЙ ДИАМЕТР КОРПУСА ===
            elif current_state_value == "MEASURE_BODY_ONLY_PROCESS" and new_cmd == 31:
                self.write_cycle_flag(31)
                if hasattr(self, '_body_only_measurement_started'):
                    delattr(self, '_body_only_measurement_started')
                print(" [30→31] Подсчёт раздельного диаметра корпуса...")
            elif current_state_value == "MEASURE_BODY_ONLY_CALCULATE" and new_cmd == 14:
                self.write_cycle_flag(14)
                self.bottom_calculated = False
                self.frequency_counter = 0
                self.frequency_start_time = None
                self.last_frequency_display = 0
                if hasattr(self, '_bottom_frequency_initialized'):
                    delattr(self, '_bottom_frequency_initialized')
                print(" [31→14] Подсчёт раздельного диаметра корпуса завершён, начало измерения нижней стенки")
            elif current_state_value == "MEASURE_BODY_ONLY_CALCULATE" and new_cmd == 40:
                self.write_cycle_flag(40)
                self.body2_calculated = False
                self.body2_quality_required = True
                self.frequency_counter = 0
                self.frequency_start_time = None
                self.last_frequency_display = 0
                self.cached_distance_sensor3_to_center_body2 = None
                self.sensor3_body2_measurements = []
                self.temp_sensor3_body2_buffer = []
                self.body2_diameter_buffer = []
                print(" [31→40] Начало измерения диаметра корпуса 2")

            # === ДИАМЕТР КОРПУСА 2 ===
            elif current_state_value == "MEASURE_BODY2_PROCESS" and new_cmd == 41:
                self.write_cycle_flag(41)
                if hasattr(self, '_body2_measurement_started'):
                    delattr(self, '_body2_measurement_started')
                print(" [40→41] Подсчёт диаметра корпуса 2...")
            elif current_state_value == "MEASURE_BODY2_CALCULATE" and new_cmd == 14:
                self.write_cycle_flag(14)
                self.bottom_calculated = False
                self.frequency_counter = 0
                self.frequency_start_time = None
                self.last_frequency_display = 0
                if hasattr(self, '_bottom_frequency_initialized'):
                    delattr(self, '_bottom_frequency_initialized')
                print(" [41→14] Подсчёт диаметра корпуса 2 завершён, начало измерения нижней стенки")
            
            # === НИЖНЯЯ СТЕНКА ===
            elif current_state_value == "MEASURE_BOTTOM_PROCESS" and new_cmd == 15:
                # 14 → 15: команда на подсчёт нижней стенки
                self.write_cycle_flag(15)
                # Очищаем флаг начала измерения для возможности повторного запуска
                if hasattr(self, '_bottom_measurement_started'):
                    delattr(self, '_bottom_measurement_started')
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
                # Очищаем буферы серийного порта при переходе в IDLE
                self.clear_serial_buffers()
                print(" [16→0] Оценка завершена, цикл завершён, возврат в IDLE")
            
            # === ЗАВЕРШЕНИЕ КАЛИБРОВКИ ВЫСОТЫ ===
            elif current_state_value == "CALIBRATE_HEIGHT" and new_cmd == 0:
                # 103 → 0: завершение калибровки высоты
                self.write_cycle_flag(0)
                self.clear_measurement_buffers()
                # Очищаем буферы серийного порта при переходе в IDLE
                self.clear_serial_buffers()
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
                # Очищаем буферы серийного порта при переходе в IDLE
                self.clear_serial_buffers()
                print(" [104→0] Отладка регистров завершена, возврат в IDLE")
            
            # === ЗАВЕРШЕНИЕ КАЛИБРОВОК 100/101/102/105/107/108 ПО КОМАНДЕ 0 ===
            # При переходе CMD -> 0 завершаем калибровку: рассчитываем результаты и записываем их
            elif current_state_value == "CALIBRATE_WALL" and new_cmd == 0:
                print(" [CALIBRATE_WALL→0] Завершение калибровки стенки, расчет результатов...")
                self._finish_calibration_wall()
                return
            elif current_state_value == "CALIBRATE_BOTTOM" and new_cmd == 0:
                print(" [CALIBRATE_BOTTOM→0] Завершение калибровки дна, расчет результатов...")
                self._finish_calibration_bottom()
                return
            elif current_state_value == "CALIBRATE_FLANGE" and new_cmd == 0:
                print(" [CALIBRATE_FLANGE→0] Завершение калибровки фланца, расчет результатов...")
                self._finish_calibration_flange()
                return
            elif current_state_value == "CALIBRATE_FLANGE_DIAMETER" and new_cmd == 0:
                print(" [CALIBRATE_FLANGE_DIAMETER→0] Завершение калибровки диаметра фланца, расчет результатов...")
                self._finish_calibration_flange_diameter()
                return
            elif current_state_value == "CALIBRATE_BODY_DIAMETER_SEPARATE" and new_cmd == 0:
                print(" [CALIBRATE_BODY_DIAMETER_SEPARATE→0] Завершение калибровки диаметра корпуса (раздельно), расчет результатов...")
                self._finish_calibration_body_diameter_separate()
                return
            elif current_state_value == "CALIBRATE_BODY2_DIAMETER" and new_cmd == 0:
                print(" [CALIBRATE_BODY2_DIAMETER→0] Завершение калибровки диаметра корпуса 2, расчет результатов...")
                self._finish_calibration_body2_diameter()
                return
            
            # === ПРЕРЫВАНИЕ ЦИКЛА (ОШИБКИ) ===
            elif new_cmd == 0 and self.measurement_cycle_active:
                # Любой переход в 0 во время активного цикла (кроме успешного 16→0) = прерывание
                self.write_cycle_flag(-1)
                self.measurement_cycle_active = False
                self.clear_measurement_buffers()
                # Очищаем буферы серийного порта при переходе в IDLE
                self.clear_serial_buffers()
                print(f" [{current_state_value}→0] Цикл прерван! Ошибка.")

            # === ПОТОКОВЫЙ РЕЖИМ (CMD=200 - QUAD всех датчиков) ===
            elif current_state_value == "IDLE" and new_cmd == 200:
                # Устанавливаем статус равным номеру команды
                self.write_cycle_flag(200)
                self.clear_measurement_buffers()
                # Очищаем буферы серийного порта перед началом QUAD режима
                self.clear_serial_buffers()
                print(f" [0→200] Начало QUAD потокового режима (все 4 датчика)")
            elif current_state_value == "STREAM_QUAD" and new_cmd == 0:
                # Выход из потокового режима → 0
                self.write_cycle_flag(0)
                self.clear_measurement_buffers()
                # Останавливаем QUAD режим
                if self.sensors and self.stream_active_quad:
                    try:
                        # QUAD режим не использует потоковый режим датчиков, просто останавливаем измерения
                        self.stream_active_quad = False
                        print(" QUAD потоковый режим остановлен")
                    except Exception as e:
                        print(f" Ошибка остановки QUAD режима: {e}")
                # Очищаем буферы серийного порта при переходе в IDLE
                self.clear_serial_buffers()
                print(f" [STREAM_QUAD→0] Выход из потокового режима")
                
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
                    # Очищаем буферы серийного порта после переподключения
                    self.clear_serial_buffers()
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
    
    def evaluate_product_quality(self) -> dict:
        """
        Оценка качества изделия после завершения цикла измерения
        
        Returns:
            Словарь с ключами:
            - 'result': "GOOD", "CONDITIONALLY_GOOD" или "BAD"
            - 'param_details': Список словарей с информацией о каждом параметре
                (name, status, measured, base, check_type)
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
                    'measured_regs': [(40057, 40058)],  # измеренная высота с ПЛК (одно значение)
                    'base_regs': (40376, 40377),  # базовое значение
                    'cond_bad_regs': (40378, 40379),  # условно-негодная погрешность
                    'bad_regs': (40380, 40381),  # негодная погрешность
                    'check_type': 'one_sided',  # односторонняя проверка
                    'single_value': True  # значение приходит от ПЛК в 40057-40058
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
                    'measured_regs': [(40059, 40060)],  # Измеренная толщина фланца с ПК (один регистр)
                    'base_regs': (40370, 40371),
                    'cond_bad_regs': (40372, 40373),
                    'bad_regs': (40374, 40375),
                    'check_type': 'one_sided',  # односторонняя проверка
                    'single_value': True  # Флаг: одно значение вместо трех (макс/сред/мин)
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
                    'measured_regs': [(30054, 30055), (30052, 30053), (30056, 30057)],  # макс, сред, мин
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

            # Диаметр корпуса 2 оцениваем только если в цикле была команда 40
            if self.body2_quality_required:
                parameters.append(
                    {
                        'name': 'body_diameter_2',
                        'measured_regs': [(30059, 30060), (30061, 30062), (30063, 30064)],
                        'base_regs': (40346, 40347),
                        'cond_bad_regs': (40348, 40349),
                        'bad_regs': (40350, 40351),
                        'check_type': 'one_sided'
                    }
                )
            
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
                
                # Специальная обработка для flange_thickness (читаем из holding регистра 40059-40060)
                if param.get('single_value', False):
                    # Для одного значения читаем из соответствующего регистра
                    if param['name'] == 'flange_thickness':
                        value = self.read_measured_flange_thickness()
                        if value is None:
                            print(f" [CMD=16] ОШИБКА: Не удалось прочитать измеренную толщину фланца из 40059-40060!")
                            value = 0.0  # Значение по умолчанию
                        measured_values = [value]
                    elif param['name'] == 'height':
                        value = self.read_measured_height()
                        if value is None:
                            print(f" [CMD=16] ОШИБКА: Не удалось прочитать измеренную высоту из 40057-40058!")
                            value = 0.0  # Значение по умолчанию
                        measured_values = [value]
                    else:
                        # Для других параметров с одним значением читаем как раньше (из input регистра)
                        value = self.read_float_from_registers(param['measured_regs'][0], 'input')
                        measured_values = [value]
                else:
                    # Для параметров с тремя значениями (макс, сред, мин)
                    for reg_pair in param['measured_regs']:
                        value = self.read_float_from_registers(reg_pair, 'input')
                        measured_values.append(value)
                
                # Сохраняем значения в measurement_data
                if param.get('single_value', False):
                    # Для одного значения сохраняем как среднее
                    measurement_data[f"{param['name']}_avg"] = measured_values[0]
                    measurement_data[f"{param['name']}_max"] = measured_values[0]
                    measurement_data[f"{param['name']}_min"] = measured_values[0]
                else:
                    # Для трех значений
                    measurement_data[f"{param['name']}_max"] = measured_values[0]
                    measurement_data[f"{param['name']}_avg"] = measured_values[1]
                    measurement_data[f"{param['name']}_min"] = measured_values[2]
                
                # Выводим проверяемый параметр и диапазоны допуска
                print(f"\n === {param['name'].upper()} ({param['check_type']}) ===")
                if param['check_type'] == 'two_sided':
                    print(f"   БАЗА={base_value:.3f}мм | "
                          f"Условно-негодная (ниже)={base_value + cond_bad_error:.3f}мм | "
                          f"Негодная (ниже)={base_value + bad_error:.3f}мм | "
                          f"Негодная (выше)={base_value + (positive_bad_error if positive_bad_error is not None else 0):.3f}мм")
                else:
                    print(f"   БАЗА={base_value:.3f}мм | "
                          f"Условно-негодная граница={base_value + cond_bad_error:.3f}мм | "
                          f"Негодная граница={base_value + bad_error:.3f}мм")
                
                # Проверяем только выбранные значения
                param_errors = []
                if param.get('single_value', False):
                    # Для одного значения проверяем только его
                    measured = measured_values[0]
                    if param['check_type'] == 'two_sided':
                        status = self.check_single_value_with_upper_limit(
                            measured, base_value, cond_bad_error, bad_error, positive_bad_error
                        )
                    else:  # one_sided
                        status = self.check_single_value(measured, base_value, cond_bad_error, bad_error)
                    param_errors.append(status)
                    print(f" [ИЗМЕРЕНИЕ] {measured:.3f} → {status}")
                else:
                    # Для трех значений проверяем выбранные индексы
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
                
                # Сохраняем информацию о параметре для статистики
                # Сохраняем все проверенные значения с их статусами для правильного определения направления
                checked_values = []
                if param.get('single_value', False):
                    # Для одного значения
                    checked_values.append({
                        'value': measured_values[0],
                        'status': param_errors[0] if param_errors else 'GOOD',
                        'index': 0
                    })
                else:
                    # Для нескольких значений сохраняем только проверенные (по check_indices)
                    value_names = ['МАКС', 'СРЕД', 'МИН']
                    for i, idx in enumerate(check_indices):
                        checked_values.append({
                            'value': measured_values[idx],
                            'status': param_errors[i] if i < len(param_errors) else 'GOOD',
                            'index': idx,
                            'name': value_names[idx]
                        })
                
                param_info = {
                    'name': param['name'],
                    'status': param_status,
                    'base': base_value,
                    'check_type': param['check_type'],
                    'cond_bad_error': cond_bad_error,
                    'bad_error': bad_error,
                    'positive_bad_error': positive_bad_error if param['check_type'] == 'two_sided' else None,
                    'checked_values': checked_values
                }
                if 'param_details' not in measurement_data:
                    measurement_data['param_details'] = []
                measurement_data['param_details'].append(param_info)
                
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
            
            # Сохраняем средние значения в БД для отчёта
            if self.db_integration:
                try:
                    self.db_integration.db.save_quality_measurement(shift_number, {
                        'height_avg': measurement_data.get('height_avg'),
                        'upper_wall_avg': measurement_data.get('upper_wall_avg'),
                        'body_diameter_avg': measurement_data.get('body_diameter_avg'),
                        'body_diameter_2_avg': measurement_data.get('body_diameter_2_avg'),
                        'flange_diameter_avg': measurement_data.get('flange_diameter_avg'),
                        'bottom_wall_avg': measurement_data.get('bottom_wall_avg'),
                        'flange_thickness_avg': measurement_data.get('flange_thickness_avg'),
                        'bottom_avg': measurement_data.get('bottom_avg')
                    })
                except Exception as e:
                    print(f" [CMD=16] Ошибка сохранения измерений в БД: {e}")
            
            # Возвращаем словарь с результатом и информацией о параметрах
            return {
                'result': result,
                'param_details': measurement_data.get('param_details', [])
            }
            
        except Exception as e:
            print(f" Ошибка оценки качества: {e}")
            return {'result': 'BAD', 'param_details': []}
    
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
            # При записи: base_address - 1 = младшее слово, base_address = старшее слово
            # При чтении: reg_pair[0] = base_address = старшее слово, reg_pair[0] - 1 = младшее слово
            first_idx = reg_pair[0] - base_offset      # Старшее слово (base_address)
            second_idx = reg_pair[0] - base_offset + 1  # Младшее слово (base_address)
            
            # Читаем значения (В HMI: base_address = старшее слово, base_address - 1 = младшее слово)
            high_word = self.modbus_server.slave_context.getValues(function_code, first_idx, 1)[0]
            low_word = self.modbus_server.slave_context.getValues(function_code, second_idx, 1)[0]
            
            # Преобразуем в float
            combined = (high_word << 16) | low_word
            float_value = struct.unpack('!f', struct.pack('!I', combined))[0]
            
            return float_value
            
        except Exception as e:
            print(f" Ошибка чтения float из регистров {reg_pair}: {e}")
            return 0.0
    
    def read_input_register(self, register_number: int) -> int:
        """
        Чтение значения Input регистра по его адресу (30001+)
        """
        try:
            if not self.modbus_server or not self.modbus_server.slave_context:
                return 0
            
            index = register_number - 30001
            if index < 0:
                return 0
            
            value = self.modbus_server.slave_context.getValues(4, index, 1)[0]
            return int(value)
        except Exception as e:
            print(f" Ошибка чтения Input регистра {register_number}: {e}")
            return 0
    
    def generate_shift_report(self, shift_number: int):
        """
        Формирует DOCX отчёт по результатам смены
        """
        if not DOCX_AVAILABLE:
            print(" [REPORT] Невозможно сформировать отчёт: не установлена библиотека python-docx")
            return
        
        if not self.modbus_server or not self.modbus_server.slave_context:
            print(" [REPORT] Modbus сервер не инициализирован, отчёт не создан")
            return
        
        try:
            timestamp = datetime.now()
            formatted_time = timestamp.strftime("%d.%m.%Y %H:%M:%S")
            filename = f"shift_{shift_number}_{timestamp.strftime('%Y%m%d_%H%M%S')}.docx"
            filepath = os.path.join(self.data_dir, filename)
            
            # Счётчики изделий
            total_measured = self.read_input_register(30101)
            total_good = self.read_input_register(30102)
            total_conditionally_good = self.read_input_register(30103)
            total_bad = self.read_input_register(30104)
            
            # Карта параметров для таблиц
            parameter_config = [
                {
                    'label': 'Высота',
                    'cond_less': 30201,
                    'cond_greater': None,
                    'bad_less': 30210,
                    'bad_greater': 30219
                },
                {
                    'label': 'Толщина стенки вверх',
                    'cond_less': 30202,
                    'cond_greater': None,
                    'bad_less': 30211,
                    'bad_greater': 30220
                },
                {
                    'label': 'Толщина стенки вниз',
                    'cond_less': 30205,
                    'cond_greater': 30204,
                    'bad_less': 30213,
                    'bad_greater': 30214
                },
                {
                    'label': 'Диаметр корпуса',
                    'cond_less': 30209,
                    'cond_greater': None,
                    'bad_less': 30218,
                    'bad_greater': 30223
                },
                {
                    'label': 'Толщина дна',
                    'cond_less': 30206,
                    'cond_greater': 30207,
                    'bad_less': 30215,
                    'bad_greater': 30216
                },
                {
                    'label': 'Толщина фланца',
                    'cond_less': 30203,
                    'cond_greater': None,
                    'bad_less': 30212,
                    'bad_greater': 30221
                },
                {
                    'label': 'Диаметр фланца',
                    'cond_less': 30208,
                    'cond_greater': None,
                    'bad_less': 30217,
                    'bad_greater': 30222
                },
                {
                    'label': 'Диаметр корпуса 2',
                    'cond_less': 30224,
                    'cond_greater': None,
                    'bad_less': 30225,
                    'bad_greater': 30226
                },
            ]
            
            def get_reg_value(reg_number: Optional[int]) -> int:
                if reg_number is None:
                    return 0
                return self.read_input_register(reg_number)
            
            conditional_rows = []
            bad_rows = []
            for param in parameter_config:
                conditional_rows.append({
                    'label': param['label'],
                    'less': get_reg_value(param['cond_less']),
                    'greater': get_reg_value(param['cond_greater'])
                })
                bad_rows.append({
                    'label': param['label'],
                    'less': get_reg_value(param['bad_less']),
                    'greater': get_reg_value(param['bad_greater'])
                })
            
            # Создаём документ
            document = Document()
            style = document.styles['Normal']
            style.font.name = 'Arial'
            style.font.size = Pt(11)
            
            title = document.add_paragraph("Протокол измерений")
            title.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
            title.runs[0].bold = True
            title.runs[0].font.size = Pt(16)
            
            document.add_paragraph("")  # Отступ
            
            header_table = document.add_table(rows=2, cols=4)
            header_table.style = 'Table Grid'
            header_cells = header_table.rows[0].cells
            header_cells[0].text = "Тип"
            header_cells[1].text = ""
            header_cells[2].text = "Изделие"
            header_cells[3].text = ""
            
            row2 = header_table.rows[1].cells
            row2[0].text = "Смена"
            row2[1].text = str(shift_number)
            row2[2].text = "Дата"
            row2[3].text = formatted_time
            
            document.add_paragraph("")
            
            summary_table = document.add_table(rows=2, cols=4)
            summary_table.style = 'Table Grid'
            summary_table.rows[0].cells[0].text = "Измерено, шт."
            summary_table.rows[0].cells[1].text = "Годные, шт."
            summary_table.rows[0].cells[2].text = "Условно годные, шт."
            summary_table.rows[0].cells[3].text = "Брак, шт."
            
            summary_table.rows[1].cells[0].text = str(total_measured)
            summary_table.rows[1].cells[1].text = str(total_good)
            summary_table.rows[1].cells[2].text = str(total_conditionally_good)
            summary_table.rows[1].cells[3].text = str(total_bad)
            
            document.add_paragraph("")
            
            heading = document.add_paragraph("Сводные таблицы результатов измерений")
            heading.runs[0].bold = True
            
            def add_parameter_table(title_text: str, rows: List[dict]):
                document.add_paragraph("")
                table = document.add_table(rows=len(rows) + 2, cols=3)
                table.style = 'Table Grid'
                
                top_row = table.rows[0].cells
                top_row[0].text = "Параметр"
                top_row[1].text = title_text
                top_row[1].merge(top_row[2])
                
                second_row = table.rows[1].cells
                second_row[0].text = ""
                second_row[1].text = "< нормы"
                second_row[2].text = "> нормы"
                
                for idx, row in enumerate(rows, start=2):
                    table.rows[idx].cells[0].text = row['label']
                    table.rows[idx].cells[1].text = str(row['less'])
                    table.rows[idx].cells[2].text = str(row['greater'])
            
            add_parameter_table("Условно годные, шт.", conditional_rows)
            add_parameter_table("Забраковано, шт.", bad_rows)
            
            # Добавляем второй лист с таблицей измерений
            document.add_page_break()
            
            # Заголовок второго листа
            title2 = document.add_paragraph("Протокол измерений")
            title2.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
            title2.runs[0].bold = True
            title2.runs[0].font.size = Pt(16)
            
            document.add_paragraph("")  # Отступ
            
            # Шапка второго листа (Смена и Дата)
            header_table2 = document.add_table(rows=2, cols=4)
            header_table2.style = 'Table Grid'
            header_cells2 = header_table2.rows[0].cells
            header_cells2[0].text = "Тип"
            header_cells2[1].text = ""
            header_cells2[2].text = "Изделие"
            header_cells2[3].text = ""
            
            row2_2 = header_table2.rows[1].cells
            row2_2[0].text = "Смена"
            row2_2[1].text = str(shift_number)
            row2_2[2].text = "Дата"
            row2_2[3].text = formatted_time
            
            document.add_paragraph("")
            
            # Получаем измерения из БД
            measurements = []
            if self.db_integration:
                try:
                    measurements = self.db_integration.db.get_shift_measurements(shift_number)
                except Exception as e:
                    print(f" [REPORT] Ошибка получения измерений из БД: {e}")
            
            # Создаём таблицу измерений
            if measurements:
                # Заголовок таблицы
                heading2 = document.add_paragraph("Сводные таблицы результатов измерений")
                heading2.runs[0].bold = True
                heading2.runs[0].italic = True
                heading2.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
                
                document.add_paragraph("")
                
                # Таблица измерений
                measurements_table = document.add_table(rows=len(measurements) + 1, cols=9)
                measurements_table.style = 'Table Grid'
                
                # Заголовки столбцов
                header_row = measurements_table.rows[0].cells
                header_row[0].text = "№ п/п"
                header_row[1].text = "Высота, мм"
                header_row[2].text = "Толщина стенки вверху, мм среднее"
                header_row[3].text = "Диаметр корпуса, мм среднее"
                header_row[4].text = "Диаметр фланца, мм среднее"
                header_row[5].text = "Толщина стенки внизу, мм среднее"
                header_row[6].text = "Толщина фланца, мм"
                header_row[7].text = "Толщина дна, мм среднее"
                header_row[8].text = "Диаметр корпуса 2, мм среднее"
                
                # Выравнивание заголовков
                for cell in header_row:
                    for paragraph in cell.paragraphs:
                        paragraph.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
                        for run in paragraph.runs:
                            run.bold = True
                
                # Заполняем данные
                for idx, meas in enumerate(measurements, start=1):
                    row = measurements_table.rows[idx].cells
                    row[0].text = str(idx)
                    row[1].text = f"{meas.get('height_avg', 0):.3f}" if meas.get('height_avg') is not None else "0.000"
                    row[2].text = f"{meas.get('upper_wall_avg', 0):.3f}" if meas.get('upper_wall_avg') is not None else "0.000"
                    row[3].text = f"{meas.get('body_diameter_avg', 0):.3f}" if meas.get('body_diameter_avg') is not None else "0.000"
                    row[4].text = f"{meas.get('flange_diameter_avg', 0):.3f}" if meas.get('flange_diameter_avg') is not None else "0.000"
                    row[5].text = f"{meas.get('bottom_wall_avg', 0):.3f}" if meas.get('bottom_wall_avg') is not None else "0.000"
                    row[6].text = f"{meas.get('flange_thickness_avg', 0):.3f}" if meas.get('flange_thickness_avg') is not None else "0.000"
                    row[7].text = f"{meas.get('bottom_avg', 0):.3f}" if meas.get('bottom_avg') is not None else "0.000"
                    row[8].text = f"{meas.get('body_diameter_2_avg', 0):.3f}" if meas.get('body_diameter_2_avg') is not None else ""
                    
                    # Выравнивание: первый столбец по центру, остальные по центру
                    row[0].paragraphs[0].alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
                    for i in range(1, 9):
                        row[i].paragraphs[0].alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
            
            document.save(filepath)
            print(f" [REPORT] Сформирован отчёт по смене {shift_number}: {filepath}")
            
            # Копируем отчёт на флешку, если она доступна
            self.copy_report_to_flash(filepath, filename)
        
        except Exception as e:
            print(f" [REPORT] Ошибка формирования отчёта: {e}")
    
    def find_flash_drive_path(self, preferred_name: str = "ARS") -> Optional[str]:
        """
        Поиск точки монтирования флешки:
        1) Сначала диск с именем preferred_name (например, ARS)
        2) Иначе первая доступная для записи точка монтирования
        """
        candidate_roots = ["/media", "/run/media", "/mnt"]
        preferred_name = preferred_name.upper()
        preferred_candidates = []
        fallback_candidates = []

        def add_candidate(path: str):
            if not os.path.isdir(path):
                return
            if not os.access(path, os.W_OK):
                return
            if os.path.basename(path).upper() == preferred_name:
                preferred_candidates.append(path)
            else:
                fallback_candidates.append(path)

        for root in candidate_roots:
            if not os.path.isdir(root):
                continue
            try:
                for level1 in os.listdir(root):
                    path1 = os.path.join(root, level1)
                    if not os.path.isdir(path1):
                        continue
                    add_candidate(path1)
                    try:
                        for level2 in os.listdir(path1):
                            path2 = os.path.join(path1, level2)
                            add_candidate(path2)
                    except Exception:
                        continue
            except Exception:
                continue

        if preferred_candidates:
            return sorted(preferred_candidates)[0]
        if fallback_candidates:
            return sorted(fallback_candidates)[0]
        return None

    def copy_report_to_flash(self, source_filepath: str, filename: str):
        """
        Копирует отчёт на флешку, если она доступна
        
        Args:
            source_filepath: Полный путь к исходному файлу отчёта
            filename: Имя файла отчёта
        """
        # Ищем флешку: сначала по имени ARS, иначе первую подключенную
        flash_drive_path = self.find_flash_drive_path("ARS")
        
        try:
            if not flash_drive_path:
                print(" [REPORT] Флешка не найдена: нет доступных точек монтирования")
                return
            
            # Создаём путь для сохранения на флешке
            flash_filepath = os.path.join(flash_drive_path, filename)
            
            # Копируем файл
            shutil.copy2(source_filepath, flash_filepath)
            
            print(f" [REPORT] Отчёт скопирован на флешку: {flash_filepath}")
            
        except PermissionError:
            print(f" [REPORT] Ошибка доступа к флешке {flash_drive_path}: нет прав на запись")
        except Exception as e:
            print(f" [REPORT] Ошибка копирования отчёта на флешку: {e}")

    def reset_measurement_result_registers(self):
        """
        Обнуление всех итоговых измерительных регистров (min/avg/max) перед новым циклом CMD=10.
        """
        if not self.modbus_server or not self.modbus_server.slave_context:
            return

        base_addresses = [
            # Верхняя стенка
            30016, 30018, 30020,
            # Нижняя стенка
            30022, 30024, 30026,
            # Толщина дна
            30028, 30030, 30032,
            # Толщина фланца
            30034, 30036, 30038,
            # Высота
            30040, 30042, 30044,
            # Диаметр корпуса
            30046, 30048, 30050,
            # Диаметр фланца
            30052, 30054, 30056,
            # Диаметр корпуса 2
            30059, 30061, 30063,
        ]

        for base_addr in base_addresses:
            self.write_stream_result_to_input_registers(0.0, base_addr)

        print(" [CMD=10] Обнулены все регистры итоговых измерений (min/avg/max)")
    
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
    
    def increment_parameter_statistics(self, quality_result: dict):
        """
        Инкрементация регистров статистики параметров
        
        Args:
            quality_result: Словарь с ключами 'result' и 'param_details'
                - result: "GOOD", "CONDITIONALLY_GOOD" или "BAD"
                - param_details: Список словарей с информацией о параметрах
        """
        try:
            if not self.modbus_server or not self.modbus_server.slave_context:
                return
            
            result = quality_result.get('result', 'GOOD')
            param_details = quality_result.get('param_details', [])
            
            # Если деталь годная - ничего не инкрементируем
            if result == 'GOOD':
                return
            
            # Маппинг параметров на регистры статистики
            # Формат: (param_name, check_type) -> (условно_негодный_регистр, негодный_меньше_регистр, негодный_больше_регистр)
            param_registers = {
                ('height', 'one_sided'): (200, 209, 218),  # 30201, 30210, 30219
                ('upper_wall', 'one_sided'): (201, 210, 219),  # 30202, 30211, 30220
                ('flange_thickness', 'one_sided'): (202, 211, 220),  # 30203, 30212, 30221
                ('body_diameter', 'one_sided'): (208, 217, 222),  # 30209, 30218, 30223
                ('body_diameter_2', 'one_sided'): (223, 224, 225),  # 30224, 30225, 30226
                ('flange_diameter', 'one_sided'): (207, 216, 221),  # 30208, 30217, 30222
                ('bottom_wall', 'two_sided'): {
                    'cond_bad_greater': 203,  # 30204
                    'cond_bad_less': 204,  # 30205
                    'bad_less': 212,  # 30213
                    'bad_greater': 213  # 30214
                },
                ('bottom', 'two_sided'): {
                    'cond_bad_less': 205,  # 30206
                    'cond_bad_greater': 206,  # 30207
                    'bad_less': 214,  # 30215
                    'bad_greater': 215  # 30216
                }
            }
            
            # Инкрементируем регистры для проблемных параметров
            for param_info in param_details:
                param_name = param_info['name']
                param_status = param_info['status']
                check_type = param_info['check_type']
                base = param_info['base']
                cond_bad_error = param_info.get('cond_bad_error', 0)
                bad_error = param_info.get('bad_error', 0)
                positive_bad_error = param_info.get('positive_bad_error')
                checked_values = param_info.get('checked_values', [])
                
                # Пропускаем годные параметры
                if param_status == 'GOOD':
                    continue
                
                # Определяем ключ для поиска регистров
                key = (param_name, check_type)
                
                if check_type == 'one_sided':
                    # Для односторонних параметров
                    if key in param_registers:
                        cond_reg, bad_less_reg, bad_greater_reg = param_registers[key]
                        
                        # Отслеживаем направления отклонения для всех проверенных значений
                        has_cond_bad = False
                        has_bad_greater = False
                        has_bad_less = False
                        
                        for checked in checked_values:
                            value = checked['value']
                            value_status = checked['status']
                            
                            if result == 'CONDITIONALLY_GOOD' and value_status == 'CONDITIONALLY_GOOD':
                                has_cond_bad = True
                            
                            elif result == 'BAD' and value_status == 'BAD':
                                # Определяем направление отклонения для негодного значения
                                if value > base:
                                    # Больше нормы
                                    has_bad_greater = True
                                elif value < base + bad_error:
                                    # Меньше нормы (меньше негодной границы)
                                    has_bad_less = True
                        
                        # Инкрементируем регистры
                        if result == 'CONDITIONALLY_GOOD' and param_status == 'CONDITIONALLY_GOOD' and has_cond_bad:
                            current = self.modbus_server.slave_context.getValues(4, cond_reg, 1)[0]
                            self.modbus_server.slave_context.setValues(4, cond_reg, [current + 1])
                            print(f" [СТАТИСТИКА] Инкрементирован регистр {30001 + cond_reg} ({param_name}, условно-негодный)")
                        
                        if result == 'BAD' and param_status == 'BAD':
                            if has_bad_greater:
                                current = self.modbus_server.slave_context.getValues(4, bad_greater_reg, 1)[0]
                                self.modbus_server.slave_context.setValues(4, bad_greater_reg, [current + 1])
                                print(f" [СТАТИСТИКА] Инкрементирован регистр {30001 + bad_greater_reg} ({param_name}, негодный, больше нормы)")
                            if has_bad_less:
                                current = self.modbus_server.slave_context.getValues(4, bad_less_reg, 1)[0]
                                self.modbus_server.slave_context.setValues(4, bad_less_reg, [current + 1])
                                print(f" [СТАТИСТИКА] Инкрементирован регистр {30001 + bad_less_reg} ({param_name}, негодный, меньше нормы)")
                
                elif check_type == 'two_sided':
                    # Для двусторонних параметров
                    if key in param_registers:
                        regs = param_registers[key]
                        
                        # Отслеживаем направления отклонения для всех проверенных значений
                        has_cond_bad_greater = False
                        has_cond_bad_less = False
                        has_bad_greater = False
                        has_bad_less = False
                        
                        for checked in checked_values:
                            value = checked['value']
                            value_status = checked['status']
                            
                            if result == 'CONDITIONALLY_GOOD' and value_status == 'CONDITIONALLY_GOOD':
                                # Определяем направление для условно-негодного значения
                                if value > base:
                                    has_cond_bad_greater = True
                                elif value < base:
                                    has_cond_bad_less = True
                            
                            elif result == 'BAD' and value_status == 'BAD':
                                # Определяем направление для негодного значения
                                if value > base + (positive_bad_error if positive_bad_error else 0):
                                    # Больше верхней границы
                                    has_bad_greater = True
                                elif value < base + bad_error:
                                    # Меньше нижней границы
                                    has_bad_less = True
                        
                        # Инкрементируем регистры
                        if result == 'CONDITIONALLY_GOOD' and param_status == 'CONDITIONALLY_GOOD':
                            if has_cond_bad_greater:
                                reg_idx = regs['cond_bad_greater']
                                current = self.modbus_server.slave_context.getValues(4, reg_idx, 1)[0]
                                self.modbus_server.slave_context.setValues(4, reg_idx, [current + 1])
                                print(f" [СТАТИСТИКА] Инкрементирован регистр {30001 + reg_idx} ({param_name}, условно-негодный, больше нормы)")
                            if has_cond_bad_less:
                                reg_idx = regs['cond_bad_less']
                                current = self.modbus_server.slave_context.getValues(4, reg_idx, 1)[0]
                                self.modbus_server.slave_context.setValues(4, reg_idx, [current + 1])
                                print(f" [СТАТИСТИКА] Инкрементирован регистр {30001 + reg_idx} ({param_name}, условно-негодный, меньше нормы)")
                        
                        if result == 'BAD' and param_status == 'BAD':
                            if has_bad_greater:
                                reg_idx = regs['bad_greater']
                                current = self.modbus_server.slave_context.getValues(4, reg_idx, 1)[0]
                                self.modbus_server.slave_context.setValues(4, reg_idx, [current + 1])
                                print(f" [СТАТИСТИКА] Инкрементирован регистр {30001 + reg_idx} ({param_name}, негодный, больше нормы)")
                            if has_bad_less:
                                reg_idx = regs['bad_less']
                                current = self.modbus_server.slave_context.getValues(4, reg_idx, 1)[0]
                                self.modbus_server.slave_context.setValues(4, reg_idx, [current + 1])
                                print(f" [СТАТИСТИКА] Инкрементирован регистр {30001 + reg_idx} ({param_name}, негодный, меньше нормы)")
            
        except Exception as e:
            print(f" Ошибка инкрементации статистики параметров: {e}")
            import traceback
            traceback.print_exc()
    
    def check_shift_change(self):
        """
        Проверка смены смены и сброс счётчиков при необходимости
        """
        try:
            if not self.modbus_server or not self.modbus_server.slave_context:
                return
            
            # Читаем текущее значение смены из регистра 40100
            new_shift_number = self.modbus_server.slave_context.getValues(3, 99, 1)[0]
            
            # При первом запуске просто синхронизируемся без сброса
            if not self.shift_initialized:
                self.current_shift_number = new_shift_number
                self.shift_initialized = True
                print(f" [SHIFT] Инициализация смены: текущая смена = {new_shift_number} (без сброса)")
                return

            # Если смена изменилась после инициализации
            if new_shift_number != self.current_shift_number:
                print(f" Обнаружена смена смены: {self.current_shift_number} -> {new_shift_number}")
                
                previous_shift = self.current_shift_number
                if previous_shift is not None:
                    self.generate_shift_report(previous_shift)
                    # Очищаем измерения предыдущей смены после формирования отчёта
                    if self.db_integration:
                        try:
                            self.db_integration.db.clear_shift_measurements(previous_shift)
                        except Exception as e:
                            print(f" [SHIFT] Ошибка очистки измерений смены {previous_shift}: {e}")
                
                # Принудительно сохраняем новый номер смены в БД
                if self.db_integration:
                    try:
                        self.db_integration.db.save_single_register(
                            40100, 'holding', int(new_shift_number), "Номер смены"
                        )
                        print(f" [SHIFT] Сохранен номер смены {new_shift_number} в БД")
                    except Exception as e:
                        print(f" [SHIFT] Ошибка сохранения номера смены в БД: {e}")
                
                # Сбрасываем все счётчики изделий
                self.reset_product_counters()
                
                # Обновляем текущую смену
                self.current_shift_number = new_shift_number
                
        except Exception as e:
            print(f" Ошибка проверки смены: {e}")
    
    def reset_product_counters(self):
        """
        Сброс всех счётчиков изделий и статистики параметров при смене смены
        """
        try:
            if not self.modbus_server or not self.modbus_server.slave_context:
                return
            
            # Сбрасываем счётчики в Input регистрах 30101-30104
            self.modbus_server.slave_context.setValues(4, 100, [0])  # 30101 - всего
            self.modbus_server.slave_context.setValues(4, 101, [0])  # 30102 - годных
            self.modbus_server.slave_context.setValues(4, 102, [0])  # 30103 - условно-годных
            self.modbus_server.slave_context.setValues(4, 103, [0])  # 30104 - негодных
            
            # Сбрасываем регистры статистики параметров 30201-30226
            # Индексы 200-225 соответствуют 30201-30226
            for idx in range(200, 226):
                self.modbus_server.slave_context.setValues(4, idx, [0])
            
            print(" Счётчики изделий и статистика параметров сброшены для новой смены")
            
        except Exception as e:
            print(f" Ошибка сброса счётчиков: {e}")
    
    def clear_serial_buffers(self):
        """Очистка буферов серийного порта ОС Windows"""
        try:
            if self.sensors and self.sensors.ser:
                lock = getattr(self, 'sensor_reading_lock', None)
                if lock:
                    lock_context = lock
                else:
                    lock_context = contextlib.nullcontext()

                with lock_context:
                    if hasattr(self.sensors.ser, 'reset_input_buffer'):
                        self.sensors.ser.reset_input_buffer()
                    if hasattr(self.sensors.ser, 'reset_output_buffer'):
                        self.sensors.ser.reset_output_buffer()
        except Exception as e:
            # Игнорируем ошибки очистки буферов
            pass
    
    def flush_sensor_queue(self):
        """Полностью очищает очередь измерений сенсоров"""
        if not hasattr(self, 'sensor_data_queue') or self.sensor_data_queue is None:
            return
        try:
            while True:
                self.sensor_data_queue.get_nowait()
        except Empty:
            pass

    def finalize_calibration_failure(
        self,
        cmd_code: int,
        message: str,
        cleanup_flags: List[str] = None,
        register_doublewords: List[int] = None,
        cache_attrs: List[str] = None,
    ):
        """Общий обработчик ошибок калибровок: логирует, сбрасывает команду и возвращает систему в IDLE"""
        if message:
            print(message)
        try:
            self.write_cycle_flag(-1)
        except Exception:
            pass

        # Обнуляем калибровочные регистры, если указано
        if register_doublewords and self.modbus_server and self.modbus_server.slave_context:
            for base_addr in register_doublewords:
                try:
                    # base_addr - реальный адрес (например 40010). В setValues используем индекс (addr-40000)
                    idx = base_addr - 40000
                    self.modbus_server.slave_context.setValues(3, idx, [0])      # старшее слово
                    self.modbus_server.slave_context.setValues(3, idx + 1, [0])  # младшее слово
                except Exception as e:
                    print(f" [CALIBRATION] Ошибка обнуления регистров {base_addr}-{base_addr+1}: {e}")

        self.reset_command()
        self.clear_measurement_buffers()
        self.clear_serial_buffers()
        if cleanup_flags:
            for flag in cleanup_flags:
                if hasattr(self, flag):
                    delattr(self, flag)
        if cache_attrs:
            for attr in cache_attrs:
                if hasattr(self, attr):
                    setattr(self, attr, None)
        if hasattr(self, 'calibration_in_progress') and self.calibration_in_progress:
            self.calibration_in_progress = False
        self.current_state = SystemState.IDLE

    def is_valid_measurement(self, value: float, max_range: float = None, min_range: float = None) -> bool:
        """
        Проверка валидности измерения датчика
        
        Args:
            value: Значение измерения в мм
            max_range: Максимальный диапазон датчика (по умолчанию sensor_range_mm * 2)
            min_range: Минимальный диапазон датчика (по умолчанию 20 мм - базовое расстояние)
        
        Returns:
            True если значение валидно, False если некорректно (None, 0, отрицательное, вне диапазона)
        """
        if value is None:
            return False
        if value <= 0.0:
            return False  # Нулевые и отрицательные значения некорректны
        
        # Устанавливаем минимальный диапазон (базовое расстояние датчика - 20 мм)
        if min_range is None:
            min_range = 20.0  # Базовое расстояние датчика
        
        # Проверяем минимальный диапазон
        if value < min_range:
            return False  # Значение меньше минимального диапазона
        
        # Устанавливаем максимальный диапазон
        if max_range is None:
            max_range = self.sensor_range_mm * 2.0  # Максимальный диапазон (25 * 2 = 50 мм)
        
        # Проверяем максимальный диапазон
        if value > max_range:
            return False  # Значение вне диапазона датчика
        
        return True
    
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
        
        # Буферы команды 11 (фланец)
        self.sensor1_flange_measurements = []
        self.sensor3_measurements = []
        self.sensor4_measurements = []
        self.temp_sensor1_flange_buffer = []
        self.temp_sensor3_buffer = []
        self.temp_sensor4_buffer = []
        # Буферы диаметров и толщин - очищаем при каждом новом измерении
        self.body_diameter_buffer = []
        self.flange_diameter_buffer = []
        self.bottom_thickness_buffer = []
        
        # Буферы команды 12
        self.sensor1_bottom_measurements = []
        self.sensor2_bottom_measurements = []
        self.temp_sensor1_bottom_buffer = []
        self.temp_sensor2_bottom_buffer = []
        self.bottom_wall_thickness_buffer = []

        # Буферы раздельных команд диаметров (20/30/40)
        self.sensor3_flange_only_measurements = []
        self.sensor3_body_only_measurements = []
        self.sensor3_body2_measurements = []
        self.temp_sensor3_flange_only_buffer = []
        self.temp_sensor3_body_only_buffer = []
        self.temp_sensor3_body2_buffer = []
        self.body_only_diameter_buffer = []
        self.body2_diameter_buffer = []
        
        # Буферы QUAD потокового режима (CMD=200)
        self.stream_temp_sensor1_buffer = []
        self.stream_temp_sensor2_buffer = []
        self.stream_temp_sensor3_buffer = []
        self.stream_temp_sensor4_buffer = []
        
        print(" Буферы измерений очищены")
        self.flush_sensor_queue()
    
    def stop_all_streams(self):
        """Остановка всех активных потоковых режимов"""
        if self.sensors and self.stream_active_quad:
            try:
                self.stream_active_quad = False
                print(" Остановлен QUAD потоковый режим")
            except Exception as e:
                print(f" Ошибка остановки QUAD режима: {e}")
    
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
        elif self.current_state == SystemState.CALIBRATE_FLANGE_DIAMETER:
            self.handle_calibrate_flange_diameter_state()
        elif self.current_state == SystemState.CALIBRATE_BODY_DIAMETER_SEPARATE:
            self.handle_calibrate_body_diameter_separate_state()
        elif self.current_state == SystemState.CALIBRATE_BODY2_DIAMETER:
            self.handle_calibrate_body2_diameter_state()
        elif self.current_state == SystemState.CONFIGURE_SENSOR3_RANGE:
            self.handle_configure_sensor3_range_state()
        elif self.current_state == SystemState.DEBUG_REGISTERS:
            self.handle_debug_registers_state()
            
        # Измерение высоты - ОТКЛЮЧЕНО: расчет высоты производится на ПЛК
        # Регистр 40057-40058 записывается только ПЛК/HMI
        # elif self.current_state == SystemState.MEASURE_HEIGHT_PROCESS:
        #     self.handle_measure_height_process_state()
            
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
        elif self.current_state == SystemState.MEASURE_FLANGE_ONLY_PROCESS:
            self.handle_measure_flange_only_process_state()
        elif self.current_state == SystemState.MEASURE_FLANGE_ONLY_CALCULATE:
            self.handle_calculate_flange_only_state()
        elif self.current_state == SystemState.MEASURE_BODY_ONLY_PROCESS:
            self.handle_measure_body_only_process_state()
        elif self.current_state == SystemState.MEASURE_BODY_ONLY_CALCULATE:
            self.handle_calculate_body_only_state()
        elif self.current_state == SystemState.MEASURE_BODY2_PROCESS:
            self.handle_measure_body2_process_state()
        elif self.current_state == SystemState.MEASURE_BODY2_CALCULATE:
            self.handle_calculate_body2_state()
            
        # Основной цикл измерения - нижняя стенка
        elif self.current_state == SystemState.MEASURE_BOTTOM_PROCESS:
            self.handle_measure_bottom_process_state()
        elif self.current_state == SystemState.MEASURE_BOTTOM_CALCULATE:
            self.handle_calculate_bottom_state()
            
        # Оценка качества изделия
        elif self.current_state == SystemState.QUALITY_EVALUATION:
            self.handle_quality_evaluation_state()
            
        # Потоковый режим (QUAD - все 4 датчика)
        elif self.current_state == SystemState.STREAM_QUAD:
            self.handle_stream_quad_state()
            
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
            sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.read_sensors_safe()
            if sensor1_mm is None:
                time.sleep(0.001)
                return
            
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
            # Фильтруем некорректные измерения перед добавлением
            if self.is_valid_measurement(sensor1_mm):
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
    
    def handle_calibrate_flange_diameter_state(self):
        """
        CMD=105: Калибровка диаметра фланца
        - Читаем эталонный диаметр фланца из регистров 40030-40031
        - Непрерывно собираем данные от датчика 3 до получения CMD=0
        - Расчет и запись результатов выполняется при переходе CMD -> 0
        """
        if not self.sensors:
            self.finalize_calibration_failure(
                105,
                " [CMD=105] Ошибка: датчики не подключены!",
                cleanup_flags=['calibrate_flange_diameter_started'],
                register_doublewords=[40032],
                cache_attrs=['cached_distance_sensor3_to_center'],
            )
            return
        
        try:
            # Инициализация при первом запуске
            if not hasattr(self, 'calibrate_flange_diameter_started'):
                self.calibration_in_progress = True
                self.calibrate_flange_diameter_started = True
                self.calibrate_flange_diameter_sensor3_buffer = []
                self.calibrate_flange_diameter_measurement_count = 0
                self.calibrate_flange_diameter_last_log_time = time.time()
                
                # Очищаем буферы серийного порта перед началом измерений
                self.clear_serial_buffers()
                self.flush_sensor_queue()
                
                # Очищаем буфер датчика 3
                self.measurement_buffer['sensor3'].clear()
                
                # Читаем эталонный диаметр фланца из регистров 40030-40031
                reference_flange_diameter = self.read_reference_flange_diameter()
                print(f"🔧 НАЧАЛО КАЛИБРОВКИ ДИАМЕТРА ФЛАНЦА (CMD=105)")
                print(f" [CMD=105] Эталонный диаметр фланца: {reference_flange_diameter:.3f} мм")
                
                if reference_flange_diameter <= 0:
                    self.finalize_calibration_failure(
                        105,
                        " [CMD=105] ОШИБКА: Эталонный диаметр фланца должен быть больше 0!",
                        cleanup_flags=['calibrate_flange_diameter_started'],
                        register_doublewords=[40032],
                        cache_attrs=['cached_distance_sensor3_to_center'],
                    )
                    return
                
                # Устанавливаем статус калибровки
                self.write_cycle_flag(105)
                print(f" [CMD=105] Непрерывный сбор данных от датчика 3...")
            
            # Непрерывный сбор данных (без таймаута)
            sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.read_sensors_safe()
            if sensor3_mm is None:
                time.sleep(0.002)
                return
                
            # Сохраняем только валидные измерения датчика 3
            if self.is_valid_measurement(sensor3_mm):
                self.measurement_buffer['sensor3'].append(sensor3_mm)
                self.calibrate_flange_diameter_sensor3_buffer.append(sensor3_mm)
                self.calibrate_flange_diameter_measurement_count += 1

            # Показываем прогресс раз в секунду
            current_time = time.time()
            if current_time - getattr(self, 'calibrate_flange_diameter_last_log_time', 0) >= 1.0:
                print(f" [CMD=105] Сбор данных... Измерений датчика 3: {self.calibrate_flange_diameter_measurement_count}")
                self.calibrate_flange_diameter_last_log_time = current_time
                
        except Exception as e:
            print(f" [CMD=105] Ошибка калибровки диаметра фланца: {e}")
            import traceback
            traceback.print_exc()
            self.finalize_calibration_failure(
                105,
                f" [CMD=105] Ошибка калибровки: {e}",
                cleanup_flags=['calibrate_flange_diameter_started'],
                register_doublewords=[40032],
                cache_attrs=['cached_distance_sensor3_to_center'],
            )
    
    def read_reference_flange_diameter(self) -> float:
        """Чтение эталонного диаметра фланца из регистров 40030, 40031"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # HMI: старшее слово в 40030, младшее в 40031
                values = self.modbus_server.slave_context.getValues(3, 30, 2)  # 40030-40031 -> индексы 29-30
                if values and len(values) >= 2:
                    high_word = int(values[0])  # 40030 - старший
                    low_word = int(values[1])  # 40031 - младший
                    diameter = self.doubleword_to_float(low_word, high_word)
                    return diameter
        except Exception as e:
            print(f" [CMD=105] Ошибка чтения эталонного диаметра фланца: {e}")
        return 0.0

    def handle_calibrate_body_diameter_separate_state(self):
        """
        CMD=107: Калибровка раздельного диаметра корпуса
        - Читаем эталонный диаметр из регистров 40034-40035
        - Непрерывно собираем данные от датчика 3 до получения CMD=0
        - Расчет и запись выполняются при переходе CMD -> 0
        """
        if not self.sensors:
            self.finalize_calibration_failure(
                107,
                " [CMD=107] Ошибка: датчики не подключены!",
                cleanup_flags=['calibrate_body_diameter_separate_started'],
                register_doublewords=[40038],
                cache_attrs=['cached_distance_sensor3_to_center_body'],
            )
            return

        try:
            if not hasattr(self, 'calibrate_body_diameter_separate_started'):
                self.calibration_in_progress = True
                self.calibrate_body_diameter_separate_started = True
                self.calibrate_body_diameter_separate_sensor3_buffer = []
                self.calibrate_body_diameter_separate_measurement_count = 0
                self.calibrate_body_diameter_separate_last_log_time = time.time()

                self.clear_serial_buffers()
                self.flush_sensor_queue()
                self.measurement_buffer['sensor3'].clear()

                reference_body_diameter = self.read_reference_body_diameter_separate()
                print("🔧 НАЧАЛО КАЛИБРОВКИ РАЗДЕЛЬНОГО ДИАМЕТРА КОРПУСА (CMD=107)")
                print(f" [CMD=107] Эталонный диаметр: {reference_body_diameter:.3f} мм")

                if reference_body_diameter <= 0:
                    self.finalize_calibration_failure(
                        107,
                        " [CMD=107] ОШИБКА: Эталонный диаметр должен быть больше 0!",
                        cleanup_flags=['calibrate_body_diameter_separate_started'],
                        register_doublewords=[40038],
                        cache_attrs=['cached_distance_sensor3_to_center_body'],
                    )
                    return

                self.write_cycle_flag(107)
                print(" [CMD=107] Непрерывный сбор данных от датчика 3...")

            sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.read_sensors_safe()
            if sensor3_mm is None:
                time.sleep(0.002)
                return

            if self.is_valid_measurement(sensor3_mm):
                self.measurement_buffer['sensor3'].append(sensor3_mm)
                self.calibrate_body_diameter_separate_sensor3_buffer.append(sensor3_mm)
                self.calibrate_body_diameter_separate_measurement_count += 1

            current_time = time.time()
            if current_time - getattr(self, 'calibrate_body_diameter_separate_last_log_time', 0) >= 1.0:
                print(f" [CMD=107] Сбор данных... Измерений датчика 3: {self.calibrate_body_diameter_separate_measurement_count}")
                self.calibrate_body_diameter_separate_last_log_time = current_time

        except Exception as e:
            print(f" [CMD=107] Ошибка калибровки раздельного диаметра корпуса: {e}")
            import traceback
            traceback.print_exc()
            self.finalize_calibration_failure(
                107,
                f" [CMD=107] Ошибка калибровки: {e}",
                cleanup_flags=['calibrate_body_diameter_separate_started'],
                register_doublewords=[40038],
                cache_attrs=['cached_distance_sensor3_to_center_body'],
            )

    def handle_calibrate_body2_diameter_state(self):
        """
        CMD=108: Калибровка диаметра корпуса 2
        - Читаем эталонный диаметр из регистров 40036-40037
        - Непрерывно собираем данные от датчика 3 до получения CMD=0
        - Расчет и запись выполняются при переходе CMD -> 0
        """
        if not self.sensors:
            self.finalize_calibration_failure(
                108,
                " [CMD=108] Ошибка: датчики не подключены!",
                cleanup_flags=['calibrate_body2_diameter_started'],
                register_doublewords=[40040],
                cache_attrs=['cached_distance_sensor3_to_center_body2'],
            )
            return

        try:
            if not hasattr(self, 'calibrate_body2_diameter_started'):
                self.calibration_in_progress = True
                self.calibrate_body2_diameter_started = True
                self.calibrate_body2_diameter_sensor3_buffer = []
                self.calibrate_body2_diameter_measurement_count = 0
                self.calibrate_body2_diameter_last_log_time = time.time()

                self.clear_serial_buffers()
                self.flush_sensor_queue()
                self.measurement_buffer['sensor3'].clear()

                reference_body2_diameter = self.read_reference_body2_diameter()
                print("🔧 НАЧАЛО КАЛИБРОВКИ ДИАМЕТРА КОРПУСА 2 (CMD=108)")
                print(f" [CMD=108] Эталонный диаметр: {reference_body2_diameter:.3f} мм")

                if reference_body2_diameter <= 0:
                    self.finalize_calibration_failure(
                        108,
                        " [CMD=108] ОШИБКА: Эталонный диаметр должен быть больше 0!",
                        cleanup_flags=['calibrate_body2_diameter_started'],
                        register_doublewords=[40040],
                        cache_attrs=['cached_distance_sensor3_to_center_body2'],
                    )
                    return

                self.write_cycle_flag(108)
                print(" [CMD=108] Непрерывный сбор данных от датчика 3...")

            sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.read_sensors_safe()
            if sensor3_mm is None:
                time.sleep(0.002)
                return

            if self.is_valid_measurement(sensor3_mm):
                self.measurement_buffer['sensor3'].append(sensor3_mm)
                self.calibrate_body2_diameter_sensor3_buffer.append(sensor3_mm)
                self.calibrate_body2_diameter_measurement_count += 1

            current_time = time.time()
            if current_time - getattr(self, 'calibrate_body2_diameter_last_log_time', 0) >= 1.0:
                print(f" [CMD=108] Сбор данных... Измерений датчика 3: {self.calibrate_body2_diameter_measurement_count}")
                self.calibrate_body2_diameter_last_log_time = current_time

        except Exception as e:
            print(f" [CMD=108] Ошибка калибровки диаметра корпуса 2: {e}")
            import traceback
            traceback.print_exc()
            self.finalize_calibration_failure(
                108,
                f" [CMD=108] Ошибка калибровки: {e}",
                cleanup_flags=['calibrate_body2_diameter_started'],
                register_doublewords=[40040],
                cache_attrs=['cached_distance_sensor3_to_center_body2'],
            )

    def read_reference_body_diameter_separate(self) -> float:
        """Чтение эталонного диаметра корпуса (раздельно) из регистров 40034, 40035"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                values = self.modbus_server.slave_context.getValues(3, 34, 2)  # 40034-40035
                if values and len(values) >= 2:
                    high_word = int(values[0])
                    low_word = int(values[1])
                    return self.doubleword_to_float(low_word, high_word)
        except Exception as e:
            print(f" [CMD=107] Ошибка чтения эталонного диаметра: {e}")
        return 0.0

    def read_reference_body2_diameter(self) -> float:
        """Чтение эталонного диаметра корпуса 2 из регистров 40036, 40037"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                values = self.modbus_server.slave_context.getValues(3, 36, 2)  # 40036-40037
                if values and len(values) >= 2:
                    high_word = int(values[0])
                    low_word = int(values[1])
                    return self.doubleword_to_float(low_word, high_word)
        except Exception as e:
            print(f" [CMD=108] Ошибка чтения эталонного диаметра: {e}")
        return 0.0

    def write_distance_sensor3_to_center_body(self, distance: float):
        """Запись расстояния датчик 3 - центр для раздельного диаметра корпуса в 40038-40039"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                low_word, high_word = self.float_to_doubleword(distance)
                self.modbus_server.slave_context.setValues(3, 38, [int(high_word)])
                self.modbus_server.slave_context.setValues(3, 39, [int(low_word)])
                print(f" [CMD=107] Записано расстояние 40038-40039: {distance:.3f} мм")
        except Exception as e:
            print(f" [CMD=107] Ошибка записи расстояния 40038-40039: {e}")

    def write_distance_sensor3_to_center_body2(self, distance: float):
        """Запись расстояния датчик 3 - центр для диаметра корпуса 2 в 40040-40041"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                low_word, high_word = self.float_to_doubleword(distance)
                self.modbus_server.slave_context.setValues(3, 40, [int(high_word)])
                self.modbus_server.slave_context.setValues(3, 41, [int(low_word)])
                print(f" [CMD=108] Записано расстояние 40040-40041: {distance:.3f} мм")
        except Exception as e:
            print(f" [CMD=108] Ошибка записи расстояния 40040-40041: {e}")
    
    def handle_configure_sensor3_range_state(self):
        """
        CMD=106: Настройка диапазонов для дискретного сигнала датчика 3
        - Читаем начало диапазона из регистров 40404-40405 (в мм)
        - Читаем конец диапазона из регистров 40406-40407 (в мм)
        - Вычисляем значения для протокола RIFTEK по формуле
        - Записываем параметры 0Ch-0Fh в датчик 3
        - Сохраняем параметры в FLASH память датчика
        """
        if not self.sensors or not self.sensors.ser:
            print(" [CMD=106] Ошибка: датчики не подключены!")
            self.write_cycle_flag(-1)
            self.current_state = SystemState.ERROR
            return
        
        try:
            # Инициализация при первом запуске
            if not hasattr(self, 'configure_sensor3_range_started'):
                self.configure_sensor3_range_started = True
                print(f"🔧 НАЧАЛО НАСТРОЙКИ ДИАПАЗОНОВ ДАТЧИКА 3 (CMD=106)")
                
                # Читаем начало диапазона из регистров 40404-40405
                range_start_mm = self.read_range_start()
                print(f" [CMD=106] Начало диапазона: {range_start_mm:.3f} мм")
                
                # Читаем конец диапазона из регистров 40406-40407
                range_end_mm = self.read_range_end()
                print(f" [CMD=106] Конец диапазона: {range_end_mm:.3f} мм")
                
                # Вычисляем значения для протокола RIFTEK
                # Формула: riftek_value = int((16384/25) * (mm_value - 25))
                riftek_value_min = int((16384 / 25) * (range_start_mm - 20))
                riftek_value_max = int((16384 / 25) * (range_end_mm - 20))
                
                # Ограничиваем значения диапазоном 0-16383
                riftek_value_min = max(0, min(16383, riftek_value_min))
                riftek_value_max = max(0, min(16383, riftek_value_max))
                
                print(f" [CMD=106] Значение RIFTEK (начало): {riftek_value_min}")
                print(f" [CMD=106] Значение RIFTEK (конец): {riftek_value_max}")
                
                # Записываем параметры в датчик 3 (адрес 0x03)
                sensor_address = 3
                
                # Параметр 0Ch - младший байт начала окна
                if not self.write_riftek_parameter(sensor_address, 0x0C, riftek_value_min & 0xFF):
                    raise Exception("Ошибка записи параметра 0Ch")
                
                # Параметр 0Dh - старший байт начала окна
                if not self.write_riftek_parameter(sensor_address, 0x0D, (riftek_value_min >> 8) & 0xFF):
                    raise Exception("Ошибка записи параметра 0Dh")
                
                # Параметр 0Eh - младший байт конца окна
                if not self.write_riftek_parameter(sensor_address, 0x0E, riftek_value_max & 0xFF):
                    raise Exception("Ошибка записи параметра 0Eh")
                
                # Параметр 0Fh - старший байт конца окна
                if not self.write_riftek_parameter(sensor_address, 0x0F, (riftek_value_max >> 8) & 0xFF):
                    raise Exception("Ошибка записи параметра 0Fh")
                
                print(" [CMD=106] Параметры записаны в датчик 3")
                
                # Сохраняем параметры в FLASH память
                if not self.save_riftek_parameters_to_flash(sensor_address):
                    raise Exception("Ошибка сохранения параметров в FLASH")
                
                print(" [CMD=106] Параметры сохранены в FLASH память датчика 3")
                
                # Устанавливаем статус успешного завершения
                self.write_cycle_flag(106)
                print(" [CMD=106] Настройка диапазонов завершена успешно")
                
                # Задержка перед сбросом команды
                time.sleep(1)
                
                # Переходим в IDLE и сбрасываем команду
                self.current_state = SystemState.IDLE
                self.reset_command()
                if hasattr(self, 'configure_sensor3_range_started'):
                    delattr(self, 'configure_sensor3_range_started')
                
        except Exception as e:
            print(f" [CMD=106] Ошибка настройки диапазонов: {e}")
            import traceback
            traceback.print_exc()
            self.write_cycle_flag(-1)
            
            # Задержка перед сбросом команды при ошибке
            time.sleep(1)
            
            self.reset_command()
            self.current_state = SystemState.ERROR
            if hasattr(self, 'configure_sensor3_range_started'):
                delattr(self, 'configure_sensor3_range_started')
    
    def read_range_start(self) -> float:
        """Чтение начала диапазона из регистров 40404-40405"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # 40404-40405 -> индексы 403-404
                # HMI: старшее слово в 40404, младшее в 40405
                values = self.modbus_server.slave_context.getValues(3, 404, 2)
                if values and len(values) >= 2:
                    high_word = int(values[0])  # 40404 - старшее слово
                    low_word = int(values[1])  # 40405 - младшее слово
                    return self.doubleword_to_float(low_word, high_word)
        except Exception as e:
            print(f" [CMD=106] Ошибка чтения начала диапазона: {e}")
        return 0.0
    
    def read_range_end(self) -> float:
        """Чтение конца диапазона из регистров 40406-40407"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # 40406-40407 -> индексы 405-406
                # HMI: старшее слово в 40406, младшее в 40407
                values = self.modbus_server.slave_context.getValues(3, 406, 2)
                if values and len(values) >= 2:
                    high_word = int(values[0])  # 40406 - старшее слово
                    low_word = int(values[1])  # 40407 - младшее слово
                    return self.doubleword_to_float(low_word, high_word)
        except Exception as e:
            print(f" [CMD=106] Ошибка чтения конца диапазона: {e}")
        return 0.0
    
    def write_riftek_parameter(self, sensor_address: int, param_code: int, param_value: int) -> bool:
        """
        Запись параметра в датчик по протоколу RIFTEK
        
        Args:
            sensor_address: Адрес датчика (1-4)
            param_code: Код параметра (0x00-0xFF)
            param_value: Значение параметра (0-255 для однобайтных параметров)
            
        Returns:
            True если команда отправлена успешно
        """
        try:
            if not self.sensors or not self.sensors.ser:
                return False
            
            # Формат команды записи параметра (03h):
            # Байт 0: 0|ADR (адрес датчика, старший бит = 0)
            # Байт 1: 1|000|COD (код запроса 03h = 0x83)
            # Байт 2: 1|SB|CNT|MSG[0] lo (младшая тетрада кода параметра)
            # Байт 3: 1|SB|CNT|MSG[0] hi (старшая тетрада кода параметра)
            # Байт 4: 1|SB|CNT|MSG[1] lo (младшая тетрада значения параметра)
            # Байт 5: 1|SB|CNT|MSG[1] hi (старшая тетрада значения параметра)
            
            # Код параметра передается потетрадно
            param_code_lo = param_code & 0x0F
            param_code_hi = (param_code >> 4) & 0x0F
            
            # Значение параметра передается потетрадно
            param_value_lo = param_value & 0x0F
            param_value_hi = (param_value >> 4) & 0x0F
            
            # Формируем команду
            command = bytes([
                sensor_address,  # Байт 0: адрес датчика
                0x83,  # Байт 1: код запроса 03h (0x83 = 1|000|0011)
                0x80 | param_code_lo,  # Байт 2: младшая тетрада кода параметра
                0x80 | param_code_hi,  # Байт 3: старшая тетрада кода параметра
                0x80 | param_value_lo,  # Байт 4: младшая тетрада значения
                0x80 | param_value_hi,  # Байт 5: старшая тетрада значения
            ])
            
            # Отправляем команду
            self.sensors.ser.write(command)
            time.sleep(0.01)  # Небольшая задержка для обработки команды датчиком
            
            return True
            
        except Exception as e:
            print(f" [CMD=106] Ошибка записи параметра {param_code:02X}h: {e}")
            return False
    
    def save_riftek_parameters_to_flash(self, sensor_address: int) -> bool:
        """
        Сохранение параметров в FLASH память датчика (команда 04h с константой 0xAA)
        
        Args:
            sensor_address: Адрес датчика (1-4)
            
        Returns:
            True если команда отправлена успешно
        """
        try:
            if not self.sensors or not self.sensors.ser:
                return False
            
            # Формат команды сохранения в FLASH (04h):
            # Байт 0: 0|ADR (адрес датчика)
            # Байт 1: 1|000|COD (код запроса 04h = 0x84)
            # Байт 2: 1|SB|CNT|MSG[0] lo (младшая тетрада константы 0xAA = 0xA)
            # Байт 3: 1|SB|CNT|MSG[0] hi (старшая тетрада константы 0xAA = 0xA)
            
            command = bytes([
                sensor_address,  # Байт 0: адрес датчика
                0x84,  # Байт 1: код запроса 04h (0x84 = 1|000|0100)
                0x8A,  # Байт 2: младшая тетрада 0xAA (0xA = 0x80 | 0x0A)
                0x8A,  # Байт 3: старшая тетрада 0xAA (0xA = 0x80 | 0x0A)
            ])
            
            # Отправляем команду
            self.sensors.ser.write(command)
            time.sleep(0.1)  # Задержка для сохранения в FLASH (может занять время)
            
            return True
            
        except Exception as e:
            print(f" [CMD=106] Ошибка сохранения в FLASH: {e}")
            return False

    def read_recipe_flange_diameter(self) -> float:
        """Чтение рецепта диаметра фланца из регистров 40388, 40389"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                values = self.modbus_server.slave_context.getValues(3, 388, 2)  # 40388-40389
                if values and len(values) >= 2:
                    high_word = int(values[0])  # 40388 - старший
                    low_word = int(values[1])   # 40389 - младший
                    diameter = self.doubleword_to_float(low_word, high_word)
                    return diameter
        except Exception as e:
            print(f" Ошибка чтения рецепта диаметра фланца: {e}")
        return 0.0
    
    def read_recipe_body_diameter(self) -> float:
        """Чтение рецепта диаметра корпуса из регистров 40382, 40383"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                values = self.modbus_server.slave_context.getValues(3, 382, 2)  # 40382-40383
                if values and len(values) >= 2:
                    high_word = int(values[0])  # 40382 - старший
                    low_word = int(values[1])   # 40383 - младший
                    diameter = self.doubleword_to_float(low_word, high_word)
                    return diameter
        except Exception as e:
            print(f" Ошибка чтения рецепта диаметра корпуса: {e}")
        return 0.0

    def read_upper_wall_offset_coeff(self) -> float:
        """Коэффициент смещения толщины верхней стенки (40500-40501)"""
        return self._read_offset_coeff(500)

    def read_lower_wall_offset_coeff(self) -> float:
        """Коэффициент смещения толщины нижней стенки (40502-40503)"""
        return self._read_offset_coeff(502)

    def read_body_diameter_offset_coeff(self) -> float:
        """Коэффициент смещения диаметра корпуса (40504-40505)"""
        return self._read_offset_coeff(504)

    def read_flange_diameter_offset_coeff(self) -> float:
        """Коэффициент смещения диаметра фланца (40506-40507)"""
        return self._read_offset_coeff(506)

    def read_bottom_thickness_offset_coeff(self) -> float:
        """Коэффициент смещения толщины дна (40508-40509)"""
        return self._read_offset_coeff(508)
    
    def read_upper_wall_extrapolation_coeff(self) -> float:
        """Коэффициент экстраполяции толщины верхней стенки (40511-40512)"""
        return self._read_offset_coeff(510)  # 40511 - 40000 = 511
    
    def read_bottom_wall_extrapolation_coeff(self) -> float:
        """Коэффициент экстраполяции толщины нижней стенки (40513-40514)"""
        return self._read_offset_coeff(512)  # 40513 - 40000 = 513
    
    def read_body_diameter_extrapolation_coeff(self) -> float:
        """Коэффициент экстраполяции диаметра корпуса (40515-40516)"""
        return self._read_offset_coeff(514)  # 40515 - 40000 = 515
    
    def read_flange_diameter_extrapolation_coeff(self) -> float:
        """Коэффициент экстраполяции диаметра фланца (40517-40518)"""
        return self._read_offset_coeff(516)  # 40517 - 40000 = 517
    
    def read_bottom_thickness_extrapolation_coeff(self) -> float:
        """Коэффициент экстраполяции толщины дна (40519-40520)"""
        return self._read_offset_coeff(518)  # 40519 - 40000 = 519

    def read_body2_diameter_extrapolation_coeff(self) -> float:
        """Коэффициент экстраполяции диаметра корпуса 2 (40521-40522)"""
        return self._read_offset_coeff(520)

    def read_body2_diameter_offset_coeff(self) -> float:
        """Коэффициент смещения диаметра корпуса 2 (40523-40524)"""
        return self._read_offset_coeff(522)

    EXTREMA_OFFSET_REGISTERS = {
        'upper_wall': (560, 562),
        'lower_wall': (564, 566),
        'body_diameter': (568, 570),
        'flange_diameter': (572, 574),
        'bottom_thickness': (576, 578),
        'body_diameter_2': (580, 582),
    }

    def apply_extrema_offsets(self, parameter: str, max_value: float, min_value: float) -> Tuple[float, float]:
        """Прибавляет отдельные смещения к окончательным max/min; среднее не изменяется."""
        registers = self.EXTREMA_OFFSET_REGISTERS.get(parameter)
        if registers is None:
            raise ValueError(f"Неизвестный параметр коэффициентов min/max: {parameter}")

        min_offset = self._read_offset_coeff(registers[0])
        max_offset = self._read_offset_coeff(registers[1])
        adjusted_max = max_value + max_offset
        adjusted_min = min_value + min_offset

        print(
            f" [СМЕЩЕНИЕ MIN/MAX] {parameter}: "
            f"max {max_value:.6f} + {max_offset:.6f} = {adjusted_max:.6f}; "
            f"min {min_value:.6f} + {min_offset:.6f} = {adjusted_min:.6f}"
        )
        return adjusted_max, adjusted_min

    def _sanitize_percentile_bounds(self, lower: float, upper: float, param_name: str = "") -> Tuple[float, float]:
        """
        Нормализация процентилей из HMI.
        Допустимый диапазон: 0..100, lower < upper.
        При невалидных значениях используется fallback 0/100 (без процентильного отсечения).
        """
        fallback_lower = 0.0
        fallback_upper = 100.0

        try:
            lower_val = float(lower)
            upper_val = float(upper)
        except Exception:
            return fallback_lower, fallback_upper

        if not (math.isfinite(lower_val) and math.isfinite(upper_val)):
            return fallback_lower, fallback_upper

        lower_val = max(0.0, min(100.0, lower_val))
        upper_val = max(0.0, min(100.0, upper_val))

        # Частый случай пустых регистров после старта
        if abs(lower_val) < 1e-9 and abs(upper_val) < 1e-9:
            return fallback_lower, fallback_upper

        if lower_val >= upper_val:
            print(f" [PERCENTILE] Некорректные границы для '{param_name}': Pmin={lower_val}, Pmax={upper_val}. Использую 0/100")
            return fallback_lower, fallback_upper

        return lower_val, upper_val

    def read_percentile_bounds(self, param_key: str) -> Tuple[float, float]:
        """
        Чтение настраиваемых процентилей (Pmin, Pmax) из holding регистров.
        Возвращает (lower, upper).
        """
        percentile_registers = {
            'height': ((40530, 40531), (40532, 40533)),
            'upper_wall': ((40534, 40535), (40536, 40537)),
            'bottom_wall': ((40538, 40539), (40540, 40541)),
            'bottom': ((40542, 40543), (40544, 40545)),
            'body_diameter': ((40546, 40547), (40548, 40549)),
            'flange_diameter': ((40550, 40551), (40552, 40553)),
            'body_diameter_2': ((40554, 40555), (40556, 40557)),
        }

        if param_key not in percentile_registers:
            return 0.0, 100.0

        pmin_regs, pmax_regs = percentile_registers[param_key]
        pmin = self.read_float_from_registers(pmin_regs, 'holding')
        pmax = self.read_float_from_registers(pmax_regs, 'holding')
        return self._sanitize_percentile_bounds(pmin, pmax, param_key)

    def get_smoothing_window_size(self) -> int:
        """
        Единый размер окна сглаживания из регистра 40558-40559.
        Правила:
        - 0 или 1 -> 1 точка
        - >1 -> округление до целого (2.45->2, 2.64->3, 4.01->4)
        """
        value = self.read_float_from_registers((40558, 40559), 'holding')
        if not math.isfinite(value):
            return 1
        if value <= 1.0:
            return 1
        return max(1, int(math.floor(value + 0.5)))

    @staticmethod
    def _median_from_sorted(sorted_values: list) -> float:
        count = len(sorted_values)
        if count == 0:
            return 0.0
        mid = count // 2
        if count % 2 == 0:
            return (sorted_values[mid - 1] + sorted_values[mid]) / 2.0
        return sorted_values[mid]

    def _filtered_average_with_median(self, values: list, min_filtered_count: int) -> float:
        """
        Усреднение с медианным фильтром:
        - медиана текущего окна
        - отбрасываем точки, отклоняющиеся от медианы > 1.5 мм
        - если отфильтрованных точек недостаточно, возвращаем медиану
        """
        if not values:
            return 0.0

        sorted_values = sorted(values)
        median_value = self._median_from_sorted(sorted_values)
        filtered_values = [v for v in values if abs(v - median_value) <= 1.5]
        if len(filtered_values) >= max(1, min_filtered_count):
            return sum(filtered_values) / len(filtered_values)
        return median_value

    def _read_offset_coeff(self, base_index: int) -> float:
        """Общий метод чтения коэффициента смещения"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                values = self.modbus_server.slave_context.getValues(3, base_index, 2)
                if values and len(values) >= 2:
                    high_word = int(values[0])
                    low_word = int(values[1])
                    return self.doubleword_to_float(low_word, high_word)
        except Exception as e:
            print(f" Ошибка чтения коэффициента смещения (base_index={base_index}): {e}")
        return 0.0
    
    def write_distance_sensor3_to_center(self, distance: float):
        """Запись расстояния между датчиком 3 и центром в регистры 40032, 40033"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                low_word, high_word = self.float_to_doubleword(distance)
                # HMI читает: старшее слово из 40032, младшее из 40033
                self.modbus_server.slave_context.setValues(3, 32, [int(high_word)])  # 40032 - старший (индекс 31)
                self.modbus_server.slave_context.setValues(3, 33, [int(low_word)])   # 40033 - младший (индекс 32)
                # Сохранение в БД
                # Отключено сохранение в БД - сохраняем только указанные величины
                # if self.db_integration:
                #     self.db_integration.save_doubleword_register(40032, 'holding', distance, 'Расстояние датчик 3 - центр')
                print(f" [CMD=105] Записано расстояние датчик 3 - центр 40032-40033: {distance:.3f} мм (high: {int(high_word)}, low: {int(low_word)})")
        except Exception as e:
            print(f" [CMD=105] Ошибка записи расстояния датчик 3 - центр: {e}")

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
                # Отключено сохранение в БД - сохраняем только указанные величины
                # if self.db_integration:
                #     self.db_integration.save_doubleword_register(40055, 'holding', distance, 'Дистанция до плоскости')
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
        if not self.sensors:
            self.calibration_data['wall_distance_1_2'] = 0.0
            self.calibration_data['wall_distance_1_3'] = 0.0
            self.finalize_calibration_failure(
                100,
                " [CMD=100] Ошибка: датчики не подключены!",
                register_doublewords=[40010, 40012],
                cache_attrs=['cached_distance_1_2', 'cached_distance_1_3'],
            )
            return

        try:
            if not hasattr(self, '_wall_calibration_initialized'):
                print("🔧 НАЧАЛО КАЛИБРОВКИ СТЕНКИ")
                self.calibration_in_progress = True
                self._wall_calibration_initialized = True
                self._wall_reference_thickness = self.read_reference_thickness()
                print(f" Толщина эталона: {self._wall_reference_thickness:.3f} мм")
                if self._wall_reference_thickness <= 0:
                    self.calibration_data['wall_distance_1_2'] = 0.0
                    self.calibration_data['wall_distance_1_3'] = 0.0
                    self.finalize_calibration_failure(
                        100,
                        " [CMD=100] ОШИБКА: толщина эталона должна быть больше 0!",
                        cleanup_flags=['_wall_calibration_initialized', '_wall_reference_thickness'],
                        register_doublewords=[40010, 40012],
                        cache_attrs=['cached_distance_1_2', 'cached_distance_1_3'],
                    )
                    return

                self.clear_serial_buffers()
                self.flush_sensor_queue()
                self.measurement_buffer['sensor1'].clear()
                self.measurement_buffer['sensor2'].clear()
                self.measurement_buffer['sensor3'].clear()
                self.wall_sample_count = 0
                self.wall_last_log_time = time.time()
                self.write_cycle_flag(100)

            sensor1_mm, sensor2_mm, sensor3_mm, _ = self.read_sensors_safe()
            if sensor1_mm is None or sensor2_mm is None or sensor3_mm is None:
                time.sleep(0.002)
                return

            if (self.is_valid_measurement(sensor1_mm) and
                    self.is_valid_measurement(sensor2_mm) and
                    self.is_valid_measurement(sensor3_mm)):
                self.measurement_buffer['sensor1'].append(sensor1_mm)
                self.measurement_buffer['sensor2'].append(sensor2_mm)
                self.measurement_buffer['sensor3'].append(sensor3_mm)
                self.wall_sample_count += 1

                current_time = time.time()
                if current_time - getattr(self, 'wall_last_log_time', 0) >= 1.0:
                    print(f" [CMD=100] Сбор данных... Измерений: {self.wall_sample_count}")
                    self.wall_last_log_time = current_time
        except Exception as e:
            self.calibration_data['wall_distance_1_2'] = 0.0
            self.calibration_data['wall_distance_1_3'] = 0.0
            self.finalize_calibration_failure(
                100,
                f" Ошибка калибровки: {e}",
                cleanup_flags=['_wall_calibration_initialized', '_wall_reference_thickness', 'wall_sample_count', 'wall_last_log_time'],
                register_doublewords=[40010, 40012],
                cache_attrs=['cached_distance_1_2', 'cached_distance_1_3'],
            )
    
    def handle_calibrate_bottom_state(self):
        """Обработка калибровки дна (CMD = 101)"""
        cleanup_flags = [
            'calibrate_bottom_started',
            'calibrate_bottom_start_time',
            'calibrate_bottom_sensor4_buffer',
            'calibrate_bottom_measurement_count',
            '_reference_bottom_thickness',
            'wall_last_log_time',
        ]

        if not self.sensors:
            self.calibration_data['bottom_distance_4'] = 0.0
            self.finalize_calibration_failure(
                101,
                " [CMD=101] Ошибка: датчики не подключены!",
                cleanup_flags,
                register_doublewords=[40014],
                cache_attrs=['cached_distance_sensor4'],
            )
            return

        try:
            if not hasattr(self, 'calibrate_bottom_started'):
                self.calibration_in_progress = True
                self.calibrate_bottom_started = True
                self.calibrate_bottom_sensor4_buffer = []
                self.calibrate_bottom_measurement_count = 0
                self.calibrate_bottom_last_log_time = time.time()

                self.clear_serial_buffers()
                self.flush_sensor_queue()
                self.measurement_buffer['sensor4'].clear()

                reference_bottom_thickness = self.read_reference_bottom_thickness()
                print("🔧 НАЧАЛО КАЛИБРОВКИ ДНА")
                print(f" Эталонная толщина дна: {reference_bottom_thickness:.3f} мм")
                if reference_bottom_thickness <= 0:
                    self.calibration_data['bottom_distance_4'] = 0.0
                    self.finalize_calibration_failure(
                        101,
                        " [CMD=101] ОШИБКА: эталонная толщина дна должна быть больше 0!",
                        cleanup_flags,
                        register_doublewords=[40014],
                        cache_attrs=['cached_distance_sensor4'],
                    )
                    return
                self._reference_bottom_thickness = reference_bottom_thickness

                self.write_cycle_flag(101)

            sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.read_sensors_safe()
            if sensor4_mm is None:
                time.sleep(0.002)
                return

            if self.is_valid_measurement(sensor4_mm):
                self.measurement_buffer['sensor4'].append(sensor4_mm)
                self.calibrate_bottom_sensor4_buffer.append(sensor4_mm)
                self.calibrate_bottom_measurement_count += 1

                current_time = time.time()
                if current_time - getattr(self, 'calibrate_bottom_last_log_time', 0) >= 1.0:
                    print(f" [CMD=101] Сбор данных... Измерений датчика 4: {self.calibrate_bottom_measurement_count}")
                    self.calibrate_bottom_last_log_time = current_time

        except Exception as e:
            self.calibration_data['bottom_distance_4'] = 0.0
            self.finalize_calibration_failure(
                101,
                f" Ошибка калибровки дна: {e}",
                cleanup_flags,
                register_doublewords=[40014],
                cache_attrs=['cached_distance_sensor4'],
            )
    
    def handle_calibrate_flange_state(self):
        """
        CMD=102: Калибровка эталонного диаметра корпуса
        - Читаем эталонный диаметр из регистров 40006-40007
        - Непрерывно собираем данные от датчика 1 до получения CMD=0
        - Расчет и запись результатов выполняется при переходе CMD -> 0
        """
        if not self.sensors:
            self.calibration_data['flange_distance_1_center'] = 0.0
            self.finalize_calibration_failure(
                102,
                " [CMD=102] Ошибка: датчики не подключены!",
                cleanup_flags=['calibrate_flange_started'],
                register_doublewords=[40016],
                cache_attrs=['cached_distance_to_center'],
            )
            return
        
        try:
            # Инициализация при первом запуске
            if not hasattr(self, 'calibrate_flange_started'):
                self.calibration_in_progress = True
                self.calibrate_flange_started = True
                self.calibrate_flange_sensor1_buffer = []
                self.calibrate_flange_measurement_count = 0
                self.calibrate_flange_last_log_time = time.time()
                
                # Очищаем буферы серийного порта перед началом измерений
                self.clear_serial_buffers()
                self.flush_sensor_queue()
                
                # Очищаем буфер датчика 1
                self.measurement_buffer['sensor1'].clear()
                
                # Читаем эталонный диаметр из регистров 40006-40007
                reference_diameter = self.read_reference_diameter()
                print(f"🔧 НАЧАЛО КАЛИБРОВКИ ЭТАЛОННОГО ДИАМЕТРА (CMD=102)")
                print(f" [CMD=102] Эталонный диаметр: {reference_diameter:.3f} мм")
                
                if reference_diameter <= 0:
                    self.calibration_data['flange_distance_1_center'] = 0.0
                    self.finalize_calibration_failure(
                        102,
                        " [CMD=102] ОШИБКА: Эталонный диаметр должен быть больше 0!",
                        cleanup_flags=['calibrate_flange_started'],
                        register_doublewords=[40016],
                        cache_attrs=['cached_distance_to_center'],
                    )
                    return
                
                # Устанавливаем статус калибровки
                self.write_cycle_flag(102)
                print(f" [CMD=102] Непрерывный сбор данных от датчика 1...")
            
            # Непрерывный сбор данных (без таймаута)
            sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.read_sensors_safe()
            if sensor1_mm is None:
                time.sleep(0.002)
                return

            # Сохраняем только валидные измерения датчика 1
            if self.is_valid_measurement(sensor1_mm):
                self.measurement_buffer['sensor1'].append(sensor1_mm)
                self.calibrate_flange_sensor1_buffer.append(sensor1_mm)
                self.calibrate_flange_measurement_count += 1

            # Показываем прогресс раз в секунду
            current_time = time.time()
            if current_time - getattr(self, 'calibrate_flange_last_log_time', 0) >= 1.0:
                print(f" [CMD=102] Сбор данных... Измерений датчика 1: {self.calibrate_flange_measurement_count}")
                self.calibrate_flange_last_log_time = current_time
                
        except Exception as e:
            print(f" [CMD=102] Ошибка калибровки эталонного диаметра: {e}")
            import traceback
            traceback.print_exc()
            self.calibration_data['flange_distance_1_center'] = 0.0
            self.finalize_calibration_failure(
                102,
                f" [CMD=102] Ошибка калибровки: {e}",
                cleanup_flags=['calibrate_flange_started'],
                register_doublewords=[40016],
                cache_attrs=['cached_distance_to_center'],
            )
    
    def _finish_calibration_wall(self):
        """Завершение калибровки стенки (CMD=100): расчет и запись результатов"""
        try:
            # Проверяем, что калибровка была инициализирована
            if not hasattr(self, '_wall_calibration_initialized'):
                print(" [CMD=100] ОШИБКА: Калибровка не была инициализирована!")
                self.finalize_calibration_failure(
                    100,
                    " [CMD=100] ОШИБКА: Калибровка не была инициализирована!",
                    cleanup_flags=['_wall_calibration_initialized', '_wall_reference_thickness'],
                    register_doublewords=[40010, 40012],
                    cache_attrs=['cached_distance_1_2', 'cached_distance_1_3'],
                )
                return
            
            # Проверяем наличие данных
            if (len(self.measurement_buffer['sensor1']) == 0 or 
                len(self.measurement_buffer['sensor2']) == 0 or 
                len(self.measurement_buffer['sensor3']) == 0):
                print(f" [CMD=100] ОШИБКА: Недостаточно данных для расчета!")
                print(f" [CMD=100] Данные: Д1={len(self.measurement_buffer['sensor1'])}, Д2={len(self.measurement_buffer['sensor2'])}, Д3={len(self.measurement_buffer['sensor3'])}")
                self.finalize_calibration_failure(
                    100,
                    " [CMD=100] ОШИБКА: Недостаточно данных для расчета!",
                    cleanup_flags=['_wall_calibration_initialized', '_wall_reference_thickness'],
                    register_doublewords=[40010, 40012],
                    cache_attrs=['cached_distance_1_2', 'cached_distance_1_3'],
                )
                return
            
            # Усредняем измерения
            try:
                avg_sensor1, avg_sensor2, avg_sensor3 = self.calculate_averages()
            except ValueError as e:
                print(f" [CMD=100] ОШИБКА при усреднении: {e}")
                self.finalize_calibration_failure(
                    100,
                    f" [CMD=100] ОШИБКА при усреднении: {e}",
                    cleanup_flags=['_wall_calibration_initialized', '_wall_reference_thickness'],
                    register_doublewords=[40010, 40012],
                    cache_attrs=['cached_distance_1_2', 'cached_distance_1_3'],
                )
                return
            
            print(f" [CMD=100] Средние значения: Д1={avg_sensor1:.3f}мм, Д2={avg_sensor2:.3f}мм, Д3={avg_sensor3:.3f}мм")
            
            # Вычисляем расстояние между датчиками 1,2
            reference_thickness = getattr(self, '_wall_reference_thickness', 0.0)
            distance_1_2 = avg_sensor1 + avg_sensor2 + reference_thickness
            print(f" [CMD=100] Расстояние между датчиками 1,2: {distance_1_2:.3f} мм")
            
            # Вычисляем расстояние между датчиками 1,3
            distance_1_3 = avg_sensor1 - avg_sensor3
            print(f" [CMD=100] Расстояние между датчиками 1,3: {distance_1_3:.3f} мм")
            
            # Записываем результаты
            self.write_calibration_result_1_2(distance_1_2)
            self.write_calibration_result_1_3(distance_1_3)
            
            # Сохраняем в локальных данных
            self.calibration_data['wall_distance_1_2'] = distance_1_2
            self.calibration_data['wall_distance_1_3'] = distance_1_3
            
            # Очищаем буферы после калибровки
            self.clear_measurement_buffers()
            self.clear_serial_buffers()
            
            # Очищаем флаги
            if hasattr(self, '_wall_calibration_initialized'):
                delattr(self, '_wall_calibration_initialized')
            if hasattr(self, '_wall_reference_thickness'):
                delattr(self, '_wall_reference_thickness')
            if hasattr(self, 'wall_sample_count'):
                delattr(self, 'wall_sample_count')
            if hasattr(self, 'wall_last_log_time'):
                delattr(self, 'wall_last_log_time')
            
            self.calibration_in_progress = False
            self.write_cycle_flag(0)
            print(" [CMD=100] КАЛИБРОВКА СТЕНКИ ЗАВЕРШЕНА УСПЕШНО")
            
        except Exception as e:
            print(f" [CMD=100] ОШИБКА при завершении калибровки: {e}")
            import traceback
            traceback.print_exc()
            self.calibration_data['wall_distance_1_2'] = 0.0
            self.calibration_data['wall_distance_1_3'] = 0.0
            self.finalize_calibration_failure(
                100,
                f" [CMD=100] Ошибка завершения калибровки: {e}",
                cleanup_flags=['_wall_calibration_initialized', '_wall_reference_thickness'],
                register_doublewords=[40010, 40012],
                cache_attrs=['cached_distance_1_2', 'cached_distance_1_3'],
            )
    
    def _finish_calibration_bottom(self):
        """Завершение калибровки дна (CMD=101): расчет и запись результатов"""
        try:
            # Проверяем, что калибровка была инициализирована
            if not hasattr(self, 'calibrate_bottom_started'):
                print(" [CMD=101] ОШИБКА: Калибровка не была инициализирована!")
                self.finalize_calibration_failure(
                    101,
                    " [CMD=101] ОШИБКА: Калибровка не была инициализирована!",
                    cleanup_flags=['calibrate_bottom_started', '_reference_bottom_thickness'],
                    register_doublewords=[40014],
                    cache_attrs=['cached_distance_sensor4'],
                )
                return
            
            # Проверяем наличие данных
            if not hasattr(self, 'calibrate_bottom_sensor4_buffer') or len(self.calibrate_bottom_sensor4_buffer) == 0:
                print(f" [CMD=101] ОШИБКА: Недостаточно данных для расчета!")
                self.finalize_calibration_failure(
                    101,
                    " [CMD=101] ОШИБКА: Недостаточно данных для расчета!",
                    cleanup_flags=['calibrate_bottom_started', '_reference_bottom_thickness'],
                    register_doublewords=[40014],
                    cache_attrs=['cached_distance_sensor4'],
                )
                return
            
            # Усеченное среднее в диапазоне [P5..P95]
            _, avg_sensor4, _ = self.calculate_robust_stats(self.calibrate_bottom_sensor4_buffer)
            print(f" [CMD=101] Усеченное среднее (P5..P95) датчика 4: {avg_sensor4:.3f} мм (из {len(self.calibrate_bottom_sensor4_buffer)} измерений)")
            
            # Вычисляем расстояние от датчика 4 до поверхности
            reference_bottom_thickness = getattr(self, '_reference_bottom_thickness', 0.0)
            distance_4_surface = avg_sensor4 + reference_bottom_thickness
            print(f" [CMD=101] Расстояние от датчика 4 до поверхности: {distance_4_surface:.3f} мм")
            
            # Записываем результат
            self.write_calibration_result_4_surface(distance_4_surface)
            self.calibration_data['bottom_distance_4'] = distance_4_surface
            self.cached_distance_sensor4 = distance_4_surface
            
            # Очищаем буферы
            self.clear_measurement_buffers()
            self.clear_serial_buffers()
            
            # Очищаем флаги
            if hasattr(self, 'calibrate_bottom_started'):
                delattr(self, 'calibrate_bottom_started')
            if hasattr(self, '_reference_bottom_thickness'):
                delattr(self, '_reference_bottom_thickness')
            if hasattr(self, 'calibrate_bottom_sensor4_buffer'):
                delattr(self, 'calibrate_bottom_sensor4_buffer')
            if hasattr(self, 'calibrate_bottom_measurement_count'):
                delattr(self, 'calibrate_bottom_measurement_count')
            if hasattr(self, 'calibrate_bottom_last_log_time'):
                delattr(self, 'calibrate_bottom_last_log_time')
            
            self.calibration_in_progress = False
            self.write_cycle_flag(0)
            print(" [CMD=101] КАЛИБРОВКА ДНА ЗАВЕРШЕНА УСПЕШНО")
            
        except Exception as e:
            print(f" [CMD=101] ОШИБКА при завершении калибровки: {e}")
            import traceback
            traceback.print_exc()
            self.calibration_data['bottom_distance_4'] = 0.0
            self.finalize_calibration_failure(
                101,
                f" [CMD=101] Ошибка завершения калибровки: {e}",
                cleanup_flags=['calibrate_bottom_started', '_reference_bottom_thickness'],
                register_doublewords=[40014],
                cache_attrs=['cached_distance_sensor4'],
            )
    
    def _finish_calibration_flange(self):
        """Завершение калибровки фланца (CMD=102): расчет и запись результатов"""
        try:
            # Проверяем, что калибровка была инициализирована
            if not hasattr(self, 'calibrate_flange_started'):
                print(" [CMD=102] ОШИБКА: Калибровка не была инициализирована!")
                self.finalize_calibration_failure(
                    102,
                    " [CMD=102] ОШИБКА: Калибровка не была инициализирована!",
                    cleanup_flags=['calibrate_flange_started'],
                    register_doublewords=[40016],
                    cache_attrs=['cached_distance_to_center'],
                )
                return
            
            # Проверяем наличие данных
            if not hasattr(self, 'calibrate_flange_sensor1_buffer') or len(self.calibrate_flange_sensor1_buffer) == 0:
                print(f" [CMD=102] ОШИБКА: Недостаточно данных для расчета!")
                self.finalize_calibration_failure(
                    102,
                    " [CMD=102] ОШИБКА: Недостаточно данных для расчета!",
                    cleanup_flags=['calibrate_flange_started'],
                    register_doublewords=[40016],
                    cache_attrs=['cached_distance_to_center'],
                )
                return
            
            # Усеченное среднее в диапазоне [P5..P95]
            _, avg_sensor1, _ = self.calculate_robust_stats(self.calibrate_flange_sensor1_buffer)
            print(f" [CMD=102] Усеченное среднее (P5..P95) датчика 1: {avg_sensor1:.3f} мм (из {len(self.calibrate_flange_sensor1_buffer)} измерений)")
            
            # Читаем эталонный диаметр
            reference_diameter = self.read_reference_diameter()
            
            # Вычисляем расстояние между датчиком 1 и центром пересечения
            distance_1_center = (reference_diameter / 2) + avg_sensor1
            print(f" [CMD=102] Расстояние между датчиком 1 и центром: {distance_1_center:.3f} мм")
            print(f" [CMD=102] Формула: ({reference_diameter:.3f} / 2) + {avg_sensor1:.3f} = {distance_1_center:.3f}")
            
            # Записываем результат
            self.write_calibration_result_1_center(distance_1_center)
            self.cached_distance_to_center = distance_1_center
            self.calibration_data['flange_distance_1_center'] = distance_1_center
            
            # Очищаем буферы
            self.clear_measurement_buffers()
            self.clear_serial_buffers()
            
            # Очищаем флаги
            if hasattr(self, 'calibrate_flange_started'):
                delattr(self, 'calibrate_flange_started')
            if hasattr(self, 'calibrate_flange_sensor1_buffer'):
                delattr(self, 'calibrate_flange_sensor1_buffer')
            if hasattr(self, 'calibrate_flange_measurement_count'):
                delattr(self, 'calibrate_flange_measurement_count')
            if hasattr(self, 'calibrate_flange_start_time'):
                delattr(self, 'calibrate_flange_start_time')
            if hasattr(self, 'calibrate_flange_measurement_duration'):
                delattr(self, 'calibrate_flange_measurement_duration')
            if hasattr(self, 'calibrate_flange_completed'):
                delattr(self, 'calibrate_flange_completed')
            
            self.calibration_in_progress = False
            self.write_cycle_flag(0)
            print(" [CMD=102] КАЛИБРОВКА ФЛАНЦА ЗАВЕРШЕНА УСПЕШНО")
            
        except Exception as e:
            print(f" [CMD=102] ОШИБКА при завершении калибровки: {e}")
            import traceback
            traceback.print_exc()
            self.calibration_data['flange_distance_1_center'] = 0.0
            self.finalize_calibration_failure(
                102,
                f" [CMD=102] Ошибка завершения калибровки: {e}",
                cleanup_flags=['calibrate_flange_started'],
                register_doublewords=[40016],
                cache_attrs=['cached_distance_to_center'],
            )
    
    def _finish_calibration_flange_diameter(self):
        """Завершение калибровки диаметра фланца (CMD=105): расчет и запись результатов"""
        try:
            # Проверяем, что калибровка была инициализирована
            if not hasattr(self, 'calibrate_flange_diameter_started'):
                print(" [CMD=105] ОШИБКА: Калибровка не была инициализирована!")
                self.finalize_calibration_failure(
                    105,
                    " [CMD=105] ОШИБКА: Калибровка не была инициализирована!",
                    cleanup_flags=['calibrate_flange_diameter_started'],
                    register_doublewords=[40032],
                    cache_attrs=['cached_distance_sensor3_to_center'],
                )
                return
            
            # Проверяем наличие данных
            if not hasattr(self, 'calibrate_flange_diameter_sensor3_buffer') or len(self.calibrate_flange_diameter_sensor3_buffer) == 0:
                print(f" [CMD=105] ОШИБКА: Недостаточно данных для расчета!")
                self.finalize_calibration_failure(
                    105,
                    " [CMD=105] ОШИБКА: Недостаточно данных для расчета!",
                    cleanup_flags=['calibrate_flange_diameter_started'],
                    register_doublewords=[40032],
                    cache_attrs=['cached_distance_sensor3_to_center'],
                )
                return
            
            # Усеченное среднее в диапазоне [P5..P95]
            _, avg_sensor3, _ = self.calculate_robust_stats(self.calibrate_flange_diameter_sensor3_buffer)
            print(f" [CMD=105] Усеченное среднее (P5..P95) датчика 3: {avg_sensor3:.3f} мм (из {len(self.calibrate_flange_diameter_sensor3_buffer)} измерений)")
            
            # Читаем эталонный диаметр фланца
            reference_flange_diameter = self.read_reference_flange_diameter()
            
            # Вычисляем расстояние между датчиком 3 и центром пересечения
            distance_sensor3_to_center = (reference_flange_diameter / 2) + avg_sensor3
            print(f" [CMD=105] Расстояние между датчиком 3 и центром: {distance_sensor3_to_center:.3f} мм")
            print(f" [CMD=105] Формула: ({reference_flange_diameter:.3f} / 2) + {avg_sensor3:.3f} = {distance_sensor3_to_center:.3f}")
            
            # Записываем результат
            self.write_distance_sensor3_to_center(distance_sensor3_to_center)
            self.cached_distance_sensor3_to_center = distance_sensor3_to_center
            
            # Очищаем буферы
            self.clear_measurement_buffers()
            self.clear_serial_buffers()
            
            # Очищаем флаги
            if hasattr(self, 'calibrate_flange_diameter_started'):
                delattr(self, 'calibrate_flange_diameter_started')
            if hasattr(self, 'calibrate_flange_diameter_sensor3_buffer'):
                delattr(self, 'calibrate_flange_diameter_sensor3_buffer')
            if hasattr(self, 'calibrate_flange_diameter_measurement_count'):
                delattr(self, 'calibrate_flange_diameter_measurement_count')
            if hasattr(self, 'calibrate_flange_diameter_start_time'):
                delattr(self, 'calibrate_flange_diameter_start_time')
            if hasattr(self, 'calibrate_flange_diameter_measurement_duration'):
                delattr(self, 'calibrate_flange_diameter_measurement_duration')
            if hasattr(self, 'calibrate_flange_diameter_completed'):
                delattr(self, 'calibrate_flange_diameter_completed')
            
            self.calibration_in_progress = False
            self.write_cycle_flag(0)
            print(" [CMD=105] КАЛИБРОВКА ДИАМЕТРА ФЛАНЦА ЗАВЕРШЕНА УСПЕШНО")
            
        except Exception as e:
            print(f" [CMD=105] ОШИБКА при завершении калибровки: {e}")
            import traceback
            traceback.print_exc()
            self.finalize_calibration_failure(
                105,
                f" [CMD=105] Ошибка завершения калибровки: {e}",
                cleanup_flags=['calibrate_flange_diameter_started'],
                register_doublewords=[40032],
                cache_attrs=['cached_distance_sensor3_to_center'],
            )

    def _finish_calibration_body_diameter_separate(self):
        """Завершение калибровки раздельного диаметра корпуса (CMD=107)"""
        try:
            if not hasattr(self, 'calibrate_body_diameter_separate_started'):
                self.finalize_calibration_failure(
                    107,
                    " [CMD=107] ОШИБКА: Калибровка не была инициализирована!",
                    cleanup_flags=['calibrate_body_diameter_separate_started'],
                    register_doublewords=[40038],
                    cache_attrs=['cached_distance_sensor3_to_center_body'],
                )
                return

            if (not hasattr(self, 'calibrate_body_diameter_separate_sensor3_buffer') or
                len(self.calibrate_body_diameter_separate_sensor3_buffer) == 0):
                self.finalize_calibration_failure(
                    107,
                    " [CMD=107] ОШИБКА: Недостаточно данных для расчета!",
                    cleanup_flags=['calibrate_body_diameter_separate_started'],
                    register_doublewords=[40038],
                    cache_attrs=['cached_distance_sensor3_to_center_body'],
                )
                return

            _, avg_sensor3, _ = self.calculate_robust_stats(self.calibrate_body_diameter_separate_sensor3_buffer)
            reference_diameter = self.read_reference_body_diameter_separate()
            distance_sensor3_to_center = (reference_diameter / 2) + avg_sensor3

            print(f" [CMD=107] Среднее датчика 3: {avg_sensor3:.3f} мм")
            print(f" [CMD=107] Расстояние датчик 3 - центр: {distance_sensor3_to_center:.3f} мм")

            self.write_distance_sensor3_to_center_body(distance_sensor3_to_center)
            self.cached_distance_sensor3_to_center_body = distance_sensor3_to_center

            self.clear_measurement_buffers()
            self.clear_serial_buffers()

            for attr in [
                'calibrate_body_diameter_separate_started',
                'calibrate_body_diameter_separate_sensor3_buffer',
                'calibrate_body_diameter_separate_measurement_count',
                'calibrate_body_diameter_separate_last_log_time',
            ]:
                if hasattr(self, attr):
                    delattr(self, attr)

            self.calibration_in_progress = False
            self.write_cycle_flag(0)
            print(" [CMD=107] КАЛИБРОВКА РАЗДЕЛЬНОГО ДИАМЕТРА КОРПУСА ЗАВЕРШЕНА УСПЕШНО")

        except Exception as e:
            self.finalize_calibration_failure(
                107,
                f" [CMD=107] Ошибка завершения калибровки: {e}",
                cleanup_flags=['calibrate_body_diameter_separate_started'],
                register_doublewords=[40038],
                cache_attrs=['cached_distance_sensor3_to_center_body'],
            )

    def _finish_calibration_body2_diameter(self):
        """Завершение калибровки диаметра корпуса 2 (CMD=108)"""
        try:
            if not hasattr(self, 'calibrate_body2_diameter_started'):
                self.finalize_calibration_failure(
                    108,
                    " [CMD=108] ОШИБКА: Калибровка не была инициализирована!",
                    cleanup_flags=['calibrate_body2_diameter_started'],
                    register_doublewords=[40040],
                    cache_attrs=['cached_distance_sensor3_to_center_body2'],
                )
                return

            if (not hasattr(self, 'calibrate_body2_diameter_sensor3_buffer') or
                len(self.calibrate_body2_diameter_sensor3_buffer) == 0):
                self.finalize_calibration_failure(
                    108,
                    " [CMD=108] ОШИБКА: Недостаточно данных для расчета!",
                    cleanup_flags=['calibrate_body2_diameter_started'],
                    register_doublewords=[40040],
                    cache_attrs=['cached_distance_sensor3_to_center_body2'],
                )
                return

            _, avg_sensor3, _ = self.calculate_robust_stats(self.calibrate_body2_diameter_sensor3_buffer)
            reference_diameter = self.read_reference_body2_diameter()
            distance_sensor3_to_center = (reference_diameter / 2) + avg_sensor3

            print(f" [CMD=108] Среднее датчика 3: {avg_sensor3:.3f} мм")
            print(f" [CMD=108] Расстояние датчик 3 - центр: {distance_sensor3_to_center:.3f} мм")

            self.write_distance_sensor3_to_center_body2(distance_sensor3_to_center)
            self.cached_distance_sensor3_to_center_body2 = distance_sensor3_to_center

            self.clear_measurement_buffers()
            self.clear_serial_buffers()

            for attr in [
                'calibrate_body2_diameter_started',
                'calibrate_body2_diameter_sensor3_buffer',
                'calibrate_body2_diameter_measurement_count',
                'calibrate_body2_diameter_last_log_time',
            ]:
                if hasattr(self, attr):
                    delattr(self, attr)

            self.calibration_in_progress = False
            self.write_cycle_flag(0)
            print(" [CMD=108] КАЛИБРОВКА ДИАМЕТРА КОРПУСА 2 ЗАВЕРШЕНА УСПЕШНО")

        except Exception as e:
            self.finalize_calibration_failure(
                108,
                f" [CMD=108] Ошибка завершения калибровки: {e}",
                cleanup_flags=['calibrate_body2_diameter_started'],
                register_doublewords=[40040],
                cache_attrs=['cached_distance_sensor3_to_center_body2'],
            )
    
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
        
        # Очищаем буферы серийного порта перед началом измерений
        self.clear_serial_buffers()
        self.flush_sensor_queue()
        
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
                # Выполняем чтение измерений с защитой от конфликтов
                sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.read_sensors_safe()
                if sensor1_mm is None or sensor2_mm is None or sensor3_mm is None:
                    time.sleep(0.001)
                    continue
                
                # Сохраняем только валидные измерения для калибровки стенки
                # Для калибровки стенки нужны только датчики 1, 2 и 3 (датчик 4 не используется)
                # Фильтруем некорректные измерения (None, 0, отрицательные, вне диапазона)
                if (self.is_valid_measurement(sensor1_mm) and 
                    self.is_valid_measurement(sensor2_mm) and 
                    self.is_valid_measurement(sensor3_mm)):
                    # Датчик 4 не обязателен для калибровки стенки, но сохраняем если валиден
                    self.measurement_buffer['sensor1'].append(sensor1_mm)
                    self.measurement_buffer['sensor2'].append(sensor2_mm)
                    self.measurement_buffer['sensor3'].append(sensor3_mm)
                    if self.is_valid_measurement(sensor4_mm):
                        self.measurement_buffer['sensor4'].append(sensor4_mm)
                    measurement_count += 1
                else:
                    # Отладочный вывод: показываем, почему измерения не прошли валидацию
                    if measurement_count == 0 and int((time.time() - start_time)) % 2 == 0:  # Раз в 2 секунды
                        invalid_reasons = []
                        if not self.is_valid_measurement(sensor1_mm):
                            invalid_reasons.append(f"Д1={sensor1_mm}")
                        if not self.is_valid_measurement(sensor2_mm):
                            invalid_reasons.append(f"Д2={sensor2_mm}")
                        if not self.is_valid_measurement(sensor3_mm):
                            invalid_reasons.append(f"Д3={sensor3_mm}")
                        if invalid_reasons:
                            print(f" ⚠ Некорректные измерения: {', '.join(invalid_reasons)}")
                
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
        
        _, avg_sensor1, _ = self.calculate_robust_stats(self.measurement_buffer['sensor1'])
        _, avg_sensor2, _ = self.calculate_robust_stats(self.measurement_buffer['sensor2'])
        _, avg_sensor3, _ = self.calculate_robust_stats(self.measurement_buffer['sensor3'])
        
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
                
                # Отключено сохранение в БД - сохраняем только указанные величины
                # if self.db_integration:
                #     self.db_integration.save_doubleword_register(
                #         40010, 'holding', distance, 'Расстояние между датчиками 1,2'
                #     )
                
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
                
                # Отключено сохранение в БД - сохраняем только указанные величины
                # if self.db_integration:
                #     self.db_integration.save_doubleword_register(
                #         40012, 'holding', distance, 'Расстояние между датчиками 1,3'
                #     )
                
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
        
        # Очищаем буферы серийного порта перед началом измерений
        self.clear_serial_buffers()
        self.flush_sensor_queue()
        
        # Очищаем буфер датчика 4
        self.measurement_buffer['sensor4'].clear()
        
        while (time.time() - start_time) < self.measurement_duration:
            try:
                # Выполняем чтение измерений с защитой от конфликтов
                sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.read_sensors_safe()
                if sensor4_mm is None:
                    time.sleep(0.001)
                    continue

                # Сохраняем только измерения датчика 4
                # Фильтруем некорректные измерения (None, 0, отрицательные, вне диапазона)
                if self.is_valid_measurement(sensor4_mm):
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
        
        _, avg_sensor4, _ = self.calculate_robust_stats(self.measurement_buffer['sensor4'])
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
                
                # Отключено сохранение в БД - сохраняем только указанные величины
                # if self.db_integration:
                #     self.db_integration.save_doubleword_register(
                #         40014, 'holding', distance, 'Расстояние датчика 4 до поверхности'
                #     )
                
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
        
        # Очищаем буферы серийного порта перед началом измерений
        self.clear_serial_buffers()
        self.flush_sensor_queue()
        
        # Очищаем буфер датчика 1
        self.measurement_buffer['sensor1'].clear()
        
        while (time.time() - start_time) < self.measurement_duration:
            try:
                # Выполняем чтение измерений с защитой от конфликтов
                sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.read_sensors_safe()
                if sensor1_mm is None:
                    time.sleep(0.001)
                    continue

                # Сохраняем только измерения датчика 1
                # Фильтруем некорректные измерения (None, 0, отрицательные, вне диапазона)
                if self.is_valid_measurement(sensor1_mm):
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
        
        _, avg_sensor1, _ = self.calculate_robust_stats(self.measurement_buffer['sensor1'])
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
                
                # Отключено сохранение в БД - сохраняем только указанные величины
                # if self.db_integration:
                #     self.db_integration.save_doubleword_register(
                #         40016, 'holding', distance, 'Расстояние датчика 1 до центра'
                #     )
                
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
                # 40001 соответствует индексу 0 в ModbusSequentialDataBlock
                self.modbus_server.slave_context.setValues(3, 0, [0])  # 40001 = 0
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
            
            # Выполняем QUAD измерение датчиков 1 и 2 (безопасное чтение с блокировкой)
            sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.read_sensors_safe()
            if sensor1_mm is None or sensor2_mm is None:
                time.sleep(0.001)
                return
            
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
            
            # Проверяем что получили валидные данные от датчиков 1 и 2
            # Фильтруем некорректные измерения (None, 0, отрицательные, вне диапазона)
            if (self.is_valid_measurement(sensor1_mm) and self.is_valid_measurement(sensor2_mm)):
                # Добавляем в временные буферы для усреднения
                self.temp_sensor1_buffer.append(sensor1_mm)
                self.temp_sensor2_buffer.append(sensor2_mm)
                
                window_size = self.get_smoothing_window_size()
                # Когда накопилось нужное количество измерений - усредняем и записываем
                if len(self.temp_sensor1_buffer) >= window_size:
                    min_filtered_count = max(1, window_size // 2)
                    avg_sensor1 = self._filtered_average_with_median(self.temp_sensor1_buffer, min_filtered_count)
                    avg_sensor2 = self._filtered_average_with_median(self.temp_sensor2_buffer, min_filtered_count)
                    
                    # Добавляем усредненные значения в основные буферы
                    self.sensor1_measurements.append(avg_sensor1)
                    self.sensor2_measurements.append(avg_sensor2)
                    
                    # Используем кешированное расстояние (вместо чтения из Modbus)
                    distance_1_2 = self.cached_distance_1_2
                    wall_upper_offset = self.read_upper_wall_offset_coeff()
                    
                    if distance_1_2 is not None:
                        # Вычисляем толщину стенки по формуле (используем усредненные значения)
                        wall_thickness = distance_1_2 - avg_sensor1 - avg_sensor2 + wall_upper_offset
                        self.wall_thickness_buffer.append(wall_thickness)
                        
                        # Выводим текущие значения каждые 100 усредненных измерений (уменьшена частота для ускорения)
                        if len(self.wall_thickness_buffer) % 100 == 0:
                            print(f" Усредненное измерение #{len(self.wall_thickness_buffer)}: "
                                  f"Датчик1={avg_sensor1:.3f}мм, Датчик2={avg_sensor2:.3f}мм, "
                                  f"Толщина={wall_thickness:.3f}мм")
                    else:
                        print(" Ошибка: не удалось прочитать калиброванное расстояние 1,2")
                    
                    # Очищаем временные буферы для следующих измерений окна сглаживания
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
    
    def read_calibrated_distance_sensor3_to_center(self) -> float:
        """Чтение калиброванного расстояния датчика 3 до центра из регистров 40032, 40033"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                values = self.modbus_server.slave_context.getValues(3, 32, 2)  # 40032-40033 -> индексы 32-33
                if values and len(values) >= 2:
                    high_word = int(values[0])  # 40032 - старший
                    low_word = int(values[1])   # 40033 - младший
                    distance = self.doubleword_to_float(low_word, high_word)
                    return distance
        except Exception as e:
            print(f" Ошибка чтения расстояния датчика 3 до центра: {e}")
        return None

    def read_calibrated_distance_sensor3_to_center_body(self) -> float:
        """Чтение калиброванного расстояния датчика 3 до центра (раздельный диаметр корпуса) из 40038-40039"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                values = self.modbus_server.slave_context.getValues(3, 38, 2)  # 40038-40039
                if values and len(values) >= 2:
                    high_word = int(values[0])
                    low_word = int(values[1])
                    return self.doubleword_to_float(low_word, high_word)
        except Exception as e:
            print(f" Ошибка чтения расстояния датчика 3 до центра (раздельный корпус): {e}")
        return None

    def read_calibrated_distance_sensor3_to_center_body2(self) -> float:
        """Чтение калиброванного расстояния датчика 3 до центра (корпус 2) из 40040-40041"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                values = self.modbus_server.slave_context.getValues(3, 40, 2)  # 40040-40041
                if values and len(values) >= 2:
                    high_word = int(values[0])
                    low_word = int(values[1])
                    return self.doubleword_to_float(low_word, high_word)
        except Exception as e:
            print(f" Ошибка чтения расстояния датчика 3 до центра (корпус 2): {e}")
        return None
    
    def apply_extrapolation_to_buffer(self, buffer: list, extrapolation_coeff: float) -> list:
        """
        Применение экстраполяции к буферу измерений
        
        Формула: экстраполированное_значение = среднее + коэффициент * (измеренное - среднее)
        
        Args:
            buffer: Список измеренных значений
            extrapolation_coeff: Коэффициент экстраполяции
            
        Returns:
            Список экстраполированных значений
        """
        if not buffer or len(buffer) == 0:
            return buffer
        
        # Вычисляем среднее из исходного буфера
        avg_value = sum(buffer) / len(buffer)
        
        # Применяем экстраполяцию к каждому значению
        extrapolated_buffer = []
        for value in buffer:
            extrapolated_value = avg_value + extrapolation_coeff * (value - avg_value)
            extrapolated_buffer.append(extrapolated_value)
        
        return extrapolated_buffer

    def _calculate_percentile(self, sorted_values: list, percentile: float) -> float:
        """Линейная интерполяция процентиля для отсортированного списка."""
        if not sorted_values:
            raise ValueError("Пустой список для расчета процентиля")
        if len(sorted_values) == 1:
            return float(sorted_values[0])

        bounded = max(0.0, min(100.0, percentile))
        index = (len(sorted_values) - 1) * (bounded / 100.0)
        lower_index = int(math.floor(index))
        upper_index = int(math.ceil(index))

        lower_value = float(sorted_values[lower_index])
        upper_value = float(sorted_values[upper_index])
        if lower_index == upper_index:
            return lower_value

        weight = index - lower_index
        return lower_value + (upper_value - lower_value) * weight

    def calculate_robust_stats(self, values: list, lower_percentile: float = 0.0, upper_percentile: float = 100.0) -> Tuple[float, float, float]:
        """
        Возвращает статистику в формате (max, avg, min) как:
        max = P95, avg = усеченное среднее в диапазоне [P5..P95], min = P5.
        """
        valid_values = [
            float(v) for v in values
            if v is not None and not (math.isnan(v) or math.isinf(v))
        ]
        if not valid_values:
            raise ValueError("Недостаточно данных для расчета робастной статистики")

        sorted_values = sorted(valid_values)
        p5 = self._calculate_percentile(sorted_values, lower_percentile)
        p95 = self._calculate_percentile(sorted_values, upper_percentile)

        trimmed_values = [v for v in valid_values if p5 <= v <= p95]
        if not trimmed_values:
            # Fallback по требованию: среднее экстраполированного буфера
            trimmed_values = valid_values

        trimmed_mean = sum(trimmed_values) / len(trimmed_values)
        return p95, trimmed_mean, p5

    def calculate_robust_stats_details(self, values: list, lower_percentile: float = 0.0, upper_percentile: float = 100.0) -> dict:
        """
        Расширенная статистика для отладки:
        - p95 / p5
        - trimmed_mean
        - размер исходного и усеченного буфера
        """
        valid_values = [
            float(v) for v in values
            if v is not None and not (math.isnan(v) or math.isinf(v))
        ]
        if not valid_values:
            raise ValueError("Недостаточно данных для расчета робастной статистики")

        sorted_values = sorted(valid_values)
        p5 = self._calculate_percentile(sorted_values, lower_percentile)
        p95 = self._calculate_percentile(sorted_values, upper_percentile)
        trimmed_values = [v for v in valid_values if p5 <= v <= p95]
        if not trimmed_values:
            trimmed_values = valid_values

        trimmed_mean = sum(trimmed_values) / len(trimmed_values)
        return {
            "p95": p95,
            "trimmed_mean": trimmed_mean,
            "p5": p5,
            "source_count": len(valid_values),
            "trimmed_count": len(trimmed_values),
            "raw_min": min(valid_values),
            "raw_max": max(valid_values),
        }
    
    def process_wall_measurement_results(self):
        """Обработка результатов измерения стенки при переходе 10→11"""
        try:
            if len(self.wall_thickness_buffer) == 0:
                print(" Ошибка: нет данных измерений для обработки")
                return
            
            # Читаем коэффициент экстраполяции для верхней стенки
            extrapolation_coeff = self.read_upper_wall_extrapolation_coeff()
            
            # Применяем экстраполяцию к буферу
            if abs(extrapolation_coeff) > 0.0001:  # Применяем только если коэффициент не равен нулю
                extrapolated_buffer = self.apply_extrapolation_to_buffer(self.wall_thickness_buffer, extrapolation_coeff)
                print(f" [ЭКСТРАПОЛЯЦИЯ] Применен коэффициент {extrapolation_coeff:.6f} к толщине верхней стенки")
            else:
                extrapolated_buffer = self.wall_thickness_buffer
            
            # Вычисляем статистику из экстраполированных значений:
            # max=Pmax, avg=усеченное среднее [Pmin..Pmax], min=Pmin
            pmin, pmax = self.read_percentile_bounds('upper_wall')
            max_thickness, avg_thickness, min_thickness = self.calculate_robust_stats(extrapolated_buffer, pmin, pmax)
            
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
                max_val, min_val = self.apply_extrema_offsets('upper_wall', max_val, min_val)
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
                len(self.bottom_thickness_buffer) == 0):
                print(" Ошибка: нет данных измерений фланца для обработки")
                return
            
            # ДИАГНОСТИКА: Выводим ВСЕ буферы усредненных значений датчиков
            print(f"\n{'='*80}")
            print(f" ДИАГНОСТИКА: ВСЕ БУФЕРЫ УСРЕДНЕННЫХ ЗНАЧЕНИЙ ДАТЧИКОВ")
            print(f"{'='*80}")
            
            print(f"\n [БУФЕР УСРЕДНЕННЫХ ЗНАЧЕНИЙ ДАТЧИКА 1] Размер: {len(self.sensor1_flange_measurements)}")
            if len(self.sensor1_flange_measurements) > 0:
                print(f"   ВСЕ значения: {[f'{x:.3f}' for x in self.sensor1_flange_measurements]}")
                # Статистика по датчику 1
                max_sensor1, avg_sensor1, min_sensor1 = self.calculate_robust_stats(self.sensor1_flange_measurements)
                print(f"   СТАТИСТИКА: макс={max_sensor1:.3f}мм, сред={avg_sensor1:.3f}мм, мин={min_sensor1:.3f}мм")
            else:
                print(f"   БУФЕР ПУСТ!")
            
            print(f"\n [БУФЕР УСРЕДНЕННЫХ ЗНАЧЕНИЙ ДАТЧИКА 3] Размер: {len(self.sensor3_measurements)}")
            if len(self.sensor3_measurements) > 0:
                print(f"   ВСЕ значения: {[f'{x:.3f}' for x in self.sensor3_measurements]}")
                # Статистика по датчику 3
                max_sensor3, avg_sensor3, min_sensor3 = self.calculate_robust_stats(self.sensor3_measurements)
                print(f"   СТАТИСТИКА: макс={max_sensor3:.3f}мм, сред={avg_sensor3:.3f}мм, мин={min_sensor3:.3f}мм")
            else:
                print(f"   БУФЕР ПУСТ!")
            
            print(f"\n [БУФЕР УСРЕДНЕННЫХ ЗНАЧЕНИЙ ДАТЧИКА 4] Размер: {len(self.sensor4_measurements)}")
            if len(self.sensor4_measurements) > 0:
                print(f"   ВСЕ значения: {[f'{x:.3f}' for x in self.sensor4_measurements]}")
                # Статистика по датчику 4
                max_sensor4, avg_sensor4, min_sensor4 = self.calculate_robust_stats(self.sensor4_measurements)
                print(f"   СТАТИСТИКА: макс={max_sensor4:.3f}мм, сред={avg_sensor4:.3f}мм, мин={min_sensor4:.3f}мм")
            else:
                print(f"   БУФЕР ПУСТ!")
            
            # ДИАГНОСТИКА: Выводим ВСЕ буферы рассчитанных значений
            print(f"\n{'='*80}")
            print(f" ДИАГНОСТИКА: ВСЕ БУФЕРЫ РАССЧИТАННЫХ ЗНАЧЕНИЙ")
            print(f"{'='*80}")
            
            print(f"\n [БУФЕР ДИАМЕТР КОРПУСА] Размер: {len(self.body_diameter_buffer)}")
            if len(self.body_diameter_buffer) > 0:
                print(f"   ВСЕ значения: {[f'{x:.3f}' for x in self.body_diameter_buffer]}")
            else:
                print(f"   БУФЕР ПУСТ!")
            
            print(f"\n [БУФЕР ДИАМЕТР ФЛАНЦА] Размер: {len(self.flange_diameter_buffer)}")
            if len(self.flange_diameter_buffer) > 0:
                print(f"   ВСЕ значения: {[f'{x:.3f}' for x in self.flange_diameter_buffer]}")
            else:
                print(f"   БУФЕР ПУСТ!")
            
            print(f"\n [БУФЕР ТОЛЩИНА ДНА] Размер: {len(self.bottom_thickness_buffer)}")
            if len(self.bottom_thickness_buffer) > 0:
                print(f"   ВСЕ значения: {[f'{x:.3f}' for x in self.bottom_thickness_buffer]}")
            else:
                print(f"   БУФЕР ПУСТ!")
            
            print(f"\n{'='*80}\n")
            
            # Вычисляем статистику для диаметра корпуса
            # Фильтруем некорректные значения (0, отрицательные, NaN, inf) перед вычислением статистики
            valid_body_radii = [r for r in self.body_diameter_buffer 
                               if r is not None and r > 0 and not (math.isnan(r) or math.isinf(r))]
            if len(valid_body_radii) == 0:
                print(" ОШИБКА: Нет валидных значений радиуса корпуса!")
                return
            
            # Применяем экстраполяцию к радиусам корпуса
            body_extrapolation_coeff = self.read_body_diameter_extrapolation_coeff()
            if abs(body_extrapolation_coeff) > 0.0001:
                valid_body_radii = self.apply_extrapolation_to_buffer(valid_body_radii, body_extrapolation_coeff)
                print(f" [ЭКСТРАПОЛЯЦИЯ] Применен коэффициент {body_extrapolation_coeff:.6f} к радиусу корпуса")
            
            # Вычисляем диаметры из противоположных точек (0° и 180°)
            # Формула: (distance_to_center - avg_sensor1_0deg) + (distance_to_center - avg_sensor1_180deg) + offset
            body_diameter_offset = self.read_body_diameter_offset_coeff()
            body_pmin, body_pmax = self.read_percentile_bounds('body_diameter')
            opposite_body_diameters = []
            
            if len(valid_body_radii) >= 2:
                # Определяем количество измерений за полный оборот (360°)
                # Для любого количества оборотов (360°, 720°, 1080° и т.д.)
                # противоположная точка всегда на расстоянии N/2
                total_measurements = len(valid_body_radii)
                half_size = total_measurements // 2
                
                # Берем противоположные точки (смещенные на 180 градусов)
                for i in range(half_size):
                    radius_0deg = valid_body_radii[i]          # Точка на текущем угле
                    radius_180deg = valid_body_radii[i + half_size]  # Точка на 180° от текущей
                    # Формула: (distance_to_center - avg_sensor1_0deg) + (distance_to_center - avg_sensor1_180deg) + offset
                    body_diameter = radius_0deg + radius_180deg + body_diameter_offset
                    opposite_body_diameters.append(body_diameter)
                
                if len(opposite_body_diameters) > 0:
                    max_body_diameter, avg_body_diameter, min_body_diameter = self.calculate_robust_stats(
                        opposite_body_diameters, body_pmin, body_pmax
                    )
                    print(f" [DIAMETER] Вычислено {len(opposite_body_diameters)} диаметров корпуса из противоположных точек")
                else:
                    # Fallback: если не удалось вычислить противоположные точки
                    print(" [WARNING] Не удалось вычислить диаметры корпуса из противоположных точек, используем старый метод")
                    body_diameters = [(radius * 2 + body_diameter_offset) for radius in valid_body_radii]
                    max_body_diameter, avg_body_diameter, min_body_diameter = self.calculate_robust_stats(
                        body_diameters, body_pmin, body_pmax
                    )
            else:
                # Если измерений недостаточно для противоположных точек
                print(" [WARNING] Недостаточно измерений для противоположных точек корпуса, используем старый метод")
                body_diameters = [(radius * 2 + body_diameter_offset) for radius in valid_body_radii]
                max_body_diameter, avg_body_diameter, min_body_diameter = self.calculate_robust_stats(
                    body_diameters, body_pmin, body_pmax
                )
            
            # Вычисляем статистику для диаметра фланца
            # Фильтруем некорректные значения (0, отрицательные, NaN, inf) перед вычислением статистики
            valid_flange_radii = [r for r in self.flange_diameter_buffer 
                                 if r is not None and r > 0 and not (math.isnan(r) or math.isinf(r))]
            if len(valid_flange_radii) == 0:
                print(" ОШИБКА: Нет валидных значений радиуса фланца!")
                return
            
            # Применяем экстраполяцию к радиусам фланца
            flange_extrapolation_coeff = self.read_flange_diameter_extrapolation_coeff()
            if abs(flange_extrapolation_coeff) > 0.0001:
                valid_flange_radii = self.apply_extrapolation_to_buffer(valid_flange_radii, flange_extrapolation_coeff)
                print(f" [ЭКСТРАПОЛЯЦИЯ] Применен коэффициент {flange_extrapolation_coeff:.6f} к радиусу фланца")
            
            # Вычисляем диаметры фланца из противоположных точек (0° и 180°)
            # Формула: (distance_to_center_flange - avg_sensor3_0deg) + (distance_to_center_flange - avg_sensor3_180deg) + offset
            flange_diameter_offset = self.read_flange_diameter_offset_coeff()
            flange_pmin, flange_pmax = self.read_percentile_bounds('flange_diameter')
            opposite_flange_diameters = []
            
            if len(valid_flange_radii) >= 2:
                # Определяем количество измерений за полный оборот (360°)
                # Для любого количества оборотов (360°, 720°, 1080° и т.д.)
                # противоположная точка всегда на расстоянии N/2
                total_measurements = len(valid_flange_radii)
                half_size = total_measurements // 2
                
                # Берем противоположные точки (смещенные на 180 градусов)
                for i in range(half_size):
                    radius_0deg = valid_flange_radii[i]          # Точка на текущем угле
                    radius_180deg = valid_flange_radii[i + half_size]  # Точка на 180° от текущей
                    # Формула: (distance_to_center_flange - avg_sensor3_0deg) + (distance_to_center_flange - avg_sensor3_180deg) + offset
                    flange_diameter = radius_0deg + radius_180deg + flange_diameter_offset
                    opposite_flange_diameters.append(flange_diameter)
                
                if len(opposite_flange_diameters) > 0:
                    max_flange_diameter, avg_flange_diameter, min_flange_diameter = self.calculate_robust_stats(
                        opposite_flange_diameters, flange_pmin, flange_pmax
                    )
                    print(f" [DIAMETER] Вычислено {len(opposite_flange_diameters)} диаметров фланца из противоположных точек")
                else:
                    # Fallback: если не удалось вычислить противоположные точки
                    print(" [WARNING] Не удалось вычислить диаметры фланца из противоположных точек, используем старый метод")
                    flange_diameters = [(radius * 2 + flange_diameter_offset) for radius in valid_flange_radii]
                    max_flange_diameter, avg_flange_diameter, min_flange_diameter = self.calculate_robust_stats(
                        flange_diameters, flange_pmin, flange_pmax
                    )
            else:
                # Если измерений недостаточно для противоположных точек
                print(" [WARNING] Недостаточно измерений для противоположных точек фланца, используем старый метод")
                flange_diameters = [(radius * 2 + flange_diameter_offset) for radius in valid_flange_radii]
                max_flange_diameter, avg_flange_diameter, min_flange_diameter = self.calculate_robust_stats(
                    flange_diameters, flange_pmin, flange_pmax
                )
            
            # Толщина фланца теперь передаётся с ПК, не рассчитывается здесь
            
            # Вычисляем статистику для толщины дна
            # Применяем экстраполяцию к толщине дна
            bottom_extrapolation_coeff = self.read_bottom_thickness_extrapolation_coeff()
            if abs(bottom_extrapolation_coeff) > 0.0001:
                extrapolated_bottom_thickness = self.apply_extrapolation_to_buffer(self.bottom_thickness_buffer, bottom_extrapolation_coeff)
                print(f" [ЭКСТРАПОЛЯЦИЯ] Применен коэффициент {bottom_extrapolation_coeff:.6f} к толщине дна")
            else:
                extrapolated_bottom_thickness = self.bottom_thickness_buffer
            
            bottom_pmin, bottom_pmax = self.read_percentile_bounds('bottom')
            max_bottom_thickness, avg_bottom_thickness, min_bottom_thickness = self.calculate_robust_stats(
                extrapolated_bottom_thickness, bottom_pmin, bottom_pmax
            )
            
            print(f" Результаты измерения фланца:")
            print(f"   Измерений: {len(self.body_diameter_buffer)}")
            print(f"   Диаметр корпуса: макс={max_body_diameter:.3f}мм, сред={avg_body_diameter:.3f}мм, мин={min_body_diameter:.3f}мм")
            print(f"   Диаметр фланца: макс={max_flange_diameter:.3f}мм, сред={avg_flange_diameter:.3f}мм, мин={min_flange_diameter:.3f}мм")
            print(f"   Толщина дна: макс={max_bottom_thickness:.3f}мм, сред={avg_bottom_thickness:.3f}мм, мин={min_bottom_thickness:.3f}мм")
            
            # Записываем результаты в регистры
            self.write_flange_measurement_results(
                max_body_diameter, avg_body_diameter, min_body_diameter,
                max_flange_diameter, avg_flange_diameter, min_flange_diameter,
                max_bottom_thickness, avg_bottom_thickness, min_bottom_thickness
            )
            
        except Exception as e:
            print(f" Ошибка обработки результатов измерения фланца: {e}")
    
    def write_flange_measurement_results(self, 
                                       max_body_diameter: float, avg_body_diameter: float, min_body_diameter: float,
                                       max_flange_diameter: float, avg_flange_diameter: float, min_flange_diameter: float,
                                       max_bottom_thickness: float, avg_bottom_thickness: float, min_bottom_thickness: float):
        """Запись результатов измерения фланца в регистры"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                max_body_diameter, min_body_diameter = self.apply_extrema_offsets(
                    'body_diameter', max_body_diameter, min_body_diameter
                )
                max_flange_diameter, min_flange_diameter = self.apply_extrema_offsets(
                    'flange_diameter', max_flange_diameter, min_flange_diameter
                )
                max_bottom_thickness, min_bottom_thickness = self.apply_extrema_offsets(
                    'bottom_thickness', max_bottom_thickness, min_bottom_thickness
                )
                # Диаметр корпуса → 30046-30051
                self.write_stream_result_to_input_registers(max_body_diameter, 30046)   # Максимальное
                self.write_stream_result_to_input_registers(avg_body_diameter, 30048)   # Среднее
                self.write_stream_result_to_input_registers(min_body_diameter, 30050)   # Минимальное
                
                # Диаметр фланца → 30052-30057
                self.write_stream_result_to_input_registers(max_flange_diameter, 30054) # Максимальное
                self.write_stream_result_to_input_registers(avg_flange_diameter, 30052) # Среднее
                self.write_stream_result_to_input_registers(min_flange_diameter, 30056) # Минимальное
                
                # Толщина фланца теперь передаётся с ПК, не записывается здесь
                
                # Толщина дна → 30028-30033
                self.write_stream_result_to_input_registers(max_bottom_thickness, 30028) # Максимальное
                self.write_stream_result_to_input_registers(avg_bottom_thickness, 30030) # Среднее
                self.write_stream_result_to_input_registers(min_bottom_thickness, 30032) # Минимальное
                
                print(f" Результаты фланца записаны в регистры")
                
        except Exception as e:
            print(f" Ошибка записи результатов измерения фланца: {e}")

    def calculate_diameter_stats_from_radii(
        self,
        radii_buffer: list,
        extrapolation_coeff: float,
        offset_coeff: float,
        label: str,
        percentile_param_key: str = 'body_diameter',
    ):
        """Расчёт max/avg/min диаметра по буферу радиусов (max=P95, avg=усеченное среднее, min=P5)"""
        valid_radii = [r for r in radii_buffer if r is not None and r > 0 and not (math.isnan(r) or math.isinf(r))]
        if len(valid_radii) == 0:
            print(f" ОШИБКА: Нет валидных значений радиуса ({label})!")
            return None

        pmin, pmax = self.read_percentile_bounds(percentile_param_key)
        debug_body2 = (label == "корпус 2")
        if debug_body2:
            raw_avg = sum(valid_radii) / len(valid_radii)
            print(f" [CMD=41][DEBUG] Радиусы до экстраполяции: N={len(valid_radii)}, min={min(valid_radii):.6f}, avg={raw_avg:.6f}, max={max(valid_radii):.6f}")
            print(f" [CMD=41][DEBUG] Коэффициенты: extrapolation={extrapolation_coeff:.9f}, offset={offset_coeff:.9f}")
            print(f" [CMD=41][DEBUG] Процентили: Pmin={pmin:.3f}, Pmax={pmax:.3f}")

        if abs(extrapolation_coeff) > 0.0001:
            valid_radii = self.apply_extrapolation_to_buffer(valid_radii, extrapolation_coeff)
            print(f" [ЭКСТРАПОЛЯЦИЯ] {label}: применен коэффициент {extrapolation_coeff:.6f}")
        if debug_body2:
            ext_avg = sum(valid_radii) / len(valid_radii)
            print(f" [CMD=41][DEBUG] Радиусы после экстраполяции: N={len(valid_radii)}, min={min(valid_radii):.6f}, avg={ext_avg:.6f}, max={max(valid_radii):.6f}")

        if len(valid_radii) >= 2:
            total_measurements = len(valid_radii)
            half_size = total_measurements // 2
            opposite_diameters = []
            for i in range(half_size):
                diameter_val = valid_radii[i] + valid_radii[i + half_size] + offset_coeff
                opposite_diameters.append(diameter_val)

            if len(opposite_diameters) > 0:
                max_val, avg_val, min_val = self.calculate_robust_stats(opposite_diameters, pmin, pmax)
                if debug_body2:
                    details = self.calculate_robust_stats_details(opposite_diameters, pmin, pmax)
                    print(
                        f" [CMD=41][DEBUG] Диаметры из противоположных точек: N={details['source_count']}, "
                        f"raw_min={details['raw_min']:.6f}, raw_max={details['raw_max']:.6f}"
                    )
                    print(
                        f" [CMD=41][DEBUG] Робастная статистика: P95={details['p95']:.6f}, "
                        f"TrimmedMean={details['trimmed_mean']:.6f}, P5={details['p5']:.6f}, "
                        f"trimmed_N={details['trimmed_count']}/{details['source_count']}"
                    )
            else:
                diameters = [(radius * 2 + offset_coeff) for radius in valid_radii]
                max_val, avg_val, min_val = self.calculate_robust_stats(diameters, pmin, pmax)
                if debug_body2:
                    details = self.calculate_robust_stats_details(diameters, pmin, pmax)
                    print(f" [CMD=41][DEBUG] Fallback по диаметрам (2*R+offset), N={details['source_count']}")
                    print(
                        f" [CMD=41][DEBUG] Робастная статистика: P95={details['p95']:.6f}, "
                        f"TrimmedMean={details['trimmed_mean']:.6f}, P5={details['p5']:.6f}, "
                        f"trimmed_N={details['trimmed_count']}/{details['source_count']}"
                    )
        else:
            diameters = [(radius * 2 + offset_coeff) for radius in valid_radii]
            max_val, avg_val, min_val = self.calculate_robust_stats(diameters, pmin, pmax)
            if debug_body2:
                details = self.calculate_robust_stats_details(diameters, pmin, pmax)
                print(f" [CMD=41][DEBUG] Мало точек, fallback по диаметрам (2*R+offset), N={details['source_count']}")
                print(
                    f" [CMD=41][DEBUG] Робастная статистика: P95={details['p95']:.6f}, "
                    f"TrimmedMean={details['trimmed_mean']:.6f}, P5={details['p5']:.6f}, "
                    f"trimmed_N={details['trimmed_count']}/{details['source_count']}"
                )

        return max_val, avg_val, min_val

    def process_flange_only_measurement_results(self):
        """Подсчёт результатов раздельного измерения фланца и толщины дна (CMD=21)"""
        if len(self.flange_diameter_buffer) == 0:
            print(" Ошибка: нет данных раздельного измерения фланца")
            return
        if len(self.bottom_thickness_buffer) == 0:
            print(" Ошибка: нет данных раздельного измерения толщины дна")
            return

        flange_stats = self.calculate_diameter_stats_from_radii(
            self.flange_diameter_buffer,
            self.read_flange_diameter_extrapolation_coeff(),
            self.read_flange_diameter_offset_coeff(),
            "фланец (раздельно)",
            percentile_param_key='flange_diameter',
        )
        if not flange_stats:
            return
        max_flange, avg_flange, min_flange = flange_stats

        bottom_extrapolation_coeff = self.read_bottom_thickness_extrapolation_coeff()
        if abs(bottom_extrapolation_coeff) > 0.0001:
            extrapolated_bottom = self.apply_extrapolation_to_buffer(self.bottom_thickness_buffer, bottom_extrapolation_coeff)
            print(f" [ЭКСТРАПОЛЯЦИЯ] Толщина дна (раздельно): применен коэффициент {bottom_extrapolation_coeff:.6f}")
        else:
            extrapolated_bottom = self.bottom_thickness_buffer

        bottom_pmin, bottom_pmax = self.read_percentile_bounds('bottom')
        max_bottom, avg_bottom, min_bottom = self.calculate_robust_stats(extrapolated_bottom, bottom_pmin, bottom_pmax)

        self.write_flange_only_measurement_results(
            max_flange, avg_flange, min_flange,
            max_bottom, avg_bottom, min_bottom
        )
        print(f" Результаты раздельного фланца: макс={max_flange:.3f}, сред={avg_flange:.3f}, мин={min_flange:.3f}")
        print(f" Результаты толщины дна (раздельно): макс={max_bottom:.3f}, сред={avg_bottom:.3f}, мин={min_bottom:.3f}")

    def process_body_only_measurement_results(self):
        """Подсчёт результатов раздельного диаметра корпуса (CMD=31)"""
        if len(self.body_only_diameter_buffer) == 0:
            print(" Ошибка: нет данных раздельного измерения диаметра корпуса")
            return

        stats = self.calculate_diameter_stats_from_radii(
            self.body_only_diameter_buffer,
            self.read_body_diameter_extrapolation_coeff(),
            self.read_body_diameter_offset_coeff(),
            "корпус (раздельно)",
            percentile_param_key='body_diameter',
        )
        if not stats:
            return
        max_val, avg_val, min_val = stats
        self.write_body_only_measurement_results(max_val, avg_val, min_val)
        print(f" Результаты раздельного диаметра корпуса: макс={max_val:.3f}, сред={avg_val:.3f}, мин={min_val:.3f}")

    def process_body2_measurement_results(self):
        """Подсчёт результатов диаметра корпуса 2 (CMD=41)"""
        if len(self.body2_diameter_buffer) == 0:
            print(" Ошибка: нет данных измерения диаметра корпуса 2")
            return

        stats = self.calculate_diameter_stats_from_radii(
            self.body2_diameter_buffer,
            self.read_body2_diameter_extrapolation_coeff(),
            self.read_body2_diameter_offset_coeff(),
            "корпус 2",
            percentile_param_key='body_diameter_2',
        )
        if not stats:
            return
        max_val, avg_val, min_val = stats
        self.write_body2_measurement_results(max_val, avg_val, min_val)
        print(f" Результаты диаметра корпуса 2: макс={max_val:.3f}, сред={avg_val:.3f}, мин={min_val:.3f}")

    def write_flange_only_measurement_results(
        self,
        max_flange: float, avg_flange: float, min_flange: float,
        max_bottom: float, avg_bottom: float, min_bottom: float
    ):
        """Запись результатов раздельного измерения фланца и толщины дна"""
        max_flange, min_flange = self.apply_extrema_offsets('flange_diameter', max_flange, min_flange)
        max_bottom, min_bottom = self.apply_extrema_offsets('bottom_thickness', max_bottom, min_bottom)
        # Диаметр фланца → 30052-30057
        self.write_stream_result_to_input_registers(max_flange, 30054)
        self.write_stream_result_to_input_registers(avg_flange, 30052)
        self.write_stream_result_to_input_registers(min_flange, 30056)
        # Толщина дна → 30028-30033
        self.write_stream_result_to_input_registers(max_bottom, 30028)
        self.write_stream_result_to_input_registers(avg_bottom, 30030)
        self.write_stream_result_to_input_registers(min_bottom, 30032)

    def write_body_only_measurement_results(self, max_val: float, avg_val: float, min_val: float):
        """Запись результатов раздельного измерения диаметра корпуса в 30046-30051"""
        max_val, min_val = self.apply_extrema_offsets('body_diameter', max_val, min_val)
        self.write_stream_result_to_input_registers(max_val, 30046)
        self.write_stream_result_to_input_registers(avg_val, 30048)
        self.write_stream_result_to_input_registers(min_val, 30050)

    def write_body2_measurement_results(self, max_val: float, avg_val: float, min_val: float):
        """Запись результатов диаметра корпуса 2 в 30059-30064"""
        max_val, min_val = self.apply_extrema_offsets('body_diameter_2', max_val, min_val)
        self.write_stream_result_to_input_registers(max_val, 30059)
        self.write_stream_result_to_input_registers(avg_val, 30061)
        self.write_stream_result_to_input_registers(min_val, 30063)
    
    def process_bottom_wall_measurement_results(self):
        """Обработка результатов измерения нижней стенки при переходе 12→0"""
        try:
            if len(self.bottom_wall_thickness_buffer) == 0:
                print(" Ошибка: нет данных измерений нижней стенки для обработки")
                return
            
            # Читаем коэффициент экстраполяции для нижней стенки
            extrapolation_coeff = self.read_bottom_wall_extrapolation_coeff()
            
            # Применяем экстраполяцию к буферу
            if abs(extrapolation_coeff) > 0.0001:  # Применяем только если коэффициент не равен нулю
                extrapolated_buffer = self.apply_extrapolation_to_buffer(self.bottom_wall_thickness_buffer, extrapolation_coeff)
                print(f" [ЭКСТРАПОЛЯЦИЯ] Применен коэффициент {extrapolation_coeff:.6f} к толщине нижней стенки")
            else:
                extrapolated_buffer = self.bottom_wall_thickness_buffer
            
            # Вычисляем статистику из экстраполированных значений:
            # max=Pmax, avg=усеченное среднее [Pmin..Pmax], min=Pmin
            pmin, pmax = self.read_percentile_bounds('bottom_wall')
            max_bottom_wall_thickness, avg_bottom_wall_thickness, min_bottom_wall_thickness = self.calculate_robust_stats(
                extrapolated_buffer, pmin, pmax
            )
            
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
                max_val, min_val = self.apply_extrema_offsets('lower_wall', max_val, min_val)
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
            
            # Выполняем QUAD измерение датчиков 1, 3 и 4 (безопасное чтение с блокировкой)
            sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.read_sensors_safe()
            if sensor1_mm is None or sensor3_mm is None or sensor4_mm is None:
                time.sleep(0.001)
                return
            
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
            
            # Проверяем что получили валидные данные от датчиков 1, 3 и 4
            # Фильтруем некорректные измерения (None, 0, отрицательные, вне диапазона)
            if (self.is_valid_measurement(sensor1_mm) and 
                self.is_valid_measurement(sensor3_mm) and 
                self.is_valid_measurement(sensor4_mm)):
                # Добавляем в временные буферы для усреднения
                self.temp_sensor1_flange_buffer.append(sensor1_mm)
                self.temp_sensor3_buffer.append(sensor3_mm)
                self.temp_sensor4_buffer.append(sensor4_mm)
                
                window_size = self.get_smoothing_window_size()
                # Когда накопилось нужное количество измерений - усредняем и записываем
                if len(self.temp_sensor1_flange_buffer) >= window_size:
                    min_filtered_count = max(1, window_size // 2)
                    avg_sensor1 = self._filtered_average_with_median(self.temp_sensor1_flange_buffer, min_filtered_count)
                    avg_sensor3 = self._filtered_average_with_median(self.temp_sensor3_buffer, min_filtered_count)
                    avg_sensor4 = self._filtered_average_with_median(self.temp_sensor4_buffer, min_filtered_count)
                    
                    # Добавляем усредненные значения в основные буферы
                    self.sensor1_flange_measurements.append(avg_sensor1)
                    self.sensor3_measurements.append(avg_sensor3)
                    self.sensor4_measurements.append(avg_sensor4)
                    
                    # Используем кешированные калиброванные значения
                    distance_to_center = self.cached_distance_to_center
                    distance_to_center_flange = self.cached_distance_sensor3_to_center  # Расстояние датчика 3 до центра (из команды 105)
                    distance_1_3 = self.cached_distance_1_3
                    distance_sensor4 = self.cached_distance_sensor4
                    recipe_diametr_body = self.read_recipe_body_diameter()
                    recipe_diametr_flange = self.read_recipe_flange_diameter()
                    body_diameter_offset = self.read_body_diameter_offset_coeff()
                    flange_diameter_offset = self.read_flange_diameter_offset_coeff()
                    bottom_thickness_offset = self.read_bottom_thickness_offset_coeff()
                    
                    if (distance_to_center is not None and distance_to_center_flange is not None and 
                        distance_1_3 is not None and distance_sensor4 is not None):
                        
                        # 1) Радиус корпуса (Датчик 1) - сохраняем радиус для последующего расчета из противоположных точек
                        # Формула: (расстояние до центра - показание датчика 1)
                        body_radius = distance_to_center - avg_sensor1
                        self.body_diameter_buffer.append(body_radius)  # Временно используем тот же буфер для радиусов
                        
                        # 2) Радиус фланца (Датчик 3) - сохраняем радиус для последующего расчета из противоположных точек
                        # Формула: (расстояние датчика 3 до центра - показание датчика 3)
                        flange_radius = distance_to_center_flange - avg_sensor3
                        self.flange_diameter_buffer.append(flange_radius)  # Временно используем тот же буфер для радиусов
                        
                        # 3) Толщина фланца - теперь передаётся с ПК, не рассчитывается здесь
                        
                        # 4) Толщина дна (Датчик 4)
                        bottom_thickness = distance_sensor4 - avg_sensor4 + bottom_thickness_offset
                        self.bottom_thickness_buffer.append(bottom_thickness)
                        
                        # Выводим прогресс каждые 100 усредненных измерений
                        if len(self.body_diameter_buffer) % 100 == 0:
                            print(f" [CMD=12] Собрано: {len(self.body_diameter_buffer)} измерений")
                            print(f"   Радиус корпуса={body_radius:.3f}мм, Радиус фланца={flange_radius:.3f}мм")
                            print(f"   Толщина дна={bottom_thickness:.3f}мм")
                    else:
                        print(" Ошибка: не удалось прочитать калиброванные значения")
                    
                    # Очищаем временные буферы для следующих измерений окна сглаживания
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
            # Важно: всегда сбрасываем счетчики при входе в состояние измерения нижней стенки
            # чтобы не использовать данные от предыдущего измерения верхней стенки
            if not hasattr(self, '_bottom_frequency_initialized') or self.frequency_start_time is None:
                self.frequency_start_time = time.time()
                self.last_frequency_display = self.frequency_start_time
                self.frequency_counter = 0
                self._bottom_frequency_initialized = True
            
            # Статус уже установлен в manage_measurement_cycle_flag
            # Просто продолжаем сбор данных
            
            # Выполняем QUAD измерение датчиков 1 и 2 (безопасное чтение с блокировкой)
            sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.read_sensors_safe()
            if sensor1_mm is None or sensor2_mm is None:
                time.sleep(0.001)
                return
            
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
            
            # Проверяем что получили валидные данные от датчиков 1 и 2
            # Фильтруем некорректные измерения (None, 0, отрицательные, вне диапазона)
            if (self.is_valid_measurement(sensor1_mm) and self.is_valid_measurement(sensor2_mm)):
                # Добавляем в временные буферы для усреднения
                self.temp_sensor1_bottom_buffer.append(sensor1_mm)
                self.temp_sensor2_bottom_buffer.append(sensor2_mm)
                
                window_size = self.get_smoothing_window_size()
                # Когда накопилось нужное количество измерений - усредняем и записываем
                if len(self.temp_sensor1_bottom_buffer) >= window_size:
                    min_filtered_count = max(1, window_size // 2)
                    avg_sensor1 = self._filtered_average_with_median(self.temp_sensor1_bottom_buffer, min_filtered_count)
                    avg_sensor2 = self._filtered_average_with_median(self.temp_sensor2_bottom_buffer, min_filtered_count)
                    
                    # Добавляем усредненные значения в основные буферы
                    self.sensor1_bottom_measurements.append(avg_sensor1)
                    self.sensor2_bottom_measurements.append(avg_sensor2)
                    
                    lower_wall_offset = self.read_lower_wall_offset_coeff()
                    # Используем кешированное расстояние (вместо чтения из Modbus)
                    distance_1_2 = self.cached_distance_1_2
                    
                    if distance_1_2 is not None:
                        # Вычисляем толщину нижней стенки по формуле (используем усредненные значения)
                        bottom_wall_thickness = distance_1_2 - avg_sensor1 - avg_sensor2 + lower_wall_offset
                        self.bottom_wall_thickness_buffer.append(bottom_wall_thickness)
                        
                        # Выводим текущие значения каждые 100 усредненных измерений (уменьшена частота для ускорения)
                        if len(self.bottom_wall_thickness_buffer) % 100 == 0:
                            print(f" Усредненное измерение #{len(self.bottom_wall_thickness_buffer)}: "
                                  f"Датчик1={avg_sensor1:.3f}мм, Датчик2={avg_sensor2:.3f}мм, "
                                  f"Толщина нижней стенки={bottom_wall_thickness:.3f}мм")
                    else:
                        print(" Ошибка: не удалось прочитать калиброванное расстояние 1,2")
                    
                    # Очищаем временные буферы для следующих измерений окна сглаживания
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
            sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.read_sensors_safe()
            if sensor1_mm is None:
                time.sleep(0.001)
                return
            
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
            
            # Проверяем валидность измерения (фильтруем None, 0, отрицательные, вне диапазона)
            if self.is_valid_measurement(sensor1_mm):
                # Датчик показывает валидное ненулевое значение - есть препятствие!
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
            
            # Вычисляем статистику:
            # max=Pmax, avg=усеченное среднее [Pmin..Pmax], min=Pmin
            pmin, pmax = self.read_percentile_bounds('height')
            max_height, avg_height, min_height = self.calculate_robust_stats(self.height_measurements, pmin, pmax)
            
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
    
    def read_measured_flange_thickness(self) -> float:
        """Чтение регистров 40059-40060 (измеренная толщина фланца)"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                values = self.modbus_server.slave_context.getValues(3, 59, 2)  # 40059-40060
                if values and len(values) == 2:
                    high_word = int(values[0])  # 40059 - старший
                    low_word = int(values[1])   # 40060 - младший
                    thickness = self.doubleword_to_float(low_word, high_word)
                    return thickness
        except Exception as e:
            print(f" Ошибка чтения регистров 40059-40060: {e}")
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
        # Очищаем буферы перед началом измерений (один раз при входе в состояние)
        if not hasattr(self, '_wall_measurement_started'):
            self._wall_measurement_started = True
            self.clear_measurement_buffers()
            self.clear_serial_buffers()
            print(f" [CMD=10] Буферы очищены перед началом измерений")
        
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
        # Очищаем буферы перед началом измерений (один раз при входе в состояние)
        if not hasattr(self, '_flange_measurement_started'):
            self._flange_measurement_started = True
            self.clear_measurement_buffers()
            self.clear_serial_buffers()
            print(f" [CMD=12] Буферы очищены перед началом измерений")
        
        # Загружаем калиброванные расстояния в кеш (один раз при входе в состояние)
        if self.cached_distance_to_center is None:
            self.cached_distance_to_center = self.read_calibrated_distance_to_center()
            self.cached_distance_1_3 = self.read_calibrated_distance_1_3()
            self.cached_distance_sensor4 = self.read_calibrated_distance_sensor4()
            self.cached_distance_sensor3_to_center = self.read_calibrated_distance_sensor3_to_center()
            print(f" Загружены расстояния: центр={self.cached_distance_to_center:.3f}мм, "
                  f"1-3={self.cached_distance_1_3:.3f}мм, sensor4={self.cached_distance_sensor4:.3f}мм, "
                  f"sensor3_to_center={self.cached_distance_sensor3_to_center:.3f}мм")
        
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

    def collect_sensor3_radius_measurement(self, temp_buffer: list, radii_buffer: list, distance_to_center: float, cmd_label: str):
        """Сбор и фильтрация радиуса по датчику 3 с усреднением по 10 измерений"""
        sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.read_sensors_safe()
        if sensor3_mm is None:
            time.sleep(0.001)
            return

        self.frequency_counter += 1
        current_time = time.time()
        if self.frequency_start_time is not None and current_time - self.last_frequency_display >= 1.0:
            elapsed = current_time - self.frequency_start_time
            if elapsed > 0:
                instant_freq = self.frequency_counter / elapsed
                print(f" [{cmd_label}] Частота опроса: {instant_freq:.1f} Гц | Измерений: {self.frequency_counter}")
            self.last_frequency_display = current_time

        if not self.is_valid_measurement(sensor3_mm):
            return

        temp_buffer.append(sensor3_mm)
        window_size = self.get_smoothing_window_size()
        if len(temp_buffer) < window_size:
            return

        min_filtered_count = max(1, window_size // 2)
        avg_sensor3 = self._filtered_average_with_median(temp_buffer, min_filtered_count)

        radius = distance_to_center - avg_sensor3
        radii_buffer.append(radius)
        temp_buffer.clear()

    def collect_flange_and_bottom_measurement(self):
        """Сбор данных для CMD=20: диаметр фланца (датчик 3) и толщина дна (датчик 4)"""
        sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.read_sensors_safe()
        if sensor3_mm is None or sensor4_mm is None:
            time.sleep(0.001)
            return

        self.frequency_counter += 1
        current_time = time.time()
        if self.frequency_start_time is not None and current_time - self.last_frequency_display >= 1.0:
            elapsed = current_time - self.frequency_start_time
            if elapsed > 0:
                instant_freq = self.frequency_counter / elapsed
                print(f" [CMD=20] Частота опроса: {instant_freq:.1f} Гц | Измерений: {self.frequency_counter}")
            self.last_frequency_display = current_time

        if not (self.is_valid_measurement(sensor3_mm) and self.is_valid_measurement(sensor4_mm)):
            return

        self.temp_sensor3_flange_only_buffer.append(sensor3_mm)
        self.temp_sensor4_buffer.append(sensor4_mm)
        window_size = self.get_smoothing_window_size()
        if len(self.temp_sensor3_flange_only_buffer) < window_size:
            return

        min_filtered_count = max(1, window_size // 2)
        avg_sensor3 = self._filtered_average_with_median(self.temp_sensor3_flange_only_buffer, min_filtered_count)
        avg_sensor4 = self._filtered_average_with_median(self.temp_sensor4_buffer, min_filtered_count)

        flange_radius = self.cached_distance_sensor3_to_center - avg_sensor3
        bottom_thickness = self.cached_distance_sensor4 - avg_sensor4 + self.read_bottom_thickness_offset_coeff()
        self.flange_diameter_buffer.append(flange_radius)
        self.bottom_thickness_buffer.append(bottom_thickness)

        self.temp_sensor3_flange_only_buffer.clear()
        self.temp_sensor4_buffer.clear()

    def handle_measure_flange_only_process_state(self):
        """CMD=20: Раздельный сбор данных диаметра фланца и толщины дна"""
        if not hasattr(self, '_flange_only_measurement_started'):
            self._flange_only_measurement_started = True
            self.clear_measurement_buffers()
            self.clear_serial_buffers()
            self.frequency_counter = 0
            self.frequency_start_time = time.time()
            self.last_frequency_display = self.frequency_start_time
            self.cached_distance_sensor3_to_center = self.read_calibrated_distance_sensor3_to_center()
            self.cached_distance_sensor4 = self.read_calibrated_distance_sensor4()
            print(" [CMD=20] Буферы очищены, старт раздельного измерения фланца и толщины дна")

        if self.cached_distance_sensor3_to_center is None:
            print(" [CMD=20] Ошибка: нет калиброванного расстояния датчик3-центр для фланца")
            return
        if self.cached_distance_sensor4 is None:
            print(" [CMD=20] Ошибка: нет калиброванного расстояния датчика 4 для толщины дна")
            return

        self.collect_flange_and_bottom_measurement()

    def handle_calculate_flange_only_state(self):
        """CMD=21: Подсчёт раздельного диаметра фланца"""
        try:
            if not self.flange_only_calculated:
                print(" [CMD=21] Подсчёт раздельного диаметра фланца...")
                self.process_flange_only_measurement_results()
                self.write_cycle_flag(212)
                self.flange_only_calculated = True
                print(" [STATUS=212] Подсчёт раздельного фланца завершён")
        except Exception as e:
            print(f" Ошибка подсчёта раздельного диаметра фланца: {e}")
            self.current_state = SystemState.ERROR

    def handle_measure_body_only_process_state(self):
        """CMD=30: Раздельный сбор данных диаметра корпуса"""
        if not hasattr(self, '_body_only_measurement_started'):
            self._body_only_measurement_started = True
            self.clear_measurement_buffers()
            self.clear_serial_buffers()
            self.frequency_counter = 0
            self.frequency_start_time = time.time()
            self.last_frequency_display = self.frequency_start_time
            self.cached_distance_sensor3_to_center_body = self.read_calibrated_distance_sensor3_to_center_body()
            print(" [CMD=30] Буферы очищены, старт раздельного измерения диаметра корпуса")

        if self.cached_distance_sensor3_to_center_body is None:
            print(" [CMD=30] Ошибка: нет калиброванного расстояния датчик3-центр (раздельный корпус)")
            return

        self.collect_sensor3_radius_measurement(
            self.temp_sensor3_body_only_buffer,
            self.body_only_diameter_buffer,
            self.cached_distance_sensor3_to_center_body,
            "CMD=30",
        )

    def handle_calculate_body_only_state(self):
        """CMD=31: Подсчёт раздельного диаметра корпуса"""
        try:
            if not self.body_only_calculated:
                print(" [CMD=31] Подсчёт раздельного диаметра корпуса...")
                self.process_body_only_measurement_results()
                self.write_cycle_flag(312)
                self.body_only_calculated = True
                print(" [STATUS=312] Подсчёт раздельного диаметра корпуса завершён")
        except Exception as e:
            print(f" Ошибка подсчёта раздельного диаметра корпуса: {e}")
            self.current_state = SystemState.ERROR

    def handle_measure_body2_process_state(self):
        """CMD=40: Сбор данных диаметра корпуса 2"""
        if not hasattr(self, '_body2_measurement_started'):
            self._body2_measurement_started = True
            self.clear_measurement_buffers()
            self.clear_serial_buffers()
            self.frequency_counter = 0
            self.frequency_start_time = time.time()
            self.last_frequency_display = self.frequency_start_time
            self.cached_distance_sensor3_to_center_body2 = self.read_calibrated_distance_sensor3_to_center_body2()
            self.body2_quality_required = True
            print(" [CMD=40] Буферы очищены, старт измерения диаметра корпуса 2")

        if self.cached_distance_sensor3_to_center_body2 is None:
            print(" [CMD=40] Ошибка: нет калиброванного расстояния датчик3-центр (корпус 2)")
            return

        self.collect_sensor3_radius_measurement(
            self.temp_sensor3_body2_buffer,
            self.body2_diameter_buffer,
            self.cached_distance_sensor3_to_center_body2,
            "CMD=40",
        )

    def handle_calculate_body2_state(self):
        """CMD=41: Подсчёт диаметра корпуса 2"""
        try:
            if not self.body2_calculated:
                print(" [CMD=41] Подсчёт диаметра корпуса 2...")
                self.process_body2_measurement_results()
                self.write_cycle_flag(412)
                self.body2_calculated = True
                print(" [STATUS=412] Подсчёт диаметра корпуса 2 завершён")
        except Exception as e:
            print(f" Ошибка подсчёта диаметра корпуса 2: {e}")
            self.current_state = SystemState.ERROR
    
    def handle_measure_bottom_process_state(self):
        """
        CMD=14: Сбор данных измерения нижней стенки
        Просто собираем данные, не делаем подсчёт
        """
        # Очищаем буферы перед началом измерений (один раз при входе в состояние)
        if not hasattr(self, '_bottom_measurement_started'):
            self._bottom_measurement_started = True
            self.clear_measurement_buffers()
            self.clear_serial_buffers()
            # Сбрасываем счетчики частоты для нового измерения (важно!)
            self.frequency_counter = 0
            self.frequency_start_time = None
            self.last_frequency_display = 0
            # Сбрасываем флаг инициализации частоты для нижней стенки
            if hasattr(self, '_bottom_frequency_initialized'):
                delattr(self, '_bottom_frequency_initialized')
            print(f" [CMD=14] Буферы очищены перед началом измерений, счетчики частоты сброшены")
        
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
                
                # Извлекаем итоговый результат
                result = quality_result.get('result', 'BAD') if isinstance(quality_result, dict) else quality_result
                
                # Обновление счётчиков изделий
                self.update_product_counters(result)
                
                # Инкрементация статистики параметров
                if isinstance(quality_result, dict):
                    self.increment_parameter_statistics(quality_result)
                
                # Увеличиваем номер изделия
                self.increment_product_number()
                
                # Устанавливаем статус "готово к завершению"
                self.write_cycle_flag(116)
                print(f" [STATUS=116] Оценка завершена ({result}), готов к CMD=0")
                
                # Отмечаем что оценка выполнена
                self.quality_evaluated = True
                
                # Очищаем буферы после расчетов команды 16
                self.clear_measurement_buffers()
                self.clear_serial_buffers()
                print(f" [CMD=16] Буферы очищены после оценки качества")
            
        except Exception as e:
            print(f" Ошибка оценки качества: {e}")
            self.current_state = SystemState.ERROR
    
    # ===== КОНЕЦ НОВЫХ МЕТОДОВ =====
    
    def handle_stream_quad_state(self):
        """Потоковый режим QUAD (CMD=200) - все 4 датчика одновременно"""
        if not self.sensors:
            print(" Ошибка: датчики не подключены!")
            self.current_state = SystemState.ERROR
            return
        
        # Проверяем смену команды
        current_cmd = self.get_current_command()
        if current_cmd != 200:
            print(f" Команда изменилась с 200 на {current_cmd}. Выходим из QUAD потокового режима")
            # Останавливаем QUAD режим
            self.stream_active_quad = False
            self.handle_command(current_cmd)
            return
        
        try:
            # Инициализация при первом запуске
            if not self.stream_active_quad:
                # Очищаем буферы серийного порта перед началом QUAD режима
                self.clear_serial_buffers()
                self.flush_sensor_queue()
                self.stream_active_quad = True
                self.stream_measurement_count = 0
                self.stream_start_time = time.time()
                self.stream_temp_sensor1_buffer = []
                self.stream_temp_sensor2_buffer = []
                self.stream_temp_sensor3_buffer = []
                self.stream_temp_sensor4_buffer = []
                print(" Запущен QUAD потоковый режим (все 4 датчика)")
            
            # Забираем очередное измерение из потока чтения датчиков
            sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.read_sensors_safe()
            if None in (sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm):
                # Данные ещё не готовы – делаем короткую паузу и повторяем цикл
                time.sleep(0.001)
                return
            
            self.stream_measurement_count += 1
            
            # Периодическая очистка буферов серийного порта (каждые 10 секунд) для предотвращения переполнения
            # На слабых процессорах буферы могут переполняться быстрее
            if self.stream_measurement_count % 1000 == 0:  # ~10 секунд при 109 Гц
                self.clear_serial_buffers()
                if self.stream_measurement_count % 3000 == 0:  # Сообщение только каждые 30 секунд
                    print(f" [CMD=200] Периодическая очистка буферов серийного порта (измерение #{self.stream_measurement_count})")
            
            # Проверка валидности данных: фильтруем явно некорректные значения
            # Диапазон: 0-50 мм (базовое 20 + диапазон 25)
            max_valid_value = 50.0
            min_valid_value = 0.0
            
            # Проверяем валидность каждого значения
            values_valid = True
            invalid_sensors = []
            
            if sensor1_mm is not None and (sensor1_mm < min_valid_value or sensor1_mm > max_valid_value):
                values_valid = False
                invalid_sensors.append(1)
            if sensor2_mm is not None and (sensor2_mm < min_valid_value or sensor2_mm > max_valid_value):
                values_valid = False
                invalid_sensors.append(2)
            if sensor3_mm is not None and (sensor3_mm < min_valid_value or sensor3_mm > max_valid_value):
                values_valid = False
                invalid_sensors.append(3)
            if sensor4_mm is not None and (sensor4_mm < min_valid_value or sensor4_mm > max_valid_value):
                values_valid = False
                invalid_sensors.append(4)
            
            # Выводим ошибки некорректных значений не чаще раза в 5 секунд
            if invalid_sensors and self.stream_measurement_count % 500 == 0:
                print(f" [CMD=200] ⚠ Некорректные значения датчиков {invalid_sensors}: "
                      f"Д1={sensor1_mm:.3f}мм Д2={sensor2_mm:.3f}мм Д3={sensor3_mm:.3f}мм Д4={sensor4_mm:.3f}мм (должно быть 0-50)")
                # При обнаружении некорректных значений очищаем буферы
                self.clear_serial_buffers()
            
            # Если получили валидные измерения от всех датчиков И значения в допустимом диапазоне
            if (all(v is not None for v in [sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm]) and values_valid):
                # Добавляем в временные буферы для усреднения
                self.stream_temp_sensor1_buffer.append(sensor1_mm)
                self.stream_temp_sensor2_buffer.append(sensor2_mm)
                self.stream_temp_sensor3_buffer.append(sensor3_mm)
                self.stream_temp_sensor4_buffer.append(sensor4_mm)
                
                # Когда накопилось 10 измерений - усредняем и записываем в регистры
                window_size = self.get_smoothing_window_size()
                if len(self.stream_temp_sensor1_buffer) >= window_size:
                    # Вычисляем средние значения для каждого датчика
                    avg_sensor1 = sum(self.stream_temp_sensor1_buffer) / len(self.stream_temp_sensor1_buffer)
                    avg_sensor2 = sum(self.stream_temp_sensor2_buffer) / len(self.stream_temp_sensor2_buffer)
                    avg_sensor3 = sum(self.stream_temp_sensor3_buffer) / len(self.stream_temp_sensor3_buffer)
                    avg_sensor4 = sum(self.stream_temp_sensor4_buffer) / len(self.stream_temp_sensor4_buffer)
                    
                    # Записываем все 4 регистра одновременно
                    # Оптимизация: группируем записи для снижения нагрузки на Modbus
                    try:
                        # Записываем все регистры последовательно (быстрее чем параллельно)
                        self.write_stream_result_to_input_registers(avg_sensor1, 30001)  # Датчик 1
                        self.write_stream_result_to_input_registers(avg_sensor2, 30003)  # Датчик 2
                        self.write_stream_result_to_input_registers(avg_sensor3, 30005)  # Датчик 3
                        self.write_stream_result_to_input_registers(avg_sensor4, 30007)  # Датчик 4
                    except Exception as e:
                        print(f" ОШИБКА ЗАПИСИ В РЕГИСТРЫ: {e}")
                        # При ошибке записи очищаем буферы серийного порта
                        self.clear_serial_buffers()
                    
                    # Выводим результат раз в секунду
                    current_time = time.time()
                    if not hasattr(self, '_last_stream_quad_print'):
                        self._last_stream_quad_print = current_time
                    
                    if current_time - self._last_stream_quad_print >= 1.0:
                        elapsed = current_time - self.stream_start_time
                        frequency = self.stream_measurement_count / elapsed if elapsed > 0 else 0
                        
                        print(f" [CMD=200] QUAD: {elapsed:5.1f}с | Измерений: {self.stream_measurement_count:6d} | "
                              f"Частота: {frequency:7.1f} Гц | "
                              f"Д1={avg_sensor1:.3f}мм Д2={avg_sensor2:.3f}мм Д3={avg_sensor3:.3f}мм Д4={avg_sensor4:.3f}мм")
                        
                        self._last_stream_quad_print = current_time
                    
                    # Очищаем временные буферы для следующих измерений окна сглаживания
                    self.stream_temp_sensor1_buffer = []
                    self.stream_temp_sensor2_buffer = []
                    self.stream_temp_sensor3_buffer = []
                    self.stream_temp_sensor4_buffer = []
            else:
                # Ошибка получения данных (None значения)
                # Некорректные значения уже обработаны выше с очисткой буферов
                if not values_valid:
                    # Некорректные значения уже обработаны выше - просто пропускаем
                    pass
                elif self.stream_measurement_count % 500 == 0:  # Показываем ошибку не чаще раза в 5 секунд
                    print(f" [CMD=200] Ошибка измерения (None): Д1={sensor1_mm}, Д2={sensor2_mm}, Д3={sensor3_mm}, Д4={sensor4_mm}")
                    # При None значениях также очищаем буферы
                    self.clear_serial_buffers()
            
        except Exception as e:
            print(f" Ошибка QUAD потокового режима: {e}")
            # Останавливаем QUAD режим при ошибке
            self.stream_active_quad = False
            self.current_state = SystemState.ERROR
    def write_stream_result_to_input_registers(self, value: float, base_address: int):
        """Запись результата потокового измерения в Input регистры"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # Конвертируем float в два 16-битных регистра
                low_word, high_word = self.float_to_doubleword(value)
                
                # Вычисляем индексы регистров
                # По описанию: base_address - 1 содержит младшее слово, base_address содержит старшее слово
                # Например, для 30052: индекс 51 = младшее слово, индекс 52 = старшее слово
                reg_index_low = base_address - 30000    # Младший регистр (base_address - 1)
                reg_index_high = base_address - 30000 + 1      # Старший регистр (base_address)
                
                # Записываем в Input регистры (функция 4)
                self.modbus_server.slave_context.setValues(4, reg_index_low, [int(high_word)])    # Младший
                self.modbus_server.slave_context.setValues(4, reg_index_high, [int(low_word)])  # Старш
                
        except Exception as e:
            print(f" ОШИБКА ЗАПИСИ В INPUT РЕГИСТРЫ {base_address}-{base_address+1}: {e}")
            import traceback
            traceback.print_exc()
    
    def handle_error_state(self):
        """Обработка состояния ошибки"""
        print(" Состояние ошибки. Проверьте систему.")


def check_single_instance():
    """Проверка, что запущен только один экземпляр программы"""
    current_pid = os.getpid()
    script_name = os.path.basename(__file__)
    
    # Метод 1: Проверка через поиск других процессов с тем же скриптом
    if HAS_PSUTIL:
        try:
            found_processes = []
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    other_pid = proc.pid  # proc.pid надёжнее чем proc.info['pid']
                    if other_pid == current_pid:
                        continue
                    
                    cmdline = proc.info.get('cmdline', [])
                    if cmdline and any(script_name in str(arg) for arg in cmdline):
                        # Найден другой процесс с тем же скриптом
                        # Проверяем, что это действительно наш скрипт
                        if any('laser_geometry_system.py' in str(arg) for arg in cmdline):
                            found_processes.append(other_pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            # Исключаем текущий процесс (на случай если psutil вернул его в списке)
            found_processes = [p for p in found_processes if p != current_pid]
            
            # Если найдены другие процессы, ждем немного и проверяем снова
            # (возможно, это старые процессы, которые завершаются)
            if found_processes:
                print(f"[ПРОВЕРКА] Найдены другие процессы: {found_processes}, ожидание 2 секунды...")
                time.sleep(2)
                
                # Повторная проверка
                still_running = []
                for pid in found_processes:
                    if os.path.exists(f'/proc/{pid}'):
                        try:
                            proc = psutil.Process(pid)
                            cmdline = proc.cmdline()
                            if any('laser_geometry_system.py' in str(arg) for arg in cmdline):
                                still_running.append(pid)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass  # Процесс завершился
                
                if still_running:
                    return False, f"Программа уже запущена (PID: {still_running})"
        except Exception as e:
            pass  # Если не удалось проверить через psutil, продолжаем
    
    # Метод 2: Проверка через socket (порт 502)
    try:
        test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        test_sock.settimeout(0.1)
        result = test_sock.connect_ex(('192.168.1.50', 502))
        test_sock.close()
        if result == 0:
            # Порт занят - возможно, другой процесс уже запущен
            return False, "Порт 502 уже занят (возможно, программа уже запущена)"
    except Exception:
        pass  # Если не удалось проверить порт, продолжаем
    
    # Метод 3: Lock файл с fcntl (самый надежный для Linux)
    lock_file_path = '/tmp/laser_geometry_system.lock'
    lock_file = None
    
    try:
        # Пытаемся открыть lock файл
        lock_file = open(lock_file_path, 'w')
        
        # Пытаемся заблокировать файл (неблокирующий режим)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        
        # Записываем PID текущего процесса
        lock_file.write(str(current_pid))
        lock_file.flush()
        
        # Функция для очистки lock файла при выходе
        def cleanup_lock():
            try:
                if lock_file:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    lock_file.close()
                if os.path.exists(lock_file_path):
                    os.remove(lock_file_path)
            except:
                pass
        
        # Регистрируем очистку при выходе
        import atexit
        atexit.register(cleanup_lock)
        
        return True, None
        
    except (IOError, OSError) as e:
        # Файл уже заблокирован - другой процесс запущен
        if lock_file:
            lock_file.close()
        
        # Проверяем, действительно ли процесс еще работает
        try:
            if os.path.exists(lock_file_path):
                with open(lock_file_path, 'r') as f:
                    pid_str = f.read().strip()
                    if pid_str:
                        old_pid = int(pid_str)
                        
                        # Проверяем, существует ли процесс с этим PID
                        if os.path.exists(f'/proc/{old_pid}'):
                            return False, f"Программа уже запущена (PID: {old_pid})"
                        else:
                            # Процесс не существует, удаляем старый lock файл
                            try:
                                os.remove(lock_file_path)
                                # Пытаемся запуститься снова
                                return check_single_instance()
                            except:
                                return False, "Не удалось удалить старый lock файл"
                    else:
                        # Lock файл пустой, удаляем его
                        try:
                            os.remove(lock_file_path)
                            return check_single_instance()
                        except:
                            return False, "Не удалось удалить пустой lock файл"
            else:
                # Lock файл не существует, но fcntl не смог заблокировать
                return False, "Не удалось заблокировать файл (возможно, другой процесс использует его)"
        except (ValueError, OSError) as e:
            # Ошибка при чтении/проверке lock файла
            try:
                if os.path.exists(lock_file_path):
                    os.remove(lock_file_path)
                    return check_single_instance()
            except:
                pass
            return False, f"Ошибка проверки lock файла: {e}"
    
    except Exception as e:
        if lock_file:
            lock_file.close()
        return False, f"Ошибка проверки единственного экземпляра: {e}"


def main():
    """Главная функция"""
    # ВАЖНО: Проверка на единственный экземпляр должна быть ПЕРВОЙ операцией
    # до любых других действий, чтобы предотвратить race condition
    can_run, error_msg = check_single_instance()
    if not can_run:
        print(f"ОШИБКА: {error_msg}")
        print("Программа уже запущена. Завершаем...")
        sys.exit(1)
    
    # Небольшая задержка, чтобы дать время первому процессу полностью инициализироваться
    # Это помогает предотвратить ситуацию, когда два процесса проходят проверку почти одновременно
    time.sleep(0.5)
    
    # Повторная проверка после задержки (на случай, если другой процесс успел запуститься)
    can_run, error_msg = check_single_instance()
    if not can_run:
        print(f"ОШИБКА: {error_msg}")
        print("Обнаружен другой экземпляр после задержки. Завершаем...")
        sys.exit(1)
    
    print("СИСТЕМА ЛАЗЕРНОЙ ГЕОМЕТРИИ")
    print("Интеграция датчиков РФ602 + Modbus + Автомат состояний")
    print("=" * 60)
    
    # Настройки системы
    PORT = '/dev/ttyUSB0'  # Измените при необходимости на другой ttyUSB/ttyACM порт
    BAUDRATE = 921600
    MODBUS_PORT = 502
    TEST_MODE = False  # Режим с реальными датчиками
    
    # Создание и запуск системы
    system = LaserGeometrySystem(PORT, BAUDRATE, MODBUS_PORT, test_mode=TEST_MODE)
    
    try:
        system.start_system()
    except KeyboardInterrupt:
        print("\n Остановка по запросу пользователя")
    except Exception as e:
        print(f"Критическая ошибка: {e}")
    finally:
        try:
            system.stop_system()
        except Exception:
            pass
        # Финальная очистка оптимизаций
        cleanup_laser_system_optimizations()


if __name__ == "__main__":
    main()
 