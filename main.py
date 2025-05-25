import logging
import signal
import sys
from fastapi import FastAPI, Request
import uvicorn
from mediamtx_manager import MediaMTXManager
from web.api import router as api_router, set_stream_monitor, get_streams_from_db, _start_ffmpeg_publication_process, ffmpeg_processes
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import os
import asyncio
from web.stream_monitor import StreamMonitor
import subprocess
from db import SessionLocal, Drone
import httpx
from loguru import logger

def main() -> None:
    mtx = MediaMTXManager()
    set_stream_monitor(mtx.monitor)

    def signal_handler(sig, frame):
        mtx.stop()
        sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        if not mtx.start():
            print("Не удалось запустить MediaMTX")
            sys.exit(1)

        app = FastAPI(title="RTMP-RTSP Монитор")
        app.include_router(api_router, prefix="/api")
        
        if not os.path.exists('templates'):
            os.makedirs('templates')
        if not os.path.exists('static'):
            os.makedirs('static')
        app.mount("/static", StaticFiles(directory="static"), name="static")
        templates = Jinja2Templates(directory="templates")
        
        @app.get("/")
        async def index(request: Request):
            return templates.TemplateResponse("index.html", {"request": request})
        
        print("Запуск веб-интерфейса на http://localhost:8000")

        stream_monitor = StreamMonitor()

        @app.on_event("startup")
        async def startup_event():
            print("Приложение запускается...")
            set_stream_monitor(stream_monitor)
            await stream_monitor.start()
            
            print("Загрузка существующих потоков из БД и запуск FFmpeg...\n")
            streams = await get_streams_from_db()
            print(f"Найдено {len(streams)} потоков в БД.")
            
            auth = ("admin", "admin")

            for stream in streams:
                stream_key = stream.get("stream_key")
                source_type = stream.get("source_type")
                file_path = stream.get("file_path")
                loop_file = stream.get("loop_file", False)

                if not stream_key or not source_type:
                    print(f"Пропущен поток из БД с неполными данными: {stream}")
                    continue

                try:
                    stream_key_safe = stream_key.replace('/', '_')
                    stream_config = {
                        "name": stream_key_safe,
                        "source": "publisher"
                    }

                    async with httpx.AsyncClient() as client:
                        check_url = f"http://localhost:9997/v3/config/paths/get/{stream_key_safe}"
                        
                        try:
                            response = await client.get(check_url, auth=auth)
                            response.raise_for_status()
                            
                            patch_url = f"http://localhost:9997/v3/config/paths/patch/{stream_key_safe}"
                            response = await client.patch(patch_url, json=stream_config, auth=auth)
                            response.raise_for_status()
                            print(f"Обновлена конфигурация MediaMTX для потока {stream_key}")

                        except httpx.HTTPStatusError as e:
                            if e.response.status_code == 404:
                                add_url = f"http://localhost:9997/v3/config/paths/add/{stream_key_safe}"
                                response = await client.post(add_url, json=stream_config, auth=auth)
                                if response.status_code == 400:
                                    print(f"Ошибка 400 при добавлении пути {stream_key_safe}: {response.text}")
                                response.raise_for_status()
                                print(f"Добавлен новый путь в MediaMTX для потока {stream_key}")
                            else:
                                print(f"HTTP ошибка {e.response.status_code}: {e.response.text}")
                                raise

                    session = SessionLocal()
                    try:
                        db_stream = session.query(Drone).filter(Drone.id == stream_key).first()
                        if db_stream:
                            db_stream.status = "active"
                            session.commit()
                            print(f"Обновлен статус потока {stream_key} на 'active' в БД")
                    except Exception as e:
                        print(f"Ошибка обновления статуса в БД для потока {stream_key}: {e}")
                    finally:
                        session.close()

                    if source_type in ["camera", "screen", "file"]:
                        if source_type == "file" and not file_path:
                            print(f"Пропуск запуска FFmpeg для потока {stream_key}: отсутствует путь к файлу")
                            continue
                            
                        asyncio.create_task(_start_ffmpeg_publication_process(
                            stream_key, 
                            source_type, 
                            file_path, 
                            loop_file
                        ))
                        print(f"Запущен FFmpeg для потока {stream_key} ({source_type})")
                    else:
                        print(f"Неизвестный source_type '{source_type}' для потока {stream_key}")

                except Exception as e:
                    print(f"Ошибка при восстановлении потока {stream_key}: {e}")
                    continue

            print("Восстановление потоков при старте завершено.")

        @app.on_event("shutdown")
        async def shutdown_event():
            print("Приложение останавливается...")
            await stream_monitor.stop()
            print(f"Остановка {len(ffmpeg_processes)} запущенных процессов FFmpeg...")
            for stream_key, process in list(ffmpeg_processes.items()):
                if process.poll() is None:
                    print(f"Остановка процесса FFmpeg для потока {stream_key} (PID: {process.pid})")
                    try:
                        process.terminate()
                        await asyncio.to_thread(process.wait, timeout=5)
                    except subprocess.TimeoutExpired:
                        print(f"Принудительная остановка процесса FFmpeg для потока {stream_key}")
                        process.kill()
                    except Exception as e:
                        print(f"Ошибка при остановке процесса FFmpeg для потока {stream_key}: {e}")
                del ffmpeg_processes[stream_key]
            print("Все процессы FFmpeg остановлены.")

        uvicorn.run(app, host="0.0.0.0", port=8000)

    except Exception as e:
        print(f"Критическая ошибка: {str(e)}")
        sys.exit(1)
    finally:
        mtx.stop()

if __name__ == "__main__":
    main() 