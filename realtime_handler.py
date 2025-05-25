"""
실시간 타임스탬프 동기화 및 시간 관리 (안전한 한국 시간 버전)
"""

from datetime import datetime, timezone, timedelta
import asyncio
from typing import Dict, Any

# 한국 시간대 설정 (안전한 버전)
def get_korea_time():
    """한국 시간을 안전하게 반환"""
    try:
        utc_now = datetime.now(timezone.utc)
        korea_offset = timedelta(hours=9)
        korea_tz = timezone(korea_offset)
        return utc_now.astimezone(korea_tz)
    except Exception as e:
        print(f"⚠️ 한국 시간 계산 오류: {e}, UTC 사용")
        return datetime.now(timezone.utc)

# 한국 시간대 객체
KST = timezone(timedelta(hours=9))

class RealTimeHandler:
    def __init__(self, redis_manager, websocket_manager):
        self.redis_manager = redis_manager
        self.websocket_manager = websocket_manager
        self.server_start_time = datetime.now(timezone.utc)  # 일단 UTC로 시작
        self.last_heartbeat = datetime.now(timezone.utc)
        
        # 실시간 브로드캐스트 태스크
        self.heartbeat_task = None
        self.start_heartbeat()
    
    def start_heartbeat(self):
        """실시간 하트비트 시작"""
        if self.heartbeat_task is None or self.heartbeat_task.done():
            self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            print("💓 실시간 하트비트 시작")
    
    def stop_heartbeat(self):
        """실시간 하트비트 중지"""
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()
            print("💓 실시간 하트비트 중지")
    
    async def _heartbeat_loop(self):
        """5초마다 실시간 시간 브로드캐스트"""
        try:
            while True:
                await asyncio.sleep(5)  # 5초마다
                await self._send_time_update()
        except asyncio.CancelledError:
            print("💓 하트비트 루프 취소됨")
        except Exception as e:
            print(f"❌ 하트비트 오류: {e}")
            await asyncio.sleep(10)
    
    async def _send_time_update(self):
        """모든 클라이언트에게 시간 업데이트 전송"""
        try:
            # 시간 정보 수집
            current_utc = datetime.now(timezone.utc)
            current_kst = get_korea_time()
            self.last_heartbeat = current_utc
            
            # 서버 업타임 계산
            uptime_seconds = (current_utc - self.server_start_time).total_seconds()
            
            # Redis에서 최신 데이터 확인 (안전하게)
            latest_data_time = None
            data_age_seconds = None
            
            try:
                if self.redis_manager and hasattr(self.redis_manager, 'available') and self.redis_manager.available:
                    latest_data = self.redis_manager.get_current_status()
                    if latest_data and latest_data.get("timestamp"):
                        latest_data_time = latest_data["timestamp"]
                        try:
                            latest_dt = datetime.fromisoformat(latest_data_time.replace('Z', '+00:00'))
                            data_age_seconds = (current_utc - latest_dt).total_seconds()
                        except:
                            data_age_seconds = None
            except Exception as e:
                print(f"⚠️ Redis 데이터 확인 오류: {e}")
            
            # 브로드캐스트 메시지 구성 (기존 구조 + 한국 시간 추가)
            time_update = {
                "type": "time_update",
                
                # 기존 필드들 (호환성 유지)
                "server_time_utc": current_utc.isoformat(),
                "server_timezone": "UTC",
                "local_time": current_utc.isoformat(),
                "uptime_seconds": uptime_seconds,
                "latest_data_time": latest_data_time,
                "data_age_seconds": data_age_seconds,
                "data_is_fresh": data_age_seconds < 30 if data_age_seconds else False,
                "timestamp": current_utc.isoformat(),
                
                # 새로 추가: 한국 시간 정보
                "korea_time": current_kst.strftime("%Y년 %m월 %d일 %H:%M:%S"),
                "korea_time_simple": current_kst.strftime("%Y-%m-%d %H:%M:%S"),
                "korea_hour_minute": current_kst.strftime("%H:%M"),
                "korea_date": current_kst.strftime("%Y년 %m월 %d일"),
                "server_time_kst": current_kst.isoformat(),
                "timezone_kst": "Asia/Seoul (UTC+9)"
            }
            
            # 디버깅 로그
            print(f"🕘 시간 업데이트: UTC {current_utc.strftime('%H:%M:%S')} / KST {current_kst.strftime('%H:%M:%S')}")
            
            # 모든 연결된 클라이언트에게 전송
            if self.websocket_manager and hasattr(self.websocket_manager, 'active_connections'):
                if len(self.websocket_manager.active_connections) > 0:
                    await self.websocket_manager.broadcast_to_all(time_update)
                    
        except Exception as e:
            print(f"❌ 시간 업데이트 전송 오류: {e}")
    
    def get_current_timestamp(self) -> str:
        """현재 UTC 타임스탬프 반환 (기존 호환성)"""
        return datetime.now(timezone.utc).isoformat()
    
    def get_korea_timestamp(self) -> str:
        """현재 한국 시간 타임스탬프 반환"""
        return get_korea_time().isoformat()
    
    def get_local_timestamp(self) -> str:
        """현재 로컬 타임스탬프 반환 (기존 호환성)"""
        return datetime.now().isoformat()
    
    def get_time_info(self) -> Dict[str, Any]:
        """현재 시간 정보 반환"""
        current_utc = datetime.now(timezone.utc)
        current_kst = get_korea_time()
        
        return {
            # 기존 필드들 (호환성)
            "utc_time": current_utc.isoformat(),
            "local_time": current_utc.isoformat(),
            "timezone": "UTC",
            "timestamp_unix": int(current_utc.timestamp()),
            "server_uptime": (current_utc - self.server_start_time).total_seconds(),
            "last_heartbeat": self.last_heartbeat.isoformat(),
            
            # 새로 추가: 한국 시간 정보
            "kst_time": current_kst.isoformat(),
            "korea_time": current_kst.strftime("%Y년 %m월 %d일 %H:%M:%S"),
            "korea_time_simple": current_kst.strftime("%Y-%m-%d %H:%M:%S"),
            "korea_hour_minute": current_kst.strftime("%H:%M"),
            "korea_date": current_kst.strftime("%Y년 %m월 %d일"),
            "timezone_kst": "Asia/Seoul (UTC+9)"
        }

# =========================
# main.py에서 사용할 때 추가할 부분
# =========================

"""
# main.py에서 ESP32 데이터 수신 시 한국 시간 추가하는 방법:

@app.post("/esp32/data")
async def receive_esp32_data(data: Dict[str, Any]):
    if not MODULES_AVAILABLE:
        return {"error": "Modules not available", "data_received": data}
    
    try:
        # 기존 타임스탬프 (호환성)
        if realtime_handler:
            data["server_received_at"] = realtime_handler.get_current_timestamp()
            data["server_local_time"] = realtime_handler.get_local_timestamp()
            
            # 새로 추가: 한국 시간
            data["server_received_at_kst"] = realtime_handler.get_korea_timestamp()
            korea_time = get_korea_time()
            data["korea_time"] = korea_time.strftime("%Y년 %m월 %d일 %H:%M:%S")
            data["korea_time_simple"] = korea_time.strftime("%H:%M:%S")
        
        # 기존 처리 로직...
        result = await esp32_handler.handle_esp32_data(data)
        
        # 브로드캐스트에도 한국 시간 추가
        if websocket_manager.active_connections:
            korea_now = get_korea_time()
            broadcast_data = {
                "type": "new_data",
                "source": "esp32",
                "data": data,
                
                # 기존 필드들
                "server_timestamp": realtime_handler.get_current_timestamp() if realtime_handler else datetime.now().isoformat(),
                "broadcast_time": datetime.now().isoformat(),
                
                # 새로 추가: 한국 시간
                "korea_time": korea_now.strftime("%Y년 %m월 %d일 %H:%M:%S"),
                "korea_time_simple": korea_now.strftime("%H:%M:%S"),
                "server_timestamp_kst": korea_now.isoformat()
            }
            await websocket_manager.broadcast_to_apps(broadcast_data)
        
        return result
        
    except Exception as e:
        print(f"❌ ESP32 데이터 수신 오류: {e}")
        raise HTTPException(status_code=500, detail=f"ESP32 data processing failed: {str(e)}")
"""

# =========================
# 시간 확인용 유틸리티 함수들
# =========================

def format_korea_time(dt=None):
    """한국 시간을 포맷된 문자열로 반환"""
    if dt is None:
        dt = get_korea_time()
    return dt.strftime("%Y년 %m월 %d일 %H:%M:%S")

def get_time_comparison():
    """UTC와 한국 시간 비교 정보"""
    utc_now = datetime.now(timezone.utc)
    korea_now = get_korea_time()
    
    return {
        "utc": utc_now.strftime("%H:%M:%S"),
        "korea": korea_now.strftime("%H:%M:%S"),
        "utc_full": utc_now.isoformat(),
        "korea_full": korea_now.isoformat(),
        "hour_difference": korea_now.hour - utc_now.hour,
        "is_next_day": korea_now.date() > utc_now.date()
    }