#!/usr/bin/env python3
"""
🎯 ОПТИМИЗИРОВАННЫЙ простой интерфейс для датчиков RIFTEK РФ602

ИСПРАВЛЕНЫ ТОРМОЗА при работе с реальными датчиками!
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import queue
from main import HighSpeedRiftekSensor

class SensorGUISimpleOptimized:
    """Оптимизированный простой интерфейс без тормозов"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("RIFTEK РФ602 - Измерение толщины (t = L - (d1 + d2))")
        self.root.geometry("800x600")
        self.root.resizable(True, True)
        
        # Инициализация датчиков
        self.sensors = None
        self.is_running = False
        self.measurement_thread = None
        
        # Счетчики
        self.measurement_counter = 0
        self.filtered_counter = 0
        self.data_queue = queue.Queue(maxsize=100)  # Ограничиваем размер очереди
        
        # Текущие значения
        self.current_values = {
            'sensor1': 0.0,
            'sensor2': 0.0,
            'sensor3': 0.0,
            'sensor4': 0.0,
            'thickness': 0.0  # Изменено с 'difference' на 'thickness'
        }
        
        # Калибровочная величина L для формулы t = L - (d1 + d2)
        self.calibration_L = tk.DoubleVar(value=50.0)  # По умолчанию 50мм
        
        # Настройки фильтрации
        self.enable_filtering = tk.BooleanVar(value=False)  # По умолчанию ОТКЛЮЧЕНА
        
        # Создание интерфейса
        self.setup_interface()
        
        # Запуск обновления GUI
        self.update_gui()
        
    def setup_interface(self):
        """Создание оптимизированного интерфейса"""
        
        # Заголовок
        title_frame = ttk.Frame(self.root)
        title_frame.pack(fill=tk.X, padx=10, pady=10)
        
        title_label = ttk.Label(title_frame, text="ДАТЧИКИ RIFTEK РФ602 - ИЗМЕРЕНИЕ ТОЛЩИНЫ", 
                               font=("Arial", 16, "bold"))
        title_label.pack()
        
        # Настройки подключения
        self.setup_connection_frame()
        
        # 5 окошек с показаниями
        self.setup_display_boxes()
        
        # Кнопки управления
        self.setup_control_frame()
        
        # Статистика
        self.setup_statistics_frame()
        
    def setup_connection_frame(self):
        """Настройки подключения"""
        conn_frame = ttk.LabelFrame(self.root, text="Настройки подключения", padding=10)
        conn_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Первая строка - подключение
        top_row = ttk.Frame(conn_frame)
        top_row.pack(fill=tk.X, pady=(0, 5))
        
        # COM порт
        port_frame = ttk.Frame(top_row)
        port_frame.pack(side=tk.LEFT, padx=10)
        
        ttk.Label(port_frame, text="COM порт:").pack()
        self.port_var = tk.StringVar(value="COM7")
        port_entry = ttk.Entry(port_frame, textvariable=self.port_var, width=10)
        port_entry.pack()
        
        # Baudrate
        baud_frame = ttk.Frame(top_row)
        baud_frame.pack(side=tk.LEFT, padx=10)
        
        ttk.Label(baud_frame, text="Baudrate:").pack()
        self.baudrate_var = tk.StringVar(value="921600")
        baud_entry = ttk.Entry(baud_frame, textvariable=self.baudrate_var, width=10)
        baud_entry.pack()
        
        # Статус подключения
        status_frame = ttk.Frame(top_row)
        status_frame.pack(side=tk.RIGHT, padx=10)
        
        ttk.Label(status_frame, text="Статус:").pack()
        self.status_label = ttk.Label(status_frame, text="🔴 Не подключен", 
                                     foreground="red", font=("Arial", 10, "bold"))
        self.status_label.pack()
        
        # Вторая строка - настройки фильтрации и калибровки
        bottom_row = ttk.Frame(conn_frame)
        bottom_row.pack(fill=tk.X)
        
        # Чекбокс фильтрации
        filter_frame = ttk.Frame(bottom_row)
        filter_frame.pack(side=tk.LEFT, padx=10)
        
        self.filter_checkbox = ttk.Checkbutton(
            filter_frame, 
            text="Фильтрация измерений (если один датчик 0, другой >0)", 
            variable=self.enable_filtering
        )
        self.filter_checkbox.pack()
        
        # Калибровочная величина L
        calib_frame = ttk.Frame(bottom_row)
        calib_frame.pack(side=tk.LEFT, padx=20)
        
        ttk.Label(calib_frame, text="Калибровка L (мм):", font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        calib_spinbox = ttk.Spinbox(calib_frame, textvariable=self.calibration_L, 
                                   from_=10.0, to=500.0, increment=0.1, width=8,
                                   format="%.1f")
        calib_spinbox.pack(side=tk.LEFT, padx=5)
        
        # Информация
        info_label = ttk.Label(bottom_row, 
                              text="💡 L = расстояние между датчиками", 
                              foreground="blue", font=("Arial", 9))
        info_label.pack(side=tk.RIGHT, padx=10)
        
    def setup_display_boxes(self):
        """Создание 5 окошек с показаниями"""
        display_frame = ttk.LabelFrame(self.root, text="Показания датчиков", padding=15)
        display_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Верхний ряд - 4 датчика
        top_frame = ttk.Frame(display_frame)
        top_frame.pack(fill=tk.X, pady=(0, 15))
        
        # Датчик 1
        self.create_sensor_box(top_frame, "ДАТЧИК 1", "red", 0, 0)
        self.sensor1_value = self.value_labels[0]
        
        # Датчик 2  
        self.create_sensor_box(top_frame, "ДАТЧИК 2", "purple", 0, 1)
        self.sensor2_value = self.value_labels[1]
        
        # Датчик 3
        self.create_sensor_box(top_frame, "ДАТЧИК 3", "green", 0, 2)
        self.sensor3_value = self.value_labels[2]
        
        # Датчик 4
        self.create_sensor_box(top_frame, "ДАТЧИК 4", "blue", 0, 3)
        self.sensor4_value = self.value_labels[3]
        
        # Нижний ряд - толщина
        bottom_frame = ttk.Frame(display_frame)
        bottom_frame.pack(fill=tk.X)
        
        # Окошко толщины (по центру)
        thickness_frame = ttk.Frame(bottom_frame)
        thickness_frame.pack(expand=True)
        
        thickness_box = ttk.LabelFrame(thickness_frame, text="ТОЛЩИНА t = L - (d1 + d2)", padding=20)
        thickness_box.pack(padx=10, pady=5)
        
        self.thickness_value = ttk.Label(thickness_box, text="0.000", 
                                        font=("Arial", 24, "bold"), 
                                        foreground="red")
        self.thickness_value.pack()
        
        ttk.Label(thickness_box, text="мм", font=("Arial", 12)).pack()
        
    def create_sensor_box(self, parent, title, color, row, col):
        """Создание окошка для одного датчика"""
        if not hasattr(self, 'value_labels'):
            self.value_labels = []
            
        # Рамка для датчика
        sensor_frame = ttk.LabelFrame(parent, text=title, padding=15)
        sensor_frame.grid(row=row, column=col, padx=10, pady=5, sticky="nsew")
        
        # Настройка сетки для равномерного распределения
        parent.grid_columnconfigure(col, weight=1)
        
        # Значение
        value_label = ttk.Label(sensor_frame, text="0.000", 
                               font=("Arial", 20, "bold"), 
                               foreground=color)
        value_label.pack()
        
        # Единица измерения
        unit_label = ttk.Label(sensor_frame, text="мм", font=("Arial", 10))
        unit_label.pack()
        
        self.value_labels.append(value_label)
        
    def setup_control_frame(self):
        """Кнопки управления"""
        control_frame = ttk.Frame(self.root)
        control_frame.pack(fill=tk.X, padx=10, pady=10)
        
        # Кнопка старт/стоп
        self.start_button = ttk.Button(control_frame, text="▶ СТАРТ", 
                                      command=self.toggle_measurement,
                                      style="Accent.TButton")
        self.start_button.pack(side=tk.LEFT, padx=10)
        
        # Кнопка сброса значений
        reset_button = ttk.Button(control_frame, text="🔄 СБРОС", 
                                 command=self.reset_values)
        reset_button.pack(side=tk.LEFT, padx=10)
        
        # Кнопка сохранения данных
        save_button = ttk.Button(control_frame, text="💾 СОХРАНИТЬ", 
                                command=self.save_data)
        save_button.pack(side=tk.LEFT, padx=10)
        
        # Кнопка выхода
        exit_button = ttk.Button(control_frame, text="❌ ВЫХОД", 
                                command=self.on_closing)
        exit_button.pack(side=tk.RIGHT, padx=10)
        
    def setup_statistics_frame(self):
        """Статистика"""
        stats_frame = ttk.LabelFrame(self.root, text="Статистика", padding=10)
        stats_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        
        # Левая часть - счетчики
        left_frame = ttk.Frame(stats_frame)
        left_frame.pack(side=tk.LEFT)
        
        self.measurements_label = ttk.Label(left_frame, text="Измерений: 0")
        self.measurements_label.pack(side=tk.LEFT, padx=10)
        
        self.frequency_label = ttk.Label(left_frame, text="Частота: 0.0 Гц")
        self.frequency_label.pack(side=tk.LEFT, padx=10)
        
        self.errors_label = ttk.Label(left_frame, text="Ошибок: 0")
        self.errors_label.pack(side=tk.LEFT, padx=10)
        
        self.filtered_label = ttk.Label(left_frame, text="Отфильтровано: 0")
        self.filtered_label.pack(side=tk.LEFT, padx=10)
        
        # Правая часть - время
        right_frame = ttk.Frame(stats_frame)
        right_frame.pack(side=tk.RIGHT)
        
        self.time_label = ttk.Label(right_frame, text="", font=("Arial", 10))
        self.time_label.pack()
        
    def toggle_measurement(self):
        """Переключение старт/стоп измерений"""
        if not self.is_running:
            self.start_measurement()
        else:
            self.stop_measurement()
            
    def start_measurement(self):
        """Запуск измерений"""
        try:
            # Подключение к датчикам
            port = self.port_var.get()
            baudrate = int(self.baudrate_var.get())
            
            self.sensors = HighSpeedRiftekSensor(port, baudrate, timeout=0.002)
            
            if not self.sensors.connect():
                messagebox.showerror("Ошибка", "Не удалось подключиться к датчикам!")
                return
                
            # Запуск потока измерений
            self.is_running = True
            self.measurement_thread = threading.Thread(target=self.measurement_loop, daemon=True)
            self.measurement_thread.start()
            
            # Обновление интерфейса
            self.start_button.config(text="⏸ СТОП")
            self.status_label.config(text="🟢 Подключен", foreground="green")
            
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка запуска: {e}")
            
    def stop_measurement(self):
        """Остановка измерений"""
        self.is_running = False
        
        if self.measurement_thread:
            self.measurement_thread.join(timeout=1.0)
            
        if self.sensors:
            self.sensors.disconnect()
            self.sensors = None
            
        # Обновление интерфейса
        self.start_button.config(text="▶ СТАРТ")
        self.status_label.config(text="🔴 Не подключен", foreground="red")
        
    def measurement_loop(self):
        """ОПТИМИЗИРОВАННЫЙ основной цикл измерений"""
        measurement_times = []
        error_count = 0
        max_errors = 10  # Максимум ошибок подряд перед паузой
        
        while self.is_running:
            try:
                current_time = time.time()
                
                # Выполняем QUAD измерение
                sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm = self.sensors.perform_quad_sensor_measurement(
                    25.0, 25.0, 25.0, 25.0
                )
                
                # Проверяем, что получили валидные данные
                if all(v is not None for v in [sensor1_mm, sensor2_mm, sensor3_mm, sensor4_mm]):
                    
                    # Сбрасываем счетчик ошибок при успешном измерении
                    error_count = 0
                    
                    # УСЛОВНАЯ фильтрация (только если включена)
                    should_filter = False
                    if self.enable_filtering.get():
                        sensor1_is_zero = abs(sensor1_mm) < 0.001  
                        sensor2_is_zero = abs(sensor2_mm) < 0.001
                        
                        # Проверяем условие фильтрации для датчиков 1 и 2
                        if (sensor1_is_zero and not sensor2_is_zero) or (not sensor1_is_zero and sensor2_is_zero):
                            should_filter = True
                            self.filtered_counter += 1
                    
                    # Вычисляем толщину по формуле t = L - (d1 + d2)
                    L_value = self.calibration_L.get()
                    thickness_value = L_value - (sensor1_mm + sensor2_mm)
                    
                    # ВСЕГДА увеличиваем счетчик измерений
                    self.measurement_counter += 1
                    
                    # Отправляем данные в основной поток (даже отфильтрованные)
                    data = {
                        'sensor1_value': sensor1_mm,
                        'sensor2_value': sensor2_mm,
                        'sensor3_value': sensor3_mm,
                        'sensor4_value': sensor4_mm,
                        'thickness_value': thickness_value,
                        'L_value': L_value,
                        'filtered_count': self.filtered_counter,
                        'is_filtered': should_filter,
                        'timestamp': current_time
                    }
                    
                    # Быстрая отправка данных
                    try:
                        self.data_queue.put_nowait(data)
                    except queue.Full:
                        # Если очередь полная, удаляем старые данные
                        try:
                            self.data_queue.get_nowait()  # Удаляем старое
                            self.data_queue.put_nowait(data)  # Добавляем новое
                        except queue.Empty:
                            pass
                    
                    # Расчет частоты (только последние 30 измерений)
                    measurement_times.append(current_time)
                    if len(measurement_times) > 30:
                        measurement_times = measurement_times[-30:]
                        
                else:
                    # Обработка ошибок измерения
                    error_count += 1
                    if error_count >= max_errors:
                        # Делаем паузу при множественных ошибках
                        time.sleep(0.01)
                        error_count = 0
                    
            except Exception as e:
                print(f"Ошибка измерения: {e}")
                error_count += 1
                if error_count >= max_errors:
                    time.sleep(0.01)  # Пауза при ошибках
                    error_count = 0
                
    def update_gui(self):
        """ОПТИМИЗИРОВАННОЕ обновление интерфейса"""
        updated = False
        updates_count = 0
        max_updates_per_cycle = 5  # Ограничиваем количество обновлений за цикл
        
        # Обрабатываем максимум 5 обновлений за раз для избежания тормозов
        while updates_count < max_updates_per_cycle:
            try:
                data = self.data_queue.get_nowait()
                
                # Обновляем текущие значения
                self.current_values['sensor1'] = data['sensor1_value']
                self.current_values['sensor2'] = data['sensor2_value'] 
                self.current_values['sensor3'] = data['sensor3_value']
                self.current_values['sensor4'] = data['sensor4_value']
                self.current_values['thickness'] = data['thickness_value']
                
                # Обновляем окошки с показаниями
                # Показываем отфильтрованные значения СЕРЫМ цветом
                if data.get('is_filtered', False):
                    # Отфильтрованные значения - серый цвет
                    self.sensor1_value.config(text=f"{data['sensor1_value']:.3f}", foreground="gray")
                    self.sensor2_value.config(text=f"{data['sensor2_value']:.3f}", foreground="gray")
                    self.thickness_value.config(text=f"{data['thickness_value']:.3f}", foreground="gray")
                else:
                    # Нормальные значения - обычные цвета
                    self.sensor1_value.config(text=f"{data['sensor1_value']:.3f}", foreground="red")
                    self.sensor2_value.config(text=f"{data['sensor2_value']:.3f}", foreground="purple")
                    self.thickness_value.config(text=f"{data['thickness_value']:.3f}", foreground="red")
                
                # Датчики 3 и 4 всегда нормального цвета
                self.sensor3_value.config(text=f"{data['sensor3_value']:.3f}")
                self.sensor4_value.config(text=f"{data['sensor4_value']:.3f}")
                
                # Обновляем счетчик отфильтрованных
                self.filtered_label.config(text=f"Отфильтровано: {data['filtered_count']}")
                
                updated = True
                updates_count += 1
                
            except queue.Empty:
                break
                
        # Обновляем статистику только если были обновления
        if updated and self.sensors and self.is_running:
            self.update_statistics()
            
        # Обновляем время
        current_time = time.strftime("%H:%M:%S")
        self.time_label.config(text=f"Время: {current_time}")
            
        # Планируем следующее обновление (увеличиваем интервал для стабильности)
        self.root.after(100, self.update_gui)  # 100мс вместо 50мс
        
    def update_statistics(self):
        """Обновление статистики"""
        if self.sensors:
            total_measurements = self.sensors.measurement_count
            total_errors = self.sensors.error_count
            
            # Частота измерений
            if hasattr(self.sensors, 'start_time') and self.sensors.start_time:
                elapsed = time.time() - self.sensors.start_time
                frequency = total_measurements / elapsed if elapsed > 0 else 0
            else:
                frequency = 0
                
            # Обновляем лейблы
            self.measurements_label.config(text=f"Измерений: {total_measurements}")
            self.frequency_label.config(text=f"Частота: {frequency:.1f} Гц")
            self.errors_label.config(text=f"Ошибок: {total_errors}")
            
    def reset_values(self):
        """Сброс всех значений к нулю"""
        self.sensor1_value.config(text="0.000", foreground="red")
        self.sensor2_value.config(text="0.000", foreground="purple")
        self.sensor3_value.config(text="0.000", foreground="green")
        self.sensor4_value.config(text="0.000", foreground="blue")
        self.thickness_value.config(text="0.000", foreground="red")
        
        # Сброс счетчиков
        self.measurement_counter = 0
        self.filtered_counter = 0
        
        # Сброс текущих значений
        for key in self.current_values:
            self.current_values[key] = 0.0
            
        # Обновление статистики
        self.measurements_label.config(text="Измерений: 0")
        self.frequency_label.config(text="Частота: 0.0 Гц")
        self.errors_label.config(text="Ошибок: 0")
        self.filtered_label.config(text="Отфильтровано: 0")
        
        messagebox.showinfo("Сброс", "Все значения сброшены к нулю")
        
    def save_data(self):
        """Сохранение текущих данных в файл"""
        try:
            filename = f"sensor_readings_optimized_{int(time.time())}.txt"
            
            with open(filename, 'w', encoding='utf-8') as f:
                f.write("=== ПОКАЗАНИЯ ДАТЧИКОВ RIFTEK РФ602 (ИЗМЕРЕНИЕ ТОЛЩИНЫ) ===\n")
                f.write(f"Время: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Фильтрация включена: {'Да' if self.enable_filtering.get() else 'Нет'}\n")
                f.write(f"Калибровочная величина L: {self.calibration_L.get():.3f} мм\n\n")
                f.write(f"Датчик 1 (d1):      {self.current_values['sensor1']:.6f} мм\n")
                f.write(f"Датчик 2 (d2):      {self.current_values['sensor2']:.6f} мм\n") 
                f.write(f"Датчик 3:           {self.current_values['sensor3']:.6f} мм\n")
                f.write(f"Датчик 4:           {self.current_values['sensor4']:.6f} мм\n\n")
                f.write(f"ТОЛЩИНА t = L - (d1 + d2): {self.current_values['thickness']:.6f} мм\n")
                f.write(f"Формула: t = {self.calibration_L.get():.3f} - ({self.current_values['sensor1']:.3f} + {self.current_values['sensor2']:.3f})\n\n")
                f.write(f"Измерений выполнено: {self.measurement_counter}\n")
                f.write(f"Отфильтровано:       {self.filtered_counter}\n")
                
            messagebox.showinfo("Сохранение", f"Данные сохранены в файл:\n{filename}")
            
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка сохранения: {e}")
            
    def on_closing(self):
        """Обработка закрытия окна"""
        if self.is_running:
            self.stop_measurement()
        self.root.quit()


def main():
    """Запуск оптимизированного интерфейса"""
    root = tk.Tk()
    
    # Настройка стиля
    style = ttk.Style()
    try:
        style.theme_use('vista')  # Современный стиль для Windows
    except:
        style.theme_use('default')
        
    app = SensorGUISimpleOptimized(root)
    
    # Обработка закрытия окна
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    
    try:
        root.mainloop()
    except KeyboardInterrupt:
        print("Программа остановлена пользователем")
    finally:
        if app.is_running:
            app.stop_measurement()


if __name__ == "__main__":
    main()
