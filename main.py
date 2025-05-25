import logging
import signal
import sys
from fastapi import FastAPI, Request
import uvicorn
from mediamtx_manager import MediaMTXManager
from web.api import router as api_router, set_stream_monitor, get_streams_from_db, _start_ffmpeg_publication_process, ffmpeg_processes, add_stream
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import os
import asyncio
import threading
from web.stream_monitor import StreamMonitor
import subprocess
from sqlalchemy.orm import Session
from db import SessionLocal, Drone
import httpx
from loguru import logger

def print_instructions() -> None:
    print("\n=== Инструкция ===")
    print("1. Настройка RTMP-трансляции в OBS Studio:")
    print("   - Сервер: rtmp://localhost:1935/live")
    print("   - Ключ потока: mystream")
    print("2. Запустите трансляцию в OBS")
    print("3. Для просмотра RTSP-потока:")
    print("   - VLC: rtsp://localhost:8554/live/mystream")
    print("   - ffplay: ffplay rtsp://localhost:8554/live/mystream")
    print("\nДля просмотра активных потоков используйте --status")
    print("Для запуска веб-интерфейса используйте --web")
    print("Для завершения работы нажмите Ctrl+C")

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description='Управление MediaMTX сервером')
    parser.add_argument('--status', action='store_true', help='Показать активные потоки')
    parser.add_argument('--debug', action='store_true', help='Включить отладочное логирование')
    parser.add_argument('--web', action='store_true', help='Запустить веб-интерфейс')
    args = parser.parse_args()

    if args.debug:
        pass

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
        if args.web:
            app = FastAPI(title="RTMP-RTSP Монитор")
            
            # Подключаем API роутер с префиксом /api
            app.include_router(api_router, prefix="/api")
            
            # Подключаем папки шаблонов и статики
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

            # Инициализация StreamMonitor
            stream_monitor = StreamMonitor()

            @app.on_event("startup")
            async def startup_event():
                print("Приложение запускается...")
                set_stream_monitor(stream_monitor)
                await stream_monitor.start()
                
                # Запускаем процессы FFmpeg для существующих потоков при старте
                print("Загрузка существующих потоков из БД и запуск FFmpeg...\n")
                streams = await get_streams_from_db()
                print(f"Найдено {len(streams)} потоков в БД.")
                
                # Добавляем аутентификацию для MediaMTX API
                auth = ("admin", "admin")

                for stream in streams:
                    stream_key = stream.get("stream_key")
                    source_type = stream.get("source_type")
                    file_path = stream.get("file_path")
                    loop_file = stream.get("loop_file", False)

                    if not stream_key or not source_type:
                        print(f"Пропущен поток из БД с неполными данными: {stream}")
                        continue

                    # Обновляем конфигурацию потока в MediaMTX при старте
                    try:
                        stream_key_safe = stream_key.replace('/', '_')
                        
                        # Минимальная конфигурация для MediaMTX: только источник publisher
                        # Временно убираем секцию outputs
                        stream_config = {
                            "name": stream_key_safe, # Добавляем имя пути в тело запроса
                            "source": "publisher", # Источник входящего RTMP потока
                            # outputs секция временно удалена
                            # Опционально можно добавить keepAlive: true для publisher source
                            # "keepAlive": True 
                        }

                        # Проверяем существование пути в MediaMTX с аутентификацией
                        async with httpx.AsyncClient() as client:
                            check_url = f"http://localhost:9997/v3/config/paths/get/{stream_key_safe}"
                            
                            try:
                                response = await client.get(check_url, auth=auth)
                                response.raise_for_status() # Проверка статуса ответа
                                
                                # Путь существует, обновляем конфигурацию с аутентификацией
                                patch_url = f"http://localhost:9997/v3/config/paths/patch/{stream_key_safe}"
                                response = await client.patch(patch_url, json=stream_config, auth=auth)
                                response.raise_for_status()
                                print(f"Обновлена конфигурация MediaMTX для потока {stream_key}")

                            except httpx.HTTPStatusError as e:
                                if e.response.status_code == 404:
                                    # Путь не существует, создаем новый с аутентификацией
                                    add_url = f"http://localhost:9997/v3/config/paths/add/{stream_key_safe}"
                                    response = await client.post(add_url, json=stream_config, auth=auth)
                                    # Логируем тело ответа при 400 ошибке
                                    if response.status_code == 400:
                                        print(f"Получена ошибка 400 при добавлении пути {stream_key_safe} в MediaMTX. Тело ответа: {response.text}")
                                    response.raise_for_status()
                                    print(f"Добавлен новый путь в MediaMTX для потока {stream_key}")
                                else:
                                    # Другие ошибки HTTP статуса
                                    # Логируем тело ответа для других ошибок HTTP статуса
                                    print(f"Получена HTTP ошибка {e.response.status_code} при восстановлении потока {stream_key_safe}. Тело ответа: {e.response.text}")
                                    raise e # Переподнимаем ошибку для общего обработчика

                        # Обновляем статус в БД - эту логику можно убрать или изменить.
                        # Статус "active" должен устанавливаться монитором потоков, когда он видит publisher.
                        # Пока оставим, но имейте в виду, что это может быть не совсем точная логика статуса.
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

                        # Запускаем FFmpeg процесс
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
                # Останавливаем все запущенные процессы FFmpeg при остановке приложения
                print(f"Остановка {len(ffmpeg_processes)} запущенных процессов FFmpeg...")
                for stream_key, process in list(ffmpeg_processes.items()):
                    if process.poll() is None: # Проверяем, запущен ли еще процесс
                        print(f"Остановка процесса FFmpeg для потока {stream_key} (PID: {process.pid})")
                        try:
                            process.terminate() # Попытка мягкого завершения
                            await asyncio.to_thread(process.wait, timeout=5) # Ждем завершения до 5 секунд
                        except subprocess.TimeoutExpired:
                            print(f"Процесс FFmpeg для потока {stream_key} (PID: {process.pid}) не завершился мягко, принудительная остановка.")
                            process.kill() # Принудительная остановка
                        except Exception as e:
                            print(f"Ошибка при остановке процесса FFmpeg для потока {stream_key}: {e}")
                    del ffmpeg_processes[stream_key] # Удаляем из словаря
                print("Все процессы FFmpeg остановлены.")

            uvicorn.run(app, host="0.0.0.0", port=8000)
        elif args.status:
            mtx.print_active_streams()
        else:
            print_instructions()
        mtx.print_logs()
    except Exception as e:
        print(f"Критическая ошибка: {str(e)}")
        sys.exit(1)
    finally:
        mtx.stop()

if __name__ == "__main__":
    main() 