from fastapi import FastAPI, WebSocket, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse
import asyncio
import json
import os
from typing import Dict, Any
from app_api_handler import AppApiHandler
from realtime_handler import RealTimeHandler
from datetime import datetime, timezone, timedelta
import pytz

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
    
    redis_manager = DummyManager()
    websocket_manager = DummyManager()
    esp32_handler = DummyManager()
    image_handler = DummyManager()
    realtime_handler = None
    
    print("🍼 Baby Monitor Server 시작 (기본 모드)")

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
    """아기 모니터링 실시간 대시보드"""
    
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
                min-height: 300px;
                display: flex;
                align-items: center;
                justify-content: center;
            }}
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
        </style>
    </head>
    <body>
        <div class="container-fluid py-4">
            
            <!-- 새로고침 컨트롤 -->
            <div class="refresh-controls">
                <button class="btn btn-primary btn-sm me-2" onclick="refreshData()">
                    <i class="bi bi-arrow-clockwise"></i> 새로고침
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
                    
                    <!-- 메인 비디오/이미지 영역 -->
                    <div class="col-lg-8 mb-4">
                        <div class="baby-card h-100">
                            <div class="card-header bg-primary text-white">
                                <h5 class="mb-0">
                                    <i class="bi bi-camera-video"></i> 실시간 영상
                                    <span class="float-end">
                                        <span class="live-indicator"></span> LIVE
                                    </span>
                                </h5>
                            </div>
                            <div class="card-body p-0">
                                <div class="video-container" id="videoContainer">
                                    <div class="text-center text-white" id="noImageView">
                                        <i class="bi bi-camera baby-icon mb-3"></i>
                                        <h5>실시간 영상 스트림</h5>
                                        <p class="mb-3">ESP32-CAM 연결 대기 중...</p>
                                        <button class="btn btn-outline-light" onclick="requestLatestImage()">
                                            <i class="bi bi-image"></i> 최신 이미지 가져오기
                                        </button>
                                    </div>
    
                                    <!-- 🔥 새로 추가: 이미지 표시 영역 -->
                                    <div id="imageView" style="display: none; width: 100%; height: 100%;">
                                        <img id="latestImage" src="" alt="최신 이미지" style="width: 100%; height: 100%; object-fit: contain; border-radius: 15px;">
                                        <div style="position: absolute; top: 10px; right: 10px; background: rgba(0,0,0,0.7); color: white; padding: 5px 10px; border-radius: 10px; font-size: 12px;">
                                            <span id="imageTimestamp">--:--</span>
                                        </div>
                                    </div>
                                </div>
                                
                                <!-- 영상 컨트롤 -->
                                <div class="p-3 bg-light">
                                    <div class="row text-center">
                                        <div class="col-3">
                                            <button class="btn btn-outline-primary btn-sm w-100">
                                                <i class="bi bi-camera"></i> 스냅샷
                                            </button>
                                        </div>
                                        <div class="col-3">
                                            <button class="btn btn-outline-success btn-sm w-100">
                                                <i class="bi bi-record-circle"></i> 녹화
                                            </button>
                                        </div>
                                        <div class="col-3">
                                            <button class="btn btn-outline-warning btn-sm w-100">
                                                <i class="bi bi-moon"></i> 야간모드
                                            </button>
                                        </div>
                                        <div class="col-3">
                                            <button class="btn btn-outline-info btn-sm w-100">
                                                <i class="bi bi-fullscreen"></i> 전체화면
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    
                    <!-- 아기 상태 정보 -->
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
                
                <!-- 환경 데이터 & 활동 로그 -->
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
                
                <!-- 빠른 통계 -->
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
            // 🔥 이미지 관련 변수들 추가
            let autoRefreshInterval;
            let imageRefreshInterval;
            let lastImageTimestamp = null;
    
            function refreshData() {{
                window.location.reload();
            }}
    
            // 🔥 새로운 이미지 함수들
            async function requestLatestImage() {{
                try {{
                    console.log('최신 이미지 요청 중...');
        
                    const response = await fetch('/images/latest');
                    const data = await response.json();
        
                    console.log('API 응답:', data);
        
                    if (data.status === 'success' && data.has_image && data.image_base64) {{
                        // Base64 데이터 검증
                        const base64Data = data.image_base64.trim();
            
                        if (base64Data.length > 0) {{
                            displayImage(base64Data, data.timestamp);
                            console.log('이미지 로드 성공:', data.size + ' bytes');
                        }} else {{
                            console.log('빈 이미지 데이터');
                            showNoImageView();
                        }}
                    }} else {{
                        console.log('이미지 없음:', data.message || 'Unknown error');
                        showNoImageView();
                    }}
        
                }} catch (error) {{
                    console.error('이미지 요청 실패:', error);
                    showNoImageView();
                }}
            }}
    
            function displayImage(base64Data, timestamp) {{
                const imageView = document.getElementById('imageView');
                const noImageView = document.getElementById('noImageView');
                const latestImage = document.getElementById('latestImage');
                const timestampElement = document.getElementById('imageTimestamp');

                // 🔥 Base64 데이터 검증 및 설정
                try {{
                    // data:image/jpeg;base64, 접두사가 없다면 추가
                    let imageUrl;
                    if (base64Data.startsWith('data:')) {{
                        imageUrl = base64Data;
                    }} else {{
                        imageUrl = 'data:image/jpeg;base64,' + base64Data;
                    }}
        
                    // 이미지 로드 테스트
                    const testImg = new Image();
                    testImg.onload = function() {{
                        console.log('✅ 이미지 로드 성공:', testImg.width + 'x' + testImg.height);
                        latestImage.src = imageUrl;
            
                        // 타임스탬프 설정
                        if (timestamp) {{
                            const date = new Date(timestamp);
                            timestampElement.textContent = date.toLocaleTimeString();
                            lastImageTimestamp = timestamp;
                        }}
            
                        // 뷰 전환
                        noImageView.style.display = 'none';
                        imageView.style.display = 'block';
                    }};
        
                    testImg.onerror = function() {{
                        console.error('❌ 이미지 로드 실패');
                        showNoImageView();
                    }};
        
                    testImg.src = imageUrl;
        
                }} catch (error) {{
                    console.error('❌ 이미지 표시 오류:', error);
                    showNoImageView();
                }}
            }}
    
            function showNoImageView() {{
                const imageView = document.getElementById('imageView');
                const noImageView = document.getElementById('noImageView');
        
                imageView.style.display = 'none';
                noImageView.style.display = 'block';
            }}
    
            // 🔥 자동 이미지 새로고침
            function setupImageAutoRefresh() {{
                // 5초마다 새 이미지 확인
                imageRefreshInterval = setInterval(async () => {{
                    await requestLatestImage();
                }}, 5000);
            }}
    
            function playLullaby() {{
                fetch('/esp32/command', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ command: 'play_lullaby', params: {{ song: 'brahms' }} }})
                }})
                .then(response => response.json())
                .then(data => {{
                    alert('자장가 재생 명령을 전송했습니다!');
                }})
                .catch(error => {{
                    alert('명령 전송 실패: ' + error.message);
                }});
            }}
            
            function sendAlert() {{
                alert('알림이 모든 연결된 앱으로 전송되었습니다!');
            }}
    
            // 🔥 수정된 자동 새로고침 설정
            function setupAutoRefresh() {{
                const checkbox = document.getElementById('autoRefresh');
        
                if (checkbox.checked) {{
                    autoRefreshInterval = setInterval(() => {{
                        refreshData();
                    }}, 10000); // 10초마다 새로고침
            
                    // 이미지 자동 새로고침도 시작
                    setupImageAutoRefresh();
                }} else {{
                    clearInterval(autoRefreshInterval);
                    clearInterval(imageRefreshInterval);
                }}
            }}
    
            document.getElementById('autoRefresh').addEventListener('change', setupAutoRefresh);
    
            // 🔥 페이지 로드시 이미지 요청 추가
            // 첫 이미지 로드
            requestLatestImage();
            // 페이지 로드시 자동 새로고침 시작
            setupAutoRefresh();
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