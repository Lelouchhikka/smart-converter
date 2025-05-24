import subprocess
import logging
import yaml
from pathlib import Path
from typing import Optional
from stream_monitor import StreamMonitor
from datetime import datetime

logger = logging.getLogger('MediaMTXManager')

class MediaMTXManager:
    def __init__(self, path: str = 'mediamtx.exe', config: str = 'mediamtx.minimal.yml'):
        self.path = Path(path)
        self.config = Path(config)
        self.process: Optional[subprocess.Popen] = None
        self.api_url = "http://localhost:9997"
        self.monitor = StreamMonitor()
        self._validate_environment()

    def _validate_environment(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"MediaMTX не найден по пути: {self.path}")
        if not self.config.exists():
            raise FileNotFoundError(f"Конфигурационный файл не найден: {self.config}")
        try:
            with open(self.config, 'r') as f:
                yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"Ошибка в конфигурационном файле: {str(e)}")
        self._check_ports()

    def _check_ports(self) -> None:
        import socket
        ports_to_check = {
            1935: "RTMP",
            8554: "RTSP",
            9997: "API"
        }
        for port, service in ports_to_check.items():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(('localhost', port))
                sock.close()
            except socket.error:
                logger.warning(f"Порт {port} ({service}) уже занят. Убедитесь, что нет других экземпляров MediaMTX.")

    def start(self) -> bool:
        try:
            logger.info(f"Запуск MediaMTX из {self.path}")
            cmd = [str(self.path), str(self.config)]
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            def log_reader():
                for line in self.process.stdout:
                    logger.debug(f"MediaMTX: {line.strip()}")
            import threading
            threading.Thread(target=log_reader, daemon=True).start()
            for attempt in range(15):
                if self._check_server_ready():
                    logger.info("MediaMTX успешно запущен и готов к работе")
                    self.monitor.start_monitoring()
                    return True
                logger.debug(f"Ожидание запуска MediaMTX... (попытка {attempt + 1}/15)")
                import time
                time.sleep(1)
            logger.error("MediaMTX не запустился в течение ожидаемого времени")
            return False
        except Exception as e:
            logger.error(f"Ошибка при запуске MediaMTX: {str(e)}")
            return False

    def _check_server_ready(self) -> bool:
        import requests
        try:
            endpoints = [
                "/v3/config/get",
                "/v3/paths/list",
                "/v3/rtmpsessions/list"
            ]
            for endpoint in endpoints:
                try:
                    response = requests.get(f"{self.api_url}{endpoint}", timeout=1)
                    if response.status_code == 200:
                        return True
                except requests.RequestException:
                    continue
            return False
        except Exception as e:
            logger.debug(f"Ошибка при проверке готовности сервера: {str(e)}")
            return False

    def stop(self) -> None:
        if self.process:
            logger.info("Останавливаю MediaMTX...")
            self.monitor.stop_monitoring()
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
                logger.info("MediaMTX успешно остановлен")
            except subprocess.TimeoutExpired:
                logger.warning("Принудительное завершение MediaMTX...")
                self.process.kill()
                logger.info("MediaMTX принудительно остановлен")

    def get_active_streams(self):
        return self.monitor.get_active_streams()

    def print_active_streams(self) -> None:
        streams = self.get_active_streams()
        if not streams:
            print("Активных потоков нет")
            return
        print("\nАктивные потоки:")
        for i, stream in enumerate(streams, 1):
            uptime = ""
            if stream.start_time:
                uptime = f" (работает {datetime.now() - stream.start_time})"
            print(f"\n{i}. Путь: {stream.path}")
            print(f"   Источник: {stream.source_type} ({stream.publishers} publisher(s), {stream.readers} reader(s)){uptime}")
            print(f"   RTSP URL: {stream.rtsp_url}")
            if stream.status != "active":
                print(f"   Статус: {stream.status}")

    def print_logs(self) -> None:
        if self.process:
            for line in self.process.stdout:
                print("[MediaMTX]", line.strip())

    def add_drone(self, drone_id: str, config: dict):
        """Добавляет новый дрон в мониторинг"""
        if drone_id in self.monitor.active_streams:
            raise ValueError(f"Дрон с ID {drone_id} уже существует")
        
        self.monitor.active_streams[drone_id] = {
            "rtmp_url": config["rtmp_url"],
            "rtsp_url": config["rtsp_url"],
            "status": config["status"]
        }
        
        self.monitor.telemetry_data[drone_id] = config["telemetry"]
        
        # Добавляем начальное событие
        self.monitor._last_events.append({
            "type": "stream_created",
            "stream": drone_id,
            "timestamp": datetime.now().isoformat()
        }) 