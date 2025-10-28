#!/usr/bin/env python3
"""
🔧 MODBUS SLAVE SERVER - Простой сервер с регистрами

Реализует Modbus TCP сервер с регистрами для HMI панели:
- Holding Registers (40001-40014) - команды и настройки
- Input Registers (30001-30012) - результаты измерений
"""

import time
import threading
import logging
import struct
try:
    # Для pymodbus 3.5.4
    from pymodbus.server import StartTcpServer
    from pymodbus.device import ModbusDeviceIdentification
    from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext, ModbusServerContext
    print("OK Используется pymodbus 3.x")
except ImportError:
    try:
        # Для pymodbus 2.x (fallback)
        from pymodbus.server.sync import StartTcpServer
        from pymodbus.device import ModbusDeviceIdentification
        from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext, ModbusServerContext
        print("OK Используется pymodbus 2.x")
    except ImportError:
        print("ERROR Ошибка импорта pymodbus. Установите: pip install pymodbus")
        exit(1)

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

def float_to_doubleword(value):
    """Конвертация float в два 16-битных регистра (DoubleWord)"""
    # Упаковываем float в 4 байта, затем распаковываем как два uint16
    packed = struct.pack('>f', value)  # Big-endian float
    high_word, low_word = struct.unpack('>HH', packed)
    return low_word, high_word  # Младшее слово первое

def doubleword_to_float(low_word, high_word):
    """Конвертация двух 16-битных регистров в float"""
    # Упаковываем два uint16 в 4 байта, затем распаковываем как float
    packed = struct.pack('>HH', high_word, low_word)
    return struct.unpack('>f', packed)[0]

class ModbusSlaveServer:
    """Простой Modbus Slave сервер с регистрами"""
    
    def __init__(self, enable_gui=True):
        # Настройки сервера
        self.modbus_port = 502
        self.slave_id = 1
        self.enable_gui = enable_gui
        
        # Modbus сервер
        self.modbus_server = None
        self.server_thread = None
        self.is_server_running = False
        
        # Инициализация регистров
        self.init_registers()
        
        # GUI (только если включен)
        if self.enable_gui:
            self.setup_gui()
            # Запуск мониторинга регистров
            self.monitor_registers()
        
    def init_registers(self):
        """Инициализация Modbus регистров"""
        
        # === HOLDING REGISTERS - Команды и настройки ===
        self.holding_registers = {
            # 40001: CMD команда от ПЛК
            0: 0,    # CMD команда (0=стоп, 10=стенка, 11=фланец, 12=дно)
            
            # 40002, 40003: Эталонная толщина стенки (DoubleWord float)
            1: 0.0,  # Эталонная толщина стенки (мм) - младшее слово
            2: 0.0,  # Эталонная толщина стенки (мм) - старшее слово
            
            # 40004, 40005: Эталонная толщина дна (DoubleWord float)  
            3: 0.0,  # Эталонная толщина дна (мм) - младшее слово
            4: 0.0,  # Эталонная толщина дна (мм) - старшее слово
            
            # 40006, 40007: Эталонный диаметр (DoubleWord float)
            5: 0.0,  # Эталонный диаметр (мм) - младшее слово
            6: 0.0,  # Эталонный диаметр (мм) - старшее слово
            
            # 40008, 40009: Эталонная высота (DoubleWord float)
            7: 0.0,  # Эталонная высота (мм) - младшее слово
            8: 0.0,  # Эталонная высота (мм) - старшее слово
            
            # 40010, 40011: Расстояние между датчиками 1,2 (DoubleWord float)
            9: 50.0,   # Расстояние между датчиками 1,2 (мм) - младшее слово
            10: 0.0,   # Расстояние между датчиками 1,2 (мм) - старшее слово
            
            # 40012, 40013: Расстояние между датчиками 1,3 (DoubleWord float)
            11: 50.0,  # Расстояние между датчиками 1,3 (мм) - младшее слово
            12: 0.0,   # Расстояние между датчиками 1,3 (мм) - старшее слово
            
            # 40014, 40015: Расстояние между датчиком 4 и поверхностью (DoubleWord float)
            13: 50.0,  # Расстояние между датчик 4 и поверхностью (мм) - младшее слово
            14: 0.0,   # Расстояние между датчик 4 и поверхностью (мм) - старшее слово
            
            # 40016, 40017: Расстояние между датчиком 1 и центром пересечения (DoubleWord float)
            15: 50.0,  # Расстояние между датчиком 1 и центром (мм) - младшее слово
            16: 0.0,   # Расстояние между датчиком 1 и центром (мм) - старшее слово
            
            # 40018, 40019: Расстояние между датчиком 2 и центром пересечения (DoubleWord float)
            17: 50.0,  # Расстояние между датчиком 2 и центром (мм) - младшее слово
            18: 0.0,   # Расстояние между датчиком 2 и центром (мм) - старшее слово
            
            # === РЕГИСТРЫ ДЛЯ ИЗМЕРЕНИЯ ВЫСОТЫ ===
            # 40052, 40053: Количество шагов от домашней позиции (DoubleWord integer)
            51: 0,     # Количество шагов (младшее слово)
            52: 0,     # Количество шагов (старшее слово)
            
            # 40054: Импульсов на 1 мм
            53: 1000,  # Импульсов на 1 мм (16-bit integer)
            
            # 40055, 40056: Дистанция в мм до начала плоскости (DoubleWord float)
            54: 0.0,   # Дистанция в мм до начала плоскости - младшее слово
            55: 0.0,   # Дистанция в мм до начала плоскости - старшее слово
            
            # 40057, 40058: Измеренная высота заготовки (DoubleWord float)
            56: 0.0,   # Измеренная высота заготовки - младшее слово
            57: 0.0,   # Измеренная высота заготовки - старшее слово
            
            # 40024: Сброс ошибок (1 = сбросить флаг состояния в 0)
            23: 0,     # Сброс ошибок (0=нет, 1=сбросить)
            
            # 40100: Смена (вводится на HMI)
            99: 1,     # Смена (номер смены)
            
            # 40101: Номер изделия (автоинкремент при переходе 12→0)
            100: 0,    # Номер изделия (автоматический подсчёт)
            
            # 40049: Режим проверки качества (0=все, 1=среднее, 2=макс+сред, 3=мин+сред)
            48: 0,     # Режим проверки (по умолчанию - проверять все)
            
            # 40050: Допустимое количество условно-годных ошибок
            49: 0,     # Допустимое количество условно-годных ошибок
            
            # 40051: Допустимое количество негодных ошибок
            50: 0,     # Допустимое количество негодных ошибок

            #40057,40058: 
            56: 0.0,  # измеренная высота заготовки (условно-негодная погрешность) - младшее слово
            57: 0.0,  # измеренная высота заготовки (условно-негодная погрешность) - старшее слово
            # === РАСШИРЕННЫЕ HOLDING РЕГИСТРЫ 40352-40403 ===
            # Пороговые значения и настройки для контроля качества
            
            # 40352, 40353: Верхняя стенка (толщина стенки) (DoubleWord float)
            351: 0.0,  # Верхняя стенка (толщина стенки) - младшее слово
            352: 0.0,  # Верхняя стенка (толщина стенки) - старшее слово
            
            # 40354, 40355: Верхняя стенка (условно-негодная погрешность) (DoubleWord float)
            353: 0.0,  # Верхняя стенка (условно-негодная погрешность) - младшее слово
            354: 0.0,  # Верхняя стенка (условно-негодная погрешность) - старшее слово
            
            # 40356, 40357: Верхняя стенка (негодная погрешность) (DoubleWord float)
            355: 0.0,  # Верхняя стенка (негодная погрешность) - младшее слово
            356: 0.0,  # Верхняя стенка (негодная погрешность) - старшее слово
            
            # 40358, 40359: Нижняя стенка (толщина стенки) (DoubleWord float)
            357: 0.0,  # Нижняя стенка (толщина стенки) - младшее слово
            358: 0.0,  # Нижняя стенка (толщина стенки) - старшее слово
            
            # 40360, 40361: Нижняя стенка (условно-негодная погрешность) (DoubleWord float)
            359: 0.0,  # Нижняя стенка (условно-негодная погрешность) - младшее слово
            360: 0.0,  # Нижняя стенка (условно-негодная погрешность) - старшее слово
            
            # 40362, 40363: Нижняя стенка (негодная погрешность) (DoubleWord float)
            361: 0.0,  # Нижняя стенка (негодная погрешность) - младшее слово
            362: 0.0,  # Нижняя стенка (негодная погрешность) - старшее слово
            
            # 40364, 40365: Дно (толщина стенки) (DoubleWord float)
            363: 0.0,  # Дно (толщина стенки) - младшее слово
            364: 0.0,  # Дно (толщина стенки) - старшее слово
            
            # 40366, 40367: Дно (условно-негодная погрешность) (DoubleWord float)
            365: 0.0,  # Дно (условно-негодная погрешность) - младшее слово
            366: 0.0,  # Дно (условно-негодная погрешность) - старшее слово
            
            # 40368, 40369: Дно (негодная погрешность) (DoubleWord float)
            367: 0.0,  # Дно (негодная погрешность) - младшее слово
            368: 0.0,  # Дно (негодная погрешность) - старшее слово
            
            # 40370, 40371: Фланец (толщина стенки) (DoubleWord float)
            369: 0.0,  # Фланец (толщина стенки) - младшее слово
            370: 0.0,  # Фланец (толщина стенки) - старшее слово
            
            # 40372, 40373: Фланец (условно-негодная погрешность) (DoubleWord float)
            371: 0.0,  # Фланец (условно-негодная погрешность) - младшее слово
            372: 0.0,  # Фланец (условно-негодная погрешность) - старшее слово
            
            # 40374, 40375: Фланец (негодная погрешность) (DoubleWord float)
            373: 0.0,  # Фланец (негодная погрешность) - младшее слово
            374: 0.0,  # Фланец (негодная погрешность) - старшее слово
            
            # 40376, 40377: Высота (базовая) (DoubleWord float)
            375: 0.0,  # Высота (базовая) - младшее слово
            376: 0.0,  # Высота (базовая) - старшее слово
            
            # 40378, 40379: Высота (условно-негодная погрешность) (DoubleWord float)
            377: 0.0,  # Высота (условно-негодная погрешность) - младшее слово
            378: 0.0,  # Высота (условно-негодная погрешность) - старшее слово
            
            # 40380, 40381: Высота (негодная погрешность) (DoubleWord float)
            379: 0.0,  # Высота (негодная погрешность) - младшее слово
            380: 0.0,  # Высота (негодная погрешность) - старшее слово
            
            # 40382, 40383: Диаметр корпуса (базовое) (DoubleWord float)
            381: 0.0,  # Диаметр корпуса (базовое) - младшее слово
            382: 0.0,  # Диаметр корпуса (базовое) - старшее слово
            
            # 40384, 40385: Диаметр корпуса (условно-негодная погрешность) (DoubleWord float)
            383: 0.0,  # Диаметр корпуса (условно-негодная погрешность) - младшее слово
            384: 0.0,  # Диаметр корпуса (условно-негодная погрешность) - старшее слово
            
            # 40386, 40387: Диаметр корпуса (негодная погрешность) (DoubleWord float)
            385: 0.0,  # Диаметр корпуса (негодная погрешность) - младшее слово
            386: 0.0,  # Диаметр корпуса (негодная погрешность) - старшее слово
            
            # 40388, 40389: Диаметр фланца (базовое) (DoubleWord float)
            387: 0.0,  # Диаметр фланца (базовое) - младшее слово
            388: 0.0,  # Диаметр фланца (базовое) - старшее слово
            
            # 40390, 40391: Диаметр фланца (условно-негодная погрешность) (DoubleWord float)
            389: 0.0,  # Диаметр фланца (условно-негодная погрешность) - младшее слово
            390: 0.0,  # Диаметр фланца (условно-негодная погрешность) - старшее слово
            
            # 40392, 40393: Диаметр фланца (негодная погрешность) (DoubleWord float)
            391: 0.0,  # Диаметр фланца (негодная погрешность) - младшее слово
            392: 0.0,  # Диаметр фланца (негодная погрешность) - старшее слово
            
            # 40394, 40395: Расстояние от края (DoubleWord float)
            393: 0.0,  # Расстояние от края - младшее слово
            394: 0.0,  # Расстояние от края - старшее слово
            
            # 40396, 40397: Расстояние от дна (DoubleWord float)
            395: 0.0,  # Расстояние от дна - младшее слово
            396: 0.0,  # Расстояние от дна - старшее слово
            
            # 40398, 40399: Расстояние от фланца (DoubleWord float)
            397: 0.0,  # Расстояние от фланца - младшее слово
            398: 0.0,  # Расстояние от фланца - старшее слово
            
            # 40400, 40401: Толщина дна (положительная негодная погрешность) (DoubleWord float)
            399: 0.0,  # Толщина дна (положительная негодная погрешность) - младшее слово
            400: 0.0,  # Толщина дна (положительная негодная погрешность) - старшее слово
            
            # 40402, 40403: Толщина стенки (положительная негодная погрешность) (DoubleWord float)
            401: 0.0,  # Толщина стенки (положительная негодная погрешность) - младшее слово
            402: 0.0,  # Толщина стенки (положительная негодная погрешность) - старшее слово
        }
        
        # === INPUT REGISTERS - Результаты измерений ===
        self.input_registers = {
            # 30001, 30002: Текущее расстояние до поверхности Датчик 1 (DoubleWord float)
            0: 0.0,  # Датчик 1 - расстояние до поверхности (мм) - младшее слово
            1: 0.0,  # Датчик 1 - расстояние до поверхности (мм) - старшее слово
            
            # 30003, 30004: Текущее расстояние до поверхности Датчик 2 (DoubleWord float)
            2: 0.0,  # Датчик 2 - расстояние до поверхности (мм) - младшее слово
            3: 0.0,  # Датчик 2 - расстояние до поверхности (мм) - старшее слово
            
            # 30005, 30006: Текущее расстояние до поверхности Датчик 3 (DoubleWord float)
            4: 0.0,  # Датчик 3 - расстояние до поверхности (мм) - младшее слово
            5: 0.0,  # Датчик 3 - расстояние до поверхности (мм) - старшее слово
            
            # 30007, 30008: Текущее расстояние до поверхности Датчик 4 (DoubleWord float)
            6: 0.0,  # Датчик 4 - расстояние до поверхности (мм) - младшее слово
            7: 0.0,  # Датчик 4 - расстояние до поверхности (мм) - старшее слово
            
            # 30009: Флаг цикла измерения (статус)
            8: 0,    # Флаг цикла (0=готов, 9/90/91=высота, 10/11/110=стенка, 12/13/112=фланец, 14/15/114=дно, 16/116=качество)
            
            # 30010, 30011: Последняя рассчитанная толщина стенки (DoubleWord float)
            9: 0.0,   # Последняя рассчитанная толщина стенки (мм) - младшее слово
            10: 0.0,  # Последняя рассчитанная толщина стенки (мм) - старшее слово
            
            # 30012, 30013: Последняя рассчитанная толщина фланца (DoubleWord float)
            11: 0.0,  # Последняя рассчитанная толщина фланца (мм) - младшее слово
            12: 0.0,  # Последняя рассчитанная толщина фланца (мм) - старшее слово
            
            # 30014, 30015: Последняя рассчитанная толщина дна (DoubleWord float)
            13: 0.0,  # Последняя рассчитанная толщина дна (мм) - младшее слово
            14: 0.0,  # Последняя рассчитанная толщина дна (мм) - старшее слово
            
            # === РЕЗУЛЬТАТЫ ИЗМЕРЕНИЙ (MAX, AVG, MIN) ===
            
            # 30016, 30017: Максимальная толщина верхней стенки (DoubleWord float)
            15: 0.0,  # Максимальная толщина верхней стенки - младшее слово
            16: 0.0,  # Максимальная толщина верхней стенки - старшее слово
            
            # 30018, 30019: Средняя толщина верхней стенки (DoubleWord float)
            17: 0.0,  # Средняя толщина верхней стенки - младшее слово
            18: 0.0,  # Средняя толщина верхней стенки - старшее слово
            
            # 30020, 30021: Минимальная толщина верхней стенки (DoubleWord float)
            19: 0.0,  # Минимальная толщина верхней стенки - младшее слово
            20: 0.0,  # Минимальная толщина верхней стенки - старшее слово
            
            # 30022, 30023: Максимальная толщина нижней стенки (DoubleWord float)
            21: 0.0,  # Максимальная толщина нижней стенки - младшее слово
            22: 0.0,  # Максимальная толщина нижней стенки - старшее слово
            
            # 30024, 30025: Средняя толщина нижней стенки (DoubleWord float)
            23: 0.0,  # Средняя толщина нижней стенки - младшее слово
            24: 0.0,  # Средняя толщина нижней стенки - старшее слово
            
            # 30026, 30027: Минимальная толщина нижней стенки (DoubleWord float)
            25: 0.0,  # Минимальная толщина нижней стенки - младшее слово
            26: 0.0,  # Минимальная толщина нижней стенки - старшее слово
            
            # 30028, 30029: Максимальная толщина дна (DoubleWord float)
            27: 0.0,  # Максимальная толщина дна - младшее слово
            28: 0.0,  # Максимальная толщина дна - старшее слово
            
            # 30030, 30031: Средняя толщина дна (DoubleWord float)
            29: 0.0,  # Средняя толщина дна - младшее слово
            30: 0.0,  # Средняя толщина дна - старшее слово
            
            # 30032, 30033: Минимальная толщина дна (DoubleWord float)
            31: 0.0,  # Минимальная толщина дна - младшее слово
            32: 0.0,  # Минимальная толщина дна - старшее слово
            
            # 30034, 30035: Максимальная толщина фланца (DoubleWord float)
            33: 0.0,  # Максимальная толщина фланца - младшее слово
            34: 0.0,  # Максимальная толщина фланца - старшее слово
            
            # 30036, 30037: Средняя толщина фланца (DoubleWord float)
            35: 0.0,  # Средняя толщина фланца - младшее слово
            36: 0.0,  # Средняя толщина фланца - старшее слово
            
            # 30038, 30039: Минимальная толщина фланца (DoubleWord float)
            37: 0.0,  # Минимальная толщина фланца - младшее слово
            38: 0.0,  # Минимальная толщина фланца - старшее слово
            
            # 30040, 30041: Максимальная высота (DoubleWord float)
            39: 0.0,  # Максимальная высота - младшее слово
            40: 0.0,  # Максимальная высота - старшее слово
            
            # 30042, 30043: Средняя высота (DoubleWord float)
            41: 0.0,  # Средняя высота - младшее слово
            42: 0.0,  # Средняя высота - старшее слово
            
            # 30044, 30045: Минимальная высота (DoubleWord float)
            43: 0.0,  # Минимальная высота - младшее слово
            44: 0.0,  # Минимальная высота - старшее слово
            
            # 30046, 30047: Максимальный диаметр корпуса (DoubleWord float)
            45: 0.0,  # Максимальный диаметр корпуса - младшее слово
            46: 0.0,  # Максимальный диаметр корпуса - старшее слово
            
            # 30048, 30049: Средний диаметр корпуса (DoubleWord float)
            47: 0.0,  # Средний диаметр корпуса - младшее слово
            48: 0.0,  # Средний диаметр корпуса - старшее слово
            
            # 30050, 30051: Минимальный диаметр корпуса (DoubleWord float)
            49: 0.0,  # Минимальный диаметр корпуса - младшее слово
            50: 0.0,  # Минимальный диаметр корпуса - старшее слово
            
            # 30052, 30053: Максимальный диаметр фланца (DoubleWord float)
            51: 0.0,  # Максимальный диаметр фланца - младшее слово
            52: 0.0,  # Максимальный диаметр фланца - старшее слово
            
            # 30054, 30055: Средний диаметр фланца (DoubleWord float)
            53: 0.0,  # Средний диаметр фланца - младшее слово
            54: 0.0,  # Средний диаметр фланца - старшее слово
            
            # 30056, 30057: Минимальный диаметр фланца (DoubleWord float)
            55: 0.0,  # Минимальный диаметр фланца - младшее слово
            56: 0.0,  # Минимальный диаметр фланца - старшее слово
            
            # === СЧЁТЧИКИ ИЗДЕЛИЙ ЗА СМЕНУ ===
            # 30101: Количество изделий за смену
            100: 0,   # Общее количество изделий
            
            # 30102: Количество годных изделий за смену
            101: 0,   # Годные изделия
            
            # 30103: Количество условно-негодных изделий за смену
            102: 0,   # Условно-негодные изделия
            
            # 30104: Количество негодных изделий за смену
            103: 0,   # Негодные изделия
        }
        
        # Создание Modbus datastore
        self.create_modbus_context()
        
    def create_modbus_context(self):
        """Создание контекста Modbus с регистрами"""
        
        # Holding Registers (функции 3, 6, 16) - 40001-40500 (расширенный диапазон)
        holding_block = ModbusSequentialDataBlock(1, [0] * 500)
        
        # Input Registers (функция 4) - 30001-30500 (расширенный диапазон)
        input_block = ModbusSequentialDataBlock(1, [0] * 500)
        
        # Заполняем начальными значениями
        for addr, value in self.holding_registers.items():
            if isinstance(value, float):
                # Конвертируем float в целое число для Modbus (умножаем на 1000)
                int_value = int(value * 1000)
                holding_block.setValues(addr + 1, [int_value])
            else:
                holding_block.setValues(addr + 1, [int(value)])
            
        for addr, value in self.input_registers.items():
            if isinstance(value, float):
                # Конвертируем float в целое число для Modbus (умножаем на 1000)
                int_value = int(value * 1000)
                input_block.setValues(addr + 1, [int_value])
            else:
                input_block.setValues(addr + 1, [int(value)])
        
        # Создание контекста slave
        self.slave_context = ModbusSlaveContext(
            di=None,  # Discrete Inputs
            co=None,  # Coils
            hr=holding_block,  # Holding Registers
            ir=input_block     # Input Registers
        )
        
        # Контекст сервера
        self.server_context = ModbusServerContext(slaves={self.slave_id: self.slave_context}, single=False)
        
    def setup_gui(self):
        """Создание GUI интерфейса"""
        self.root = tk.Tk()
        self.root.title("MODBUS SLAVE SERVER - Регистры для HMI")
        self.root.geometry("1000x700")
        self.root.resizable(True, True)
        
        # Главный фрейм
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Заголовок
        title_label = ttk.Label(main_frame, text="MODBUS SLAVE SERVER", 
                               font=("Arial", 16, "bold"))
        title_label.pack(pady=10)
        
        # Настройки сервера
        self.setup_server_frame(main_frame)
        
        # Регистры
        self.setup_registers_frame(main_frame)
        
        # Управление
        self.setup_control_frame(main_frame)
        
        # Лог
        self.setup_log_frame(main_frame)
        
        # Обработка закрытия
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
    def setup_server_frame(self, parent):
        """Настройки сервера"""
        server_frame = ttk.LabelFrame(parent, text="Настройки Modbus TCP сервера", padding=10)
        server_frame.pack(fill=tk.X, pady=5)
        
        settings_row = ttk.Frame(server_frame)
        settings_row.pack(fill=tk.X, pady=5)
        
        ttk.Label(settings_row, text="TCP порт:").pack(side=tk.LEFT)
        self.port_var = tk.StringVar(value=str(self.modbus_port))
        ttk.Entry(settings_row, textvariable=self.port_var, width=8).pack(side=tk.LEFT, padx=5)
        
        ttk.Label(settings_row, text="Slave ID:").pack(side=tk.LEFT, padx=(20,0))
        self.slave_var = tk.StringVar(value=str(self.slave_id))
        ttk.Entry(settings_row, textvariable=self.slave_var, width=8).pack(side=tk.LEFT, padx=5)
        
        # Статус сервера
        self.server_status = ttk.Label(settings_row, text="[STOP] Сервер остановлен", 
                                      foreground="red", font=("Arial", 10, "bold"))
        self.server_status.pack(side=tk.RIGHT, padx=10)
        
    def setup_registers_frame(self, parent):
        """Отображение регистров"""
        reg_frame = ttk.LabelFrame(parent, text="Modbus регистры", padding=10)
        reg_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Notebook для разделения типов регистров
        notebook = ttk.Notebook(reg_frame)
        notebook.pack(fill=tk.BOTH, expand=True)
        
        # Holding Registers
        holding_frame = ttk.Frame(notebook)
        notebook.add(holding_frame, text="Holding Registers (40001+)")
        
        # Input Registers
        input_frame = ttk.Frame(notebook)
        notebook.add(input_frame, text="Input Registers (30001+)")
        
        # Создание таблиц регистров
        self.create_registers_table(holding_frame, "holding")
        self.create_registers_table(input_frame, "input")
        
    def create_registers_table(self, parent, reg_type):
        """Создание таблицы регистров"""
        
        # Фрейм для таблицы
        table_frame = ttk.Frame(parent)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Treeview для таблицы
        columns = ("Регистр", "Название", "Значение", "Единицы", "Описание")
        tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=12)
        
        # Настройка колонок
        tree.heading("Регистр", text="Регистр")
        tree.heading("Название", text="Название")
        tree.heading("Значение", text="Значение")
        tree.heading("Единицы", text="Единицы")
        tree.heading("Описание", text="Описание")
        
        tree.column("Регистр", width=80)
        tree.column("Название", width=200)
        tree.column("Значение", width=100)
        tree.column("Единицы", width=80)
        tree.column("Описание", width=300)
        
        # Скроллбар
        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Заполнение данными
        if reg_type == "holding":
            self.holding_tree = tree
            self.populate_holding_registers()
        else:
            self.input_tree = tree
            self.populate_input_registers()
            
    def populate_holding_registers(self):
        """Заполнение Holding Registers"""
        registers_data = [
            ("40001", "CMD команда от ПЛК", "0", "", "0=стоп, 10=стенка, 11=фланец, 12=дно"),
            ("40002", "Эталонная толщина стенки", "0.0", "мм", "DoubleWord float"),
            ("40003", "Эталонная толщина стенки", "0.0", "мм", "DoubleWord float (старшее слово)"),
            ("40004", "Эталонная толщина дна", "0.0", "мм", "DoubleWord float"),
            ("40005", "Эталонная толщина дна", "0.0", "мм", "DoubleWord float (старшее слово)"),
            ("40006", "Эталонный диаметр", "0.0", "мм", "DoubleWord float"),
            ("40007", "Эталонный диаметр", "0.0", "мм", "DoubleWord float (старшее слово)"),
            ("40008", "Эталонная высота", "0.0", "мм", "DoubleWord float"),
            ("40009", "Эталонная высота", "0.0", "мм", "DoubleWord float (старшее слово)"),
            ("40010", "Расстояние между датчиками 1,2", "50.0", "мм", "DoubleWord float"),
            ("40011", "Расстояние между датчиками 1,2", "0.0", "мм", "DoubleWord float (старшее слово)"),
            ("40012", "Расстояние между датчиками 1,3", "50.0", "мм", "DoubleWord float"),
            ("40013", "Расстояние между датчиками 1,3", "0.0", "мм", "DoubleWord float (старшее слово)"),
            ("40014", "Расстояние датчик 4 - поверхность", "50.0", "мм", "DoubleWord float"),
            ("40015", "Расстояние датчик 4 - поверхность", "0.0", "мм", "DoubleWord float (старшее слово)"),
            ("40016", "Расстояние датчик 1 - центр", "50.0", "мм", "DoubleWord float"),
            ("40017", "Расстояние датчик 1 - центр", "0.0", "мм", "DoubleWord float (старшее слово)"),
            ("40018", "Расстояние датчик 2 - центр", "50.0", "мм", "DoubleWord float"),
            ("40019", "Расстояние датчик 2 - центр", "0.0", "мм", "DoubleWord float (старшее слово)"),
        ]
        
        for reg_data in registers_data:
            self.holding_tree.insert("", tk.END, values=reg_data)
            
    def populate_input_registers(self):
        """Заполнение Input Registers"""
        registers_data = [
            ("30001", "Текущее расстояние датчик 1", "0.0", "мм", "DoubleWord float"),
            ("30002", "Текущее расстояние датчик 1", "0.0", "мм", "DoubleWord float (старшее слово)"),
            ("30003", "Текущее расстояние датчик 2", "0.0", "мм", "DoubleWord float"),
            ("30004", "Текущее расстояние датчик 2", "0.0", "мм", "DoubleWord float (старшее слово)"),
            ("30005", "Текущее расстояние датчик 3", "0.0", "мм", "DoubleWord float"),
            ("30006", "Текущее расстояние датчик 3", "0.0", "мм", "DoubleWord float (старшее слово)"),
            ("30007", "Текущее расстояние датчик 4", "0.0", "мм", "DoubleWord float"),
            ("30008", "Текущее расстояние датчик 4", "0.0", "мм", "DoubleWord float (старшее слово)"),
            ("30010", "Последняя толщина стенки", "0.0", "мм", "DoubleWord float"),
            ("30011", "Последняя толщина стенки", "0.0", "мм", "DoubleWord float (старшее слово)"),
            ("30012", "Последняя толщина фланца", "0.0", "мм", "DoubleWord float"),
            ("30013", "Последняя толщина фланца", "0.0", "мм", "DoubleWord float (старшее слово)"),
            ("30014", "Последняя толщина дна", "0.0", "мм", "DoubleWord float"),
            ("30015", "Последняя толщина дна", "0.0", "мм", "DoubleWord float (старшее слово)"),
        ]
        
        for reg_data in registers_data:
            self.input_tree.insert("", tk.END, values=reg_data)
            
    def setup_control_frame(self, parent):
        """Кнопки управления"""
        control_frame = ttk.Frame(parent)
        control_frame.pack(fill=tk.X, pady=10)
        
        # Кнопки сервера
        ttk.Button(control_frame, text="START Запустить Modbus сервер", 
                  command=self.start_modbus_server).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="STOP Остановить сервер", 
                  command=self.stop_modbus_server).pack(side=tk.LEFT, padx=5)
        
        # Тестовые кнопки
        ttk.Button(control_frame, text="TEST Установить тестовые данные", 
                  command=self.set_test_data).pack(side=tk.LEFT, padx=20)
        
        # Кнопка выхода
        ttk.Button(control_frame, text="EXIT Выход", 
                  command=self.on_closing).pack(side=tk.RIGHT, padx=5)
        
    def setup_log_frame(self, parent):
        """Лог сообщений"""
        log_frame = ttk.LabelFrame(parent, text="Лог событий", padding=5)
        log_frame.pack(fill=tk.X, pady=5)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, width=80)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
    def log_message(self, message):
        """Добавление сообщения в лог"""
        timestamp = time.strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        
        if self.enable_gui and hasattr(self, 'log_text'):
            self.log_text.insert(tk.END, log_entry + "\n")
            self.log_text.see(tk.END)
            
            # Ограничиваем размер лога
            lines = self.log_text.get("1.0", tk.END).split('\n')
            if len(lines) > 100:
                self.log_text.delete("1.0", "10.0")
        else:
            # Выводим в консоль если GUI отключен
            print(log_entry)
            
    def start_modbus_server(self):
        """Запуск Modbus TCP сервера"""
        try:
            if self.enable_gui:
                self.modbus_port = int(self.port_var.get())
                self.slave_id = int(self.slave_var.get())
            # Если GUI отключен, используем значения по умолчанию
            
            # Пересоздаем контекст с новым slave_id
            self.create_modbus_context()
            
            # Создание identity
            identity = ModbusDeviceIdentification()
            identity.VendorName = 'RIFTEK'
            identity.ProductCode = 'RF602'
            identity.VendorUrl = 'https://riftek.com'
            identity.ProductName = 'Modbus Slave Server'
            identity.ModelName = 'RF602 Registers'
            identity.MajorMinorRevision = '1.0'
            
            # Запуск сервера в отдельном потоке
            self.server_thread = threading.Thread(
                target=self.run_modbus_server,
                args=(self.modbus_port, identity),
                daemon=True
            )
            self.server_thread.start()
            
            self.is_server_running = True
            
            if self.enable_gui and hasattr(self, 'server_status'):
                self.server_status.config(text=f"[RUN] Сервер работает :{self.modbus_port}", foreground="green")
            
            self.log_message(f"Modbus TCP сервер запущен на порту {self.modbus_port}, Slave ID: {self.slave_id}")
            
        except Exception as e:
            self.log_message(f"Ошибка запуска сервера: {e}")
            if self.enable_gui:
                messagebox.showerror("Ошибка", f"Ошибка запуска сервера: {e}")
            
    def run_modbus_server(self, port, identity):
        """Запуск Modbus сервера"""
        try:
            self.log_message(f"Запуск TCP сервера на 0.0.0.0:{port}")
            # Для pymodbus 3.5.4 - упрощенный синтаксис
            StartTcpServer(
                context=self.server_context, 
                identity=identity, 
                address=("0.0.0.0", port)
            )
        except Exception as e:
            self.log_message(f"Ошибка работы сервера: {e}")
            print(f"Детальная ошибка: {e}")  # Для отладки
            
    def stop_modbus_server(self):
        """Остановка Modbus сервера"""
        self.is_server_running = False
        
        if self.enable_gui and hasattr(self, 'server_status'):
            self.server_status.config(text="[STOP] Сервер остановлен", foreground="red")
        
        self.log_message("Modbus сервер остановлен")
        
    def set_test_data(self):
        """Установка тестовых данных в регистры"""
        try:
            # Тестовые данные для Input Registers (имитация показаний датчиков)
            test_input_data = {
                0: 12.5,   # 30001: Датчик 1 = 12.5 мм (младшее слово)
                1: 0.0,    # 30002: Датчик 1 = 12.5 мм (старшее слово)
                2: 8.3,    # 30003: Датчик 2 = 8.3 мм (младшее слово)
                3: 0.0,    # 30004: Датчик 2 = 8.3 мм (старшее слово)
                4: 15.2,   # 30005: Датчик 3 = 15.2 мм (младшее слово)
                5: 0.0,    # 30006: Датчик 3 = 15.2 мм (старшее слово)
                6: 22.1,   # 30007: Датчик 4 = 22.1 мм (младшее слово)
                7: 0.0,    # 30008: Датчик 4 = 22.1 мм (старшее слово)
                9: 29.2,   # 30010: Толщина стенки = 29.2 мм (младшее слово)
                10: 0.0,   # 30011: Толщина стенки = 29.2 мм (старшее слово)
                11: 35.5,  # 30012: Толщина фланца = 35.5 мм (младшее слово)
                12: 0.0,   # 30013: Толщина фланца = 35.5 мм (старшее слово)
                13: 18.7,  # 30014: Толщина дна = 18.7 мм (младшее слово)
                14: 0.0,   # 30015: Толщина дна = 18.7 мм (старшее слово)
            }
            
            # Тестовые данные для Holding Registers
            test_holding_data = {
                0: 10,     # 40001: CMD = 10 (измерение стенки)
                1: 30.0,   # 40002: Эталон стенки = 30.0 мм (младшее слово)
                2: 0.0,    # 40003: Эталон стенки = 30.0 мм (старшее слово)
                3: 20.0,   # 40004: Эталон дна = 20.0 мм (младшее слово)
                4: 0.0,    # 40005: Эталон дна = 20.0 мм (старшее слово)
                5: 100.0,  # 40006: Эталон диаметр = 100.0 мм (младшее слово)
                6: 0.0,    # 40007: Эталон диаметр = 100.0 мм (старшее слово)
            }
            
            # Записываем тестовые данные в контекст
            for addr, value in test_input_data.items():
                self.input_registers[addr] = value
                if isinstance(value, float):
                    int_value = int(value * 1000)
                    self.slave_context.setValues(4, addr + 1, [int_value])  # Функция 4 - Input Registers
                else:
                    self.slave_context.setValues(4, addr + 1, [int(value)])
                
            for addr, value in test_holding_data.items():
                self.holding_registers[addr] = value
                if isinstance(value, float):
                    int_value = int(value * 1000)
                    self.slave_context.setValues(3, addr + 1, [int_value])  # Функция 3 - Holding Registers
                else:
                    self.slave_context.setValues(3, addr + 1, [int(value)])
                
            self.log_message("Тестовые данные установлены в регистры")
            
            if self.enable_gui:
                messagebox.showinfo("Успех", "Тестовые данные установлены!\n\nТеперь HMI может читать эти значения.")
            
        except Exception as e:
            self.log_message(f"Ошибка установки тестовых данных: {e}")
            if self.enable_gui:
                messagebox.showerror("Ошибка", f"Ошибка: {e}")
            
    def monitor_registers(self):
        """Мониторинг изменений в регистрах"""
        try:
            if self.is_server_running and self.slave_context:
                # Проверяем изменения в Holding Registers (могли быть изменены HMI)
                for addr in self.holding_registers.keys():
                    try:
                        values = self.slave_context.getValues(3, addr + 1, 1)
                        if values and values[0] != self.holding_registers[addr]:
                            old_value = self.holding_registers[addr]
                            new_value = values[0]
                            self.holding_registers[addr] = new_value
                            self.log_message(f"Регистр 4000{addr+1} изменен: {old_value} → {new_value}")
                    except:
                        pass
                        
            # Обновляем отображение регистров (только если GUI включен)
            if self.enable_gui:
                self.update_registers_display()
            
        except Exception as e:
            pass  # Игнорируем ошибки мониторинга
            
        # Планируем следующую проверку (только если GUI включен)
        if self.enable_gui and hasattr(self, 'root'):
            self.root.after(1000, self.monitor_registers)  # Каждую секунду
        
    def update_registers_display(self):
        """Обновление отображения регистров в таблицах"""
        try:
            # Обновляем Holding Registers
            for i, item in enumerate(self.holding_tree.get_children()):
                if i < len(self.holding_registers):
                    addr = list(self.holding_registers.keys())[i]
                    value = self.holding_registers[addr]
                    
                    current_values = list(self.holding_tree.item(item, "values"))
                    current_values[2] = str(value)
                    self.holding_tree.item(item, values=current_values)
                    
            # Обновляем Input Registers
            for i, item in enumerate(self.input_tree.get_children()):
                if i < len(self.input_registers):
                    addr = list(self.input_registers.keys())[i]
                    value = self.input_registers[addr]
                    
                    current_values = list(self.input_tree.item(item, "values"))
                    current_values[2] = str(value)
                    self.input_tree.item(item, values=current_values)
        except:
            pass
            
    def on_closing(self):
        """Обработка закрытия приложения"""
        self.stop_modbus_server()
        if self.enable_gui and hasattr(self, 'root'):
            self.root.quit()
        
    def run(self):
        """Запуск GUI"""
        if self.enable_gui and hasattr(self, 'root'):
            self.root.mainloop()


def main():
    """Запуск Modbus Slave сервера"""
    print("🚀 Запуск MODBUS SLAVE SERVER...")
    print("📊 Сервер с регистрами для HMI панели")
    print("🔧 Holding Registers: 40001-40014 (команды и настройки)")
    print("📖 Input Registers: 30001-30012 (результаты измерений)")
    
    app = ModbusSlaveServer()
    app.run()


if __name__ == "__main__":
    main()
