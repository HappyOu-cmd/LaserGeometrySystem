#!/usr/bin/env python3
"""
GUI для мониторинга Modbus регистров в реальном времени
Полностью переработанная версия
"""

import tkinter as tk
from tkinter import ttk
import threading
import time
import struct
from typing import Optional


class ModbusDebugGUI:
    """Современный GUI для мониторинга Modbus регистров"""
    
    def __init__(self, modbus_server=None):
        """
        Инициализация GUI
        
        Args:
            modbus_server: Ссылка на Modbus сервер
        """
        self.modbus_server = modbus_server
        self.is_running = False
        self.update_thread = None
        
        # Создание GUI
        self.setup_gui()
        
        # Автообновление
        self.auto_update_enabled = True
        
    def setup_gui(self):
        """Создание интерфейса"""
        self.root = tk.Tk()
        self.root.title("Laser Geometry System - Мониторинг регистров")
        self.root.geometry("1400x900")
        
        # Стили
        style = ttk.Style()
        style.theme_use('clam')
        
        # Главный контейнер с вкладками
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # === ВКЛАДКА 1: СИСТЕМА ===
        tab_system = ttk.Frame(notebook)
        notebook.add(tab_system, text="СИСТЕМА")
        self.create_system_tab(tab_system)
        
        # === ВКЛАДКА 2: HOLDING REGISTERS ===
        tab_holding = ttk.Frame(notebook)
        notebook.add(tab_holding, text="HOLDING REGISTERS")
        self.create_holding_tab(tab_holding)
        
        # === ВКЛАДКА 3: INPUT REGISTERS ===
        tab_input = ttk.Frame(notebook)
        notebook.add(tab_input, text="INPUT REGISTERS")
        self.create_input_tab(tab_input)
        
        # === ВКЛАДКА 4: СЧЕТЧИКИ ===
        tab_counters = ttk.Frame(notebook)
        notebook.add(tab_counters, text="СЧЕТЧИКИ")
        self.create_counters_tab(tab_counters)
        
    def create_system_tab(self, parent):
        """Вкладка состояния системы"""
        # Фрейм статуса
        status_frame = ttk.LabelFrame(parent, text="СТАТУС СИСТЕМЫ", padding=10)
        status_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Создаем большие лейблы для ключевых параметров
        self.lbl_command = tk.Label(status_frame, text="CMD: 0", font=("Arial", 24, "bold"), fg="blue")
        self.lbl_command.pack(pady=10)
        
        self.lbl_status = tk.Label(status_frame, text="СТАТУС: 0 (Готов)", font=("Arial", 20), fg="green")
        self.lbl_status.pack(pady=10)
        
        # Рамка с производственными данными
        prod_frame = ttk.LabelFrame(status_frame, text="ПРОИЗВОДСТВО", padding=10)
        prod_frame.pack(fill=tk.X, pady=10)
        
        self.lbl_shift = tk.Label(prod_frame, text="Смена: -", font=("Arial", 16))
        self.lbl_shift.pack(pady=5)
        
        self.lbl_product = tk.Label(prod_frame, text="Изделие: -", font=("Arial", 16))
        self.lbl_product.pack(pady=5)
        
        # Кнопка сброса
        btn_reset = ttk.Button(status_frame, text="СБРОС ОШИБОК (40024=1)", 
                              command=self.reset_error)
        btn_reset.pack(pady=20)
        
    def create_holding_tab(self, parent):
        """Вкладка Holding регистров"""
        # Таблица
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(tree_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # TreeView
        columns = ("Адрес", "Тип", "Значение", "Float", "Описание")
        self.tree_holding = ttk.Treeview(tree_frame, columns=columns, show='headings', 
                                         yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.tree_holding.yview)
        
        # Заголовки
        self.tree_holding.heading("Адрес", text="Адрес")
        self.tree_holding.heading("Тип", text="Тип")
        self.tree_holding.heading("Значение", text="Значение")
        self.tree_holding.heading("Float", text="Float")
        self.tree_holding.heading("Описание", text="Описание")
        
        # Ширина колонок
        self.tree_holding.column("Адрес", width=100)
        self.tree_holding.column("Тип", width=80)
        self.tree_holding.column("Значение", width=100)
        self.tree_holding.column("Float", width=120)
        self.tree_holding.column("Описание", width=400)
        
        self.tree_holding.pack(fill=tk.BOTH, expand=True)
        
        # Заполняем данными
        self.populate_holding_registers()
        
    def create_input_tab(self, parent):
        """Вкладка Input регистров"""
        # Таблица
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(tree_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # TreeView
        columns = ("Адрес", "Тип", "Значение", "Float", "Описание")
        self.tree_input = ttk.Treeview(tree_frame, columns=columns, show='headings',
                                       yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.tree_input.yview)
        
        # Заголовки
        self.tree_input.heading("Адрес", text="Адрес")
        self.tree_input.heading("Тип", text="Тип")
        self.tree_input.heading("Значение", text="Значение")
        self.tree_input.heading("Float", text="Float")
        self.tree_input.heading("Описание", text="Описание")
        
        # Ширина колонок
        self.tree_input.column("Адрес", width=100)
        self.tree_input.column("Тип", width=80)
        self.tree_input.column("Значение", width=100)
        self.tree_input.column("Float", width=120)
        self.tree_input.column("Описание", width=400)
        
        self.tree_input.pack(fill=tk.BOTH, expand=True)
        
        # Заполняем данными
        self.populate_input_registers()
        
    def create_counters_tab(self, parent):
        """Вкладка счетчиков производства"""
        counters_frame = ttk.LabelFrame(parent, text="СЧЕТЧИКИ ИЗДЕЛИЙ ЗА СМЕНУ", padding=20)
        counters_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Большие лейблы для счетчиков
        self.lbl_total = tk.Label(counters_frame, text="Всего: 0", font=("Arial", 20))
        self.lbl_total.pack(pady=10)
        
        self.lbl_good = tk.Label(counters_frame, text="Годных: 0", font=("Arial", 20), fg="green")
        self.lbl_good.pack(pady=10)
        
        self.lbl_cond = tk.Label(counters_frame, text="Условно-годных: 0", font=("Arial", 20), fg="orange")
        self.lbl_cond.pack(pady=10)
        
        self.lbl_bad = tk.Label(counters_frame, text="Негодных: 0", font=("Arial", 20), fg="red")
        self.lbl_bad.pack(pady=10)
        
    def populate_holding_registers(self):
        """Заполнение Holding регистров"""
        # Ключевые регистры для отображения
        registers = [
            ("40001", "INT", "CMD команда"),
            ("40002-40003", "FLOAT", "Эталонная толщина стенки"),
            ("40020", "INT", "Количество шагов"),
            ("40021", "INT", "Импульсов на 1 мм"),
            ("40022-40023", "FLOAT", "Дистанция до плоскости"),
            ("40024", "INT", "Сброс ошибок"),
            ("40049", "INT", "Режим проверки качества"),
            ("40050", "INT", "Допуск условно-годных"),
            ("40051", "INT", "Допуск негодных"),
            ("40100", "INT", "Смена"),
            ("40101", "INT", "Номер изделия"),
            ("40352-40353", "FLOAT", "Верхняя стенка - базовое"),
            ("40354-40355", "FLOAT", "Верхняя стенка - условно-негодная"),
            ("40356-40357", "FLOAT", "Верхняя стенка - негодная"),
            ("40358-40359", "FLOAT", "Нижняя стенка - базовое"),
            ("40360-40361", "FLOAT", "Нижняя стенка - условно-негодная"),
            ("40362-40363", "FLOAT", "Нижняя стенка - негодная"),
            ("40402-40403", "FLOAT", "Нижняя стенка - положит. негодная"),
            ("40364-40365", "FLOAT", "Дно - базовое"),
            ("40366-40367", "FLOAT", "Дно - условно-негодная"),
            ("40368-40369", "FLOAT", "Дно - негодная"),
            ("40400-40401", "FLOAT", "Дно - положит. негодная"),
            ("40370-40371", "FLOAT", "Фланец - базовое"),
            ("40376-40377", "FLOAT", "Высота - базовое"),
            ("40382-40383", "FLOAT", "Диаметр корпуса - базовое"),
            ("40388-40389", "FLOAT", "Диаметр фланца - базовое"),
        ]
        
        for addr, dtype, desc in registers:
            self.tree_holding.insert("", "end", values=(addr, dtype, "-", "-", desc))
            
    def populate_input_registers(self):
        """Заполнение Input регистров"""
        registers = [
            ("30001-30002", "FLOAT", "Датчик 1 - расстояние"),
            ("30002-30003", "FLOAT", "Датчик 2 - расстояние"),
            ("30005-30006", "FLOAT", "Датчик 3 - расстояние"),
            ("30007-30008", "FLOAT", "Датчик 4 - расстояние"),
            ("30009", "INT", "Флаг цикла измерения"),
            ("30016-30017", "FLOAT", "Верхняя стенка - макс"),
            ("30018-30019", "FLOAT", "Верхняя стенка - сред"),
            ("30020-30021", "FLOAT", "Верхняя стенка - мин"),
            ("30022-30023", "FLOAT", "Нижняя стенка - макс"),
            ("30024-30025", "FLOAT", "Нижняя стенка - сред"),
            ("30026-30027", "FLOAT", "Нижняя стенка - мин"),
            ("30028-30029", "FLOAT", "Дно - макс"),
            ("30030-30031", "FLOAT", "Дно - сред"),
            ("30032-30033", "FLOAT", "Дно - мин"),
            ("30034-30035", "FLOAT", "Толщина фланца - макс"),
            ("30036-30037", "FLOAT", "Толщина фланца - сред"),
            ("30038-30039", "FLOAT", "Толщина фланца - мин"),
            ("30040-30041", "FLOAT", "Высота - макс"),
            ("30042-30043", "FLOAT", "Высота - сред"),
            ("30044-30045", "FLOAT", "Высота - мин"),
            ("30046-30047", "FLOAT", "Диаметр корпуса - макс"),
            ("30048-30049", "FLOAT", "Диаметр корпуса - сред"),
            ("30050-30051", "FLOAT", "Диаметр корпуса - мин"),
            ("30052-30053", "FLOAT", "Диаметр фланца - макс"),
            ("30054-30055", "FLOAT", "Диаметр фланца - сред"),
            ("30056-30057", "FLOAT", "Диаметр фланца - мин"),
            ("30101", "INT", "Всего изделий за смену"),
            ("30102", "INT", "Годных изделий"),
            ("30103", "INT", "Условно-негодных изделий"),
            ("30104", "INT", "Негодных изделий"),
        ]
        
        for addr, dtype, desc in registers:
            self.tree_input.insert("", "end", values=(addr, dtype, "-", "-", desc))
    
    def reset_error(self):
        """Сброс ошибок через регистр 40024"""
        try:
            if self.modbus_server and self.modbus_server.slave_context:
                # 40024 -> индекс 23 (см. modbus_slave_server.py)
                self.modbus_server.slave_context.setValues(3, 23, [1])  # ИСПРАВЛЕНО: 24 -> 23
                print("Отправлен сигнал сброса ошибок (40024=1)")
        except Exception as e:
            print(f"Ошибка сброса: {e}")
    
    def read_int_register(self, reg_type: str, address: int) -> int:
        """Чтение одиночного INT регистра"""
        try:
            if not self.modbus_server or not self.modbus_server.slave_context:
                return 0
            
            fc = 3 if reg_type == 'holding' else 4
            base = 40001 if reg_type == 'holding' else 30001  # ИСПРАВЛЕНО: база с 1, не с 0
            idx = address - base
            
            value = self.modbus_server.slave_context.getValues(fc, idx, 1)[0]
            return int(value)
        except:
            return 0
    
    def read_float_register(self, reg_type: str, address1: int, address2: int) -> float:
        """Чтение DoubleWord Float регистра"""
        try:
            if not self.modbus_server or not self.modbus_server.slave_context:
                return 0.0
            
            fc = 3 if reg_type == 'holding' else 4
            base = 40001 if reg_type == 'holding' else 30001  # ИСПРАВЛЕНО: база с 1, не с 0
            idx1 = address1 - base
            idx2 = address2 - base
            
            high_word = self.modbus_server.slave_context.getValues(fc, idx1, 1)[0]
            low_word = self.modbus_server.slave_context.getValues(fc, idx2, 1)[0]
            
            combined = (high_word << 16) | low_word
            float_value = struct.unpack('!f', struct.pack('!I', combined))[0]
            
            return float_value
        except:
            return 0.0
    
    def update_display(self):
        """Обновление отображения"""
        try:
            # Обновляем системные данные
            cmd = self.read_int_register('holding', 40001)
            status = self.read_int_register('input', 30009)
            shift = self.read_int_register('holding', 40100)
            product = self.read_int_register('holding', 40101)
            
            self.lbl_command.config(text=f"CMD: {cmd}")
            
            # Цвет статуса
            status_text = self.get_status_text(status)
            status_color = self.get_status_color(status)
            self.lbl_status.config(text=f"СТАТУС: {status} ({status_text})", fg=status_color)
            
            self.lbl_shift.config(text=f"Смена: {shift}")
            self.lbl_product.config(text=f"Изделие: {product}")
            
            # Обновляем счетчики
            total = self.read_int_register('input', 30101)
            good = self.read_int_register('input', 30102)
            cond = self.read_int_register('input', 30103)
            bad = self.read_int_register('input', 30104)
            
            self.lbl_total.config(text=f"Всего: {total}")
            self.lbl_good.config(text=f"Годных: {good}")
            self.lbl_cond.config(text=f"Условно-годных: {cond}")
            self.lbl_bad.config(text=f"Негодных: {bad}")
            
            # Обновляем Holding регистры
            for item in self.tree_holding.get_children():
                values = self.tree_holding.item(item)['values']
                addr = values[0]
                dtype = values[1]
                
                if dtype == "INT":
                    reg_addr = int(addr)
                    val = self.read_int_register('holding', reg_addr)
                    self.tree_holding.set(item, "Значение", str(val))
                    self.tree_holding.set(item, "Float", "-")
                    
                elif dtype == "FLOAT":
                    parts = addr.split('-')
                    if len(parts) == 2:
                        addr1 = int(parts[0])
                        addr2 = int(parts[1])
                        float_val = self.read_float_register('holding', addr1, addr2)
                        val1 = self.read_int_register('holding', addr1)
                        val2 = self.read_int_register('holding', addr2)
                        self.tree_holding.set(item, "Значение", f"{val1} / {val2}")
                        self.tree_holding.set(item, "Float", f"{float_val:.3f}")
            
            # Обновляем Input регистры
            for item in self.tree_input.get_children():
                values = self.tree_input.item(item)['values']
                addr = values[0]
                dtype = values[1]
                
                if dtype == "INT":
                    reg_addr = int(addr)
                    val = self.read_int_register('input', reg_addr)
                    self.tree_input.set(item, "Значение", str(val))
                    self.tree_input.set(item, "Float", "-")
                    
                elif dtype == "FLOAT":
                    parts = addr.split('-')
                    if len(parts) == 2:
                        addr1 = int(parts[0])
                        addr2 = int(parts[1])
                        float_val = self.read_float_register('input', addr1, addr2)
                        val1 = self.read_int_register('input', addr1)
                        val2 = self.read_int_register('input', addr2)
                        self.tree_input.set(item, "Значение", f"{val1} / {val2}")
                        self.tree_input.set(item, "Float", f"{float_val:.3f}")
            
        except Exception as e:
            print(f"Ошибка обновления GUI: {e}")
    
    def get_status_text(self, status: int) -> str:
        """Получение текстового описания статуса"""
        status_map = {
            0: "Готов",
            9: "Поиск препятствия",
            90: "Препятствие найдено",
            91: "Высота рассчитана",
            10: "Измерение стенки",
            11: "Подсчет стенки",
            110: "Стенка готова",
            12: "Измерение фланца",
            13: "Подсчет фланца",
            112: "Фланец готов",
            14: "Измерение дна",
            15: "Подсчет дна",
            114: "Дно готово",
            16: "Оценка качества",
            116: "Качество оценено",
            100: "Калибровка стенки",
            101: "Калибровка дна",
            102: "Калибровка фланца",
            200: "Поток датчик 1",
            201: "Поток датчик 2",
            202: "Поток датчик 3",
            203: "Поток датчик 4",
            -1: "ОШИБКА",
        }
        return status_map.get(status, "Неизвестно")
    
    def get_status_color(self, status: int) -> str:
        """Получение цвета для статуса"""
        if status == 0:
            return "green"
        elif status == -1 or status == 65535:
            return "red"
        elif status in [9, 10, 12, 14]:
            return "blue"
        elif status in [11, 13, 15, 16]:
            return "orange"
        elif status in [90, 91, 110, 112, 114, 116]:
            return "purple"
        else:
            return "black"
        
    def auto_update_loop(self):
        """Цикл автообновления"""
        while self.is_running:
            if self.auto_update_enabled:
                try:
                    if self.root and self.root.winfo_exists():
                        self.root.after(0, self.update_display)
                except Exception as e:
                    print(f"Ошибка в цикле обновления GUI: {e}")
                time.sleep(1.0)  # Обновление каждую секунду
    
    def start(self):
        """Запуск GUI"""
        self.is_running = True
        
        # Запуск потока автообновления
        self.update_thread = threading.Thread(target=self.auto_update_loop, daemon=True)
        self.update_thread.start()
        
        # Запуск GUI
        self.root.mainloop()

    def stop(self):
        """Остановка GUI"""
        self.is_running = False
        if self.root:
            self.root.quit()


if __name__ == "__main__":
    print("Запуск GUI требует подключения к Modbus серверу")
    print("Используйте laser_geometry_system.py с параметром enable_debug_gui=True")
