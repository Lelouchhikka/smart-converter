from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime
from web.templates import HTML_TEMPLATE, STREAMS_VIEW_TEMPLATE
import os
import math
import requests

router = APIRouter()
templates = Jinja2Templates(directory="templates")

def set_mtx(mtx_instance):
    global mtx
    mtx = mtx_instance

telemetry_angle = 0

@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@router.get("/view", response_class=HTMLResponse)
async def view_streams():
    return STREAMS_VIEW_TEMPLATE

@router.get("/api/streams")
async def get_streams():
    streams = mtx.get_active_streams()
    return [
        {
            "path": stream.path,
            "source_type": stream.source_type,
            "publishers": stream.publishers,
            "readers": stream.readers,
            "rtsp_url": stream.rtsp_url,
            "hls_url": stream.hls_url,
            "uptime": str(datetime.now() - stream.start_time) if stream.start_time else "N/A",
            "is_new": (datetime.now() - stream.start_time).total_seconds() < 30 if stream.start_time else False
        }
        for stream in streams
    ]

@router.get("/api/events")
async def get_events():
    try:
        if not os.path.exists("event_log.txt"):
            return []
        with open("event_log.txt", "r", encoding="utf-8") as f:
            lines = f.readlines()[-10:]
        return [line.strip() for line in lines]
    except Exception:
        return []

@router.get("/api/telemetry")
async def get_telemetry():
    # Эмуляция движения по кругу
    global telemetry_angle
    telemetry_angle = (telemetry_angle + 5) % 360
    center_lat, center_lon = 55.75, 37.61
    radius = 0.01  # ~1 км
    angle_rad = math.radians(telemetry_angle)
    lat = center_lat + radius * math.cos(angle_rad)
    lon = center_lon + radius * math.sin(angle_rad)
    return {
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "alt": 120 + 10 * math.sin(angle_rad),
        "speed": 15,
        "heading": telemetry_angle,
    }

@router.get("/api/health")
async def health():
    try:
        r = requests.get("http://localhost:9997/v3/paths/list", timeout=1)
        return {"status": "ok" if r.status_code == 200 else "fail"}
    except Exception:
        return {"status": "fail"} 