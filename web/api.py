from fastapi import APIRouter, HTTPException, UploadFile, File, WebSocket, status
from typing import Dict, List, Optional
from datetime import datetime
import json
import requests # Keep for synchronous health_check, consider replacing with httpx if all else is async
import os
# import logging # Remove standard logging
from pydantic import BaseModel
from db import SessionLocal, Drone, Position as PositionDB
import aiofiles
import subprocess
import asyncio
import httpx
import aiohttp
from loguru import logger # Import loguru

# Настройка логгера (loguru typically requires minimal setup for basic use,
# it configures a default stderr handler. For file logging or advanced config,
# you'd add logger.add(...) here)
# logger = logging.getLogger('APIRouter') # Remove this line

router = APIRouter()

# Глобальная переменная для доступа к монитору потоков
_stream_monitor = None

# Словарь для отслеживания процессов FFmpeg по stream_key
ffmpeg_processes: Dict[str, subprocess.Popen] = {}

class StreamData(BaseModel):
    stream_key: str
    rtmp_url: str
    description: Optional[str] = None

class StreamResponse(BaseModel):
    stream_key: str
    rtmp_url: str
    rtsp_url: str
    rtsp_converted_url: str
    hls_url: str
    status: str
    description: Optional[str] = None

class DroneData(BaseModel):
    id: str
    rtmp_url: str
    rtsp_url: str
    initial_position: Dict[str, float]

def set_stream_monitor(monitor):
    global _stream_monitor
    _stream_monitor = monitor

@router.get("/streams_status")
async def get_streams_status():
    """Получает список всех активных потоков напрямую из MediaMTX"""
    if not _stream_monitor:
        raise HTTPException(status_code=500, detail="Stream monitor not initialized")
    streams = _stream_monitor.get_active_streams()
    return [{
        "path": stream.path,
        "source_type": stream.source_type,
        "publishers": stream.publishers,
        "readers": stream.readers,
        "rtsp_url": stream.rtsp_url,
        "hls_url": stream.hls_url,
        "start_time": stream.start_time.isoformat() if stream.start_time else None,
        "last_seen": stream.last_seen.isoformat() if stream.last_seen else None,
        "status": stream.status
    } for stream in streams]

@router.get("/telemetry/{stream_id}")
async def get_stream_telemetry(stream_id: str):
    """Получает телеметрию для конкретного потока"""
    if not _stream_monitor:
        raise HTTPException(status_code=500, detail="Stream monitor not initialized")
    telemetry = _stream_monitor.get_telemetry(stream_id)
    if not telemetry:
        raise HTTPException(status_code=404, detail="Stream not found")
    return telemetry

@router.get("/telemetry")
async def get_telemetry():
    """Получает телеметрию всех потоков"""
    if not _stream_monitor:
        raise HTTPException(status_code=500, detail="Stream monitor not initialized")
    return _stream_monitor.get_all_telemetry()

@router.get("/analytics/stats")
async def get_analytics_stats():
    """Получает общую статистику по всем дронам"""
    if not _stream_monitor:
        raise HTTPException(status_code=500, detail="Stream monitor not initialized")
    
    telemetry = _stream_monitor.get_all_telemetry()
    drones = list(telemetry.values())
    
    if not drones:
        return {
            "active_drones": 0,
            "avg_altitude": 0,
            "avg_speed": 0,
            "avg_battery": 0,
            "avg_signal": 0
        }
    
    active_drones_count = len([d for d in drones if d.get("status") == "active"])
    # Avoid division by zero if no drones, though handled by the 'if not drones' block.
    # Also, ensure all drones have the keys before summing.
    
    total_altitude = sum(d.get("altitude", 0) for d in drones)
    total_speed = sum(d.get("speed", 0) for d in drones)
    total_battery = sum(d.get("battery", 0) for d in drones)
    total_signal = sum(d.get("signal_strength", 0) for d in drones)
    
    num_drones_for_avg = len(drones) if len(drones) > 0 else 1 # Avoid division by zero

    return {
        "active_drones": active_drones_count,
        "avg_altitude": total_altitude / num_drones_for_avg,
        "avg_speed": total_speed / num_drones_for_avg,
        "avg_battery": total_battery / num_drones_for_avg,
        "avg_signal": total_signal / num_drones_for_avg
    }

@router.get("/analytics/history/{drone_id}")
async def get_drone_history(drone_id: str, limit: int = 100):
    """Получает историю телеметрии для конкретного дрона"""
    if not _stream_monitor:
        raise HTTPException(status_code=500, detail="Stream monitor not initialized")
    
    history = _stream_monitor.get_telemetry_history(drone_id, limit)
    if not history:
        raise HTTPException(status_code=404, detail="Drone history not found")
    
    return history

@router.get("/analytics/trajectories")
async def get_trajectories():
    """Получает траектории всех дронов"""
    if not _stream_monitor:
        raise HTTPException(status_code=500, detail="Stream monitor not initialized")
    
    telemetry = _stream_monitor.get_all_telemetry()
    trajectories = {}
    
    for drone_id, data in telemetry.items():
        if "latitude" in data and "longitude" in data:
            if drone_id not in trajectories:
                trajectories[drone_id] = []
            trajectories[drone_id].append({
                "lat": data["latitude"],
                "lon": data["longitude"],
                "timestamp": data.get("timestamp", datetime.now().isoformat())
            })
    
    return trajectories

@router.post("/streams")
async def add_stream(stream_key: str, source_type: str, file_path: Optional[str] = None):
    """Добавляет новый поток в MediaMTX."""
    try:
        stream_key_safe = stream_key.replace('/', '_')
        
        hls_dir = os.path.join("static", "hls", stream_key_safe)
        os.makedirs(hls_dir, exist_ok=True)
        
        stream_config = {
            "source": "publisher",
            "rtspTransport": "tcp",
            "rtspAnyPort": True,
        }

        mediamtx_path_url = f"http://localhost:9997/v3/config/paths/get/{stream_key_safe}"
        async with aiohttp.ClientSession() as session:
            async with session.get(mediamtx_path_url) as response:
                if response.status == 200:
                    logger.info(f"Path {stream_key_safe} already exists in MediaMTX, patching configuration.")
                    async with session.patch(
                        f"http://localhost:9997/v3/config/paths/patch/{stream_key_safe}",
                        json=stream_config
                    ) as patch_response:
                        if patch_response.status != 200:
                            error_text = await patch_response.text()
                            logger.error(f"Failed to patch stream configuration {stream_key}: {error_text}")
                            raise HTTPException(
                                status_code=500,
                                detail=f"Failed to patch stream configuration: {error_text}"
                            )
                        logger.info(f"Successfully patched stream configuration {stream_key_safe}")

                elif response.status == 404:
                    logger.info(f"Path {stream_key_safe} not found in MediaMTX, adding configuration.")
                    async with session.post(
                        f"http://localhost:9997/v3/config/paths/add/{stream_key_safe}",
                        json=stream_config
                    ) as add_response:
                        if add_response.status != 200:
                            error_text = await add_response.text()
                            logger.error(f"Failed to add stream configuration {stream_key}: {error_text}")
                            raise HTTPException(
                                status_code=500,
                                detail=f"Failed to add stream configuration: {error_text}"
                            )
                        logger.info(f"Successfully added stream configuration {stream_key_safe}")
                else:
                    error_text = await response.text()
                    logger.error(f"Unexpected status from MediaMTX API for {stream_key}: {response.status}, {error_text}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Unexpected status from MediaMTX API: {response.status}, {error_text}"
                    )
        return {"status": "success", "message": "Stream configured successfully"}

    except Exception as e:
        logger.exception(f"Error configuring stream {stream_key}") # loguru automatically captures traceback
        raise HTTPException(
            status_code=500,
            detail=f"Error configuring stream: {str(e)}"
        )

@router.delete("/streams/{stream_key}")
async def delete_stream(stream_key: str):
    """Удаляет поток"""
    if not _stream_monitor:
        raise HTTPException(status_code=500, detail="Stream monitor not initialized")
    try:
        escaped_stream_key = stream_key.replace('/', '_')
        mediamtx_url = f"http://localhost:9997/v3/paths/delete/{escaped_stream_key}"
        async with httpx.AsyncClient() as client:
            response = await client.delete(mediamtx_url)
            response.raise_for_status()

        await delete_stream_from_db(stream_key)

        return {"message": f"Stream {stream_key} deleted successfully from MediaMTX and DB"}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.warning(f"Stream {stream_key} not found in MediaMTX, deleting from DB only.")
            await delete_stream_from_db(stream_key)
            return {"message": f"Stream {stream_key} not found in MediaMTX, deleted from DB."}
        else:
            logger.error(f"Error deleting stream {stream_key} from MediaMTX: {e}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Error deleting stream from MediaMTX: {e}")
    except httpx.RequestError as e:
        logger.error(f"Error connecting to MediaMTX API while deleting {stream_key}: {e}")
        await delete_stream_from_db(stream_key) # Attempt DB delete even if MediaMTX fails
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Could not connect to MediaMTX API, but deleted from DB: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error deleting stream {stream_key}")
        raise HTTPException(status_code=500, detail=f"Unexpected error deleting stream: {e}")


@router.get("/streams/{stream_key}")
async def get_stream(stream_key: str):
    """Получает информацию о конкретном потоке из БД"""
    all_streams = await get_streams_from_db()
    for stream_data in all_streams:
        if stream_data.get("stream_key") == stream_key:
             # Optionally, fetch live status from MediaMTX
             # try:
             #     mediamtx_url = f"http://localhost:9997/v3/paths/get/{stream_key.replace('/', '_')}"
             #     async with httpx.AsyncClient() as client:
             #         response = await client.get(mediamtx_url)
             #         response.raise_for_status()
             #     mediamtx_status = response.json().get("state", "inactive") # or "status"
             #     stream_data["status"] = mediamtx_status
             # except (httpx.HTTPStatusError, httpx.RequestError) as e:
             #     logger.warning(f"Could not get status from MediaMTX for {stream_key}: {e}")
             #     stream_data["status"] = "unknown"
             return stream_data

    raise HTTPException(status_code=404, detail="Stream not found in DB")

@router.get("/streams", response_model=List[StreamResponse])
async def list_streams() -> List[Dict]:
    """Получает список всех потоков из БД и их текущий статус"""
    logger.info("Received request for /api/streams")
    try:
        streams_from_db = await get_streams_from_db()
        logger.info(f"Retrieved {len(streams_from_db)} streams from DB.")
        
        streams_with_status = []
        active_mediamtx_streams = _stream_monitor.get_active_streams() if _stream_monitor else []
        logger.info(f"Retrieved {len(active_mediamtx_streams)} active streams from MediaMTX monitor.")
        active_mediamtx_streams_dict = {stream.path: stream for stream in active_mediamtx_streams}

        for stream_db in streams_from_db:
            stream_key = stream_db.get("stream_key")
            if not stream_key:
                logger.warning(f"Stream object from DB missing stream_key: {stream_db}")
                continue 

            stream_key_safe = stream_key.replace('/', '_')
            status = "inactive"
            mediamtx_data = active_mediamtx_streams_dict.get(stream_key_safe)
            hls_url = None
            rtsp_url = None # Will be taken from DB or MediaMTX

            if mediamtx_data:
                status = mediamtx_data.status
                rtsp_url = mediamtx_data.rtsp_url # Prefer live RTSP URL from MediaMTX
                # HLS URL is typically constructed based on stream key and MediaMTX config
                hls_url = f"/static/hls/{stream_key_safe}/stream.m3u8" 
            else:
                # If not active in MediaMTX, use HLS URL from DB (if stored) or construct it
                hls_url = stream_db.get("hls_url") or f"/static/hls/{stream_key_safe}/stream.m3u8"


            stream_data_for_response = {
                "stream_key": stream_key,
                "rtmp_url": stream_db.get("rtmp_url", ""),
                # Use MediaMTX RTSP if available, otherwise fallback to DB RTSP
                "rtsp_url": rtsp_url or stream_db.get("rtsp_url", ""),
                "rtsp_converted_url": rtsp_url or stream_db.get("rtsp_url", ""), # Assuming same for now
                "hls_url": hls_url,
                "status": status,
                "description": stream_db.get("description", ""),
            }
            streams_with_status.append(stream_data_for_response)
            logger.debug(f"Processed stream {stream_key}, status: {status}, HLS: {hls_url}")

        logger.info(f"Returning {len(streams_with_status)} streams to client.")
        return streams_with_status

    except Exception as e:
        logger.exception("Error in list_streams") # loguru automatically captures traceback
        raise HTTPException(
            status_code=500,
            detail=f"Internal Server Error while listing streams: {str(e)}"
        )

@router.get("/health")
async def health_check():
    """Проверка работоспособности сервера"""
    if not _stream_monitor:
        logger.warning("Health check failed: Stream monitor not initialized")
        raise HTTPException(status_code=503, detail="Service Unavailable: Stream monitor not initialized")

    try:
        # Use httpx for async request if possible, or keep requests for simplicity if this is the only sync one.
        # For consistency, let's use httpx as other parts of the code do.
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{_stream_monitor.api_url}/v3/config/paths/list")
            response.raise_for_status()
        logger.info("Health check: MediaMTX API is responsive.")
        return {"status": "healthy"}
    except httpx.RequestError as e:
        logger.error(f"Health check failed: MediaMTX API request error: {e}")
        return {"status": "unhealthy", "detail": f"MediaMTX API request error: {e}"}
    except httpx.HTTPStatusError as e:
        logger.error(f"Health check failed: MediaMTX API returned status {e.response.status_code}: {e.response.text}")
        return {"status": "unhealthy", "detail": f"MediaMTX API error: status {e.response.status_code}"}
    except Exception as e:
        logger.exception("Health check failed: Unexpected error")
        return {"status": "unhealthy", "detail": f"Unexpected error: {str(e)}"}


@router.get("/events")
async def get_events():
    """Получает последние события потоков"""
    if not _stream_monitor:
        raise HTTPException(status_code=500, detail="Stream monitor not initialized")
    events = _stream_monitor.get_stream_events()
    return [{
        "type": event_type,
        "stream": {
            "path": stream.path,
            "status": stream.status,
            "publishers": stream.publishers,
            "readers": stream.readers
        },
        "timestamp": datetime.now().isoformat()
    } for event_type, stream in events]

@router.post("/drones")
async def add_drone(drone: DroneData):
    """Добавляет новый дрон в систему и сохраняет в БД"""
    session = SessionLocal()
    try:
        db_drone = Drone(
            id=drone.id,
            rtmp_url=drone.rtmp_url,
            rtsp_url=drone.rtsp_url,
            status="active", # Initial status
            source_type="rtmp" # Assuming drone implies RTMP source for video
        )
        session.merge(db_drone) # Use merge to update if exists, or insert if new
        session.commit()
        
        pos = drone.initial_position
        db_position = PositionDB(
            drone_id=drone.id,
            lat=pos["lat"],
            lon=pos["lon"],
            altitude=0.0, # Default initial values
            speed=0.0,
            battery=100.0,
            signal_strength=100.0,
            timestamp=datetime.utcnow() # Add timestamp
        )
        session.add(db_position)
        session.commit()

        drones_dir = "config/drones"
        os.makedirs(drones_dir, exist_ok=True)
        
        drone_config = {
            "id": drone.id,
            "rtmp_url": drone.rtmp_url,
            "rtsp_url": drone.rtsp_url,
            "initial_position": drone.initial_position,
            "status": "active",
            "source_type": "rtmp",
            "telemetry": {
                "altitude": 0.0,
                "speed": 0.0,
                "battery": 100.0,
                "signal_strength": 100.0,
                "latitude": drone.initial_position["lat"],
                "longitude": drone.initial_position["lon"]
            }
        }
        config_path = os.path.join(drones_dir, f"{drone.id}.json")
        with open(config_path, "w") as f:
            json.dump(drone_config, f, indent=4)
        
        if _stream_monitor:
            try:
                _stream_monitor.add_drone(drone.id, drone_config)
                logger.info(f"Drone {drone.id} added to stream monitor.")
            except Exception as e:
                logger.error(f"Error adding drone {drone.id} to stream monitor: {str(e)}")
                # Decide if this should be a critical error for the endpoint
                # raise HTTPException(status_code=500, detail=f"Error adding drone to monitor: {str(e)}")

        # Configure MediaMTX path for the drone
        # This assumes the drone itself will publish to rtmp://mediamtx_host/drone.id
        # If a conversion is needed (e.g., from RTSP to RTMP for MediaMTX, that's a different setup)
        mediamtx_drone_path_config = {
            "source": "publisher" # MediaMTX expects a publisher for this path
        }
        # Using httpx for consistency
        async with httpx.AsyncClient() as client:
            # Check if path exists
            check_url = f"{_stream_monitor.api_url}/v3/config/paths/get/{drone.id}"
            response = await client.get(check_url)
            
            if response.status_code == 404: # Path does not exist, add it
                add_url = f"{_stream_monitor.api_url}/v3/config/paths/add/{drone.id}"
                response_add = await client.post(add_url, json=mediamtx_drone_path_config)
                response_add.raise_for_status()
                logger.info(f"MediaMTX path configured for drone {drone.id}.")
            elif response.status_code == 200: # Path exists, patch it (optional, could also skip)
                patch_url = f"{_stream_monitor.api_url}/v3/config/paths/patch/{drone.id}"
                response_patch = await client.patch(patch_url, json=mediamtx_drone_path_config)
                response_patch.raise_for_status()
                logger.info(f"MediaMTX path updated for drone {drone.id}.")
            else:
                response.raise_for_status() # Raise for other unexpected statuses


        # The 'runOnReady' for conversion seems specific to an incoming RTSP source that MediaMTX pulls.
        # If the drone publishes RTMP directly, MediaMTX handles HLS/RTSP conversion automatically.
        # The example `runOnReady` seemed to consume from `rtsp://localhost:8554/{drone.id}`
        # and republish to `rtsp://localhost:8554/{drone.id}_converted`. This might be if MediaMTX
        # itself is the source of the first RTSP stream.
        # If the drone is the source, this conversion step might be different or not needed here.
        # Assuming the drone publishes RTMP to `rtmp://mediamtx/{drone.id}` as per `drone.rtmp_url` logic.

        logger.info(f"Drone {drone.id} successfully added and configured.")
        return {"status": "success", "message": f"Дрон {drone.id} успешно добавлен"}
    
    except httpx.HTTPStatusError as e:
        logger.error(f"MediaMTX API error during drone add ({drone.id}): {e.response.status_code} - {e.response.text}")
        raise HTTPException(status_code=500, detail=f"MediaMTX API error: {e.response.text}")
    except Exception as e:
        logger.exception(f"Error adding drone {drone.id}")
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.post("/upload_video/")
async def upload_video(file: UploadFile = File(...)):
    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)
    file_location = os.path.join(upload_dir, file.filename)
    try:
        async with aiofiles.open(file_location, "wb") as out_file:
            while content := await file.read(1024 * 1024): # Read in 1MB chunks
                await out_file.write(content)
        logger.info(f"File uploaded successfully: {file_location}")
        return {"file_path": file_location}
    except Exception as e:
        logger.exception(f"Error uploading file {file.filename}")
        raise HTTPException(status_code=500, detail=f"Could not upload file: {e}")


@router.websocket("/ws/{stream_key}")
async def websocket_endpoint(websocket: WebSocket, stream_key: str):
    await websocket.accept()
    logger.info(f"WebSocket connection established for stream {stream_key}")
    monitor_task = None
    ffmpeg_process = None
    
    try:
        data = await websocket.receive_json()
        logger.info(f"WS {stream_key}: Received initial data: {data}")
        source_type = data.get("sourceType", "file")
        file_path = data.get("filePath")
        loop_file = data.get("loopFile", True)
        
        stream_key_safe = stream_key.replace('/', '_')
        
        hls_dir = os.path.join("static", "hls", stream_key_safe)
        os.makedirs(hls_dir, exist_ok=True)

        # Save stream info to DB, explicitly passing file_path and loop_file
        await save_stream_to_db(
            stream_key=stream_key,
            source_type=source_type,
            file_path=file_path,
            loop_file=loop_file
        )

        ffmpeg_command = ["ffmpeg", "-hide_banner"]

        if source_type == "file":
            logger.info(f"WS {stream_key}: Checking file_path before validation: {file_path}")
            if not file_path:
                logger.error(f"WS {stream_key}: No file path provided for file source type")
                await websocket.send_json({"status": "error", "message": "Error: No file path provided."})
                return
            
            if not os.path.exists(file_path):
                logger.error(f"WS {stream_key}: File not found: {file_path}")
                await websocket.send_json({"status": "error", "message": f"Error: File not found at {file_path}."})
                await delete_stream_from_db(stream_key)
                return
                
            if loop_file:
                ffmpeg_command += ["-stream_loop", "-1"]
            ffmpeg_command += ["-re", "-i", file_path, "-copyts"]

        else:
            logger.warning(f"WS {stream_key}: Invalid or unsupported source type '{source_type}'.")
            await websocket.send_json({"status": "error", "message": "Error: Invalid or unsupported source type."})
            await delete_stream_from_db(stream_key)
            return

        rtmp_url = f"rtmp://localhost:1935/{stream_key_safe}"
        ffmpeg_command += [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-maxrate", "1000k",
            "-bufsize", "2000k",
            "-g", "50",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-ar", "44100",
            "-b:a", "128k",
            "-f", "flv",
            rtmp_url
        ]

        logger.info(f"WS {stream_key}: FFmpeg command: {' '.join(ffmpeg_command)}")
        
        try:
            ffmpeg_process = await asyncio.create_subprocess_exec(
                *ffmpeg_command,
                stdin=subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd='.'
            )
            ffmpeg_processes[stream_key] = ffmpeg_process
            logger.info(f"WS {stream_key}: FFmpeg process started with PID: {ffmpeg_process.pid}")
            await websocket.send_json({"status": "pending", "message": "FFmpeg запущен..."})

        except FileNotFoundError:
            logger.error(f"WS {stream_key}: FFmpeg executable not found.")
            await websocket.send_json({"status": "error", "message": "Ошибка: FFmpeg не найден."})
            await delete_stream_from_db(stream_key)
            return
        except Exception as e:
            logger.error(f"WS {stream_key}: Error starting FFmpeg: {e}")
            await websocket.send_json({"status": "error", "message": f"Ошибка запуска FFmpeg: {e}"})
            await delete_stream_from_db(stream_key)
            return

        async def monitor_ffmpeg_output(p: asyncio.subprocess.Process, ws: WebSocket, sk: str):
            is_active_sent = False
            try:
                async for line_bytes in p.stderr:
                    line = line_bytes.decode('utf-8', errors='ignore').strip()
                    if line:
                        logger.trace(f"FFmpeg [{sk}]: {line}")
                        if "frame=" in line and not is_active_sent:
                            await ws.send_json({
                                "status": "active",
                                "message": "Трансляция активна"
                            })
                            is_active_sent = True
            except asyncio.CancelledError:
                logger.info(f"FFmpeg output monitoring for {sk} cancelled.")
            except Exception as e_mon:
                logger.error(f"Error monitoring FFmpeg output for {sk}: {e_mon}")
                if ws.client_state != 4:
                    try:
                        await ws.send_json({"status": "error", "message": f"Ошибка мониторинга FFmpeg: {e_mon}"})
                    except: pass
            finally:
                logger.info(f"FFmpeg output monitoring stopped for {sk}")

        monitor_task = asyncio.create_task(monitor_ffmpeg_output(ffmpeg_process, websocket, stream_key))

        while ffmpeg_process.returncode is None:
            try:
                await asyncio.sleep(1)
            except asyncio.TimeoutError:
                pass
            except Exception as ws_rx_err:
                logger.info(f"WS {stream_key}: WebSocket receive error or closed by client: {ws_rx_err}")
                break

        await ffmpeg_process.wait()
        return_code = ffmpeg_process.returncode
        logger.info(f"WS {stream_key}: FFmpeg process finished with return code: {return_code}")

        # Attempt to read stderr if process failed
        if return_code != 0 and ffmpeg_process.stderr:
            try:
                stderr_output = await asyncio.wait_for(ffmpeg_process.stderr.read(), timeout=5.0)
                logger.error(f"WS {stream_key}: FFmpeg process exited with error code {return_code}. Stderr:\n{stderr_output.decode(errors='ignore')}")
            except asyncio.TimeoutError:
                logger.error(f"WS {stream_key}: FFmpeg process exited with error code {return_code}, but failed to read stderr within timeout.")
            except Exception as e_read_err:
                logger.error(f"WS {stream_key}: Error reading FFmpeg stderr after process exit: {e_read_err}")

        if return_code != 0 and return_code is not None:
            if websocket.client_state != 4:
                await websocket.send_json({"status": "error", "message": f"FFmpeg завершился с ошибкой: {return_code}"})
        else:
            if websocket.client_state != 4:
                await websocket.send_json({"status": "completed", "message": "Трансляция завершена."})


    except json.JSONDecodeError:
        logger.warning(f"WS {stream_key}: Invalid JSON received from client.")
        if websocket.client_state != 4:
            await websocket.send_json({"status": "error", "message": "Invalid JSON format."})
    except Exception as e:
        logger.exception(f"WebSocket error for {stream_key}")
        if websocket.client_state != 4:
            try:
                await websocket.send_json({"status": "error", "message": f"Критическая ошибка: {e}"})
            except RuntimeError:
                pass
    finally:
        logger.info(f"WS {stream_key}: Cleaning up...")
        # Ensure monitor task is cancelled if it's still running
        if monitor_task and not monitor_task.done():
            logger.info(f"WS {stream_key}: Cancelling monitor task.")
            monitor_task.cancel()
            try:
                # Wait for the monitor task to finish cancellation
                await asyncio.gather(monitor_task, return_exceptions=True)
            except Exception as e_task_cancel:
                logger.error(f"WS {stream_key}: Error during monitor task cleanup: {e_task_cancel}")

        # Only attempt to terminate/kill if ffmpeg_process was successfully started
        # and it's still running
        if ffmpeg_process and stream_key in ffmpeg_processes:
            # Check if the process is still running before trying to terminate
            # Use process.returncode is None for asyncio subprocesses
            if ffmpeg_process.returncode is None:
                 logger.warning(f"WS {stream_key}: FFmpeg process {ffmpeg_process.pid} still running. Terminating.")
                 try:
                     # Remove from dict BEFORE terminating to prevent issues if terminate is slow
                     process_to_kill = ffmpeg_processes.pop(stream_key, None)
                     if process_to_kill:
                         process_to_kill.terminate()
                         await asyncio.wait_for(process_to_kill.wait(), timeout=5.0)
                         logger.info(f"WS {stream_key}: FFmpeg process {process_to_kill.pid} terminated.")
                 except asyncio.TimeoutError:
                     logger.error(f"WS {stream_key}: FFmpeg process {process_to_kill.pid} did not terminate in time. Killing.")
                     # Check again if the process is still running before killing
                     if process_to_kill and process_to_kill.returncode is None:
                         process_to_kill.kill()
                         await process_to_kill.wait()
                         logger.info(f"WS {stream_key}: FFmpeg process {process_to_kill.pid} killed.")
                 except Exception as e_kill:
                     logger.error(f"WS {stream_key}: Error terminating FFmpeg process {stream_key}: {e_kill}")
            else:
                # Process already exited, just remove from dict if present
                 ffmpeg_processes.pop(stream_key, None)
                 logger.info(f"WS {stream_key}: FFmpeg process {ffmpeg_process.pid} already exited with code {ffmpeg_process.returncode}.")


        if websocket.client_state != 4:
            try:
                logger.info(f"WS {stream_key}: Closing WebSocket connection.")
                await websocket.close()
            except RuntimeError as e_ws_close:
                logger.warning(f"WS {stream_key}: WebSocket connection already closed or error on close: {e_ws_close}")
            except Exception as e_ws_close_generic:
                logger.error(f"WS {stream_key}: Unexpected error closing WebSocket: {e_ws_close_generic}")

        logger.info(f"WS {stream_key}: WebSocket connection handler finished.")


async def save_stream_to_db(stream_key: str, source_type: str, file_path: str, loop_file: bool):
    stream_key_safe = stream_key.replace('/', '_')
    logger.info(f"Saving stream data to DB: key={stream_key}, source_type={source_type}, file_path={file_path}, loop_file={loop_file}")
    session = SessionLocal()
    try:
        # Check if stream already exists
        existing_stream = session.query(Drone).filter(Drone.id == stream_key).first()
        if existing_stream:
            logger.info(f"Updating existing stream {stream_key} in DB.")
            existing_stream.source_type = source_type
            existing_stream.file_path = file_path
            existing_stream.loop_file = loop_file
            existing_stream.status = "pending"
            existing_stream.rtmp_url = f"rtmp://localhost:1935/{stream_key_safe}"
            existing_stream.rtsp_url = f"rtsp://localhost:8554/{stream_key_safe}"
        else:
            logger.info(f"Adding new stream {stream_key} to DB.")
            db_stream = Drone(
                id=stream_key,
                rtmp_url=f"rtmp://localhost:1935/{stream_key_safe}",
                rtsp_url=f"rtsp://localhost:8554/{stream_key_safe}",
                source_type=source_type,
                file_path=file_path,
                loop_file=loop_file,
                status="pending"
            )
            session.add(db_stream)
        session.commit()
        logger.info(f"Stream {stream_key} saved/updated in DB.")
    except Exception as e:
        logger.exception(f"Error saving stream {stream_key} to DB")
        session.rollback()
    finally:
        session.close()

async def get_streams_from_db() -> List[Dict]:
    logger.info("Getting streams from DB")
    session = SessionLocal()
    streams_list = []
    try:
        streams = session.query(Drone).all()
        streams_list = [
            {
                "stream_key": stream.id,
                "rtmp_url": stream.rtmp_url,
                "rtsp_url": stream.rtsp_url,
                "source_type": stream.source_type,
                "status": stream.status,
                "hls_url": f"/static/hls/{stream.id.replace('/', '_')}/stream.m3u8",
                "description": getattr(stream, 'description', None),
                "file_path": stream.file_path,
                "loop_file": stream.loop_file
            }
            for stream in streams
        ]
        logger.info(f"Retrieved {len(streams_list)} streams from DB.")
    except Exception as e:
        logger.exception("Error getting streams from DB")
    finally:
        session.close()
    return streams_list

async def delete_stream_from_db(stream_key: str):
    logger.info(f"Deleting stream {stream_key} from DB")
    session = SessionLocal()
    try:
        stream_to_delete = session.query(Drone).filter(Drone.id == stream_key).first()
        if stream_to_delete:
            session.delete(stream_to_delete)
            session.commit()
            logger.info(f"Stream {stream_key} deleted from DB.")
        else:
            logger.warning(f"Stream {stream_key} not found in DB for deletion.")
    except Exception as e:
        logger.exception(f"Error deleting stream {stream_key} from DB")
        session.rollback()
    finally:
        session.close()


# This function seems redundant with the WebSocket logic, but kept if used elsewhere.
# If only used by WebSocket, it's better integrated there.
async def _start_ffmpeg_publication_process(
    stream_key: str,
    source_type: str,
    file_path: Optional[str] = None,
    loop_file: bool = False
):
    """Запускает процесс FFmpeg для публикации потока."""
    logger.info(f"Attempting to start FFmpeg process for {stream_key} via _start_ffmpeg_publication_process")
    ffmpeg_command = ["ffmpeg", "-hide_banner"]
    stream_key_safe = stream_key.replace('/', '_')

    if source_type == "camera":
        # ... (same as in websocket_endpoint)
        if os.name == 'nt':
            ffmpeg_command += ["-f", "dshow", "-i", "video=Integrated Camera"]
        else:
            ffmpeg_command += ["-f", "v4l2", "-i", "/dev/video0"]
    elif source_type == "screen":
        # ... (same as in websocket_endpoint)
        if os.name == 'nt':
            ffmpeg_command += ["-f", "gdigrab", "-framerate", "30", "-i", "desktop"]
        else:
            ffmpeg_command += ["-f", "x11grab", "-framerate", "30", "-i", ":0.0"]
    elif source_type == "file":
        if not file_path or not os.path.exists(file_path):
             logger.error(f"File path not provided or file not found for stream {stream_key}: {file_path}")
             return 
        if loop_file:
            ffmpeg_command += ["-stream_loop", "-1"]
        ffmpeg_command += ["-re", "-i", file_path, "-copyts"]
    else:
        logger.error(f"Invalid source type '{source_type}' for stream {stream_key}")
        return

    rtmp_url = f"rtmp://localhost:1935/{stream_key_safe}"
    ffmpeg_command += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-maxrate", "1000k",
        "-bufsize", "2000k",
        "-g", "50",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "44100",
        "-b:a", "128k",
        "-f", "flv",
        rtmp_url
    ]

    logger.info(f"Starting FFmpeg (from _start_ffmpeg_publication_process) for {stream_key}: {' '.join(ffmpeg_command)}")
    try:
        # Use asyncio.create_subprocess_exec for async context
        process = await asyncio.create_subprocess_exec(
            *ffmpeg_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        ffmpeg_processes[stream_key] = process # This global dict might need careful management if this func is used widely
        logger.info(f"FFmpeg process started with PID: {process.pid} for {stream_key} (from _start_ffmpeg_publication_process)")

        async def monitor_output(p: asyncio.subprocess.Process, sk: str):
            try:
                async for line_bytes in p.stderr:
                    line = line_bytes.decode('utf-8', errors='ignore').strip()
                    if line:
                        logger.trace(f"FFmpeg (monitored) [{sk}]: {line}")
                
                rc = await p.wait()
                logger.info(f"FFmpeg (monitored) [{sk}] exited with code {rc}")

            except Exception as e_mon:
                logger.error(f"Error monitoring FFmpeg output for {sk} (from _start_ffmpeg_publication_process): {e_mon}")
            finally:
                logger.info(f"FFmpeg output monitoring stopped for {sk} (from _start_ffmpeg_publication_process)")

        asyncio.create_task(monitor_output(process, stream_key))
        return process # Return the process object

    except FileNotFoundError:
        logger.error(f"FFmpeg executable not found for {stream_key}. Make sure FFmpeg is installed and in PATH.")
    except Exception as e:
        logger.exception(f"Error starting FFmpeg process for {stream_key} (from _start_ffmpeg_publication_process)")
    return None # Return None on failure