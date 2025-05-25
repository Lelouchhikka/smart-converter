from dataclasses import dataclass
from typing import Optional, Dict, List
from datetime import datetime
import threading
from queue import Queue
import requests
import logging
from drone_telemetry import DroneTelemetrySimulator
from db import SessionLocal, Position as PositionDB

logger = logging.getLogger('MediaMTXManager')

@dataclass
class StreamInfo:
    path: str
    source_type: str
    publishers: int
    readers: int
    rtsp_url: str
    hls_url: str = ""
    start_time: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    status: str = "active"
    bitrate: Optional[int] = None
    resolution: Optional[str] = None

class StreamMonitor:
    def __init__(self, api_url: str = "http://localhost:9997"):
        self.api_url = api_url
        self.streams: Dict[str, StreamInfo] = {}
        self.event_queue = Queue()
        self._stop_event = threading.Event()
        self.telemetry_history: Dict[str, List[Dict]] = {}
        self.max_history_size = 1000
        self.telemetry_simulator = DroneTelemetrySimulator()
        self.telemetry_simulator.start_simulation()
        
    def start_monitoring(self, interval: int = 5):
        self._stop_event.clear()
        threading.Thread(target=self._monitor_loop, args=(interval,), daemon=True).start()
        
    def stop_monitoring(self):
        self._stop_event.set()
        self.telemetry_simulator.stop_simulation()
        
    def _monitor_loop(self, interval: int):
        while not self._stop_event.is_set():
            try:
                self._update_streams()
                threading.Event().wait(interval)
            except Exception as e:
                logger.error(f"Ошибка при мониторинге потоков: {str(e)}")
                
    def _update_streams(self):
        try:
            response = requests.get(f"{self.api_url}/v3/paths/list")
            if response.status_code != 200:
                logger.error(f"Ошибка API: {response.status_code}")
                return

            data = response.json()
            current_streams = set()
            if not isinstance(data, dict) or 'items' not in data:
                logger.error(f"Неожиданный формат данных API: {data}")
                return
            for path_info in data['items']:
                if not isinstance(path_info, dict):
                    continue
                path_name = path_info.get('name')
                if not path_name:
                    continue
                current_streams.add(path_name)
                source = path_info.get('source', {})
                hls_url = f"http://localhost:8888/{path_name}/index.m3u8"
                if path_name not in self.streams:
                    stream = StreamInfo(
                        path=path_name,
                        source_type=source.get('type', 'unknown') if source else 'unknown',
                        publishers=1 if source else 0,
                        readers=len(path_info.get('readers', [])),
                        rtsp_url=f"rtsp://localhost:8554/{path_name}",
                        hls_url=hls_url,
                        start_time=datetime.now(),
                        last_seen=datetime.now()
                    )
                    self.streams[path_name] = stream
                    self.telemetry_simulator.add_drone(path_name)
                    self.event_queue.put(("stream_started", stream))
                else:
                    stream = self.streams[path_name]
                    old_publishers = stream.publishers
                    stream.publishers = 1 if source else 0
                    stream.readers = len(path_info.get('readers', []))
                    stream.last_seen = datetime.now()
                    stream.hls_url = hls_url
                    if old_publishers > 0 and stream.publishers == 0:
                        self.event_queue.put(("stream_ended", stream))
                    elif old_publishers == 0 and stream.publishers > 0:
                        self.event_queue.put(("stream_restarted", stream))

        except Exception as e:
            logger.error(f"Ошибка при обновлении потоков: {str(e)}")
            logger.debug(f"Ответ API: {response.text if 'response' in locals() else 'Нет ответа'}")

    def get_active_streams(self) -> List[StreamInfo]:
        return list(self.streams.values())
        
    def get_stream_events(self) -> List[tuple]:
        events = []
        while not self.event_queue.empty():
            events.append(self.event_queue.get())
        return events
        
    def get_telemetry(self, stream_id: str) -> Optional[dict]:
        return self.telemetry_simulator.get_telemetry(stream_id)
        
    def get_all_telemetry(self) -> Dict[str, dict]:
        return self.telemetry_simulator.get_all_telemetry()

    def add_drone(self, drone_id: str, config: dict):
        try:
            if drone_id in self.streams:
                raise ValueError(f"Дрон с ID {drone_id} уже существует")
            required_fields = ["source_type", "rtsp_url", "status"]
            missing_fields = [field for field in required_fields if field not in config]
            if missing_fields:
                raise ValueError(f"Отсутствуют обязательные поля: {', '.join(missing_fields)}")
            self.streams[drone_id] = StreamInfo(
                path=drone_id,
                source_type=config["source_type"],
                publishers=1 if config["source_type"] == "rtmp" else 0,
                readers=len(config.get('readers', [])),
                rtsp_url=config["rtsp_url"],
                hls_url=config.get("hls_url", ""),
                start_time=datetime.now(),
                last_seen=datetime.now(),
                status=config["status"],
                bitrate=config.get("bitrate"),
                resolution=config.get("resolution")
            )
            initial_lat = config.get("initial_position", {}).get("lat", 43.238949)
            initial_lon = config.get("initial_position", {}).get("lon", 76.889709)
            self.telemetry_simulator.add_drone(drone_id, initial_lat, initial_lon)
            self.event_queue.put(("stream_created", self.streams[drone_id]))
            self.telemetry_history[drone_id] = []
            
            try:
                response = requests.post(
                    f"{self.api_url}/v3/config/paths/patch/{drone_id}",
                    json={
                        "source": "rtmp",
                        "runOnPublish": f"ffmpeg -i rtsp://localhost:8554/{drone_id} -c copy -f rtsp rtsp://localhost:8554/{drone_id}_converted",
                        "runOnPublishRestart": True
                    }
                )
                if response.status_code != 200:
                    logger.warning(f"Не удалось настроить конвертер для дрона {drone_id}: {response.text}")
            except Exception as e:
                logger.warning(f"Ошибка при настройке конвертера для дрона {drone_id}: {str(e)}")
            
        except Exception as e:
            logger.error(f"Ошибка при добавлении дрона {drone_id}: {str(e)}")
            raise

    def get_telemetry_history(self, drone_id: str, limit: int = 100) -> List[Dict]:
        if drone_id not in self.telemetry_history:
            return []
        return self.telemetry_history[drone_id][-limit:]

    def load_positions_from_db(self):
        session = SessionLocal()
        for drone_id in self.streams:
            last_pos = session.query(PositionDB).filter(PositionDB.drone_id == drone_id).order_by(PositionDB.timestamp.desc()).first()
            if last_pos:
                if drone_id not in self.telemetry_simulator.drones:
                    self.telemetry_simulator.add_drone(drone_id, last_pos.lat, last_pos.lon)
                self.telemetry_simulator.drones[drone_id].latitude = last_pos.lat
                self.telemetry_simulator.drones[drone_id].longitude = last_pos.lon
                self.telemetry_simulator.drones[drone_id].altitude = last_pos.altitude
                self.telemetry_simulator.drones[drone_id].speed = last_pos.speed
                self.telemetry_simulator.drones[drone_id].battery = last_pos.battery
                self.telemetry_simulator.drones[drone_id].signal_strength = last_pos.signal_strength
        session.close()

    def save_position_to_db(self, drone_id, telemetry):
        session = SessionLocal()
        db_position = PositionDB(
            drone_id=drone_id,
            lat=telemetry['latitude'],
            lon=telemetry['longitude'],
            altitude=telemetry['altitude'],
            speed=telemetry['speed'],
            battery=telemetry['battery'],
            signal_strength=telemetry['signal_strength']
        )
        session.add(db_position)
        session.commit()
        session.close()

    def update_telemetry(self, telemetry: Dict[str, Dict]):
        self.telemetry_simulator.update_telemetry(telemetry)
        for stream_id, data in telemetry.items():
            if stream_id in self.streams:
                stream = self.streams[stream_id]
                stream.bitrate = data.get("bitrate")
                stream.resolution = data.get("resolution")
                stream.status = data.get("status", "active")
                stream.last_seen = datetime.now()
                
                if stream_id not in self.telemetry_history:
                    self.telemetry_history[stream_id] = []
                self.telemetry_history[stream_id].append({
                    **data,
                    'timestamp': datetime.now().isoformat()
                })
                
                if len(self.telemetry_history[stream_id]) > self.max_history_size:
                    self.telemetry_history[stream_id] = self.telemetry_history[stream_id][-self.max_history_size:]
                
                if stream.status == "active":
                    self.event_queue.put(("stream_started", stream))
                else:
                    self.event_queue.put(("stream_ended", stream))
                
                self.save_position_to_db(stream_id, data)

    def update_events(self, events):
        self.event_queue = Queue()
        for event in events:
            self.event_queue.put(event) 