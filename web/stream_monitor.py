import asyncio
import logging
import httpx
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import random
import math

from db import SessionLocal, Position as PositionDB # Импортируем для сохранения в БД

logger = logging.getLogger(__name__)

# Определим базовый класс или структуру для представления потока из монитора
# Это должно соответствовать тому, что ожидается в web/api.py
class MonitoredStream:
    def __init__(self, path: str, source_type: str, publishers: List[Any], readers: List[Any], status: str, rtsp_url: str = None, hls_url: str = None, start_time: datetime = None, last_seen: datetime = None):
        self.path = path
        self.source_type = source_type # например, 'publisher'
        self.publishers = publishers
        self.readers = readers
        self.status = status # например, 'active', 'inactive'
        self.rtsp_url = rtsp_url
        self.hls_url = hls_url
        self.start_time = start_time
        self.last_seen = last_seen

class StreamMonitor:
    def __init__(self, mediamtx_api_url: str = "http://localhost:9997"):
        self.mediamtx_api_url = mediamtx_api_url
        self._is_running = False
        self._monitor_task = None
        self._active_streams: Dict[str, MonitoredStream] = {}
        self.telemetry_data: Dict[str, Dict[str, Any]] = {}
        self.telemetry_history: Dict[str, List[Dict[str, Any]]] = {}
        self._stream_events: List[tuple] = []
        logger.info(f"StreamMonitor initialized with API URL: {self.mediamtx_api_url}")
        
        # Инициализация симулятора и истории телеметрии
        self.telemetry_simulator = DroneTelemetrySimulator() # Создаем экземпляр симулятора
        self.max_history_size = 1000 # Максимальное количество точек в истории

    async def start(self):
        """Запускает мониторинг MediaMTX и симулятор."""
        if self._is_running:
            logger.info("StreamMonitor is already running.")
            return
        logger.info("Starting StreamMonitor and Telemetry Simulator...")
        self._is_running = True
        # Запускаем фоновую задачу для периодического опроса MediaMTX
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        # Запускаем фоновую задачу для симуляции телеметрии
        self._telemetry_task = asyncio.create_task(self._telemetry_loop())
        logger.info("StreamMonitor and Telemetry Simulator started.")

    async def stop(self):
        """Останавливает мониторинг MediaMTX и симулятор."""
        if not self._is_running:
            logger.info("StreamMonitor is not running.")
            return
        logger.info("Stopping StreamMonitor and Telemetry Simulator...")
        self._is_running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                logger.info("StreamMonitor task cancelled.")
        if self._telemetry_task:
             self._telemetry_task.cancel()
             try:
                 await self._telemetry_task
             except asyncio.CancelledError:
                 logger.info("Telemetry simulator task cancelled.")
        logger.info("StreamMonitor and Telemetry Simulator stopped.")

    async def _monitor_loop(self):
        """Периодически опрашивает MediaMTX API."""
        while self._is_running:
            try:
                await self._fetch_streams_status()
            except Exception as e:
                logger.error(f"Error in StreamMonitor loop: {e}")
            await asyncio.sleep(5) # Опрашиваем каждые 5 секунд

    async def _telemetry_loop(self):
        """Периодически генерирует и сохраняет телеметрию."""
        while self._is_running:
            try:
                # Получаем телеметрию от симулятора для всех активных дронов (потоков)
                simulated_telemetry = self.telemetry_simulator.generate_telemetry(list(self._active_streams.keys()))
                
                # Обновляем текущую телеметрию и историю
                for drone_id, data in simulated_telemetry.items():
                    self.telemetry_data[drone_id] = data
                    
                    if drone_id not in self.telemetry_history:
                        self.telemetry_history[drone_id] = []
                        
                    # Добавляем метку времени к данным телеметрии
                    telemetry_point = {"timestamp": datetime.utcnow().isoformat(), **data}
                    self.telemetry_history[drone_id].append(telemetry_point)
                    
                    # Ограничиваем размер истории
                    if len(self.telemetry_history[drone_id]) > self.max_history_size:
                        self.telemetry_history[drone_id] = self.telemetry_history[drone_id][-self.max_history_size:]
                    
                    # Сохраняем позицию в БД
                    await self.save_position_to_db(drone_id, data)
                    
                logger.debug(f"Generated and saved telemetry for {len(simulated_telemetry)} drones.")

            except Exception as e:
                logger.error(f"Error in Telemetry Simulator loop: {e}")
            
            await asyncio.sleep(1) # Генерируем телеметрию каждую секунду

    async def _fetch_streams_status(self):
        """Получает актуальный список потоков из MediaMTX API."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.mediamtx_api_url}/v3/paths/list", timeout=4)
                response.raise_for_status()
                data = response.json()
                
                new_active_streams: Dict[str, MonitoredStream] = {}
                current_mediamtx_paths = set()
                
                paths = data.get("items", [])
                for path_data in paths:
                    path = path_data.get("name")
                    if not path:
                        continue
                    current_mediamtx_paths.add(path)
                        
                    # Получаем детальную информацию о пути
                    try:
                        path_response = await client.get(f"{self.mediamtx_api_url}/v3/paths/get/{path}")
                        path_response.raise_for_status()
                        path_info = path_response.json()
                        
                        source_info = path_info.get("source", {})
                        source_type = source_info.get("type", "unknown")
                        publishers = path_info.get("publishers", [])
                        readers = path_info.get("readers", [])
                        
                        state = path_info.get("state", "notReady")
                        status = "active" if state == "ready" and source_info else "inactive"
                        
                        rtsp_url = f"rtsp://localhost:8554/{path}"
                        hls_url = f"/static/hls/{path}/stream.m3u8"
                        
                        stream = MonitoredStream(
                            path=path,
                            source_type=source_type,
                            publishers=publishers,
                            readers=readers,
                            status=status,
                            rtsp_url=rtsp_url,
                            hls_url=hls_url,
                            start_time=datetime.now() if status == "active" and path not in self._active_streams else (self._active_streams.get(path).start_time if path in self._active_streams else None),
                            last_seen=datetime.now()
                        )
                        
                        new_active_streams[path] = stream
                        
                        # Добавляем дрон в симулятор, если его нет
                        if path not in self.telemetry_simulator.drones:
                             # Попробуем загрузить последнюю позицию из БД при добавлении дрона
                             last_pos = await self.load_last_position_from_db(path)
                             if last_pos:
                                  self.telemetry_simulator.add_drone(path, last_pos.lat, last_pos.lon)
                                  logger.info(f"Loaded last position for drone {path} from DB: {last_pos.lat}, {last_pos.lon}")
                             else:
                                 self.telemetry_simulator.add_drone(path) # Используем позицию по умолчанию
                                 logger.info(f"Added new drone {path} to simulator with default position.")

                        # Если поток стал активным
                        if status == "active" and (path not in self._active_streams or self._active_streams[path].status != "active"):
                             self._stream_events.append(("stream_started", stream))
                             logger.info(f"Stream started: {path}")
                        # Если поток стал неактивным
                        elif status == "inactive" and path in self._active_streams and self._active_streams[path].status == "active":
                             self._stream_events.append(("stream_ended", stream))
                             logger.info(f"Stream ended: {path}")

                    except Exception as e:
                        logger.error(f"Error fetching details for path {path}: {e}")
                        continue
                
                # Удаляем дроны из симулятора, если они больше не активны в MediaMTX
                # Это может быть спорным решением, т.к. дрон может быть временно оффлайн.
                # Возможно, лучше не удалять, а просто помечать как неактивные и прекращать симуляцию для них.
                # Пока оставим их в симуляторе, но не будем генерировать для них телеметрию в _telemetry_loop, если их нет в _active_streams.
                
                self._active_streams = new_active_streams
                logger.debug(f"Updated {len(self._active_streams)} streams in monitor")

        except httpx.RequestError as e:
            logger.warning(f"Could not fetch streams status from MediaMTX: {e}")
            self._active_streams = {}
        except Exception as e:
            logger.error(f"Unexpected error fetching streams status: {e}")
            self._active_streams = {}

    def get_active_streams(self) -> List[MonitoredStream]:
        """Возвращает список активных потоков."""
        return list(self._active_streams.values())

    def get_telemetry(self, stream_id: str) -> Optional[Dict[str, Any]]:
        """Получает текущую телеметрию для конкретного потока."""
        return self.telemetry_data.get(stream_id)

    def get_all_telemetry(self) -> Dict[str, Dict[str, Any]]:
        """Получает текущую телеметрию всех активных потоков."""
        # Возвращаем телеметрию только для активных потоков
        return {stream_id: self.telemetry_data[stream_id] for stream_id in self._active_streams.keys() if stream_id in self.telemetry_data}

    def get_telemetry_history(self, drone_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Получает историю телеметрии для конкретного дрона."""
        history = self.telemetry_history.get(drone_id, [])
        return history[-limit:] # Возвращаем последние 'limit' записей
        
    def get_stream_events(self) -> List[tuple]:
        """Получает последние события потоков."""
        # Возвращаем события и очищаем список
        events = list(self._stream_events)
        self._stream_events.clear()
        return events

    # Метод для сохранения позиции в БД
    async def save_position_to_db(self, drone_id: str, telemetry_data: Dict[str, Any]):
        session = SessionLocal()
        try:
            # Проверяем наличие обязательных полей для позиции
            if 'latitude' not in telemetry_data or 'longitude' not in telemetry_data:
                logger.warning(f"Skipping DB save for {drone_id}: Missing latitude/longitude in telemetry.")
                return

            db_position = PositionDB(
                drone_id=drone_id,
                lat=telemetry_data['latitude'],
                lon=telemetry_data['longitude'],
                altitude=telemetry_data.get('altitude', 0.0), # Используем .get() для безопасного доступа
                speed=telemetry_data.get('speed', 0.0),
                battery=telemetry_data.get('battery', 100.0),
                signal_strength=telemetry_data.get('signal_strength', 100.0),
                timestamp=datetime.utcnow() # Используем UTC для единообразия
            )
            session.add(db_position)
            session.commit()
            # logger.debug(f"Saved position for {drone_id} to DB.") # Может быть слишком много логов
        except Exception as e:
            logger.error(f"Error saving position for {drone_id} to DB: {e}")
            session.rollback()
        finally:
            session.close()
            
    # Метод для загрузки последней позиции из БД
    async def load_last_position_from_db(self, drone_id: str) -> Optional[PositionDB]:
         session = SessionLocal()
         try:
             last_pos = session.query(PositionDB).filter(PositionDB.drone_id == drone_id).order_by(PositionDB.timestamp.desc()).first()
             return last_pos
         except Exception as e:
             logger.error(f"Error loading last position for {drone_id} from DB: {e}")
             return None
         finally:
             session.close()

# Простой симулятор телеметрии для нескольких дронов
class DroneTelemetrySimulator:
    def __init__(self):
        self.drones: Dict[str, Dict[str, Any]] = {}
        # Координаты по умолчанию (Алматы)
        self.default_lat = 43.238949
        self.default_lon = 76.889709

    def add_drone(self, drone_id: str, initial_lat: Optional[float] = None, initial_lon: Optional[float] = None):
        """Добавляет дрон в симулятор."""
        if drone_id not in self.drones:
            self.drones[drone_id] = {
                "latitude": initial_lat if initial_lat is not None else self.default_lat + (random.random() - 0.5) * 0.01,
                "longitude": initial_lon if initial_lon is not None else self.default_lon + (random.random() - 0.5) * 0.02,
                "altitude": random.uniform(10, 100),
                "speed": random.uniform(0, 15),
                "battery": random.uniform(50, 100),
                "signal_strength": random.uniform(70, 100),
                "status": "active",
                "direction": random.uniform(0, 360) # Направление движения в градусах
            }
            logger.info(f"Drone {drone_id} added to simulator.")

    def remove_drone(self, drone_id: str):
        """Удаляет дрон из симулятора."""
        if drone_id in self.drones:
            del self.drones[drone_id]
            logger.info(f"Drone {drone_id} removed from simulator.")

    def generate_telemetry(self, active_drone_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Генерирует новую порцию телеметрии для активных дронов.
           Дроны, которых нет в active_drone_ids, игнорируются или помечаются как неактивные.
        """
        updated_telemetry: Dict[str, Dict[str, Any]] = {}
        
        for drone_id in active_drone_ids:
             if drone_id in self.drones:
                 # Простая симуляция движения (случайное изменение позиции и направления)
                 current_data = self.drones[drone_id]
                 
                 # Небольшое случайное изменение направления
                 current_data["direction"] += random.uniform(-10, 10)
                 current_data["direction"] %= 360 # Ограничиваем от 0 до 360
                 
                 # Пересчитываем смещение по координатам на основе скорости и направления
                 # Предполагаем простую модель движения (не учитываем проекции и кривизну Земли для симуляции)
                 # Это очень упрощенно, для реалистичной симуляции нужна более сложная геодезия
                 speed_mps = current_data["speed"] / 3.6 # Скорость в м/с (если speed была в км/ч)
                 # Приблизительный пересчет метров в градусы (очень грубо)
                 # 1 градус широты ~ 111 км, 1 градус долготы ~ 111*cos(широта) км
                 delta_lat = speed_mps * math.cos(math.radians(current_data["direction"])) / 111000 # Приблизительно
                 delta_lon = speed_mps * math.sin(math.radians(current_data["direction"])) / (111000 * math.cos(math.radians(current_data["latitude"]))) # Приблизительно
                 
                 current_data["latitude"] += delta_lat * random.uniform(0.5, 1.5) # Случайное ускорение/замедление
                 current_data["longitude"] += delta_lon * random.uniform(0.5, 1.5)
                 
                 # Случайные изменения других параметров
                 current_data["altitude"] = max(0, current_data["altitude"] + random.uniform(-2, 2))
                 current_data["speed"] = max(0, current_data["speed"] + random.uniform(-1, 1))
                 current_data["battery"] = max(0, min(100, current_data["battery"] - random.uniform(0.01, 0.1)))
                 current_data["signal_strength"] = max(0, min(100, current_data["signal_strength"] + random.uniform(-0.5, 0.5)))
                 
                 updated_telemetry[drone_id] = current_data
             elif drone_id in self.drones: # Если дрон был, но сейчас неактивен в MediaMTX
                  # Можно пометить его как неактивный, но пока просто игнорируем в генерации телеметрии
                  pass
        
        # Для дронов, которых нет в active_drone_ids, их телеметрия не обновляется.
        # Они останутся в self.drones с последними известными данными.

        return updated_telemetry 