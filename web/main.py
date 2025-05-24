from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse
import os
from web.api import router as api_router, set_stream_monitor
from stream_monitor import StreamMonitor
from db import SessionLocal, Drone as DroneDB, Position as PositionDB

app = FastAPI()

# Монтируем статические файлы
app.mount("/static", StaticFiles(directory="static"), name="static")

# Настраиваем шаблоны
templates = Jinja2Templates(directory="templates")

# Инициализируем монитор потоков
stream_monitor = StreamMonitor()
stream_monitor.start_monitoring()

# Передаем монитор в API роутер
set_stream_monitor(stream_monitor)

# --- Автозагрузка дронов из БД ---
session = SessionLocal()
for db_drone in session.query(DroneDB).all():
    # Получаем последнюю позицию дрона
    last_pos = session.query(PositionDB).filter(PositionDB.drone_id == db_drone.id).order_by(PositionDB.timestamp.desc()).first()
    initial_position = {"lat": last_pos.lat if last_pos else 43.238949, "lon": last_pos.lon if last_pos else 76.889709}
    config = {
        "source_type": db_drone.source_type or "rtmp",
        "rtsp_url": db_drone.rtsp_url,
        "status": db_drone.status or "active",
        "readers": [],
        "bitrate": 0,
        "resolution": "1920x1080",
        "initial_position": initial_position
    }
    try:
        stream_monitor.add_drone(db_drone.id, config)
    except Exception as e:
        print(f"[WARN] Не удалось добавить дрона {db_drone.id} из БД: {e}")
session.close()

# После загрузки дронов — подтягиваем их позиции из БД в эмулятор
stream_monitor.load_positions_from_db()

# Подключаем API роутер
app.include_router(api_router)

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/analytics")
async def analytics(request: Request):
    return templates.TemplateResponse("analytics.html", {"request": request})

@app.get("/favicon.ico")
async def favicon():
    return FileResponse("static/img/favicon.ico") 