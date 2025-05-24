import asyncio
import logging
import httpx
from typing import List, Dict, Any, Optional
from datetime import datetime

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
        self._telemetry_data: Dict[str, Dict[str, Any]] = {}
        self._telemetry_history: Dict[str, List[Dict[str, Any]]] = {}
        self._stream_events: List[tuple] = []
        logger.info(f"StreamMonitor initialized with API URL: {self.mediamtx_api_url}")

    async def start(self):
        """Запускает мониторинг MediaMTX."""
        if self._is_running:
            logger.info("StreamMonitor is already running.")
            return
        logger.info("Starting StreamMonitor...")
        self._is_running = True
        # Запускаем фоновую задачу для периодического опроса MediaMTX
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("StreamMonitor started.")

    async def stop(self):
        """Останавливает мониторинг MediaMTX."""
        if not self._is_running:
            logger.info("StreamMonitor is not running.")
            return
        logger.info("Stopping StreamMonitor...")
        self._is_running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                logger.info("StreamMonitor task cancelled.")
        logger.info("StreamMonitor stopped.")

    async def _monitor_loop(self):
        """Периодически опрашивает MediaMTX API."""
        while self._is_running:
            try:
                await self._fetch_streams_status()
            except Exception as e:
                logger.error(f"Error in StreamMonitor loop: {e}")
            await asyncio.sleep(5) # Опрашиваем каждые 5 секунд

    async def _fetch_streams_status(self):
        """Получает актуальный список потоков из MediaMTX API."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.mediamtx_api_url}/v3/paths/list", timeout=4)
                response.raise_for_status()
                data = response.json()
                
                # Обновляем словарь активных потоков
                new_active_streams: Dict[str, MonitoredStream] = {}
                
                # Получаем все пути из MediaMTX
                paths = data.get("items", [])
                for path_data in paths:
                    path = path_data.get("name")
                    if not path:
                        continue
                        
                    # Получаем детальную информацию о пути
                    try:
                        path_response = await client.get(f"{self.mediamtx_api_url}/v3/paths/get/{path}")
                        path_response.raise_for_status()
                        path_info = path_response.json()
                        
                        source_info = path_info.get("source", {})
                        source_type = source_info.get("type", "unknown")
                        publishers = path_info.get("publishers", [])
                        readers = path_info.get("readers", [])
                        
                        # Определяем статус на основе наличия источника и состояния
                        state = path_info.get("state", "notReady")
                        status = "active" if state == "ready" and source_info else "inactive"
                        
                        rtsp_url = f"rtsp://localhost:8554/{path}"
                        hls_url = f"/static/hls/{path}/stream.m3u8"
                        
                        # Создаем или обновляем информацию о потоке
                        stream = MonitoredStream(
                            path=path,
                            source_type=source_type,
                            publishers=publishers,
                            readers=readers,
                            status=status,
                            rtsp_url=rtsp_url,
                            hls_url=hls_url,
                            start_time=datetime.now() if status == "active" else None,
                            last_seen=datetime.now()
                        )
                        
                        new_active_streams[path] = stream
                        
                        # Если поток активен, но его нет в текущих потоках, добавляем событие
                        if status == "active" and path not in self._active_streams:
                            self._stream_events.append(("stream_started", stream))
                            
                    except Exception as e:
                        logger.error(f"Error fetching details for path {path}: {e}")
                        continue
                
                # Обновляем внутреннее состояние
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

    # --- Заглушки для других методов, которые могут использоваться ---
    # В зависимости от того, как эти методы использовались, возможно, потребуется их реализация.
    # Сейчас они просто возвращают пустые данные или None.

    def get_telemetry(self, stream_id: str) -> Optional[Dict[str, Any]]:
        """Получает телеметрию для конкретного потока (заглушка)."""
        # logger.debug(f"Getting telemetry for {stream_id}")
        return self._telemetry_data.get(stream_id)

    def get_all_telemetry(self) -> Dict[str, Dict[str, Any]]:
        """Получает телеметрию всех потоков (заглушка)."""
        # logger.debug("Getting all telemetry")
        return self._telemetry_data # Сейчас пустой словарь

    def get_telemetry_history(self, drone_id: str, limit: int) -> List[Dict[str, Any]]:
        """Получает историю телеметрии (заглушка)."""
        # logger.debug(f"Getting telemetry history for {drone_id}, limit {limit}")
        return self._telemetry_history.get(drone_id, []) # Сейчас пустой список
        
    def get_stream_events(self) -> List[tuple]:
        """Получает последние события потоков (заглушка)."""
        # logger.debug("Getting stream events")
        return self._stream_events # Сейчас пустой список

    # Метод add_drone был в web/api.py и, вероятно, должен остаться там или быть пересмотрен.
    # Если он нужен здесь для какой-то внутренней логики монитора, его нужно добавить. 