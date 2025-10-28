#!/usr/bin/env python3
"""
🚀 ВЫСОКОСКОРОСТНАЯ реализация протокола RIFTEK для датчиков РФ602

РЕЖИМЫ РАБОТЫ:
✅ Потоковый режим (один датчик) - ~1000+ Гц
✅ QUAD режим (четыре датчика синхронно) - ~125 Гц

СИСТЕМНЫЕ ОПТИМИЗАЦИИ:
✅ Высокий приоритет процесса (psutil)
✅ Высокое разрешение таймера Windows (1 мс)
✅ Отключение энергосбережения
✅ Поддержка pyftdi для FTDI адаптеров

СЕТЕВЫЕ ОПТИМИЗАЦИИ:
✅ Baudrate 921600 (максимум RS485)
✅ Агрессивные таймауты (2 мс)
✅ Отключение управления потоком
✅ Минимальные буферы

ОСОБЕННОСТИ:
✅ Нулевые измерения не включают базовое расстояние 20мм
✅ Broadcast синхронизация для QUAD режима
✅ Высокоскоростной потоковый режим для выбранного датчика
"""

import time
import struct
import os
import ctypes
from typing import Optional, Tuple
from collections import deque

# Попытка импорта psutil для высокого приоритета
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    print("WARNING: psutil не установлен. Установите: pip install psutil")
    HAS_PSUTIL = False

# Попытка импорта pyftdi для максимальной скорости
try:
    from pyftdi.serialext import serial_for_url
    HAS_PYFTDI = True
    print("OK pyftdi доступен - будет использован для максимальной скорости")
except ImportError:
    print("WARNING: pyftdi не установлен. Установите: pip install pyftdi")
    # Fallback на обычный serial
    try:
        import serial
        HAS_PYFTDI = False
        print("INFO: Используется стандартный pyserial")
    except ImportError:
        print("ERROR: Ни pyftdi, ни pyserial не установлены!")
        print("Установите одну из библиотек:")
        print("  pip install pyftdi  (рекомендуется для FTDI)")
        print("  pip install pyserial  (стандартная)")
        exit(1)


def detect_ftdi_devices():
    """
    Автоопределение FTDI устройств
    """
    if not HAS_PYFTDI:
        return []
    
    try:
        from pyftdi.ftdi import Ftdi
        devices = []
        for device in Ftdi.list_devices():
            url = f"ftdi://{device[0].hex()}:{device[1].hex()}/1"
            devices.append(url)
        return devices
    except Exception as e:
        print(f"WARNING: Ошибка определения FTDI: {e}")
        return []


class HighSpeedRiftekSensor:
    """Высокоскоростной класс для работы с лазерными датчиками RIFTEK по протоколу RS485"""
    
    def __init__(self, port: str = 'COM7', baudrate: int = 921600, timeout: float = 1.0):
        """
        Инициализация соединения с датчиками для ЭКСТРЕМАЛЬНО высокоскоростной работы
        
        Args:
            port: COM порт (например, 'COM7' или 'ftdi://ftdi:232h/1' для pyftdi)
            baudrate: МАКСИМАЛЬНАЯ скорость передачи данных (921600)
            timeout: Агрессивный таймаут для максимальной скорости (0.002 сек)
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None
        
        # Статистика производительности
        self.measurement_count = 0
        self.error_count = 0
        self.start_time = None
        self.last_frequency_check = 0
        
        # Буфер для анализа производительности
        self.timing_buffer = deque(maxlen=100)  # Последние 100 измерений
        
        # Диагностика времени выполнения этапов
        self.time_broadcast = 0
        self.time_sync_delay = 0
        self.time_sensor1 = 0
        self.time_sensor2 = 0
        self.time_sensor3 = 0
        self.time_sensor4 = 0
        self.time_conversion = 0
        
        # Предварительно сформированные команды для скорости
        self.broadcast_command = bytes([0x00, 0x85])  # Broadcast latch command
        self.sensor1_command = bytes([0x01, 0x86])    # Sensor 1 read command  
        self.sensor2_command = bytes([0x02, 0x86])    # Sensor 2 read command
        self.sensor3_command = bytes([0x03, 0x86])    # Sensor 3 read command
        self.sensor4_command = bytes([0x04, 0x86])    # Sensor 4 read command
        
    def connect(self) -> bool:
        """
        Установка соединения с портом используя оптимальную библиотеку
        
        Returns:
            True если соединение установлено успешно
        """
        try:
            # Используем pyftdi автоматически для высоких скоростей или если указан ftdi URL
            if HAS_PYFTDI and ('ftdi://' in self.port.lower() or self.baudrate >= 921600):
                print("INFO: Пытаемся использовать pyftdi для максимальной скорости...")
                try:
                    # Если указан обычный COM порт, попробуем автоопределение FTDI
                    if self.port.upper().startswith('COM'):
                        print("INFO: Автоопределение FTDI устройства...")
                        ftdi_devices = detect_ftdi_devices()
                        if ftdi_devices:
                            ftdi_url = ftdi_devices[0]  # Берем первое найденное
                            print(f"INFO: Найдено FTDI устройство: {ftdi_url}")
                        else:
                            ftdi_url = 'ftdi://ftdi:232h/1'  # Fallback стандартный URL
                            print("INFO: Используется стандартный FTDI URL")
                    else:
                        ftdi_url = self.port
                        
                    self.ser = serial_for_url(
                        ftdi_url,
                        baudrate=self.baudrate,
                        bytesize=8,
                        parity='E',  # Even parity
                        stopbits=1,
                        timeout=self.timeout,
                        write_timeout=0.001
                    )
                    
                    # Исправление ошибки pyftdi - добавляем отсутствующий атрибут udev
                    try:
                        # Проверяем и добавляем отсутствующий атрибут udev для pyftdi
                        if not hasattr(self.ser, 'udev'):
                            self.ser.udev = None
                        # Также проверяем другие возможные отсутствующие атрибуты
                        if not hasattr(self.ser, '_ftdi'):
                            self.ser._ftdi = None
                    except Exception:
                        # Игнорируем ошибки при добавлении атрибутов
                        pass
                    
                    print(f"OK: pyftdi успешно подключен: {ftdi_url}")
                except Exception as e:
                    print(f"WARNING: Ошибка pyftdi: {e}")
                    print("INFO: Переключаемся на стандартный pyserial...")
                    # Переключаемся на fallback
                    import serial
                    self.ser = serial.Serial(
                        port=self.port.replace('ftdi://ftdi:232h/1', 'COM7'),  # Возврат к COM порту
                        baudrate=self.baudrate,
                        bytesize=serial.EIGHTBITS,
                        parity=serial.PARITY_EVEN,
                        stopbits=serial.STOPBITS_ONE,
                        timeout=self.timeout,
                        write_timeout=0.001,
                        inter_byte_timeout=None,
                        rtscts=False,
                        dsrdtr=False,
                        xonxoff=False
                    )
            else:
                # Fallback на стандартный pyserial
                print("INFO: Используется стандартный pyserial...")
                import serial
                self.ser = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_EVEN,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=self.timeout,
                    write_timeout=0.001,
                    inter_byte_timeout=None,
                    # Отключение управления потоком для скорости
                    rtscts=False,
                    dsrdtr=False,
                    xonxoff=False
                )
            
            # Агрессивная настройка буферов для максимальной скорости
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            
            # Попытка установить размеры буферов для лучшей производительности
            try:
                if hasattr(self.ser, 'set_buffer_size'):
                    self.ser.set_buffer_size(rx_size=128, tx_size=64)
            except:
                pass  # Игнорируем ошибки настройки буферов
            
            print(f"Высокоскоростное соединение установлено:")
            print(f"  Порт: {self.port}")
            print(f"  Скорость: {self.baudrate} baud")
            print(f"  Таймаут: {self.timeout} сек")
            print(f"  Ожидаемая частота: ~1000-2000 Гц")
            
            self.start_time = time.time()
            return True
        except Exception as e:
            print(f"Ошибка подключения: {e}")
            return False
    
    def disconnect(self):
        """Закрытие соединения с выводом статистики"""
        if self.ser:
            try:
                # Проверяем, открыто ли соединение
                if hasattr(self.ser, 'is_open') and self.ser.is_open:
                    # Для pyftdi объектов добавляем все необходимые атрибуты
                    if hasattr(self.ser, '_ftdi'):
                        if not hasattr(self.ser, 'udev'):
                            self.ser.udev = None
                        if not hasattr(self.ser, '_port'):
                            self.ser._port = None
                    self.ser.close()
                elif hasattr(self.ser, '_port'):
                    # Альтернативный способ закрытия для pyftdi
                    self.ser._port = None
            except AttributeError as e:
                # Специальная обработка ошибки udev
                if 'udev' in str(e):
                    try:
                        # Пытаемся добавить отсутствующий атрибут и закрыть
                        if not hasattr(self.ser, 'udev'):
                            self.ser.udev = None
                        if not hasattr(self.ser, '_port'):
                            self.ser._port = None
                        self.ser.close()
                    except:
                        # Если все еще не работает, просто обнуляем объект
                        self.ser = None
                else:
                    # Другие AttributeError
                    pass
            except Exception:
                # Игнорируем все остальные ошибки при закрытии
                pass
            finally:
                # В любом случае обнуляем ссылку на объект
                self.ser = None
            
            # Вывод детальной статистики
            if self.start_time and self.measurement_count > 0:
                elapsed = time.time() - self.start_time
                avg_frequency = self.measurement_count / elapsed
                error_rate = (self.error_count / self.measurement_count) * 100
                
                print(f"\n{'='*50}")
                print(f"СТАТИСТИКА ВЫСОКОСКОРОСТНОЙ РАБОТЫ")
                print(f"{'='*50}")
                print(f"Время работы: {elapsed:.1f} сек")
                print(f"Всего измерений: {self.measurement_count}")
                print(f"Успешных: {self.measurement_count - self.error_count}")
                print(f"Ошибок: {self.error_count}")
                print(f"Средняя частота: {avg_frequency:.1f} Гц")
                print(f"Процент ошибок: {error_rate:.2f}%")
                
                if len(self.timing_buffer) > 1:
                    intervals = [self.timing_buffer[i] - self.timing_buffer[i-1] 
                               for i in range(1, len(self.timing_buffer))]
                    avg_interval = sum(intervals) / len(intervals)
                    if avg_interval > 0:
                        instant_freq = 1.0 / avg_interval
                        print(f"Мгновенная частота: {instant_freq:.1f} Гц")
                
                print(f"{'='*50}")
            
            print("Соединение закрыто")
    
    def send_broadcast_latch_command_fast(self):
        """
        УЛЬТРА-ОПТИМИЗИРОВАННАЯ отправка broadcast команды защелкивания (05h)
        Минимизированы все возможные задержки
        """
        start_time = time.perf_counter()
        
        try:
            # Прямая отправка предкешированной команды
            self.ser.write(self.broadcast_command)
            # КРИТИЧНО: НЕ используем flush() - это добавляет ~0.1мс задержки
            # Датчики обрабатывают команду асинхронно за ~50 мкс
            
            self.time_broadcast = time.perf_counter() - start_time
        except Exception as e:
            self.error_count += 1
            if self.error_count <= 10:  # Выводим только первые 10 ошибок
                print(f"ОШИБКА BROADCAST: {e}")
            self.time_broadcast = time.perf_counter() - start_time
    
    def request_measurement_fast(self, sensor_address: int) -> Optional[int]:
        """
        Высокоскоростной запрос результата измерения с диагностикой времени
        
        Args:
            sensor_address: Адрес датчика (1-4)
            
        Returns:
            Результат измерения в условных единицах (0-16383) или None при ошибке
        """
        start_time = time.perf_counter()
        
        try:
            # Используем предварительно сформированные команды для скорости
            if sensor_address == 1:
                command = self.sensor1_command
            elif sensor_address == 2:
                command = self.sensor2_command
            elif sensor_address == 3:
                command = self.sensor3_command
            elif sensor_address == 4:
                command = self.sensor4_command
            else:
                raise ValueError(f"Неподдерживаемый адрес датчика: {sensor_address}")
            
            # Отправляем команду
            self.ser.write(command)
            
            # Чтение ответа (4 байта - результат по тетрадам)
            response = self.ser.read(4)
            
            # Диагностика ошибок (только для первых 10 ошибок)
            if len(response) != 4:
                self.error_count += 1
                if self.error_count <= 10:  # Выводим только первые 10 ошибок
                    print(f"ОШИБКА ДАТЧИК {sensor_address}: Получено {len(response)} байт вместо 4")
                    if len(response) > 0:
                        print(f"  Полученные байты: {[hex(b) for b in response]}")
                if sensor_address == 1:
                    self.time_sensor1 = time.perf_counter() - start_time
                elif sensor_address == 2:
                    self.time_sensor2 = time.perf_counter() - start_time
                elif sensor_address == 3:
                    self.time_sensor3 = time.perf_counter() - start_time
                elif sensor_address == 4:
                    self.time_sensor4 = time.perf_counter() - start_time
                return None
            
            # Проверка формата (все байты должны иметь бит 7 = 1)
            if not all(byte & 0x80 for byte in response):
                self.error_count += 1
                if self.error_count <= 10:  # Выводим только первые 10 ошибок
                    print(f"ОШИБКА ДАТЧИК {sensor_address}: Неверный формат данных")
                    print(f"  Полученные байты: {[hex(b) for b in response]}")
                    print(f"  Бит 7 в байтах: {[bool(b & 0x80) for b in response]}")
                if sensor_address == 1:
                    self.time_sensor1 = time.perf_counter() - start_time
                elif sensor_address == 2:
                    self.time_sensor2 = time.perf_counter() - start_time
                elif sensor_address == 3:
                    self.time_sensor3 = time.perf_counter() - start_time
                elif sensor_address == 4:
                    self.time_sensor4 = time.perf_counter() - start_time
                return None
            
            # Оптимизированная сборка результата
            byte0 = (response[0] & 0x0F) | ((response[1] & 0x0F) << 4)
            byte1 = (response[2] & 0x0F) | ((response[3] & 0x0F) << 4)
            result = byte0 | (byte1 << 8)
            
            # Сохранение времени выполнения
            if sensor_address == 1:
                self.time_sensor1 = time.perf_counter() - start_time
            elif sensor_address == 2:
                self.time_sensor2 = time.perf_counter() - start_time
            elif sensor_address == 3:
                self.time_sensor3 = time.perf_counter() - start_time
            elif sensor_address == 4:
                self.time_sensor4 = time.perf_counter() - start_time
            
            return result
            
        except Exception as e:
            self.error_count += 1
            if self.error_count <= 10:  # Выводим только первые 10 ошибок
                print(f"ОШИБКА ДАТЧИК {sensor_address}: Исключение: {e}")
            if sensor_address == 1:
                self.time_sensor1 = time.perf_counter() - start_time
            elif sensor_address == 2:
                self.time_sensor2 = time.perf_counter() - start_time
            elif sensor_address == 3:
                self.time_sensor3 = time.perf_counter() - start_time
            elif sensor_address == 4:
                self.time_sensor4 = time.perf_counter() - start_time
            return None
    
    def convert_to_mm(self, raw_value: int, sensor_range_mm: float, base_distance_mm: float = 20.0) -> float:
        """
        Преобразование сырого значения в АБСОЛЮТНОЕ расстояние в миллиметрах
        
        Args:
            raw_value: Сырое значение от датчика (0-16383)
            sensor_range_mm: Диапазон датчика в миллиметрах (25 мм)
            base_distance_mm: Базовое расстояние датчика (20 мм)
            
        Returns:
            АБСОЛЮТНОЕ расстояние в миллиметрах
        """
        # Если измерение 0, не прибавляем базовое расстояние
        if raw_value == 0:
            return 0.0
        
        # Формула: Абсолютное расстояние = Базовое + (D * Диапазон / 16384)
        # где D - сырое значение, Диапазон = 25 мм, Базовое = 20 мм
        offset_mm = (raw_value * sensor_range_mm) / 16384.0
        absolute_distance = base_distance_mm + offset_mm
        return absolute_distance
    

    def start_stream_mode(self, sensor_address: int) -> bool:
        """
        Запуск режима потока данных для указанного датчика (команда 07h)
        
        Args:
            sensor_address: Адрес датчика (1 или 2)
            
        Returns:
            True если команда отправлена успешно
        """
        try:
            # Формат команды: Байт0 = 0|ADR, Байт1 = 1|000|COD
            # ADR = sensor_address, COD = 07h (запрос потока)
            command = bytes([sensor_address, 0x87])  # 0x87 = 1|000|0111
            
            self.ser.write(command)
            print(f"Режим потока данных запущен для датчика {sensor_address}")
            return True
            
        except Exception as e:
            print(f"Ошибка запуска потока для датчика {sensor_address}: {e}")
            return False
    
    def stop_stream_mode(self, sensor_address: int) -> bool:
        """
        Остановка режима потока данных (команда 08h)
        
        Args:
            sensor_address: Адрес датчика (1 или 2)
            
        Returns:
            True если команда отправлена успешно
        """
        try:
            # Формат команды: Байт0 = 0|ADR, Байт1 = 1|000|COD
            # ADR = sensor_address, COD = 08h (остановка потока)
            command = bytes([sensor_address, 0x88])  # 0x88 = 1|000|1000
            
            self.ser.write(command)
            print(f"Режим потока данных остановлен для датчика {sensor_address}")
            return True
            
        except Exception as e:
            print(f"Ошибка остановки потока для датчика {sensor_address}: {e}")
            return False
    
    def read_stream_data(self, sensor_range_mm: float = 25.0) -> Optional[float]:
        """
        Чтение одного пакета данных из потока с улучшенной обработкой
        
        Args:
            sensor_range_mm: Диапазон датчика в мм
            
        Returns:
            Значение в миллиметрах или None при ошибке
        """
        try:
            # Читаем 4 байта как в обычном режиме
            response = self.ser.read(4)
            
            if len(response) != 4:
                # Если получили неполный пакет, пробуем дочитать
                if len(response) > 0:
                    remaining = self.ser.read(4 - len(response))
                    response += remaining
                    if len(response) != 4:
                        return None
                else:
                    return None
                
            # Проверка формата (все байты должны иметь бит 7 = 1)
            if not all(byte & 0x80 for byte in response):
                # Пытаемся найти начало следующего валидного пакета
                for i in range(1, 4):
                    if response[i] & 0x80:  # Найден байт с битом 7=1
                        # Читаем недостающие байты
                        remaining_bytes = self.ser.read(i)
                        if len(remaining_bytes) == i:
                            new_response = response[i:] + remaining_bytes
                            if len(new_response) == 4 and all(b & 0x80 for b in new_response):
                                response = new_response
                                break
                else:
                    return None
            
            # Сборка результата из тетрад
            byte0 = (response[0] & 0x0F) | ((response[1] & 0x0F) << 4)
            byte1 = (response[2] & 0x0F) | ((response[3] & 0x0F) << 4)
            raw_value = byte0 | (byte1 << 8)
            
            # Проверка на разумные значения (0-16383)
            if raw_value > 16383:
                return None
            
            # Преобразование в АБСОЛЮТНОЕ расстояние в миллиметрах
            if raw_value == 0:
                return 0.0
            offset_mm = (raw_value * sensor_range_mm) / 16384.0
            return 20.0 + offset_mm  # Базовое расстояние + смещение
            
        except Exception as e:
            return None
    
    def ultra_fast_stream_measurement(self, sensor_address: int, sensor_range_mm: float = 25.0, 
                                    duration: float = 10.0) -> None:
        """
        Ультра-быстрое измерение в режиме потока данных
        Теоретическая скорость: ~9480 Гц при 921600 baud
        
        Args:
            sensor_address: Адрес датчика (1 или 2)
            sensor_range_mm: Диапазон датчика в мм
            duration: Продолжительность измерения в секундах
        """
        print(f"\n{'='*60}")
        print(f"УЛЬТРА-БЫСТРЫЙ РЕЖИМ ПОТОКА ДАННЫХ")
        print(f"Датчик: {sensor_address} | Диапазон: {sensor_range_mm} мм")
        print(f"Теоретическая скорость: ~9480 Гц")
        print(f"{'='*60}")
        
        # Запуск потока
        if not self.start_stream_mode(sensor_address):
            return
        
        start_time = time.time()
        last_display = start_time
        measurement_count = 0
        valid_count = 0
        
        try:
            while (time.time() - start_time) < duration:
                # Чтение данных из потока
                measurement = self.read_stream_data(sensor_range_mm)
                measurement_count += 1
                
                if measurement is not None:
                    valid_count += 1
                    
                # Обновление статистики каждую секунду
                current_time = time.time()
                if (current_time - last_display) >= 1.0:
                    elapsed = current_time - start_time
                    frequency = measurement_count / elapsed if elapsed > 0 else 0
                    success_rate = (valid_count / measurement_count * 100) if measurement_count > 0 else 0
                    
                    if measurement is not None:
                        print(f"Время: {elapsed:5.1f}с | Измерений: {measurement_count:6d} | "
                              f"Частота: {frequency:7.1f} Гц | Успех: {success_rate:5.1f}% | "
                              f"Данные: {measurement:6.3f}мм")
                    else:
                        print(f"Время: {elapsed:5.1f}с | Измерений: {measurement_count:6d} | "
                              f"Частота: {frequency:7.1f} Гц | Успех: {success_rate:5.1f}% | "
                              f"Синхронизация потока...")
                    
                    last_display = current_time
                    
        except KeyboardInterrupt:
            print("\nОстановка по запросу пользователя")
        finally:
            # Остановка потока
            self.stop_stream_mode(sensor_address)
            
            # Финальная статистика
            elapsed = time.time() - start_time
            avg_frequency = measurement_count / elapsed if elapsed > 0 else 0
            success_rate = (valid_count / measurement_count * 100) if measurement_count > 0 else 0
            
            print(f"\n{'='*60}")
            print(f"ИТОГОВАЯ СТАТИСТИКА ПОТОКОВОГО РЕЖИМА")
            print(f"{'='*60}")
            print(f"Время работы: {elapsed:.1f} сек")
            print(f"Всего пакетов: {measurement_count}")
            print(f"Валидных: {valid_count}")
            print(f"Средняя частота: {avg_frequency:.1f} Гц")
            print(f"Процент успеха: {success_rate:.1f}%")
            print(f"Ускорение по сравнению с запросным режимом: {avg_frequency/31.3:.1f}x")
    

    def perform_quad_sensor_measurement(self, sensor1_range_mm: float = 25.0, 
                                       sensor2_range_mm: float = 25.0,
                                       sensor3_range_mm: float = 25.0,
                                       sensor4_range_mm: float = 25.0) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """
        Ультра-быстрое синхронное измерение с четырех датчиков
        Broadcast синхронизация для получения одновременных измерений со всех датчиков
        
        Args:
            sensor1_range_mm: Диапазон первого датчика в мм (25 мм)
            sensor2_range_mm: Диапазон второго датчика в мм (25 мм)
            sensor3_range_mm: Диапазон третьего датчика в мм (25 мм)
            sensor4_range_mm: Диапазон четвертого датчика в мм (25 мм)
            
        Returns:
            Кортеж (измерение_датчик1_мм, измерение_датчик2_мм, измерение_датчик3_мм, измерение_датчик4_мм)
        """
        measurement_start = time.perf_counter()
        
        # 1. Broadcast команда защелкивания для синхронизации всех 4 датчиков
        self.send_broadcast_latch_command_fast()
        
        # 2. Минимальная задержка для синхронизации с диагностикой
        sync_start = time.perf_counter()
        time.sleep(0.00005)  # 50 мкс - агрессивная синхронизация
        self.time_sync_delay = time.perf_counter() - sync_start
        
        # 3. Быстрое последовательное чтение результатов со всех четырех датчиков
        result1_raw = self.request_measurement_fast(1)
        result2_raw = self.request_measurement_fast(2)
        result3_raw = self.request_measurement_fast(3)
        result4_raw = self.request_measurement_fast(4)
        
        # 4. Быстрое преобразование в миллиметры с предрассчитанными коэффициентами
        conversion_start = time.perf_counter()
        result1_mm = None
        result2_mm = None
        result3_mm = None
        result4_mm = None
        
        # Предрассчитанные коэффициенты для скорости
        sensor1_coeff = sensor1_range_mm / 16384.0
        sensor2_coeff = sensor2_range_mm / 16384.0
        sensor3_coeff = sensor3_range_mm / 16384.0
        sensor4_coeff = sensor4_range_mm / 16384.0
        BASE_DISTANCE = 20.0  # мм - базовое расстояние для РФ602-20/25
        
        if result1_raw is not None:
            if result1_raw == 0:
                result1_mm = 0.0
            else:
                offset1 = result1_raw * sensor1_coeff
                result1_mm = BASE_DISTANCE + offset1
            
        if result2_raw is not None:
            if result2_raw == 0:
                result2_mm = 0.0
            else:
                offset2 = result2_raw * sensor2_coeff
                result2_mm = BASE_DISTANCE + offset2
            
        if result3_raw is not None:
            if result3_raw == 0:
                result3_mm = 0.0
            else:
                offset3 = result3_raw * sensor3_coeff
                result3_mm = BASE_DISTANCE + offset3
            
        if result4_raw is not None:
            if result4_raw == 0:
                result4_mm = 0.0
            else:
                offset4 = result4_raw * sensor4_coeff
                result4_mm = BASE_DISTANCE + offset4
        
        self.time_conversion = time.perf_counter() - conversion_start
        
        # Обновление статистики
        self.measurement_count += 1
        self.timing_buffer.append(measurement_start)
        
        return result1_mm, result2_mm, result3_mm, result4_mm


def apply_system_optimizations():
    """
    Применение системных оптимизаций Windows для максимальной производительности
    """
    print("🔧 ПРИМЕНЕНИЕ СИСТЕМНЫХ ОПТИМИЗАЦИЙ...")
    
    # Показываем статус библиотек
    print(f"📚 ДОСТУПНЫЕ БИБЛИОТЕКИ:")
    print(f"   psutil: {'✅ Доступен' if HAS_PSUTIL else '❌ Не установлен'}")
    print(f"   pyftdi: {'✅ Доступен' if HAS_PYFTDI else '❌ Не установлен'}")
    
    if HAS_PYFTDI:
        ftdi_devices = detect_ftdi_devices()
        if ftdi_devices:
            print(f"   FTDI устройства: {len(ftdi_devices)} найдено")
            for i, device in enumerate(ftdi_devices):
                print(f"     {i+1}. {device}")
        else:
            print(f"   FTDI устройства: ❌ Не найдены")
    print()
    
    # 1. Высокий приоритет процесса
    if HAS_PSUTIL:
        try:
            p = psutil.Process(os.getpid())
            p.nice(psutil.HIGH_PRIORITY_CLASS)
            print("✅ Установлен высокий приоритет процесса")
        except Exception as e:
            print(f"⚠️  Не удалось установить приоритет: {e}")
    else:
        print("⚠️  psutil недоступен - приоритет не изменен")
    
    # 2. Отключение Windows Timer Resolution (для точности времени)
    print("🕐 Настройка точности таймера Windows...")
    try:
        # Устанавливаем минимальное разрешение таймера (1 мс)
        result = ctypes.windll.winmm.timeBeginPeriod(1)
        if result == 0:  # TIMERR_NOERROR
            print("✅ Установлено высокое разрешение таймера (1 мс)")
        else:
            print("⚠️  Не удалось установить разрешение таймера")
    except Exception as e:
        print(f"⚠️  Ошибка настройки таймера: {e}")
    
    # 3. Дополнительные оптимизации
    try:
        # Отключаем спящий режим для текущего процесса
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        print("✅ Отключен спящий режим системы")
    except:
        print("⚠️  Не удалось отключить спящий режим")
    
    print("🚀 Системные оптимизации применены!\n")


def main():
    """Высокоскоростная основная функция для максимальной производительности"""
    
    # Применяем системные оптимизации
    apply_system_optimizations()
    
    print("=" * 70)
    print("ВЫСОКОСКОРОСТНАЯ ПРОГРАММА ОПРАШИВАНИЯ ДАТЧИКОВ RIFTEK")
    print("Потоковый режим (один датчик) и QUAD режим (4 датчика)")
    print("=" * 70)
    
    # Выбор режима работы
    print("\nВыберите режим работы:")
    print("1. Потоковый режим (один датчик, максимальная скорость) - ~1000+ Гц")
    print("2. 🆕 QUAD режим (четыре датчика синхронно) - ~125 Гц")
    print("3. 📊 ГРАФИЧЕСКИЙ режим (QUAD с визуализацией в реальном времени)")
    print("4. 📱 КОМПАКТНЫЙ графический режим (для небольших экранов)")
    print("5. 🔢 ПРОСТОЙ режим (5 окошек с числами, БЕЗ графиков)")
    
    try:
        choice = input("\nВведите номер режима (1-5): ").strip()
    except KeyboardInterrupt:
        print("\nВыход")
        return
        
    if choice == "1":
        # Потоковый режим
        print("\nВыберите датчик для потокового режима:")
        print("1. Датчик 1")
        print("2. Датчик 2")
        print("3. Датчик 3")
        print("4. Датчик 4")
        
        try:
            sensor_choice = input("Введите номер датчика (1-4): ").strip()
            sensor_address = int(sensor_choice)
            if sensor_address not in [1, 2, 3, 4]:
                print("Неверный номер датчика")
                return
        except (ValueError, KeyboardInterrupt):
            print("Неверный ввод")
            return
            
    elif choice == "2":
        # QUAD режим - четыре датчика синхронно
        pass  # Ничего дополнительно настраивать не нужно
        
    elif choice == "3":
        # Графический режим
        try:
            print("Запуск графического интерфейса...")
            import subprocess
            import sys
            subprocess.run([sys.executable, "gui_sensors.py"])
            return
        except Exception as e:
            print(f"Ошибка запуска графического режима: {e}")
            return
            
    elif choice == "4":
        # Компактный графический режим
        try:
            print("Запуск компактного графического интерфейса...")
            import subprocess
            import sys
            subprocess.run([sys.executable, "gui_sensors_compact.py"])
            return
        except Exception as e:
            print(f"Ошибка запуска компактного режима: {e}")
            return
            
    elif choice == "5":
        # Простой режим с окошками
        try:
            print("Запуск оптимизированного простого интерфейса...")
            import subprocess
            import sys
            subprocess.run([sys.executable, "gui_sensors_simple_optimized.py"])
            return
        except Exception as e:
            print(f"Ошибка запуска простого режима: {e}")
            return
        
    else:
        print("Неверный выбор")
        return
    
    # Настройки для ЭКСТРЕМАЛЬНОЙ скорости
    PORT = 'COM7'           # Ваш COM порт (для pyftdi используйте 'ftdi://ftdi:232h/1')
    BAUDRATE = 921600       # МАКСИМАЛЬНАЯ скорость RS485
    TIMEOUT = 0.002         # АГРЕССИВНЫЙ таймаут 2 мс для максимальной скорости

    
    # Параметры датчиков РФ602-20/25
    BASE_DISTANCE = 20.0    # мм - базовое расстояние
    SENSOR1_RANGE = 25.0    # мм - диапазон измерения
    SENSOR2_RANGE = 25.0    # мм - диапазон измерения
    SENSOR3_RANGE = 25.0    # мм - диапазон измерения
    SENSOR4_RANGE = 25.0    # мм - диапазон измерения
    
    print(f"Настройки:")
    print(f"  COM порт: {PORT}")
    print(f"  Скорость: {BAUDRATE} baud")
    print(f"  Модель датчика: РФ602-20/25")
    print(f"  Базовое расстояние: {BASE_DISTANCE} мм")  
    print(f"  Диапазон измерения: {SENSOR1_RANGE} мм")
    print(f"  Рабочий диапазон: {BASE_DISTANCE} - {BASE_DISTANCE + SENSOR1_RANGE} мм")
    print(f"\n🔧 КРИТИЧЕСКИ ВАЖНЫЕ НАСТРОЙКИ ДАТЧИКОВ ДЛЯ МАКСИМАЛЬНОЙ СКОРОСТИ:")
    print(f"  📊 Maximum Exposure Time: 3 μs (минимум!)")
    print(f"  📊 Sampling Period: 106 μs (минимум для 9.4 кГц)")
    print(f"  📊 Hold Last Valid Reading: 0 ms (ОТКЛЮЧИТЬ!)")
    print(f"  📊 Averaging: ОТКЛЮЧЕНО (1 значение)")
    print(f"  📊 Network Address: Датчик 1 = адрес 1, Датчик 2 = адрес 2")
    print(f"  📊 Baud Rate: 921600 (МАКСИМАЛЬНАЯ!)")
    print(f"\n🚀 ОЖИДАЕМЫЕ РЕЗУЛЬТАТЫ:")
    print(f"  ✅ Время опроса: 0.2-0.4 мс на датчик (было 1.0 мс)")
    print(f"  ✅ Общая частота: 700-1000+ Гц (было 500 Гц)")
    print(f"  ✅ Ускорение: в 2-3 раза!")
    print()
    
    # Создание высокоскоростного экземпляра класса
    sensors = HighSpeedRiftekSensor(PORT, BAUDRATE, TIMEOUT)
    
    # Попытка подключения
    if not sensors.connect():
        print("Не удалось подключиться к датчикам. Проверьте:")
        print("1. Правильность COM порта")
        print("2. Подключение кабеля RS485")
        print("3. Питание датчиков")
        return
    
    try:
        if choice == "1":
            # Потоковый режим - ультра-быстрый
            print(f"Запуск потокового режима для датчика {sensor_address}")
            print("Нажмите Ctrl+C для остановки\n")
            if sensor_address in [1, 2]:
                sensors.ultra_fast_stream_measurement(sensor_address, SENSOR1_RANGE, duration=30.0)
            else:
                sensors.ultra_fast_stream_measurement(sensor_address, SENSOR3_RANGE, duration=30.0)
            
        elif choice == "2":
            # QUAD режим - четыре датчика синхронно
            print("🆕 QUAD РЕЖИМ - ЧЕТЫРЕ ДАТЧИКА СИНХРОННО")
            print("Broadcast синхронизация для 4 датчиков - ожидаемая частота ~125 Гц")
            print("Адреса датчиков: 1, 2, 3, 4")
            print("Нажмите Ctrl+C для остановки\n")
            
            display_counter = 0
            last_display_time = time.time()
            measurements_for_freq = deque(maxlen=50)
            
            try:
                while True:
                    cycle_start = time.time()
                    
                    # Quad измерение с 4 датчиков
                    sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = sensors.perform_quad_sensor_measurement(
                        SENSOR1_RANGE, SENSOR2_RANGE, SENSOR3_RANGE, SENSOR4_RANGE
                    )
                    
                    measurements_for_freq.append(cycle_start)
                    display_counter += 1
                    current_time = time.time()
                    
                    # Обновление дисплея каждые 0.5 секунды
                    if display_counter >= 100 or (current_time - last_display_time) >= 0.5:
                        
                        # Мгновенная частота
                        if len(measurements_for_freq) >= 2:
                            time_span = measurements_for_freq[-1] - measurements_for_freq[0]
                            if time_span > 0:
                                instant_freq = (len(measurements_for_freq) - 1) / time_span
                            else:
                                instant_freq = 0
                        else:
                            instant_freq = 0
                        
                        # Статистика
                        total_measurements = sensors.measurement_count
                        total_errors = sensors.error_count
                        success_rate = ((total_measurements - total_errors) / total_measurements * 100) if total_measurements > 0 else 0
                        
                        print(f"Измерений: {total_measurements:5d} | Частота: {instant_freq:6.1f} Гц | Успех: {success_rate:5.1f}%")
                        
                        # Диагностика времени этапов для 4 датчиков
                        print(f"Время этапов (мс): BC:{sensors.time_broadcast*1000:.2f} | "
                              f"Sync:{sensors.time_sync_delay*1000:.2f}")
                        print(f"  Датчики: S1:{sensors.time_sensor1*1000:.2f} | S2:{sensors.time_sensor2*1000:.2f} | "
                              f"S3:{sensors.time_sensor3*1000:.2f} | S4:{sensors.time_sensor4*1000:.2f}")
                        
                        if all(v is not None for v in [sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm]):
                            # Расчет дельт между парами датчиков (1-2 и 3-4)
                            delta_1_2 = abs(sensor1_mm - sensor2_mm)
                            delta_3_4 = abs(sensor3_mm - sensor4_mm)
                            
                            print(f"Пара 1-2: Д1:{sensor1_mm:6.3f}мм | Д2:{sensor2_mm:6.3f}мм | Δ1-2:{delta_1_2:6.3f}мм")
                            print(f"Пара 3-4: Д3:{sensor3_mm:6.3f}мм | Д4:{sensor4_mm:6.3f}мм | Δ3-4:{delta_3_4:6.3f}мм")
                        else:
                            print("ОШИБКА ЧТЕНИЯ ОДНОГО ИЛИ БОЛЕЕ ДАТЧИКОВ")
                        
                        print()
                        
                        display_counter = 0
                        last_display_time = current_time
                    
            except KeyboardInterrupt:
                print("\n\nОстановка QUAD режима по запросу пользователя")
            
    except KeyboardInterrupt:
        print("\n\nОстановка по запросу пользователя")
    except Exception as e:
        print(f"\nОшибка в основном цикле: {e}")
    finally:
        try:
            sensors.disconnect()
        except:
            pass  # Игнорируем любые ошибки при отключении
        
        # Сохранение данных в CSV файл
        if sensors.measurement_count > 50:
            try:
                filename = f"high_speed_measurements_{int(time.time())}.csv"
                with open(filename, 'w') as f:
                    f.write("timestamp,measurement_count,error_count\n")
                    f.write(f"{time.time()},{sensors.measurement_count},{sensors.error_count}\n")
                print(f"Краткая статистика сохранена в файл: {filename}")
            except Exception as e:
                print(f"Ошибка сохранения файла: {e}")


def print_optimization_guide():
    """
    Печать подробного руководства по оптимизации для достижения максимальной частоты
    """
    print("""
""")


def cleanup_system_optimizations():
    """
    Очистка системных оптимизаций при завершении программы
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


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n🛑 Программа остановлена пользователем")
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
    finally:
        cleanup_system_optimizations()
        print("\n" + "="*70)
        print("ПРОГРАММА ЗАВЕРШЕНА")
        print("="*70)
    
    print_optimization_guide()
