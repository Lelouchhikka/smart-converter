import math
import time
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Tuple
import threading
import json

@dataclass
class DroneTelemetry:
    stream_id: str
    latitude: float
    longitude: float
    altitude: float
    speed: float
    battery: float
    signal_strength: float
    timestamp: datetime
    status: str = "active"
    flight_pattern: str = "circle"  # circle, figure8, zigzag
    pattern_params: Dict = None

class DroneTelemetrySimulator:
    def __init__(self):
        self.drones: Dict[str, DroneTelemetry] = {}
        self._stop_event = threading.Event()
        self._update_interval = 1.0  # секунды
        
    def add_drone(self, stream_id: str, initial_lat: float = 43.238949, initial_lon: float = 76.889709):
        """Добавляет новый дрон для эмуляции телеметрии"""
        pattern = random.choice(["circle", "figure8", "zigzag"])
        pattern_params = self._get_pattern_params(pattern, initial_lat, initial_lon)
        
        self.drones[stream_id] = DroneTelemetry(
            stream_id=stream_id,
            latitude=initial_lat,
            longitude=initial_lon,
            altitude=random.uniform(50, 150),
            speed=random.uniform(5, 15),
            battery=random.uniform(60, 100),
            signal_strength=random.uniform(70, 100),
            timestamp=datetime.now(),
            flight_pattern=pattern,
            pattern_params=pattern_params
        )
        
    def _get_pattern_params(self, pattern: str, center_lat: float, center_lon: float) -> Dict:
        """Генерирует параметры для паттерна движения"""
        if pattern == "circle":
            return {
                "center_lat": center_lat,
                "center_lon": center_lon,
                "radius": random.uniform(0.005, 0.015),
                "speed": random.uniform(0.5, 2.0)
            }
        elif pattern == "figure8":
            return {
                "center_lat": center_lat,
                "center_lon": center_lon,
                "width": random.uniform(0.01, 0.02),
                "height": random.uniform(0.005, 0.015),
                "speed": random.uniform(0.5, 2.0)
            }
        else:  # zigzag
            return {
                "start_lat": center_lat,
                "start_lon": center_lon,
                "width": random.uniform(0.01, 0.02),
                "height": random.uniform(0.005, 0.015),
                "speed": random.uniform(0.5, 2.0),
                "direction": 1
            }
            
    def _update_position(self, drone: DroneTelemetry, t: float) -> Tuple[float, float]:
        """Обновляет позицию дрона в соответствии с паттерном движения"""
        if drone.flight_pattern == "circle":
            params = drone.pattern_params
            angle = t * params["speed"]
            lat = params["center_lat"] + params["radius"] * math.cos(angle)
            lon = params["center_lon"] + params["radius"] * math.sin(angle)
            return lat, lon
            
        elif drone.flight_pattern == "figure8":
            params = drone.pattern_params
            angle = t * params["speed"]
            lat = params["center_lat"] + params["height"] * math.sin(angle)
            lon = params["center_lon"] + params["width"] * math.sin(2 * angle)
            return lat, lon
            
        else:  # zigzag
            params = drone.pattern_params
            t_scaled = t * params["speed"]
            lat = params["start_lat"] + params["height"] * math.sin(t_scaled)
            lon = params["start_lon"] + params["width"] * (t_scaled % 2 - 1) * params["direction"]
            if t_scaled % 2 < 0.1:  # Меняем направление в крайних точках
                params["direction"] *= -1
            return lat, lon

    def remove_drone(self, stream_id: str):
        """Удаляет дрон из эмуляции"""
        if stream_id in self.drones:
            del self.drones[stream_id]
            
    def start_simulation(self):
        """Запускает эмуляцию телеметрии для всех дронов"""
        self._stop_event.clear()
        threading.Thread(target=self._simulation_loop, daemon=True).start()
        
    def stop_simulation(self):
        """Останавливает эмуляцию"""
        self._stop_event.set()
        
    def _simulation_loop(self):
        """Основной цикл эмуляции телеметрии"""
        start_time = time.time()
        while not self._stop_event.is_set():
            current_time = time.time() - start_time
            for drone in self.drones.values():
                # Обновляем позицию дрона
                drone.latitude, drone.longitude = self._update_position(drone, current_time)
                
                # Обновляем другие параметры
                drone.altitude += random.uniform(-5, 5)
                drone.altitude = max(50, min(150, drone.altitude))
                
                drone.speed += random.uniform(-1, 1)
                drone.speed = max(5, min(15, drone.speed))
                
                drone.battery -= random.uniform(0.1, 0.3)
                drone.battery = max(0, min(100, drone.battery))
                
                drone.signal_strength += random.uniform(-2, 2)
                drone.signal_strength = max(0, min(100, drone.signal_strength))
                
                drone.timestamp = datetime.now()
                
                # Обновляем статус
                if drone.battery <= 0:
                    drone.status = "low_battery"
                elif drone.signal_strength < 30:
                    drone.status = "low_signal"
                else:
                    drone.status = "active"
                    
            time.sleep(self._update_interval)
            
    def get_telemetry(self, stream_id: str) -> Optional[DroneTelemetry]:
        """Получает текущую телеметрию для конкретного дрона"""
        return self.drones.get(stream_id)
        
    def get_all_telemetry(self) -> Dict[str, DroneTelemetry]:
        """Получает телеметрию всех дронов"""
        return self.drones.copy()
        
    def to_json(self, stream_id: str) -> Optional[str]:
        """Конвертирует телеметрию дрона в JSON"""
        drone = self.get_telemetry(stream_id)
        if drone:
            return json.dumps({
                "stream_id": drone.stream_id,
                "latitude": drone.latitude,
                "longitude": drone.longitude,
                "altitude": drone.altitude,
                "speed": drone.speed,
                "battery": drone.battery,
                "signal_strength": drone.signal_strength,
                "timestamp": drone.timestamp.isoformat(),
                "status": drone.status
            })
        return None 