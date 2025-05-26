from fastapi import FastAPI, WebSocket, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse 
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import json
import os
from typing import Dict, Any, List
from app_api_handler import AppApiHandler
from realtime_handler import RealTimeHandler
from datetime import datetime, timezone, timedelta
import pytz
import queue  
import threading  
import time 
import base64

KST = pytz.timezone('Asia/Seoul')

def get_korea_time():
    """한국 시간을 반환"""
    utc_now = datetime.now(timezone.utc)
    korea_offset = timedelta(hours=9)
    korea_tz = timezone(korea_offset)
    return utc_now.astimezone(korea_tz)

try:
    from redis_manager import RedisManager
    from esp32_handler import ESP32Handler
    from websocket_manager import WebSocketManager
    from image_handler import ImageHandler
    MODULES_AVAILABLE = True
    print("✅ 모든 모듈 import 성공")
except ImportError as e:
    print(f"⚠️ 모듈 import 실패: {e}")
    print("📝 기본 모드로 실행됩니다")
    MODULES_AVAILABLE = False

# FastAPI 앱 초기화
app = FastAPI(
    title="Baby Monitor Server",
    description="ESP32-CAM과 모바일 앱을 연결하는 중계 서버",
    version="2.0.0"
)

# 🔥 수정: 매니저들을 한 번만 초기화
if MODULES_AVAILABLE:
    try:
        # Redis 연결 재시도 로직 추가
        print("🔍 Redis 연결 시도...")
        redis_manager = RedisManager()
        
        # Redis 연결 실패 시 재시도 (Railway 일시적 문제 대응)
        if not redis_manager.available:
            print("⚠️ Redis 첫 연결 실패 - 3초 후 재시도...")
            import time
            time.sleep(3)
            redis_manager = RedisManager()  # 재시도
            
        if not redis_manager.available:
            print("⚠️ Redis 연결 실패 - fallback 모드로 실행")
        
        websocket_manager = WebSocketManager()
        esp32_handler = ESP32Handler(redis_manager, websocket_manager)
        image_handler = ImageHandler()
        realtime_handler = RealTimeHandler(redis_manager, websocket_manager)
        redis_manager = RedisManager()
        
        # 🔥 MJPEG 매니저 초기화
        mjpeg_manager = MJPEGStreamManager()
        
        print("🍼 Baby Monitor Server 시작")
        print("🎥 MJPEG 스트리밍 준비 완료")
        
        print("🍼 Baby Monitor Server 시작")
        print(f"📊 Redis: {'연결됨' if redis_manager.available else '연결 안됨'}")
        print("💓 실시간 하트비트 시작")
        print("💓 실시간 시간 동기화 활성화")
        
    except Exception as e:
        print(f"❌ 모듈 초기화 오류: {e}")
        MODULES_AVAILABLE = False
        
if not MODULES_AVAILABLE:
    # 기본 모드 - 모듈 없이 실행
    class DummyManager:
        def __init__(self):
            self.available = False
            self.active_connections = []
            self.esp32_status = "disconnected"
            self.esp32_ip = None
    
    class DummyMJPEGManager:
        def __init__(self):
            self.stream_stats = {"viewers": 0, "frame_count": 0, "esp_eye_connected": False}
        def get_stats(self):
            return self.stream_stats
        def broadcast_frame(self, data):
            pass
        def add_viewer(self):
            return None
        def remove_viewer(self, q):
            pass
    
    mjpeg_manager = DummyMJPEGManager()
    redis_manager = DummyManager()
    websocket_manager = DummyManager()
    esp32_handler = DummyManager()
    image_handler = DummyManager()
    realtime_handler = None
    
    print("🍼 Baby Monitor Server 시작 (기본 모드)")

# 🔥 MJPEG 스트리밍 매니저 클래스 추가
class MJPEGStreamManager:
    def __init__(self):
        self.active_streams: List[queue.Queue] = []
        self.latest_frame: bytes = None
        self.stream_stats = {
            "viewers": 0,
            "frame_count": 0,
            "last_frame_time": None,
            "esp_eye_connected": False
        }
        self.lock = threading.Lock()
        print("🎥 MJPEG 스트리밍 매니저 초기화")
    
    def add_viewer(self) -> queue.Queue:
        """새로운 시청자 추가"""
        with self.lock:
            frame_queue = queue.Queue(maxsize=10)
            self.active_streams.append(frame_queue)
            self.stream_stats["viewers"] = len(self.active_streams)
            print(f"🔗 새 시청자 연결됨 (총 {self.stream_stats['viewers']}명)")
            
            # 최신 프레임이 있으면 즉시 전송
            if self.latest_frame:
                try:
                    mjpeg_frame = self._create_mjpeg_frame(self.latest_frame)
                    frame_queue.put_nowait(mjpeg_frame)
                except queue.Full:
                    pass
                    
            return frame_queue
    
    def remove_viewer(self, frame_queue: queue.Queue):
        """시청자 제거"""
        with self.lock:
            if frame_queue in self.active_streams:
                self.active_streams.remove(frame_queue)
                self.stream_stats["viewers"] = len(self.active_streams)
                print(f"❌ 시청자 연결 해제됨 (총 {self.stream_stats['viewers']}명)")
    
    def _create_mjpeg_frame(self, frame_data: bytes) -> bytes:
        """🔥 올바른 MJPEG 프레임 형식 생성 (Bytes 오류 수정)"""
        # 🔥 수정: 모든 부분을 bytes로 처리
        boundary = b"--frame\r\n"
        content_type = b"Content-Type: image/jpeg\r\n"
        content_length = f"Content-Length: {len(frame_data)}\r\n\r\n".encode('utf-8')
        frame_end = b"\r\n"
        
        # bytes 객체들만 연결
        mjpeg_frame = boundary + content_type + content_length + frame_data + frame_end
        return mjpeg_frame
    
    def broadcast_frame(self, frame_data: bytes):
        """모든 시청자에게 프레임 브로드캐스트"""
        if not frame_data or len(frame_data) < 100:
            return
            
        with self.lock:
            # 최신 프레임 저장
            self.latest_frame = frame_data
            self.stream_stats["frame_count"] += 1
            self.stream_stats["last_frame_time"] = time.time()
            self.stream_stats["esp_eye_connected"] = True
            
            # MJPEG 프레임 생성
            mjpeg_frame = self._create_mjpeg_frame(frame_data)
            
            # 모든 활성 스트림에 전송
            dead_queues = []
            for frame_queue in self.active_streams[:]:
                try:
                    # 큐가 가득 찬 경우 오래된 프레임 제거
                    while frame_queue.qsize() >= frame_queue.maxsize - 1:
                        try:
                            frame_queue.get_nowait()
                        except queue.Empty:
                            break
                    
                    frame_queue.put_nowait(mjpeg_frame)
                except Exception as e:
                    print(f"⚠️ 큐 오류: {e}")
                    dead_queues.append(frame_queue)
            
            # 죽은 큐 정리
            for dead_queue in dead_queues:
                self.remove_viewer(dead_queue)
            
            # 로깅 (30프레임마다)
            if self.stream_stats["frame_count"] % 30 == 0:
                print(f"📺 프레임 {self.stream_stats['frame_count']} 브로드캐스트 "
                      f"(시청자: {len(self.active_streams)}, 크기: {len(frame_data)} bytes)")
    
    def get_stats(self) -> Dict[str, Any]:
        """스트리밍 통계 반환"""
        current_time = time.time()
        return {
            **self.stream_stats,
            "last_frame_age": current_time - self.stream_stats["last_frame_time"] if self.stream_stats["last_frame_time"] else None,
            "has_latest_frame": self.latest_frame is not None
        }

# 🔥 전역 MJPEG 매니저 인스턴스
mjpeg_manager = MJPEGStreamManager()

# 🔥 수정: 통합된 ESP32 데이터 엔드포인트 (중복 제거)
@app.post("/esp32/data")
async def receive_esp32_data(request: Request, data: Dict[str, Any]):
    """ESP32에서 통합 데이터 수신 (센서 + 이미지 혼합 가능)"""
    if not MODULES_AVAILABLE:
        return {
            "status": "fallback_mode",
            "message": "Modules not available - running in basic mode",
            "data_received": {
                "size": len(str(data)),
                "has_image": "image" in data,
                "has_sensor": any(key in data for key in ["temperature", "humidity", "movement", "sound"])
            }
        }
    
    try:
        client_ip = request.client.host
        
        # 데이터 타입 판별
        has_image = "image" in data and data["image"]
        has_sensor = any(key in data for key in ["temperature", "humidity", "movement", "sound"])
        
        print(f"📡 ESP32 데이터 수신 from {client_ip}: 이미지={has_image}, 센서={has_sensor}")
        
        results = []
        
        # 이미지 데이터 처리
        if has_image:
            print(f"👁️ 이미지 데이터 처리 중... ({len(data.get('image', ''))} bytes)")
            try:
                image_result = await esp32_handler.handle_esp_eye_data(data, client_ip)
                results.append({"type": "image", "result": image_result})
            except Exception as img_error:
                print(f"❌ 이미지 처리 오류: {img_error}")
                results.append({"type": "image", "result": {"error": str(img_error)}})
        
        # 센서 데이터 처리
        if has_sensor:
            print(f"📊 센서 데이터 처리 중...")
            try:
                sensor_result = await esp32_handler.handle_esp32_data(data, client_ip)
                results.append({"type": "sensor", "result": sensor_result})
            except Exception as sensor_error:
                print(f"❌ 센서 처리 오류: {sensor_error}")
                results.append({"type": "sensor", "result": {"error": str(sensor_error)}})
        
        # 둘 다 없으면 기본 처리
        if not has_image and not has_sensor:
            try:
                default_result = await esp32_handler.handle_esp32_data(data, client_ip)
                results.append({"type": "default", "result": default_result})
            except Exception as default_error:
                print(f"❌ 기본 처리 오류: {default_error}")
                results.append({"type": "default", "result": {"error": str(default_error)}})
        
        # 🔥 실시간 브로드캐스트 (WebSocket)
        if websocket_manager.active_connections:
            try:
                broadcast_data = {
                    "type": "new_data",
                    "source": "esp32",
                    "data": data,
                    "client_ip": client_ip,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                await websocket_manager.broadcast_to_apps(broadcast_data)
                print(f"📡 {len(websocket_manager.active_connections)}개 앱에 브로드캐스트 완료")
            except Exception as broadcast_error:
                print(f"⚠️ 브로드캐스트 오류: {broadcast_error}")
        
        return {
            "status": "success",
            "message": "ESP32 데이터 처리 완료",
            "received_from": client_ip,
            "processed_types": [r["type"] for r in results],
            "results": results,
            "broadcast_sent": len(websocket_manager.active_connections) if hasattr(websocket_manager, 'active_connections') else 0,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        print(f"❌ ESP32 데이터 수신 총 오류: {e}")
        raise HTTPException(status_code=500, detail=f"ESP32 data processing failed: {str(e)}")

# 🔥 수정: 개별 엔드포인트들 (호환성 유지)
@app.post("/esp32/sensor")
async def receive_esp32_sensor_data(request: Request, data: Dict[str, Any]):
    # print(f"📡 센서 전용 엔드포인트 호출 - /esp32/data로 리다이렉트")
    # return await receive_esp32_data(request, data)  # ❌ 이 줄 주석 처리
    
    # ✅ 올바른 처리
    if not MODULES_AVAILABLE:
        return {"error": "Modules not available", "data_received": data}
    
    try:
        client_ip = request.client.host
        print(f"📡 ESP32 센서 데이터 수신 from {client_ip}: {data}")
        
        result = await esp32_handler.handle_esp32_data(data, client_ip)
        
        return {
            **result,
            "received_from": client_ip,
            "endpoint": "esp32_sensor"
        }
    except Exception as e:
        print(f"❌ ESP32 센서 데이터 수신 오류: {e}")
        raise HTTPException(status_code=500, detail=f"ESP32 sensor data processing failed: {str(e)}")


@app.post("/esp32/image")
async def receive_esp_eye_image_data(request: Request, data: Dict[str, Any]):
    """ESP Eye에서 이미지 데이터만 수신 (호환성 유지)"""
    print(f"👁️ 이미지 전용 엔드포인트 호출 - /esp32/data로 리다이렉트")
    return await receive_esp32_data(request, data)

@app.post("/esp32/command")
async def send_command_to_esp32(command_data: Dict[str, Any]):
    """ESP32에 WiFi로 명령 전송"""
    if not MODULES_AVAILABLE:
        return {
            "status": "fallback_mode",
            "message": "Modules not available", 
            "command": command_data
        }
    
    try:
        success = await esp32_handler.send_command_to_esp32(command_data)
        
        return {
            "status": "success" if success else "failed",
            "message": f"Command {'sent to' if success else 'failed to send to'} ESP32",
            "command": command_data.get("command"),
            "timestamp": datetime.now().isoformat(),
            "esp32_ip": esp32_handler.esp32_ip if hasattr(esp32_handler, 'esp32_ip') else None
        }
        
    except Exception as e:
        print(f"❌ ESP32 명령 전송 오류: {e}")
        raise HTTPException(status_code=500, detail=f"Command sending failed: {str(e)}")

# 🔥 수정: 실시간 시간 정보 API
@app.get("/app/time")
def get_time_info():
    """앱에서 서버 시간 정보 조회 (한국 시간)"""
    if realtime_handler:
        return realtime_handler.get_time_info()
    else:
        current_kst = datetime.now(KST)
        current_utc = datetime.now(timezone.utc)
        
        return {
            "utc_time": current_utc.isoformat(),
            "kst_time": current_kst.isoformat(),
            "local_time": current_kst.strftime("%Y-%m-%d %H:%M:%S"),
            "formatted_time": current_kst.strftime("%Y년 %m월 %d일 %H:%M:%S"),
            "timezone": "Asia/Seoul",
            "message": "실시간 핸들러 비활성화" if not realtime_handler else "정상"
        }

@app.get("/")
def read_root():
    """서버 상태 및 정보"""
    return {
        "message": "Baby Monitor Server is running!",
        "role": "ESP32-CAM ↔ Mobile App Bridge",
        "version": "2.0.0",
        "modules_available": MODULES_AVAILABLE,
        "status": {
            "redis": "connected" if (MODULES_AVAILABLE and redis_manager.available) else "disconnected", 
            "active_app_connections": len(websocket_manager.active_connections) if (MODULES_AVAILABLE and hasattr(websocket_manager, 'active_connections')) else 0,
            "esp32": esp32_handler.esp32_status if (MODULES_AVAILABLE and hasattr(esp32_handler, 'esp32_status')) else "unknown"
        },
        "endpoints": {
            "esp32_data": "/esp32/data (POST) - 통합 데이터 수신",
            "esp32_sensor": "/esp32/sensor (POST) - 센서 데이터",
            "esp32_image": "/esp32/image (POST) - 이미지 데이터",
            "esp32_command": "/esp32/command (POST) - ESP32 명령", 
            "app_websocket": "/app/stream (WebSocket)",
            "current_status": "/status",
            "time_info": "/app/time",
            "health_check": "/health",
            "test_page": "/test",
            "dashboard": "/dashboard"
        },
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
def health_check():
    """기본 헬스 체크 (JSON)"""
    return {
        "status": "healthy",
        "modules_available": MODULES_AVAILABLE,
        "redis": (MODULES_AVAILABLE and redis_manager.available) if MODULES_AVAILABLE else False,
        "esp32_connected": (esp32_handler.esp32_status == "connected") if (MODULES_AVAILABLE and hasattr(esp32_handler, 'esp32_status')) else False,
        "active_connections": len(websocket_manager.active_connections) if (MODULES_AVAILABLE and hasattr(websocket_manager, 'active_connections')) else 0,
        "timestamp": datetime.now().isoformat()
    }

# 🔥 수정: 앱 종료 시 정리
@app.on_event("shutdown")
async def shutdown_event():
    """서버 종료 시 정리 작업"""
    if realtime_handler:
        try:
            realtime_handler.stop_heartbeat()
            print("💓 실시간 하트비트 중지됨")
        except Exception as e:
            print(f"⚠️ 하트비트 중지 오류: {e}")

# 🔥 수정: 앱 API 핸들러 초기화
if MODULES_AVAILABLE:
    try:
        app_api_handler = AppApiHandler(
            redis_manager=redis_manager,
            websocket_manager=websocket_manager,
            esp32_handler=esp32_handler,
            image_handler=image_handler
        )
        
        # 앱 API 라우터를 메인 앱에 포함
        app.include_router(app_api_handler.get_router())
        print("📱 앱 API 핸들러 초기화 완료")
        
    except Exception as e:
        print(f"⚠️ 앱 API 핸들러 초기화 실패: {e}")
        app_api_handler = None
else:
    app_api_handler = None
    print("📱 앱 API 핸들러 비활성화 (모듈 없음)")

@app.get("/images/debug")
async def debug_image_data():
    """이미지 데이터 디버깅"""
    if not MODULES_AVAILABLE:
        return {"error": "Modules not available"}
    
    try:
        if redis_manager and hasattr(redis_manager, 'get_latest_image'):
            image_data = redis_manager.get_latest_image()
            if image_data:
                image_b64 = image_data.get("image_base64", "")
                return {
                    "has_data": True,
                    "timestamp": image_data.get("timestamp"),
                    "image_length": len(image_b64),
                    "image_starts_with": image_b64[:50] if image_b64 else "empty",
                    "image_ends_with": image_b64[-50:] if len(image_b64) > 50 else image_b64,
                    "metadata": image_data.get("metadata", {})
                }
        
        return {"has_data": False, "message": "No image data found"}
        
    except Exception as e:
        return {"error": str(e)}

# 🔥 새로 추가: 이미지 관련 API 엔드포인트
@app.get("/images/latest")
async def get_latest_image():
    """최신 이미지 조회"""
    if not MODULES_AVAILABLE:
        return {"error": "Modules not available"}
    
    try:
        # Redis에서 최신 이미지 데이터 가져오기
        if redis_manager and hasattr(redis_manager, 'get_latest_image'):
            image_data = redis_manager.get_latest_image()
            if image_data:
                return {
                    "status": "success",
                    "has_image": True,
                    "image_base64": image_data.get("image_base64", ""),
                    "timestamp": image_data.get("timestamp"),
                    "metadata": image_data.get("metadata", {}),
                    "size": len(image_data.get("image_base64", ""))
                }
        
        return {
            "status": "no_image",
            "has_image": False,
            "message": "최신 이미지가 없습니다"
        }
        
    except Exception as e:
        print(f"❌ 최신 이미지 조회 오류: {e}")
        return {
            "status": "error",
            "has_image": False,
            "message": f"이미지 조회 실패: {str(e)}"
        }

@app.get("/images/latest/data")
async def get_latest_image_data():
    """최신 이미지 데이터만 (base64)"""
    if not MODULES_AVAILABLE:
        return {"error": "Modules not available"}
    
    try:
        if redis_manager and hasattr(redis_manager, 'get_latest_image'):
            image_data = redis_manager.get_latest_image()
            if image_data and image_data.get("image_base64"):
                return {
                    "image": image_data["image_base64"],
                    "timestamp": image_data.get("timestamp"),
                    "format": image_data.get("metadata", {}).get("format", "jpeg")
                }
        
        return {"image": None, "timestamp": None}
        
    except Exception as e:
        print(f"❌ 이미지 데이터 조회 오류: {e}")
        return {"image": None, "error": str(e)}

# 🔥 새로 추가: 상태 엔드포인트
@app.get("/status")
def get_detailed_status():
    """상세 서버 상태 정보"""
    return {
        "server": {
            "status": "running",
            "version": "2.0.0",
            "modules_available": MODULES_AVAILABLE,
            "startup_time": datetime.now().isoformat()
        },
        "redis": {
            "available": (MODULES_AVAILABLE and redis_manager.available) if MODULES_AVAILABLE else False,
            "status": "connected" if (MODULES_AVAILABLE and redis_manager.available) else "disconnected"
        },
        "esp32": {
            "status": esp32_handler.esp32_status if (MODULES_AVAILABLE and hasattr(esp32_handler, 'esp32_status')) else "unknown",
            "ip": esp32_handler.esp32_ip if (MODULES_AVAILABLE and hasattr(esp32_handler, 'esp32_ip')) else None
        },
        "websocket": {
            "active_connections": len(websocket_manager.active_connections) if (MODULES_AVAILABLE and hasattr(websocket_manager, 'active_connections')) else 0
        },
        "timestamp": datetime.now().isoformat()
    }

# 🔥 간단한 테스트 엔드포인트
# 🔥 Redis 수동 재연결 엔드포인트
@app.post("/admin/redis/reconnect")
def reconnect_redis():
    """Redis 수동 재연결 시도"""
    if not MODULES_AVAILABLE:
        return {"error": "Modules not available"}
    
    try:
        success = redis_manager.reconnect()
        return {
            "status": "success" if success else "failed",
            "redis_available": redis_manager.available,
            "message": "Redis 재연결 성공" if success else "Redis 재연결 실패",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"재연결 시도 중 오류: {e}",
            "timestamp": datetime.now().isoformat()
        }

# =========================
# ESP32 관련 엔드포인트
# =========================

# main.py의 /stream 엔드포인트 디버깅 강화 버전

@app.get("/stream")
async def mjpeg_stream_viewer():
    """클라이언트용 MJPEG 스트림 (디버깅 강화)"""
    
    print("🎬 /stream 엔드포인트 호출됨")
    
    def generate_stream():
        """MJPEG 스트림 생성기 (디버깅 강화)"""
        print("📺 스트림 생성기 시작")
        
        if not MODULES_AVAILABLE:
            print("⚠️ 모듈 사용 불가 - 더미 응답")
            yield b"--frame\r\nContent-Type: text/plain\r\nContent-Length: 26\r\n\r\nMJPEG service unavailable\r\n"
            return
        
        # mjpeg_manager 존재 확인
        if not hasattr(mjpeg_manager, 'add_viewer'):
            print("❌ mjpeg_manager에 add_viewer 메서드 없음")
            yield b"--frame\r\nContent-Type: text/plain\r\nContent-Length: 30\r\n\r\nMJPEG manager not available\r\n"
            return
            
        frame_queue = mjpeg_manager.add_viewer()
        if frame_queue is None:
            print("❌ frame_queue가 None")
            yield b"--frame\r\nContent-Type: text/plain\r\nContent-Length: 26\r\n\r\nMJPEG service unavailable\r\n"
            return
        
        print(f"✅ 시청자 추가됨, 큐 생성: {frame_queue}")
        
        try:
            consecutive_timeouts = 0
            max_timeouts = 12  # 60초로 연장
            frame_sent = 0
            
            # 🔥 즉시 최신 프레임 전송 (있다면)
            if mjpeg_manager.latest_frame:
                print(f"📤 최신 프레임 즉시 전송: {len(mjpeg_manager.latest_frame)} bytes")
                initial_frame = mjpeg_manager._create_mjpeg_frame(mjpeg_manager.latest_frame)
                yield initial_frame
                frame_sent += 1
            
            while consecutive_timeouts < max_timeouts:
                try:
                    # 큐에서 프레임 대기 (5초 타임아웃)
                    frame_data = frame_queue.get(timeout=5.0)
                    yield frame_data
                    frame_sent += 1
                    consecutive_timeouts = 0
                    
                    if frame_sent % 10 == 0:
                        print(f"📺 스트림 전송 중: {frame_sent}프레임 전송됨")
                    
                except queue.Empty:
                    consecutive_timeouts += 1
                    print(f"⏰ 스트림 타임아웃 {consecutive_timeouts}/{max_timeouts}")
                    
                    # Keep-alive: 최신 프레임 재전송
                    if mjpeg_manager.latest_frame:
                        print("🔄 최신 프레임 재전송")
                        keep_alive = mjpeg_manager._create_mjpeg_frame(mjpeg_manager.latest_frame)
                        yield keep_alive
                        frame_sent += 1
                        consecutive_timeouts = max(0, consecutive_timeouts - 1)  # 카운터 감소
                    else:
                        # 더미 keep-alive
                        print("📡 Keep-alive 전송")
                        keep_alive = b"--frame\r\nContent-Type: text/plain\r\nContent-Length: 9\r\n\r\nkeepalive\r\n"
                        yield keep_alive
                    
                except Exception as e:
                    print(f"❌ 스트림 생성 중 오류: {e}")
                    break
            
            print(f"🔚 스트림 종료: 총 {frame_sent}프레임 전송")
        
        except Exception as e:
            print(f"❌ 스트림 생성기 전체 오류: {e}")
        finally:
            # 시청자 정리
            print("🧹 시청자 정리 중...")
            if hasattr(mjpeg_manager, 'remove_viewer'):
                mjpeg_manager.remove_viewer(frame_queue)
            print("🔌 스트림 연결 완전 종료")
    
    print("🎥 StreamingResponse 생성 중...")
    response = StreamingResponse(
        generate_stream(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache", 
            "Expires": "0",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "X-Accel-Buffering": "no",
        }
    )
    print("📤 StreamingResponse 반환")
    return response

# 🔥 추가 디버깅 엔드포인트
@app.get("/stream/debug")
def debug_stream():
    """스트림 디버깅 정보"""
    try:
        manager_info = {
            "mjpeg_manager_exists": 'mjpeg_manager' in globals(),
            "mjpeg_manager_type": str(type(mjpeg_manager)) if 'mjpeg_manager' in globals() else "Not found",
            "has_add_viewer": hasattr(mjpeg_manager, 'add_viewer') if 'mjpeg_manager' in globals() else False,
            "has_latest_frame": hasattr(mjpeg_manager, 'latest_frame') and mjpeg_manager.latest_frame is not None if 'mjpeg_manager' in globals() else False,
            "modules_available": MODULES_AVAILABLE,
        }
        
        if 'mjpeg_manager' in globals() and hasattr(mjpeg_manager, 'get_stats'):
            stats = mjpeg_manager.get_stats()
            manager_info.update({
                "stats": stats,
                "latest_frame_size": len(mjpeg_manager.latest_frame) if mjpeg_manager.latest_frame else 0
            })
        
        return {
            "debug_info": manager_info,
            "timestamp": get_korea_time().isoformat()
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "timestamp": get_korea_time().isoformat()
        }

# 🔥 간단한 테스트 스트림 (비교용)
@app.get("/stream/simple")
def simple_test_stream():
    """간단한 테스트 스트림"""
    
    def generate_simple():
        print("🧪 간단한 테스트 스트림 시작")
        
        # 더미 JPEG 데이터 (최소한의 유효한 JPEG)
        dummy_jpeg = bytes([
            0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
            0x01, 0x01, 0x00, 0x48, 0x00, 0x48, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
            0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08, 0x07, 0x07, 0x07, 0x09,
            0x09, 0x08, 0x0A, 0x0C, 0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
            0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D, 0x1A, 0x1C, 0x1C, 0x20,
            0x24, 0x2E, 0x27, 0x20, 0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
            0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27, 0x39, 0x3D, 0x38, 0x32,
            0x3C, 0x2E, 0x33, 0x34, 0x32, 0xFF, 0xC0, 0x00, 0x11, 0x08, 0x00, 0x01,
            0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0x02, 0x11, 0x01, 0x03, 0x11, 0x01,
            0xFF, 0xC4, 0x00, 0x14, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x08, 0xFF, 0xC4,
            0x00, 0x14, 0x10, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xDA, 0x00, 0x0C,
            0x03, 0x01, 0x00, 0x02, 0x11, 0x03, 0x11, 0x00, 0x3F, 0x00, 0xB2, 0xC0,
            0x07, 0xFF, 0xD9
        ])
        
        for i in range(100):  # 100프레임 전송
            # MJPEG 프레임 형식
            boundary = b"--frame\r\n"
            content_type = b"Content-Type: image/jpeg\r\n"
            content_length = f"Content-Length: {len(dummy_jpeg)}\r\n\r\n".encode()
            frame_end = b"\r\n"
            
            frame = boundary + content_type + content_length + dummy_jpeg + frame_end
            yield frame
            
            if i % 10 == 0:
                print(f"🧪 테스트 프레임 {i} 전송")
            
            import time
            time.sleep(0.1)  # 10 FPS
        
        print("🧪 테스트 스트림 종료")
    
    return StreamingResponse(
        generate_simple(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

# 🔥 스트림 테스트 페이지 개선
@app.get("/stream/test")
def enhanced_test_page():
    """향상된 스트림 테스트 페이지"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>MJPEG 스트림 테스트</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; }
            .container { max-width: 1200px; margin: 0 auto; }
            .stream-box { border: 2px solid #333; margin: 20px 0; padding: 10px; }
            img { max-width: 100%; border: 1px solid #ccc; }
            .info { background: #f0f0f0; padding: 10px; margin: 10px 0; }
            .debug { background: #ffe6e6; padding: 10px; margin: 10px 0; }
            button { padding: 10px 15px; margin: 5px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎥 MJPEG 스트림 테스트</h1>
            
            <div class="info">
                <h3>스트림 상태:</h3>
                <div id="status">확인 중...</div>
                <button onclick="checkDebug()">디버그 정보</button>
                <button onclick="location.reload()">새로고침</button>
            </div>
            
            <div class="stream-box">
                <h3>실제 ESP Eye 스트림:</h3>
                <img id="realStream" src="/stream" 
                     onerror="showError('real')" onload="showSuccess('real')">
                <div id="realStatus">로딩 중...</div>
            </div>
            
            <div class="stream-box">
                <h3>테스트 스트림 (비교용):</h3>
                <img id="testStream" src="/stream/simple" 
                     onerror="showError('test')" onload="showSuccess('test')">
                <div id="testStatus">로딩 중...</div>
            </div>
            
            <div class="debug" id="debugInfo" style="display:none;">
                <h3>디버그 정보:</h3>
                <pre id="debugContent"></pre>
            </div>
        </div>
        
        <script>
            function showError(type) {
                document.getElementById(type + 'Status').innerHTML = 
                    '<span style="color: red;">❌ 스트림 연결 실패</span>';
            }
            
            function showSuccess(type) {
                document.getElementById(type + 'Status').innerHTML = 
                    '<span style="color: green;">✅ 스트림 연결 성공</span>';
            }
            
            async function checkDebug() {
                try {
                    const response = await fetch('/stream/debug');
                    const data = await response.json();
                    document.getElementById('debugContent').textContent = 
                        JSON.stringify(data, null, 2);
                    document.getElementById('debugInfo').style.display = 'block';
                } catch (e) {
                    document.getElementById('debugContent').textContent = 'Error: ' + e.message;
                    document.getElementById('debugInfo').style.display = 'block';
                }
            }
            
            async function updateStatus() {
                try {
                    const response = await fetch('/stream/status');
                    const data = await response.json();
                    document.getElementById('status').innerHTML = 
                        `📺 시청자: ${data.viewers} | 프레임: ${data.frame_count} | ESP Eye: ${data.esp_eye_connected ? '✅ 연결됨' : '❌ 연결 안됨'} | 최신 프레임: ${data.has_latest_frame ? '✅ 있음' : '❌ 없음'}`;
                } catch (e) {
                    document.getElementById('status').innerHTML = '❌ 상태 확인 실패: ' + e.message;
                }
            }
            
            // 3초마다 상태 업데이트
            setInterval(updateStatus, 3000);
            updateStatus();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

# 🔥 스트림 상태 확인
@app.get("/stream/status")
def get_stream_status():
    """MJPEG 스트리밍 상태 조회"""
    stats = mjpeg_manager.get_stats()
    
    return {
        "status": "active" if stats["viewers"] > 0 or stats.get("esp_eye_connected", False) else "inactive",
        "viewers": stats["viewers"],
        "frame_count": stats["frame_count"],
        "last_frame_time": stats.get("last_frame_time"),
        "last_frame_age_seconds": stats.get("last_frame_age"),
        "esp_eye_connected": stats.get("esp_eye_connected", False),
        "stream_url": "/stream",
        "has_latest_frame": stats.get("has_latest_frame", False),
        "timestamp": get_korea_time().isoformat()
    }

# 🔥 간단한 테스트 페이지
@app.get("/stream/test")
def test_stream_page():
    """스트림 테스트 페이지"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>MJPEG 스트림 테스트</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; text-align: center; }
            img { border: 2px solid #333; border-radius: 10px; max-width: 90%; }
            .info { background: #f0f0f0; padding: 15px; margin: 15px 0; border-radius: 5px; }
        </style>
    </head>
    <body>
        <h1>🎥 Baby Monitor MJPEG 스트림</h1>
        
        <div class="info">
            <div id="status">연결 중...</div>
            <div>시간: <span id="time"></span></div>
        </div>
        
        <div>
            <img id="stream" src="/stream" alt="MJPEG Stream" 
                 onerror="showError()" onload="showSuccess()">
        </div>
        
        <script>
            function updateTime() {
                document.getElementById('time').textContent = new Date().toLocaleString();
            }
            
            function showError() {
                document.getElementById('status').innerHTML = '❌ 스트림 연결 실패';
            }
            
            function showSuccess() {
                document.getElementById('status').innerHTML = '✅ 스트림 연결 성공';
            }
            
            setInterval(updateTime, 1000);
            updateTime();
            
            // 3초마다 상태 확인
            setInterval(async () => {
                try {
                    const response = await fetch('/stream/status');
                    const data = await response.json();
                    document.getElementById('status').innerHTML = 
                        `📺 시청자: ${data.viewers} | 프레임: ${data.frame_count} | ESP Eye: ${data.esp_eye_connected ? '✅' : '❌'}`;
                } catch (e) {
                    // 오류 무시
                }
            }, 3000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.get("/stream/status")
def get_stream_status():
    """MJPEG 스트리밍 상태 조회"""
    stats = mjpeg_manager.get_stats()
    
    return {
        "status": "active" if stats["viewers"] > 0 or stats["esp_eye_connected"] else "inactive",
        "viewers": stats["viewers"],
        "frame_count": stats["frame_count"],
        "last_frame_time": stats["last_frame_time"],
        "last_frame_age_seconds": stats["last_frame_age"],
        "esp_eye_connected": stats["esp_eye_connected"],
        "stream_url": "/stream",
        "timestamp": get_korea_time().isoformat()
    }

@app.get("/app/stream/url")
def get_app_stream_url():
    """앱용 스트림 URL 조회"""
    stats = mjpeg_manager.get_stats()
    
    return {
        "status": "success",
        "stream_url": "/stream",  # FastAPI 서버의 스트림 엔드포인트
        "viewers": stats["viewers"],
        "streaming_active": stats["esp_eye_connected"],
        "frame_count": stats["frame_count"],
        "timestamp": get_korea_time().isoformat()
    }

# 🔥 ESP Eye 설정 가이드 (수정된 버전)
@app.get("/stream/setup", response_class=HTMLResponse)
def mjpeg_setup_guide():
    """ESP Eye MJPEG 설정 가이드"""
    
    # 현재 서버 정보 가져오기
    stats = mjpeg_manager.get_stats()
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>ESP Eye MJPEG 설정 가이드</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.7.2/font/bootstrap-icons.css" rel="stylesheet">
    </head>
    <body>
        <div class="container py-5">
            <h1 class="text-center mb-4">📹 ESP Eye MJPEG 스트리밍 설정</h1>
            
            <div class="row">
                <div class="col-lg-8 mx-auto">
                    <div class="card mb-4">
                        <div class="card-header bg-primary text-white">
                            <h5><i class="bi bi-code"></i> ESP Eye Arduino 코드</h5>
                        </div>
                        <div class="card-body">
                            <pre><code style="font-size: 12px;">
#include "esp_camera.h"
#include &lt;WiFi.h&gt;
#include &lt;HTTPClient.h&gt;

const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";
const char* serverURL = "http://YOUR_SERVER_URL:8000/video";

void setup() {{
    Serial.begin(115200);
    
    // WiFi 연결
    WiFi.begin(ssid, password);
    while (WiFi.status() != WL_CONNECTED) {{
        delay(1000);
        Serial.println("WiFi 연결 중...");
    }}
    Serial.println("WiFi 연결됨: " + WiFi.localIP().toString());
    
    // 카메라 초기화 (ESP32-CAM 표준 설정)
    camera_config_t config;
    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer = LEDC_TIMER_0;
    config.pin_d0 = Y2_GPIO_NUM;
    config.pin_d1 = Y3_GPIO_NUM;
    config.pin_d2 = Y4_GPIO_NUM;
    config.pin_d3 = Y5_GPIO_NUM;
    config.pin_d4 = Y6_GPIO_NUM;
    config.pin_d5 = Y7_GPIO_NUM;
    config.pin_d6 = Y8_GPIO_NUM;
    config.pin_d7 = Y9_GPIO_NUM;
    config.pin_xclk = XCLK_GPIO_NUM;
    config.pin_pclk = PCLK_GPIO_NUM;
    config.pin_vsync = VSYNC_GPIO_NUM;
    config.pin_href = HREF_GPIO_NUM;
    config.pin_sscb_sda = SIOD_GPIO_NUM;
    config.pin_sscb_scl = SIOC_GPIO_NUM;
    config.pin_pwdn = PWDN_GPIO_NUM;
    config.pin_reset = RESET_GPIO_NUM;
    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_JPEG;
    config.frame_size = FRAMESIZE_VGA;  // 640x480
    config.jpeg_quality = 12;           // 0-63 (낮을수록 고품질)
    config.fb_count = 2;                // 프레임 버퍼 개수
    
    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {{
        Serial.printf("카메라 초기화 실패: 0x%x", err);
        return;
    }}
    
    Serial.println("카메라 초기화 완료!");
}}

void loop() {{
    camera_fb_t * fb = esp_camera_fb_get();
    if (!fb) {{
        Serial.println("카메라 프레임 캡처 실패");
        delay(100);
        return;
    }}
    
    // HTTP POST로 JPEG 데이터 전송
    if (WiFi.status() == WL_CONNECTED) {{
        HTTPClient http;
        http.begin(serverURL);
        http.addHeader("Content-Type", "image/jpeg");
        
        int httpResponseCode = http.POST(fb->buf, fb->len);
        
        if (httpResponseCode > 0) {{
            // 성공
        }} else {{
            Serial.printf("전송 실패: %d\\n", httpResponseCode);
        }}
        
        http.end();
    }}
    
    esp_camera_fb_return(fb);
    delay(66);  // 약 15fps (1000/15 = 66ms)
}}
                            </code></pre>
                        </div>
                    </div>
                    
                    <div class="card">
                        <div class="card-header bg-success text-white">
                            <h5><i class="bi bi-info-circle"></i> 현재 스트림 상태</h5>
                        </div>
                        <div class="card-body">
                            <div id="currentStatus">로딩 중...</div>
                            <div class="mt-3">
                                <a href="/stream" class="btn btn-primary" target="_blank">
                                    <i class="bi bi-play-circle"></i> 스트림 보기
                                </a>
                                <a href="/dashboard" class="btn btn-outline-success">
                                    <i class="bi bi-speedometer2"></i> 대시보드
                                </a>
                                <a href="/stream/status" class="btn btn-outline-info" target="_blank">
                                    <i class="bi bi-code-square"></i> 상태 JSON
                                </a>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <script>
        async function updateStatus() {{
            try {{
                const response = await fetch('/stream/status');
                const data = await response.json();
                
                document.getElementById('currentStatus').innerHTML = `
                    <div class="row">
                        <div class="col-md-6">
                            <ul class="list-group">
                                <li class="list-group-item d-flex justify-content-between">
                                    <span><i class="bi bi-activity"></i> 스트림 상태</span>
                                    <span class="badge bg-${{data.status === 'active' ? 'success' : 'secondary'}}">${{data.status}}</span>
                                </li>
                                <li class="list-group-item d-flex justify-content-between">
                                    <span><i class="bi bi-eye"></i> 현재 시청자</span>
                                    <span class="badge bg-info">${{data.viewers}}명</span>
                                </li>
                                <li class="list-group-item d-flex justify-content-between">
                                    <span><i class="bi bi-camera"></i> ESP Eye 연결</span>
                                    <span class="badge bg-${{data.esp_eye_connected ? 'success' : 'danger'}}">
                                        ${{data.esp_eye_connected ? '연결됨' : '연결 안됨'}}
                                    </span>
                                </li>
                            </ul>
                        </div>
                        <div class="col-md-6">
                            <ul class="list-group">
                                <li class="list-group-item d-flex justify-content-between">
                                    <span><i class="bi bi-layers"></i> 총 프레임</span>
                                    <span class="badge bg-secondary">${{data.frame_count || 0}}</span>
                                </li>
                                <li class="list-group-item d-flex justify-content-between">
                                    <span><i class="bi bi-clock"></i> 마지막 프레임</span>
                                    <small class="text-muted">
                                        ${{data.last_frame_time ? new Date(data.last_frame_time * 1000).toLocaleTimeString() : 'N/A'}}
                                    </small>
                                </li>
                                <li class="list-group-item d-flex justify-content-between">
                                    <span><i class="bi bi-link"></i> 스트림 URL</span>
                                    <code>/stream</code>
                                </li>
                            </ul>
                        </div>
                    </div>
                `;
                
            }} catch (error) {{
                document.getElementById('currentStatus').innerHTML = 
                    '<div class="alert alert-danger"><i class="bi bi-exclamation-triangle"></i> 상태 조회 실패: ' + error.message + '</div>';
            }}
        }}
        
        // 페이지 로드 시 상태 확인
        updateStatus();
        
        // 5초마다 상태 업데이트
        setInterval(updateStatus, 5000);
        </script>
    </body>
    </html>
    """
    
    return HTMLResponse(content=html_content)

@app.post("/esp32/command")
async def send_command_to_esp32(command_data: Dict[str, Any]):
    """ESP32에 WiFi로 명령 전송"""
    if not MODULES_AVAILABLE:
        return {"error": "Modules not available", "command": command_data}
    
    try:
        success = await esp32_handler.send_command_to_esp32(command_data)
        
        return {
            "status": "success" if success else "failed",
            "message": f"Command {'sent to' if success else 'failed to send to'} ESP32",
            "command": command_data.get("command"),
            "timestamp": datetime.now().isoformat(),
            "esp32_ip": esp32_handler.esp32_ip
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Command sending failed: {str(e)}")

@app.get("/test", response_class=HTMLResponse)
def get_test_page():
    """통합 테스트 페이지"""
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Baby Monitor Server Test</title>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            .status {{ padding: 15px; margin: 15px 0; border-radius: 8px; }}
            .connected {{ background: #d4edda; color: #155724; }}
            .disconnected {{ background: #f8d7da; color: #721c24; }}
            .warning {{ background: #fff3cd; color: #856404; }}
            .section {{ background: white; padding: 20px; margin: 20px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            button {{ background: #007bff; color: white; padding: 10px 15px; border: none; border-radius: 5px; cursor: pointer; margin: 5px; }}
            button:hover {{ background: #0056b3; }}
            input, select {{ padding: 8px; margin: 5px; border: 1px solid #ddd; border-radius: 4px; }}
            .log {{ background: #f8f9fa; padding: 15px; margin: 10px 0; border-radius: 5px; height: 300px; overflow-y: auto; font-family: monospace; border: 1px solid #dee2e6; }}
            .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
            .status-indicator {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; }}
            .online {{ background-color: #28a745; }}
            .offline {{ background-color: #dc3545; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🍼 Baby Monitor Server Test Dashboard</h1>
            
            <div class="status {'connected' if MODULES_AVAILABLE and redis_manager.available else 'warning' if MODULES_AVAILABLE else 'disconnected'}">
                <h3>🔧 Server Status</h3>
                <p><strong>Modules:</strong> {'Available ✅' if MODULES_AVAILABLE else 'Not Available ❌'}</p>
                <p><span class="status-indicator {'online' if MODULES_AVAILABLE and redis_manager.available else 'offline'}"></span>
                   <strong>Redis:</strong> {'연결됨' if MODULES_AVAILABLE and redis_manager.available else '연결 안됨'}</p>
                <p><span class="status-indicator {'online' if MODULES_AVAILABLE and hasattr(esp32_handler, 'esp32_status') and esp32_handler.esp32_status == 'connected' else 'offline'}"></span>
                   <strong>ESP32:</strong> {esp32_handler.esp32_status if MODULES_AVAILABLE and hasattr(esp32_handler, 'esp32_status') else 'unknown'}</p>
                <p><span class="status-indicator online"></span>
                   <strong>App Connections:</strong> {len(websocket_manager.active_connections) if MODULES_AVAILABLE else 0}개</p>
            </div>
            
            <div class="grid">
                <div class="section">
                    <h3>📡 ESP32 Test</h3>
                    <button onclick="sendTestData()">ESP32 데이터 시뮬레이션</button>
                    <button onclick="sendCommand()">ESP32 명령 테스트</button>
                    <div>
                        <textarea id="testData" rows="5" cols="50" placeholder="ESP32 데이터 JSON">{{"baby_detected": true, "temperature": 25.5, "humidity": 60.0, "confidence": 0.85}}</textarea>
                    </div>
                </div>
                
                <div class="section">
                    <h3>📊 API Test</h3>
                    <button onclick="getStatus()">서버 상태 조회</button>
                    <button onclick="getHealth()">Health Check</button>
                    <button onclick="getRoot()">Root 정보</button>
                </div>
            </div>
            
            <div class="section">
                <h3>🔄 Real-time Log</h3>
                <div id="log" class="log">테스트 로그가 여기에 표시됩니다...</div>
                <button onclick="clearLog()">로그 지우기</button>
            </div>
        </div>

        <script>
        const log = document.getElementById('log');
        
        function addLog(message) {{
            const timestamp = new Date().toLocaleTimeString();
            log.innerHTML += '[' + timestamp + '] ' + message + '\\n';
            log.scrollTop = log.scrollHeight;
        }}
        
        function clearLog() {{
            log.innerHTML = '';
        }}
        
        async function sendTestData() {{
            try {{
                const testDataText = document.getElementById('testData').value;
                const testData = JSON.parse(testDataText);
                
                addLog('📡 ESP32 테스트 데이터 전송 중...');
                
                const response = await fetch('/esp32/data', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(testData)
                }});
                
                const result = await response.json();
                
                if (response.ok) {{
                    addLog('✅ ESP32 데이터 전송 성공: ' + (result.status || 'processed'));
                    addLog('📊 결과: ' + JSON.stringify(result, null, 2));
                }} else {{
                    addLog('❌ ESP32 데이터 전송 실패: ' + (result.detail || 'Unknown error'));
                }}
                
            }} catch (error) {{
                addLog('❌ 오류: ' + error.message);
            }}
        }}
        
        async function sendCommand() {{
            try {{
                const command = {{
                    command: 'play_lullaby',
                    params: {{ song: 'test' }}
                }};
                
                addLog('🎵 ESP32 명령 전송 중...');
                
                const response = await fetch('/esp32/command', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(command)
                }});
                
                const result = await response.json();
                addLog('🎵 명령 결과: ' + result.status + ' - ' + result.message);
                
            }} catch (error) {{
                addLog('❌ 명령 전송 오류: ' + error.message);
            }}
        }}
        
        async function getStatus() {{
            try {{
                addLog('📊 서버 상태 조회 중...');
                
                const response = await fetch('/status');
                const data = await response.json();
                
                addLog('📊 서버 상태: ' + JSON.stringify(data, null, 2));
                
            }} catch (error) {{
                addLog('❌ 상태 조회 오류: ' + error.message);
            }}
        }}
        
        async function getHealth() {{
            try {{
                addLog('🏥 Health check 수행 중...');
                
                const response = await fetch('/health');
                const data = await response.json();
                
                addLog('🏥 Health: ' + data.status + ' - Redis: ' + data.redis + ', Modules: ' + data.modules_available);
                
            }} catch (error) {{
                addLog('❌ Health check 오류: ' + error.message);
            }}
        }}
        
        async function getRoot() {{
            try {{
                addLog('🏠 Root 정보 조회 중...');
                
                const response = await fetch('/');
                const data = await response.json();
                
                addLog('🏠 Root: ' + data.message + ' - Version: ' + data.version);
                addLog('📡 Endpoints: ' + JSON.stringify(data.endpoints, null, 2));
                
            }} catch (error) {{
                addLog('❌ Root 조회 오류: ' + error.message);
            }}
        }}
        
        // 페이지 로드 시 초기 상태 확인
        window.onload = function() {{
            addLog('🍼 Baby Monitor Test Dashboard 시작');
            getHealth();
        }};
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/dashboard", response_class=HTMLResponse)
def baby_monitor_dashboard():
    """아기 모니터링 실시간 대시보드 (MJPEG 스트리밍 통합)"""
    
    # 현재 아기 모니터링 데이터 수집
    current_time = get_korea_time()
    current_time_str = current_time.strftime("%Y년 %m월 %d일 %H:%M:%S")
    time_only = current_time.strftime("%H:%M:%S")
    date_only = current_time.strftime("%Y년 %m월 %d일")
    
    # Redis에서 최신 데이터 가져오기 (가능한 경우)
    baby_status = {
        "detected": False,
        "confidence": 0.0,
        "last_seen": "데이터 없음",
        "sleep_duration": "측정 중",
        "temperature": "N/A",
        "humidity": "N/A",
        "environment_status": "알 수 없음"
    }
    
    recent_activities = []
    alerts = []
    
    # Redis 데이터 확인 (MODULES_AVAILABLE 체크)
    if MODULES_AVAILABLE and redis_manager.available:
        try:
            current_data = redis_manager.get_current_status()
            if current_data:
                baby_status.update({
                    "detected": current_data.get("baby_detected", False),
                    "confidence": current_data.get("confidence", 0.0),
                    "temperature": current_data.get("temperature", "N/A"),
                    "humidity": current_data.get("humidity", "N/A"),
                    "last_seen": current_data.get("timestamp", "알 수 없음")
                })
                
                # 환경 상태 판단
                temp = current_data.get("temperature")
                humidity = current_data.get("humidity")
                if temp and humidity:
                    if 20 <= temp <= 24 and 40 <= humidity <= 60:
                        baby_status["environment_status"] = "최적"
                    else:
                        baby_status["environment_status"] = "주의"
        except Exception as e:
            print(f"Redis 데이터 조회 실패: {e}")
    
    # 상태에 따른 색상 결정
    detection_color = "success" if baby_status["detected"] else "warning"
    confidence_color = "success" if baby_status["confidence"] > 0.7 else "warning" if baby_status["confidence"] > 0.3 else "danger"
    env_color = "success" if baby_status["environment_status"] == "최적" else "warning"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>👶 Baby Monitor - 실시간 모니터링</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.7.2/font/bootstrap-icons.css" rel="stylesheet">
        <style>
            body {{ 
                background: linear-gradient(135deg, #ffeaa7 0%, #fab1a0 50%, #e17055 100%);
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            }}
            .dashboard-container {{ 
                background: rgba(255, 255, 255, 0.95); 
                border-radius: 25px; 
                box-shadow: 0 25px 50px rgba(0,0,0,0.15);
                backdrop-filter: blur(10px);
            }}
            .baby-card {{ 
                border: none; 
                border-radius: 20px; 
                transition: all 0.3s ease;
                background: linear-gradient(145deg, #ffffff, #f8f9fa);
                box-shadow: 0 8px 25px rgba(0,0,0,0.1);
            }}
            .baby-card:hover {{ transform: translateY(-8px) scale(1.02); }}
            .live-indicator {{ 
                display: inline-block; 
                width: 12px; 
                height: 12px; 
                background: #ff6b6b; 
                border-radius: 50%; 
                animation: pulse 2s infinite;
            }}
            @keyframes pulse {{
                0% {{ opacity: 1; transform: scale(1); }}
                50% {{ opacity: 0.5; transform: scale(1.1); }}
                100% {{ opacity: 1; transform: scale(1); }}
            }}
            .video-container {{ 
                background: #000; 
                border-radius: 15px; 
                position: relative;
                min-height: 400px;
                display: flex;
                align-items: center;
                justify-content: center;
                overflow: hidden;
            }}
            .stream-overlay {{ 
                position: absolute; 
                top: 10px; 
                left: 10px; 
                background: rgba(0,0,0,0.8); 
                color: white; 
                padding: 8px 12px; 
                border-radius: 15px; 
                font-size: 12px;
                z-index: 10;
            }}
            .viewer-count {{ 
                position: absolute; 
                top: 10px; 
                right: 10px; 
                background: rgba(0,0,0,0.8); 
                color: white; 
                padding: 8px 12px; 
                border-radius: 15px; 
                font-size: 12px;
                z-index: 10;
            }}
            .stream-status-indicator {{
                display: inline-block;
                width: 8px;
                height: 8px;
                border-radius: 50%;
                margin-right: 5px;
            }}
            .status-online {{ background-color: #28a745; }}
            .status-offline {{ background-color: #dc3545; }}
            .status-loading {{ background-color: #ffc107; }}
            .status-badge {{ 
                font-size: 1.1rem; 
                padding: 8px 16px;
                border-radius: 20px;
            }}
            .metric-card {{ 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                color: white; 
                border-radius: 20px;
                transition: transform 0.3s ease;
            }}
            .metric-card:hover {{ transform: scale(1.05); }}
            .baby-icon {{ font-size: 3rem; }}
            .activity-item {{ 
                background: white; 
                border-radius: 15px; 
                border-left: 4px solid #667eea;
                transition: all 0.3s ease;
            }}
            .activity-item:hover {{ transform: translateX(10px); }}
            .refresh-controls {{ 
                position: fixed; 
                top: 20px; 
                right: 20px; 
                z-index: 1000;
            }}
            .stream-error {{
                text-align: center;
                color: white;
                padding: 40px 20px;
            }}
            .stream-loading {{
                text-align: center;
                color: white;
                padding: 40px 20px;
            }}
            .mjpeg-stream {{
                width: 100%;
                height: 100%;
                object-fit: contain;
                border-radius: 15px;
            }}
        </style>
    </head>
    <body>
        <div class="container-fluid py-4">
            
            <!-- 새로고침 컨트롤 -->
            <div class="refresh-controls">
                <button class="btn btn-primary btn-sm me-2" onclick="refreshData()">
                    <i class="bi bi-arrow-clockwise"></i> 새로고침
                </button>
                <button class="btn btn-outline-primary btn-sm me-2" onclick="toggleStreamMode()">
                    <i class="bi bi-camera-video"></i> <span id="streamModeText">스트림 모드</span>
                </button>
                <div class="form-check form-switch d-inline-block">
                    <input class="form-check-input" type="checkbox" id="autoRefresh" checked>
                    <label class="form-check-label text-white" for="autoRefresh">자동 새로고침</label>
                </div>
            </div>
            
            <div class="dashboard-container mx-auto p-4" style="max-width: 1400px;">
                
                <!-- 헤더 -->
                <div class="text-center mb-4">
                    <h1 class="display-4 text-primary mb-2">
                        <i class="bi bi-heart-fill text-danger"></i> Baby Monitor
                    </h1>
                    <p class="lead text-muted">실시간 아기 모니터링 대시보드</p>
                    <div class="d-flex justify-content-center align-items-center">
                        <span class="live-indicator me-2"></span>
                        <span class="text-success fw-bold">LIVE</span>
                        <span class="ms-3 text-muted">{current_time.strftime("%Y-%m-%d %H:%M:%S")}</span>
                    </div>
                </div>
                
                <div class="row">
                    
                    <!-- 🔥 메인 비디오/이미지 영역 (MJPEG 스트림 통합) -->
                    <div class="col-lg-8 mb-4">
                        <div class="baby-card h-100">
                            <div class="card-header bg-primary text-white">
                                <h5 class="mb-0">
                                    <i class="bi bi-camera-video"></i> 실시간 영상
                                    <span class="float-end">
                                        <span class="stream-status-indicator status-loading" id="streamStatusIndicator"></span>
                                        <span id="streamStatusText">연결 중...</span>
                                    </span>
                                </h5>
                            </div>
                            <div class="card-body p-0">
                                <div class="video-container" id="videoContainer">
                                    
                                    <!-- 🔥 MJPEG 스트림 뷰 -->
                                    <div id="mjpegStreamView" style="width: 100%; height: 100%; position: relative;">
                                        <img id="mjpegStream" 
                                             src="/stream" 
                                             alt="Live MJPEG Stream" 
                                             class="mjpeg-stream"
                                             onerror="handleStreamError()"
                                             onload="handleStreamSuccess()">
                                        
                                        <!-- 스트림 상태 오버레이 -->
                                        <div class="stream-overlay">
                                            <span class="live-indicator"></span> LIVE STREAM
                                        </div>
                                        
                                        <!-- 시청자 수 표시 -->
                                        <div class="viewer-count">
                                            <i class="bi bi-eye"></i> <span id="viewerCount">-</span>명 시청 중
                                        </div>
                                    </div>
                                    
                                    <!-- 🔥 이미지 모드 뷰 (폴백) -->
                                    <div id="imageView" style="display: none; width: 100%; height: 100%; position: relative;">
                                        <img id="latestImage" src="" alt="최신 이미지" class="mjpeg-stream">
                                        <div class="stream-overlay">
                                            <i class="bi bi-image"></i> 최신 이미지
                                        </div>
                                        <div class="viewer-count">
                                            <span id="imageTimestamp">--:--</span>
                                        </div>
                                    </div>
                                    
                                    <!-- 🔥 스트림 로딩 뷰 -->
                                    <div id="streamLoadingView" class="stream-loading" style="display: none;">
                                        <div class="spinner-border text-light mb-3" role="status">
                                            <span class="visually-hidden">Loading...</span>
                                        </div>
                                        <h5>스트림 연결 중...</h5>
                                        <p>ESP Eye 카메라와 연결을 시도하고 있습니다</p>
                                    </div>
                                    
                                    <!-- 🔥 스트림 오류 뷰 -->
                                    <div id="streamErrorView" class="stream-error" style="display: none;">
                                        <i class="bi bi-camera-video-off baby-icon mb-3"></i>
                                        <h5>스트림 연결 실패</h5>
                                        <p class="mb-3">ESP Eye 카메라가 연결되지 않았습니다</p>
                                        <div class="d-grid gap-2 col-6 mx-auto">
                                            <button class="btn btn-outline-light" onclick="retryStream()">
                                                <i class="bi bi-arrow-clockwise"></i> 다시 연결
                                            </button>
                                            <button class="btn btn-outline-info" onclick="switchToImageMode()">
                                                <i class="bi bi-image"></i> 이미지 모드로 전환
                                            </button>
                                        </div>
                                    </div>
                                </div>
                                
                                <!-- 영상 컨트롤 -->
                                <div class="p-3 bg-light">
                                    <div class="row text-center">
                                        <div class="col-3">
                                            <button class="btn btn-outline-primary btn-sm w-100" onclick="captureSnapshot()">
                                                <i class="bi bi-camera"></i> 스냅샷
                                            </button>
                                        </div>
                                        <div class="col-3">
                                            <button class="btn btn-outline-success btn-sm w-100" onclick="toggleRecording()">
                                                <i class="bi bi-record-circle"></i> <span id="recordText">녹화</span>
                                            </button>
                                        </div>
                                        <div class="col-3">
                                            <button class="btn btn-outline-warning btn-sm w-100" onclick="toggleNightMode()">
                                                <i class="bi bi-moon"></i> 야간모드
                                            </button>
                                        </div>
                                        <div class="col-3">
                                            <button class="btn btn-outline-info btn-sm w-100" onclick="openFullscreen()">
                                                <i class="bi bi-fullscreen"></i> 전체화면
                                            </button>
                                        </div>
                                    </div>
                                    
                                    <!-- 스트림 상태 정보 -->
                                    <div class="mt-2 d-flex justify-content-between align-items-center">
                                        <small class="text-muted">
                                            <span id="streamModeDisplay">MJPEG 스트림</span> | 
                                            프레임: <span id="frameCount">0</span>
                                        </small>
                                        <div>
                                            <span class="badge bg-secondary me-1" id="streamQuality">HD</span>
                                            <span class="badge" id="connectionStatus">연결 중</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    
                    <!-- 아기 상태 정보 (기존과 동일) -->
                    <div class="col-lg-4 mb-4">
                        <div class="baby-card h-100">
                            <div class="card-header bg-success text-white">
                                <h5 class="mb-0"><i class="bi bi-person-check"></i> 아기 상태</h5>
                            </div>
                            <div class="card-body">
                                
                                <!-- 감지 상태 -->
                                <div class="text-center mb-4">
                                    <div class="baby-icon text-{detection_color} mb-2">
                                        <i class="bi bi-{'person-check' if baby_status['detected'] else 'person-x'}"></i>
                                    </div>
                                    <h4 class="text-{detection_color}">
                                        {'아기 감지됨' if baby_status['detected'] else '아기 미감지'}
                                    </h4>
                                    <span class="status-badge bg-{detection_color}">
                                        신뢰도: {baby_status['confidence']:.1%}
                                    </span>
                                </div>
                                
                                <!-- 상태 정보 -->
                                <div class="list-group list-group-flush">
                                    <div class="list-group-item d-flex justify-content-between align-items-center">
                                        <span><i class="bi bi-clock"></i> 마지막 감지</span>
                                        <small class="text-muted">{baby_status['last_seen']}</small>
                                    </div>
                                    <div class="list-group-item d-flex justify-content-between align-items-center">
                                        <span><i class="bi bi-moon"></i> 수면 시간</span>
                                        <span class="badge bg-info">{baby_status['sleep_duration']}</span>
                                    </div>
                                    <div class="list-group-item d-flex justify-content-between align-items-center">
                                        <span><i class="bi bi-shield-check"></i> 안전 상태</span>
                                        <span class="badge bg-success">안전</span>
                                    </div>
                                </div>
                                
                                <!-- 빠른 액션 -->
                                <div class="mt-3">
                                    <h6>빠른 액션</h6>
                                    <div class="d-grid gap-2">
                                        <button class="btn btn-outline-primary btn-sm" onclick="playLullaby()">
                                            <i class="bi bi-music-note"></i> 자장가 재생
                                        </button>
                                        <button class="btn btn-outline-warning btn-sm" onclick="sendAlert()">
                                            <i class="bi bi-bell"></i> 알림 보내기
                                        </button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- 환경 데이터 & 활동 로그 (기존과 동일) -->
                <div class="row">
                    
                    <!-- 환경 센서 데이터 -->
                    <div class="col-lg-6 mb-4">
                        <div class="baby-card">
                            <div class="card-header bg-info text-white">
                                <h5 class="mb-0"><i class="bi bi-thermometer"></i> 환경 센서</h5>
                            </div>
                            <div class="card-body">
                                <div class="row">
                                    <div class="col-6">
                                        <div class="metric-card p-4 text-center mb-3">
                                            <i class="bi bi-thermometer-high" style="font-size: 2rem;"></i>
                                            <h3 class="mt-2">{baby_status['temperature']}°C</h3>
                                            <p class="mb-0">온도</p>
                                        </div>
                                    </div>
                                    <div class="col-6">
                                        <div class="metric-card p-4 text-center mb-3">
                                            <i class="bi bi-droplet" style="font-size: 2rem;"></i>
                                            <h3 class="mt-2">{baby_status['humidity']}%</h3>
                                            <p class="mb-0">습도</p>
                                        </div>
                                    </div>
                                </div>
                                
                                <div class="text-center">
                                    <span class="status-badge bg-{env_color}">
                                        <i class="bi bi-{'check-circle' if baby_status['environment_status'] == '최적' else 'exclamation-triangle'}"></i>
                                        환경 상태: {baby_status['environment_status']}
                                    </span>
                                </div>
                                
                                <!-- 환경 권장사항 -->
                                <div class="mt-3 p-3 bg-light rounded">
                                    <h6><i class="bi bi-lightbulb"></i> 권장사항</h6>
                                    <ul class="mb-0 small">
                                        <li>적정 온도: 20-24°C</li>
                                        <li>적정 습도: 40-60%</li>
                                        <li>통풍이 잘 되는 환경 유지</li>
                                    </ul>
                                </div>
                            </div>
                        </div>
                    </div>
                    
                    <!-- 활동 로그 -->
                    <div class="col-lg-6 mb-4">
                        <div class="baby-card">
                            <div class="card-header bg-warning text-dark">
                                <h5 class="mb-0"><i class="bi bi-list-ul"></i> 최근 활동</h5>
                            </div>
                            <div class="card-body">
                                <div class="activity-log" style="max-height: 300px; overflow-y: auto;">
                                    
                                    <div class="activity-item p-3 mb-2">
                                        <div class="d-flex justify-content-between align-items-center">
                                            <div>
                                                <i class="bi bi-person-check text-success"></i>
                                                <strong>아기 감지됨</strong>
                                            </div>
                                            <small class="text-muted">{current_time.strftime("%H:%M")}</small>
                                        </div>
                                        <small class="text-muted">신뢰도 85% | 안전한 자세</small>
                                    </div>
                                    
                                    <div class="activity-item p-3 mb-2">
                                        <div class="d-flex justify-content-between align-items-center">
                                            <div>
                                                <i class="bi bi-thermometer text-info"></i>
                                                <strong>환경 데이터 업데이트</strong>
                                            </div>
                                            <small class="text-muted">{(current_time.replace(minute=current_time.minute-2) if current_time.minute >= 2 else current_time.replace(hour=current_time.hour-1, minute=current_time.minute+58)).strftime("%H:%M")}</small>
                                        </div>
                                        <small class="text-muted">온도: 22.5°C | 습도: 55%</small>
                                    </div>
                                    
                                    <div class="activity-item p-3 mb-2">
                                        <div class="d-flex justify-content-between align-items-center">
                                            <div>
                                                <i class="bi bi-moon text-primary"></i>
                                                <strong>수면 시작</strong>
                                            </div>
                                            <small class="text-muted">{(current_time.replace(hour=current_time.hour-2) if current_time.hour >= 2 else current_time.replace(hour=current_time.hour+22, minute=0)).strftime("%H:%M")}</small>
                                        </div>
                                        <small class="text-muted">평온한 수면 상태</small>
                                    </div>
                                    
                                    <div class="text-center mt-3">
                                        <button class="btn btn-outline-secondary btn-sm">
                                            <i class="bi bi-clock-history"></i> 전체 기록 보기
                                        </button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- 빠른 통계 (기존과 동일) -->
                <div class="row">
                    <div class="col-12">
                        <div class="baby-card">
                            <div class="card-header bg-secondary text-white">
                                <h5 class="mb-0"><i class="bi bi-graph-up"></i> 오늘의 요약</h5>
                            </div>
                            <div class="card-body">
                                <div class="row text-center">
                                    <div class="col-md-3">
                                        <div class="metric-card p-3 h-100">
                                            <i class="bi bi-moon" style="font-size: 2rem;"></i>
                                            <h4 class="mt-2">8시간 30분</h4>
                                            <p class="mb-0">총 수면시간</p>
                                        </div>
                                    </div>
                                    <div class="col-md-3">
                                        <div class="metric-card p-3 h-100">
                                            <i class="bi bi-eye" style="font-size: 2rem;"></i>
                                            <h4 class="mt-2">247회</h4>
                                            <p class="mb-0">감지 횟수</p>
                                        </div>
                                    </div>
                                    <div class="col-md-3">
                                        <div class="metric-card p-3 h-100">
                                            <i class="bi bi-exclamation-triangle" style="font-size: 2rem;"></i>
                                            <h4 class="mt-2">0회</h4>
                                            <p class="mb-0">알림 발생</p>
                                        </div>
                                    </div>
                                    <div class="col-md-3">
                                        <div class="metric-card p-3 h-100">
                                            <i class="bi bi-heart-pulse" style="font-size: 2rem;"></i>
                                            <h4 class="mt-2">98%</h4>
                                            <p class="mb-0">안전 지수</p>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
        <script>
            // 🔥 스트리밍 관련 변수들
            let autoRefreshInterval;
            let imageRefreshInterval;
            let streamStatsInterval;
            let currentMode = 'stream'; // 'stream' 또는 'image'
            let isRecording = false;
            let lastImageTimestamp = null;
            let streamRetryCount = 0;
            const maxRetryCount = 3;
    
            function refreshData() {{
                window.location.reload();
            }}
    
            // 🔥 MJPEG 스트림 관리 함수들
            function handleStreamSuccess() {{
                console.log('✅ MJPEG 스트림 연결 성공');
                updateStreamStatus('online', 'LIVE');
                document.getElementById('mjpegStreamView').style.display = 'block';
                document.getElementById('streamErrorView').style.display = 'none';
                document.getElementById('streamLoadingView').style.display = 'none';
                streamRetryCount = 0;
                
                // 스트림 통계 업데이트 시작
                startStreamStatsUpdate();
            }}
    
            function handleStreamError() {{
                console.log('❌ MJPEG 스트림 연결 실패');
                updateStreamStatus('offline', '연결 실패');
                
                streamRetryCount++;
                if (streamRetryCount < maxRetryCount) {{
                    console.log(`🔄 스트림 재연결 시도 (${{streamRetryCount}}/${{maxRetryCount}})`);
                    setTimeout(retryStream, 2000 * streamRetryCount); // 지수 백오프
                }} else {{
                    document.getElementById('mjpegStreamView').style.display = 'none';
                    document.getElementById('streamErrorView').style.display = 'block';
                    document.getElementById('streamLoadingView').style.display = 'none';
                    
                    // 이미지 모드로 자동 폴백 (옵션)
                    setTimeout(() => {{
                        if (confirm('스트림 연결에 실패했습니다. 이미지 모드로 전환하시겠습니까?')) {{
                            switchToImageMode();
                        }}
                    }}, 3000);
                }}
            }}
    
            function retryStream() {{
                console.log('🔄 스트림 재연결 시도');
                updateStreamStatus('loading', '재연결 중...');
                
                document.getElementById('streamErrorView').style.display = 'none';
                document.getElementById('streamLoadingView').style.display = 'block';
                
                const streamImg = document.getElementById('mjpegStream');
                streamImg.src = '/stream?' + new Date().getTime(); // 캐시 방지
            }}
    
            function updateStreamStatus(status, text) {{
                const indicator = document.getElementById('streamStatusIndicator');
                const statusText = document.getElementById('streamStatusText');
                const connectionStatus = document.getElementById('connectionStatus');
                
                // 상태 표시기 업데이트
                indicator.className = `stream-status-indicator status-${{status}}`;
                statusText.textContent = text;
                
                // 연결 상태 배지 업데이트
                switch(status) {{
                    case 'online':
                        connectionStatus.className = 'badge bg-success';
                        connectionStatus.textContent = '연결됨';
                        break;
                    case 'offline':
                        connectionStatus.className = 'badge bg-danger';
                        connectionStatus.textContent = '연결 안됨';
                        break;
                    case 'loading':
                        connectionStatus.className = 'badge bg-warning';
                        connectionStatus.textContent = '연결 중';
                        break;
                }}
            }}
    
            // 🔥 스트림 통계 업데이트
            async function startStreamStatsUpdate() {{
                if (streamStatsInterval) {{
                    clearInterval(streamStatsInterval);
                }}
                
                streamStatsInterval = setInterval(async () => {{
                    try {{
                        const response = await fetch('/stream/status');
                        const data = await response.json();
                        
                        // 시청자 수 업데이트
                        document.getElementById('viewerCount').textContent = data.viewers || 0;
                        
                        // 프레임 수 업데이트
                        document.getElementById('frameCount').textContent = data.frame_count || 0;
                        
                        // ESP Eye 연결 상태 확인
                        if (!data.esp_eye_connected && currentMode === 'stream') {{
                            console.log('⚠️ ESP Eye 연결 끊김 감지');
                            updateStreamStatus('offline', 'ESP Eye 연결 끊김');
                        }}
                        
                    }} catch (error) {{
                        console.error('📊 스트림 통계 업데이트 실패:', error);
                    }}
                }}, 5000);
            }}
    
            // 🔥 모드 전환 함수들
            function toggleStreamMode() {{
                if (currentMode === 'stream') {{
                    switchToImageMode();
                }} else {{
                    switchToStreamMode();
                }}
            }}
    
            function switchToStreamMode() {{
                console.log('📺 스트림 모드로 전환');
                currentMode = 'stream';
                
                document.getElementById('mjpegStreamView').style.display = 'block';
                document.getElementById('imageView').style.display = 'none';
                document.getElementById('streamModeText').textContent = '이미지 모드';
                document.getElementById('streamModeDisplay').textContent = 'MJPEG 스트림';
                
                // 이미지 새로고침 중지
                if (imageRefreshInterval) {{
                    clearInterval(imageRefreshInterval);
                }}
                
                // 스트림 재시작
                retryStream();
            }}
    
            function switchToImageMode() {{
                console.log('🖼️ 이미지 모드로 전환');
                currentMode = 'image';
                
                document.getElementById('mjpegStreamView').style.display = 'none';
                document.getElementById('imageView').style.display = 'block';
                document.getElementById('streamErrorView').style.display = 'none';
                document.getElementById('streamLoadingView').style.display = 'none';
                document.getElementById('streamModeText').textContent = '스트림 모드';
                document.getElementById('streamModeDisplay').textContent = '이미지 모드';
                
                // 스트림 통계 업데이트 중지
                if (streamStatsInterval) {{
                    clearInterval(streamStatsInterval);
                }}
                
                // 이미지 새로고침 시작
                requestLatestImage();
                setupImageAutoRefresh();
                
                updateStreamStatus('offline', '이미지 모드');
            }}
    
            // 🔥 이미지 모드 함수들 (기존 코드 개선)
            async function requestLatestImage() {{
                try {{
                    console.log('📷 최신 이미지 요청 중...');
        
                    const response = await fetch('/images/latest');
                    const data = await response.json();
        
                    if (data.status === 'success' && data.has_image && data.image_base64) {{
                        const base64Data = data.image_base64.trim();
            
                        if (base64Data.length > 0) {{
                            displayImage(base64Data, data.timestamp);
                            console.log('✅ 이미지 로드 성공:', data.size + ' bytes');
                        }} else {{
                            console.log('⚠️ 빈 이미지 데이터');
                        }}
                    }} else {{
                        console.log('⚠️ 이미지 없음:', data.message || 'Unknown error');
                    }}
        
                }} catch (error) {{
                    console.error('❌ 이미지 요청 실패:', error);
                }}
            }}
    
            function displayImage(base64Data, timestamp) {{
                const latestImage = document.getElementById('latestImage');
                const timestampElement = document.getElementById('imageTimestamp');

                try {{
                    let imageUrl;
                    if (base64Data.startsWith('data:')) {{
                        imageUrl = base64Data;
                    }} else {{
                        imageUrl = 'data:image/jpeg;base64,' + base64Data;
                    }}
        
                    const testImg = new Image();
                    testImg.onload = function() {{
                        console.log('✅ 이미지 표시 성공:', testImg.width + 'x' + testImg.height);
                        latestImage.src = imageUrl;
            
                        if (timestamp) {{
                            const date = new Date(timestamp);
                            timestampElement.textContent = date.toLocaleTimeString();
                            lastImageTimestamp = timestamp;
                        }}
                    }};
        
                    testImg.onerror = function() {{
                        console.error('❌ 이미지 표시 실패');
                    }};
        
                    testImg.src = imageUrl;
        
                }} catch (error) {{
                    console.error('❌ 이미지 처리 오류:', error);
                }}
            }}
    
            function setupImageAutoRefresh() {{
                if (imageRefreshInterval) {{
                    clearInterval(imageRefreshInterval);
                }}
                
                imageRefreshInterval = setInterval(async () => {{
                    if (currentMode === 'image') {{
                        await requestLatestImage();
                    }}
                }}, 3000); // 3초마다 이미지 새로고침
            }}
    
            // 🔥 컨트롤 함수들
            function captureSnapshot() {{
                if (currentMode === 'stream') {{
                    // 스트림에서 스냅샷 캡처
                    const canvas = document.createElement('canvas');
                    const img = document.getElementById('mjpegStream');
                    canvas.width = img.naturalWidth || img.width;
                    canvas.height = img.naturalHeight || img.height;
                    
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0);
                    
                    // 다운로드
                    const link = document.createElement('a');
                    link.download = `baby_monitor_snapshot_${{new Date().toISOString().slice(0,19).replace(/:/g,'-')}}.jpg`;
                    link.href = canvas.toDataURL('image/jpeg', 0.9);
                    link.click();
                    
                    console.log('📸 스냅샷 캡처 완료');
                }} else {{
                    // 이미지 모드에서는 현재 이미지 다운로드
                    const img = document.getElementById('latestImage');
                    const link = document.createElement('a');
                    link.download = `baby_monitor_image_${{new Date().toISOString().slice(0,19).replace(/:/g,'-')}}.jpg`;
                    link.href = img.src;
                    link.click();
                    
                    console.log('📷 이미지 저장 완료');
                }}
            }}
    
            function toggleRecording() {{
                isRecording = !isRecording;
                const recordText = document.getElementById('recordText');
                
                if (isRecording) {{
                    recordText.textContent = '중지';
                    console.log('🔴 녹화 시작');
                    // 실제 녹화 로직 구현 필요
                }} else {{
                    recordText.textContent = '녹화';
                    console.log('⏹️ 녹화 중지');
                }}
            }}
    
            function toggleNightMode() {{
                // ESP32에 야간모드 명령 전송
                fetch('/esp32/command', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ 
                        command: 'night_mode', 
                        params: {{ enable: true }} 
                    }})
                }})
                .then(response => response.json())
                .then(data => {{
                    console.log('🌙 야간모드 토글:', data);
                }})
                .catch(error => {{
                    console.error('❌ 야간모드 토글 실패:', error);
                }});
            }}
    
            function openFullscreen() {{
                const videoContainer = document.getElementById('videoContainer');
                if (videoContainer.requestFullscreen) {{
                    videoContainer.requestFullscreen();
                }} else if (videoContainer.webkitRequestFullscreen) {{
                    videoContainer.webkitRequestFullscreen();
                }} else if (videoContainer.msRequestFullscreen) {{
                    videoContainer.msRequestFullscreen();
                }}
            }}
    
            // 🔥 기존 함수들
            function playLullaby() {{
                fetch('/esp32/command', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ command: 'play_lullaby', params: {{ song: 'brahms' }} }})
                }})
                .then(response => response.json())
                .then(data => {{
                    alert('🎵 자장가 재생 명령을 전송했습니다!');
                }})
                .catch(error => {{
                    alert('❌ 명령 전송 실패: ' + error.message);
                }});
            }}
            
            function sendAlert() {{
                alert('🔔 알림이 모든 연결된 앱으로 전송되었습니다!');
            }}
    
            function setupAutoRefresh() {{
                const checkbox = document.getElementById('autoRefresh');
        
                if (checkbox.checked) {{
                    autoRefreshInterval = setInterval(() => {{
                        // 전체 페이지 새로고침은 30초마다
                        refreshData();
                    }}, 30000);
                }} else {{
                    clearInterval(autoRefreshInterval);
                }}
            }}
    
            // 🔥 초기화
            document.addEventListener('DOMContentLoaded', function() {{
                console.log('🚀 Baby Monitor 대시보드 초기화');
                
                // 자동 새로고침 설정
                document.getElementById('autoRefresh').addEventListener('change', setupAutoRefresh);
                setupAutoRefresh();
                
                // 기본적으로 스트림 모드로 시작
                updateStreamStatus('loading', '연결 중...');
                
                // 스트림 로드 이벤트 리스너 (실제 이미지 로드 후 호출)
                const streamImg = document.getElementById('mjpegStream');
                streamImg.addEventListener('load', handleStreamSuccess);
                streamImg.addEventListener('error', handleStreamError);
                
                // 초기 연결 상태 확인
                setTimeout(() => {{
                    fetch('/stream/status')
                        .then(response => response.json())
                        .then(data => {{
                            console.log('📊 초기 스트림 상태:', data);
                            if (!data.esp_eye_connected) {{
                                console.log('⚠️ ESP Eye 연결되지 않음 - 이미지 모드로 시작');
                                switchToImageMode();
                            }}
                        }})
                        .catch(error => {{
                            console.log('⚠️ 스트림 상태 확인 실패 - 이미지 모드로 폴백');
                            switchToImageMode();
                        }});
                }}, 2000);
            }});
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)
@app.get("/report", response_class=HTMLResponse)
def health_report():
    """시스템 상태 보고서 페이지"""
    
    # 시스템 상태 데이터 수집
    current_time = get_korea_time()
    current_time_str = current_time.strftime("%Y년 %m월 %d일 %H:%M:%S")
    
    # Redis 상태
    redis_status = redis_manager.available if MODULES_AVAILABLE else False
    redis_info = "정상 연결됨" if redis_status else "연결되지 않음"
    redis_color = "success" if redis_status else "danger"
    
    # ESP32 상태
    esp32_connected = esp32_handler.esp32_status == "connected" if hasattr(esp32_handler, 'esp32_status') else False
    esp32_status_text = esp32_handler.esp32_status if hasattr(esp32_handler, 'esp32_status') else "unknown"
    esp32_ip = esp32_handler.esp32_ip if hasattr(esp32_handler, 'esp32_ip') else "설정되지 않음"
    esp32_color = "success" if esp32_connected else "warning"
    
    # 앱 연결 상태
    active_connections = len(websocket_manager.active_connections) if MODULES_AVAILABLE else 0
    connection_color = "success" if active_connections > 0 else "secondary"
    
    # 모듈 상태
    modules_status = "사용 가능" if MODULES_AVAILABLE else "사용 불가"
    modules_color = "success" if MODULES_AVAILABLE else "warning"
    
    # 최근 데이터 확인 (Redis 사용 가능한 경우)
    recent_data = None
    last_update = "정보 없음"
    sensor_data_html = ""
    
    if MODULES_AVAILABLE and redis_manager.available:
        try:
            recent_data = redis_manager.get_current_status()
            if recent_data:
                last_update = recent_data.get("timestamp", "알 수 없음")
                temperature = recent_data.get("temperature", "N/A")
                humidity = recent_data.get("humidity", "N/A")
                
                sensor_data_html = f"""
                <div class="mt-3">
                    <h6>최근 센서 데이터:</h6>
                    <div class="row">
                        <div class="col-6">
                            <div class="text-center p-2 bg-light rounded">
                                <i class="bi bi-thermometer text-danger"></i>
                                <div><strong>{temperature}°C</strong></div>
                                <small>온도</small>
                            </div>
                        </div>
                        <div class="col-6">
                            <div class="text-center p-2 bg-light rounded">
                                <i class="bi bi-droplet text-primary"></i>
                                <div><strong>{humidity}%</strong></div>
                                <small>습도</small>
                            </div>
                        </div>
                    </div>
                </div>
                """
        except:
            pass

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Baby Monitor 시스템 상태 보고서</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.7.2/font/bootstrap-icons.css" rel="stylesheet">
        <style>
            body {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }}
            .report-container {{ background: white; border-radius: 20px; box-shadow: 0 20px 40px rgba(0,0,0,0.1); }}
            .status-card {{ border: none; border-radius: 15px; transition: transform 0.3s ease; }}
            .status-card:hover {{ transform: translateY(-5px); }}
            .metric-box {{ background: linear-gradient(45deg, #f8f9fa, #e9ecef); border-radius: 15px; }}
            .header-section {{ background: linear-gradient(135deg, #667eea, #764ba2); color: white; border-radius: 20px 20px 0 0; }}
            .status-icon {{ font-size: 2rem; }}
            .refresh-btn {{ position: fixed; bottom: 30px; right: 30px; border-radius: 50%; width: 60px; height: 60px; }}
        </style>
    </head>
    <body>
        <div class="container py-5">
            <div class="report-container mx-auto" style="max-width: 1200px;">
                
                <!-- 헤더 섹션 -->
                <div class="header-section p-5 text-center">
                    <h1 class="display-4 mb-3">
                        <i class="bi bi-heart-pulse"></i> Baby Monitor
                    </h1>
                    <h2 class="h3 mb-4">시스템 상태 보고서</h2>
                    <div class="row">
                        <div class="col-md-6">
                            <p class="mb-1"><i class="bi bi-calendar3"></i> {current_time.strftime("%Y년 %m월 %d일")}</p>
                            <p class="mb-0"><i class="bi bi-clock"></i> {current_time.strftime("%H:%M:%S")}</p>
                        </div>
                        <div class="col-md-6">
                            <p class="mb-1"><i class="bi bi-server"></i> 서버 버전 2.0.0</p>
                            <p class="mb-0"><i class="bi bi-geo-alt"></i> Railway Cloud</p>
                        </div>
                    </div>
                </div>
                
                <!-- 전체 상태 요약 -->
                <div class="p-4 bg-light">
                    <div class="row text-center">
                        <div class="col-md-3">
                            <div class="metric-box p-3 h-100">
                                <i class="bi bi-server status-icon text-primary"></i>
                                <h4 class="mt-2">서버</h4>
                                <span class="badge bg-success fs-6">정상 작동</span>
                            </div>
                        </div>
                        <div class="col-md-3">
                            <div class="metric-box p-3 h-100">
                                <i class="bi bi-database status-icon text-{redis_color}"></i>
                                <h4 class="mt-2">Redis</h4>
                                <span class="badge bg-{redis_color} fs-6">{redis_info}</span>
                            </div>
                        </div>
                        <div class="col-md-3">
                            <div class="metric-box p-3 h-100">
                                <i class="bi bi-router status-icon text-{esp32_color}"></i>
                                <h4 class="mt-2">ESP32</h4>
                                <span class="badge bg-{esp32_color} fs-6">{esp32_status_text}</span>
                            </div>
                        </div>
                        <div class="col-md-3">
                            <div class="metric-box p-3 h-100">
                                <i class="bi bi-phone status-icon text-{connection_color}"></i>
                                <h4 class="mt-2">앱 연결</h4>
                                <span class="badge bg-{connection_color} fs-6">{active_connections}개</span>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- 상세 정보 섹션 -->
                <div class="p-5">
                    <div class="row">
                        
                        <!-- 시스템 구성 요소 -->
                        <div class="col-lg-6 mb-4">
                            <div class="card status-card h-100">
                                <div class="card-header bg-primary text-white">
                                    <h5 class="mb-0"><i class="bi bi-gear"></i> 시스템 구성 요소</h5>
                                </div>
                                <div class="card-body">
                                    <div class="list-group list-group-flush">
                                        <div class="list-group-item d-flex justify-content-between align-items-center">
                                            <span><i class="bi bi-puzzle"></i> 고급 모듈</span>
                                            <span class="badge bg-{modules_color}">{modules_status}</span>
                                        </div>
                                        <div class="list-group-item d-flex justify-content-between align-items-center">
                                            <span><i class="bi bi-database"></i> Redis 캐시</span>
                                            <span class="badge bg-{redis_color}">{redis_info}</span>
                                        </div>
                                        <div class="list-group-item d-flex justify-content-between align-items-center">
                                            <span><i class="bi bi-wifi"></i> WebSocket</span>
                                            <span class="badge bg-success">활성화</span>
                                        </div>
                                        <div class="list-group-item d-flex justify-content-between align-items-center">
                                            <span><i class="bi bi-camera"></i> 이미지 처리</span>
                                            <span class="badge bg-{modules_color}">{'활성화' if MODULES_AVAILABLE else '비활성화'}</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        
                        <!-- ESP32 디바이스 정보 -->
                        <div class="col-lg-6 mb-4">
                            <div class="card status-card h-100">
                                <div class="card-header bg-info text-white">
                                    <h5 class="mb-0"><i class="bi bi-router"></i> ESP32 디바이스</h5>
                                </div>
                                <div class="card-body">
                                    <div class="list-group list-group-flush">
                                        <div class="list-group-item d-flex justify-content-between align-items-center">
                                            <span><i class="bi bi-link"></i> 연결 상태</span>
                                            <span class="badge bg-{esp32_color}">{esp32_status_text}</span>
                                        </div>
                                        <div class="list-group-item d-flex justify-content-between align-items-center">
                                            <span><i class="bi bi-geo"></i> IP 주소</span>
                                            <code>{esp32_ip}</code>
                                        </div>
                                        <div class="list-group-item d-flex justify-content-between align-items-center">
                                            <span><i class="bi bi-clock-history"></i> 마지막 업데이트</span>
                                            <small class="text-muted">{last_update}</small>
                                        </div>
                                    </div>
                                    {sensor_data_html}
                                </div>
                            </div>
                        </div>
                        
                        <!-- 모바일 앱 연결 -->
                        <div class="col-lg-6 mb-4">
                            <div class="card status-card h-100">
                                <div class="card-header bg-success text-white">
                                    <h5 class="mb-0"><i class="bi bi-phone"></i> 모바일 앱 연결</h5>
                                </div>
                                <div class="card-body">
                                    <div class="text-center mb-3">
                                        <div class="display-4 text-{connection_color}">{active_connections}</div>
                                        <p class="mb-0">활성 연결</p>
                                    </div>
                                    <div class="list-group list-group-flush">
                                        <div class="list-group-item d-flex justify-content-between align-items-center">
                                            <span><i class="bi bi-broadcast"></i> WebSocket 상태</span>
                                            <span class="badge bg-success">활성</span>
                                        </div>
                                        <div class="list-group-item d-flex justify-content-between align-items-center">
                                            <span><i class="bi bi-arrow-repeat"></i> 실시간 업데이트</span>
                                            <span class="badge bg-success">활성화</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        
                        <!-- API 엔드포인트 -->
                        <div class="col-lg-6 mb-4">
                            <div class="card status-card h-100">
                                <div class="card-header bg-secondary text-white">
                                    <h5 class="mb-0"><i class="bi bi-code"></i> API 엔드포인트</h5>
                                </div>
                                <div class="card-body">
                                    <div class="list-group list-group-flush">
                                        <div class="list-group-item d-flex justify-content-between align-items-center">
                                            <span><code>POST /esp32/data</code></span>
                                            <span class="badge bg-success">활성</span>
                                        </div>
                                        <div class="list-group-item d-flex justify-content-between align-items-center">
                                            <span><code>WebSocket /app/stream</code></span>
                                            <span class="badge bg-success">활성</span>
                                        </div>
                                        <div class="list-group-item d-flex justify-content-between align-items-center">
                                            <span><code>GET /status</code></span>
                                            <span class="badge bg-success">활성</span>
                                        </div>
                                        <div class="list-group-item d-flex justify-content-between align-items-center">
                                            <span><code>GET /images/latest</code></span>
                                            <span class="badge bg-{'success' if MODULES_AVAILABLE else 'secondary'}">{'활성' if MODULES_AVAILABLE else '비활성'}</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    
                    <!-- 액션 버튼들 -->
                    <div class="text-center mt-4">
                        <a href="/test" class="btn btn-primary btn-lg me-3">
                            <i class="bi bi-gear"></i> 테스트 페이지
                        </a>
                        <a href="/docs" class="btn btn-outline-primary btn-lg me-3">
                            <i class="bi bi-book"></i> API 문서
                        </a>
                        <a href="/status" class="btn btn-outline-secondary btn-lg">
                            <i class="bi bi-info-circle"></i> JSON 상태
                        </a>
                    </div>
                </div>
                
                <!-- 푸터 -->
                <div class="bg-light p-3 text-center" style="border-radius: 0 0 20px 20px;">
                    <small class="text-muted">
                        <i class="bi bi-shield-check"></i> Baby Monitor Server v2.0.0 | 
                        마지막 업데이트: {current_time.strftime("%Y-%m-%d %H:%M:%S")}
                    </small>
                </div>
            </div>
        </div>
        
        <!-- 새로고침 버튼 -->
        <button class="btn btn-primary refresh-btn" onclick="window.location.reload()">
            <i class="bi bi-arrow-clockwise"></i>
        </button>
        
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
        <script>
            // 자동 새로고침 (30초마다)
            setTimeout(() => window.location.reload(), 30000);
        </script>
    </body>
    </html>
    """
    
    return HTMLResponse(content=html_content)

# 기존 초기화 코드 후에 추가
if MODULES_AVAILABLE:
    # ... 기존 매니저들 초기화 ...
    
    # 앱 API 핸들러 초기화
    app_api_handler = AppApiHandler(
        redis_manager=redis_manager,
        websocket_manager=websocket_manager,
        esp32_handler=esp32_handler,
        image_handler=image_handler
    )
    
    # 앱 API 라우터를 메인 앱에 포함
    app.include_router(app_api_handler.get_router())
    
    print("📱 앱 API 핸들러 초기화 완료")
else:
    # 기본 모드일 때는 더미 핸들러
    app_api_handler = None
    print("📱 앱 API 핸들러 비활성화 (모듈 없음)")

# ESP32에서 데이터가 올 때 앱들에게 브로드캐스트하는 함수 수정
@app.post("/esp32/data")
async def receive_esp32_data(data: Dict[str, Any]):
    """ESP32에서 WiFi로 데이터 수신"""
    if not MODULES_AVAILABLE:
        return {"error": "Modules not available", "data_received": data}
    
    try:
        # ESP32 핸들러로 전체 처리 위임
        result = await esp32_handler.handle_esp32_data(data)
        
        # 이미지가 포함되어 있으면 별도 처리
        if "image_base64" in data and data["image_base64"]:
            image_result = image_handler.process_esp32_image(
                data["image_base64"], 
                save_to_disk=True
            )
            result["image_processing"] = image_result
        
        # 🔥 새로운 부분: 모든 연결된 앱들에게 실시간 브로드캐스트
        if websocket_manager.active_connections:
            broadcast_data = {
                "type": "new_data",
                "source": "esp32",
                "data": data,
                "timestamp": datetime.now().isoformat()
            }
            await websocket_manager.broadcast_to_apps(broadcast_data)
        
        return result
        
    except Exception as e:
        print(f"❌ ESP32 데이터 수신 오류: {e}")
        raise HTTPException(status_code=500, detail=f"ESP32 data processing failed: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))

    # Railway에서 PORT가 6379로 잘못 설정된 경우 방지
    if port == 6379:
        print("⚠️ PORT가 Redis 포트(6379)로 설정됨 - 8000으로 변경")
        port = 8000
    
    print(f"🚀 서버 시작 중... 포트 {port}")
    print(f"📊 모듈 상태: {'사용 가능' if MODULES_AVAILABLE else '기본 모드'}")
    
    uvicorn.run(app, host="0.0.0.0", port=port)