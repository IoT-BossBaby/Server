"""
Baby Monitor - 모바일 앱 전용 API 핸들러 (완전 버전)
ESP32 → 서버 → 앱 데이터 전달을 위한 API 엔드포인트들
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
import asyncio
import json

# 시간대 설정 (Railway 안전 버전)
try:
    import pytz
    KST = pytz.timezone('Asia/Seoul')
except ImportError:
    # pytz가 없는 경우 UTC+9로 대체
    KST = timezone(timedelta(hours=9))

class AppApiHandler:
    def __init__(self, redis_manager, websocket_manager, esp32_handler, image_handler):
        self.redis_manager = redis_manager
        self.websocket_manager = websocket_manager
        self.esp32_handler = esp32_handler
        self.image_handler = image_handler
        self.router = APIRouter(prefix="/app", tags=["Mobile App API"])
        self._setup_routes()
    
    def _setup_routes(self):
        """API 라우트 설정"""
        # WebSocket
        self.router.add_api_websocket_route("/stream", self.websocket_stream)
        
        # 데이터 조회 API
        self.router.add_api_route("/data/latest", self.get_latest_data, methods=["GET"])
        self.router.add_api_route("/data/history", self.get_data_history, methods=["GET"])
        self.router.add_api_route("/images/latest", self.get_latest_image, methods=["GET"])
        self.router.add_api_route("/stats/daily", self.get_daily_stats, methods=["GET"])
        
        # ESP32 제어 API
        self.router.add_api_route("/command", self.send_command_to_esp32, methods=["POST"])
        
        # 설정 관리 API
        self.router.add_api_route("/settings/notifications", self.get_notification_settings, methods=["GET"])
        self.router.add_api_route("/settings/notifications", self.update_notification_settings, methods=["POST"])
        
        # 알림 관리 API
        self.router.add_api_route("/notifications/register", self.register_for_notifications, methods=["POST"])
        self.router.add_api_route("/notifications/unregister", self.unregister_notifications, methods=["POST"])
        
        # 상태 확인 API
        self.router.add_api_route("/ping", self.app_ping, methods=["GET"])
        self.router.add_api_route("/status", self.get_app_status, methods=["GET"])

    async def websocket_stream(self, websocket: WebSocket):
        """앱에서 실시간 데이터를 받기 위한 WebSocket 연결"""
        client_id = None
        try:
            # WebSocket 연결 및 클라이언트 ID 생성
            client_id = await self.websocket_manager.connect(websocket, "mobile_app")
            print(f"📱 앱 클라이언트 연결됨: {client_id}")
        
            # 연결 즉시 현재 상태 + 시간 정보 전송
            await self._send_current_status_with_time(client_id)
            
            # 연결 유지를 위한 메시지 처리 루프
            while True:
                try:
                    # 클라이언트로부터 메시지 대기 (30초 타임아웃)
                    message = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                    
                    # 메시지 처리 (자장가 제어 포함)
                    if hasattr(self.websocket_manager, 'handle_app_message'):
                        await self.websocket_manager.handle_app_message(
                            client_id, 
                            message, 
                            esp32_handler=self.esp32_handler
                        )
                    
                except asyncio.TimeoutError:
                    # 30초 동안 메시지가 없으면 ping 전송
                    ping_response = {
                        "type": "ping", 
                        "server_time_kst": datetime.now(KST).isoformat(),
                        "client_id": client_id,
                        "timestamp": datetime.now(KST).isoformat()
                    }
                    await self.websocket_manager.send_to_connection(client_id, ping_response)
                    
                except Exception as e:
                    print(f"❌ 메시지 처리 오류 ({client_id}): {e}")
                    break
                
        except WebSocketDisconnect:
            print(f"📱 앱 클라이언트 연결 해제됨: {client_id}")
        except Exception as e:
            print(f"❌ WebSocket 오류 ({client_id}): {e}")
        finally:
            # 연결 정리
            if client_id:
                self.websocket_manager.disconnect(client_id)

    async def _send_current_status_with_time(self, client_id: str):
        """현재 상태와 시간 정보를 함께 전송"""
        try:
            # 한국 시간과 UTC 시간
            current_kst = datetime.now(KST)
            current_utc = datetime.now(timezone.utc)
            
            # Redis에서 현재 데이터 조회
            current_data = None
            data_age = None
            
            if self.redis_manager and hasattr(self.redis_manager, 'available') and self.redis_manager.available:
                try:
                    current_data = self.redis_manager.get_current_status()
                    
                    # 데이터 신선도 확인
                    if current_data and current_data.get("timestamp"):
                        try:
                            data_time = datetime.fromisoformat(current_data["timestamp"].replace('Z', '+00:00'))
                            if hasattr(data_time, 'astimezone'):
                                data_time_kst = data_time.astimezone(KST)
                            else:
                                data_time_kst = data_time
                            data_age = (current_kst - data_time_kst).total_seconds()
                        except:
                            data_age = None
                except:
                    current_data = None
            
            # 응답 데이터 구성
            response = {
                "type": "current_status",
                "data": current_data,
                "server_time_utc": current_utc.isoformat(),
                "server_time_kst": current_kst.isoformat(),
                "local_time": current_kst.strftime("%Y년 %m월 %d일 %H:%M:%S"),
                "formatted_time": current_kst.strftime("%H:%M:%S"),
                "timezone": "Asia/Seoul",
                "data_age_seconds": data_age,
                "data_is_fresh": data_age < 60 if data_age else False,
                "server_info": {
                    "redis_connected": getattr(self.redis_manager, 'available', False),
                    "esp32_status": getattr(self.esp32_handler, 'esp32_status', 'unknown'),
                    "active_connections": len(getattr(self.websocket_manager, 'active_connections', {}))
                },
                "timestamp": current_kst.isoformat()
            }
            
            # 클라이언트에 전송
            await self.websocket_manager.send_to_connection(client_id, response)
            
        except Exception as e:
            print(f"❌ 현재 상태 전송 오류 ({client_id}): {e}")
            # 오류 시 기본 정보라도 전송
            try:
                error_response = {
                    "type": "current_status",
                    "data": None,
                    "server_time_kst": datetime.now(KST).isoformat(),
                    "error": f"상태 조회 실패: {str(e)}",
                    "timestamp": datetime.now(KST).isoformat()
                }
                await self.websocket_manager.send_to_connection(client_id, error_response)
            except:
                pass

    def get_latest_data(self):
        """앱에서 최신 데이터 조회"""
        try:
            # Redis에서 최신 데이터 가져오기
            latest_data = None
            if self.redis_manager and hasattr(self.redis_manager, 'get_current_status'):
                try:
                    latest_data = self.redis_manager.get_current_status()
                except:
                    latest_data = None
            
            # 이미지 메타데이터 조회 (있는 경우)
            recent_images = []
            if self.redis_manager and hasattr(self.redis_manager, 'get_recent_images'):
                try:
                    recent_images = self.redis_manager.get_recent_images(1) or []
                except:
                    recent_images = []
            
            # 응답 데이터 구성
            response = {
                "status": "success",
                "timestamp": datetime.now(KST).isoformat(),
                "data": latest_data or {
                    "timestamp": datetime.now(KST).isoformat(),
                    "baby_detected": False,
                    "temperature": 22.5,
                    "humidity": 55.0,
                    "source": "dummy"
                },
                "has_image": len(recent_images) > 0,
                "image_metadata": recent_images[0].get("metadata") if recent_images else None,
                "server_info": self._get_server_info()
            }
            
            return response
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"데이터 조회 실패: {str(e)}")

    def get_data_history(self, hours: int = 24, limit: int = 100):
        """앱에서 과거 데이터 조회"""
        try:
            # 입력값 검증
            hours = max(1, min(hours, 168))  # 1시간 ~ 7일
            limit = max(1, min(limit, 1000))  # 1개 ~ 1000개
            
            # Redis에서 과거 데이터 가져오기 (메서드가 있는 경우)
            history_data = []
            if self.redis_manager and hasattr(self.redis_manager, 'get_data_history'):
                try:
                    history_data = self.redis_manager.get_data_history(hours=hours, limit=limit) or []
                except:
                    history_data = []
            
            return {
                "status": "success",
                "timestamp": datetime.now(KST).isoformat(),
                "params": {"hours": hours, "limit": limit},
                "data_count": len(history_data),
                "history": history_data
            }
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"히스토리 조회 실패: {str(e)}")

    def get_latest_image(self, include_data: bool = False):
        """앱에서 최신 이미지 조회"""
        try:
            recent_images = []
            if self.redis_manager and hasattr(self.redis_manager, 'get_recent_images'):
                try:
                    recent_images = self.redis_manager.get_recent_images(1) or []
                except:
                    recent_images = []
            
            if recent_images:
                image_data = recent_images[0]
                response = {
                    "status": "success",
                    "timestamp": datetime.now(KST).isoformat(),
                    "image": {
                        "timestamp": image_data.get("timestamp"),
                        "metadata": image_data.get("metadata", {}),
                        "size": image_data.get("size"),
                        "format": image_data.get("format", "jpeg")
                    }
                }
                
                # 이미지 데이터 포함 여부
                if include_data:
                    response["image"]["data"] = image_data.get("image_base64")
                else:
                    response["image"]["download_url"] = f"/app/images/download/{image_data.get('id', 'latest')}"
                
                return response
            else:
                return {
                    "status": "no_image",
                    "message": "최근 이미지가 없습니다",
                    "timestamp": datetime.now(KST).isoformat()
                }
                
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"이미지 조회 실패: {str(e)}")

    async def send_command_to_esp32(self, command_data: Dict[str, Any]):
        """앱에서 ESP32로 명령 전송"""
        try:
            # 명령 유효성 검사
            if not command_data.get("command"):
                raise HTTPException(status_code=400, detail="명령이 지정되지 않았습니다")
            
            print(f"📱 앱에서 명령 수신: {command_data}")
            
            # ESP32로 명령 전송
            success = False
            if self.esp32_handler and hasattr(self.esp32_handler, 'send_command_to_esp32'):
                try:
                    success = await self.esp32_handler.send_command_to_esp32(command_data)
                except:
                    success = False
            
            # Redis에 명령 기록 저장 (메서드가 있는 경우)
            if self.redis_manager and hasattr(self.redis_manager, 'save_command_log'):
                try:
                    command_log = {
                        "source": "mobile_app_api",
                        "command": command_data,
                        "success": success,
                        "timestamp": datetime.now(KST).isoformat()
                    }
                    self.redis_manager.save_command_log(command_log)
                except:
                    pass
            
            return {
                "status": "success" if success else "failed",
                "message": f"명령을 ESP32로 {'전송했습니다' if success else '전송하지 못했습니다'}",
                "command": command_data.get("command"),
                "timestamp": datetime.now(KST).isoformat(),
                "esp32_status": getattr(self.esp32_handler, 'esp32_status', 'unknown')
            }
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"명령 전송 실패: {str(e)}")

    def get_notification_settings(self):
        """앱의 알림 설정 조회"""
        try:
            settings = None
            if self.redis_manager and hasattr(self.redis_manager, 'get_notification_settings'):
                try:
                    settings = self.redis_manager.get_notification_settings()
                except:
                    settings = None
            
            return {
                "status": "success",
                "timestamp": datetime.now(KST).isoformat(),
                "settings": settings or self._get_default_notification_settings()
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"설정 조회 실패: {str(e)}")

    def update_notification_settings(self, settings: Dict[str, Any]):
        """앱의 알림 설정 변경"""
        try:
            # 설정 유효성 검사
            validated_settings = self._validate_notification_settings(settings)
            
            # Redis에 저장 (메서드가 있는 경우)
            if self.redis_manager and hasattr(self.redis_manager, 'save_notification_settings'):
                try:
                    self.redis_manager.save_notification_settings(validated_settings)
                except:
                    pass
            
            return {
                "status": "success",
                "message": "알림 설정이 저장되었습니다",
                "timestamp": datetime.now(KST).isoformat(),
                "settings": validated_settings
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"설정 업데이트 실패: {str(e)}")

    def register_for_notifications(self, device_info: Dict[str, Any]):
        """앱에서 실시간 알림 등록"""
        try:
            # 필수 필드 검증
            required_fields = ["device_id", "platform"]
            for field in required_fields:
                if not device_info.get(field):
                    raise HTTPException(status_code=400, detail=f"{field} 필드가 필요합니다")
            
            # 등록 데이터 구성
            registration_data = {
                "device_id": device_info.get("device_id"),
                "push_token": device_info.get("push_token"),
                "platform": device_info.get("platform"),  # ios/android
                "app_version": device_info.get("app_version"),
                "registered_at": datetime.now(KST).isoformat(),
                "last_active": datetime.now(KST).isoformat(),
                "active": True
            }
            
            # Redis에 저장 (메서드가 있는 경우)
            if self.redis_manager and hasattr(self.redis_manager, 'register_notification_device'):
                try:
                    self.redis_manager.register_notification_device(registration_data)
                except:
                    pass
            
            return {
                "status": "success",
                "message": "알림 등록이 완료되었습니다",
                "timestamp": datetime.now(KST).isoformat(),
                "device_id": registration_data["device_id"]
            }
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"알림 등록 실패: {str(e)}")

    def unregister_notifications(self, device_info: Dict[str, Any]):
        """앱에서 알림 등록 해제"""
        try:
            device_id = device_info.get("device_id")
            if not device_id:
                raise HTTPException(status_code=400, detail="device_id가 필요합니다")
            
            # Redis에서 제거 (메서드가 있는 경우)
            if self.redis_manager and hasattr(self.redis_manager, 'unregister_notification_device'):
                try:
                    self.redis_manager.unregister_notification_device(device_id)
                except:
                    pass
            
            return {
                "status": "success",
                "message": "알림 등록이 해제되었습니다",
                "timestamp": datetime.now(KST).isoformat(),
                "device_id": device_id
            }
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"알림 해제 실패: {str(e)}")

    def get_daily_stats(self, date: Optional[str] = None):
        """앱에서 일일 통계 조회"""
        try:
            # 날짜 파싱
            if date:
                target_date = datetime.strptime(date, "%Y-%m-%d").date()
            else:
                target_date = datetime.now().date()
            
            # 통계 데이터 조회 (메서드가 있는 경우)
            stats = None
            if self.redis_manager and hasattr(self.redis_manager, 'get_daily_statistics'):
                try:
                    stats = self.redis_manager.get_daily_statistics(target_date)
                except:
                    stats = None
            
            return {
                "status": "success",
                "timestamp": datetime.now(KST).isoformat(),
                "date": target_date.isoformat(),
                "stats": stats or self._get_default_stats()
            }
            
        except ValueError:
            raise HTTPException(status_code=400, detail="날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"통계 조회 실패: {str(e)}")

    def app_ping(self):
        """앱에서 서버 상태 확인"""
        return {
            "status": "ok",
            "timestamp": datetime.now(KST).isoformat(),
            "server_version": "2.0.0",
            "services": self._get_server_info()
        }

    def get_app_status(self):
        """앱용 상세 상태 정보"""
        try:
            return {
                "status": "success",
                "timestamp": datetime.now(KST).isoformat(),
                "server_info": self._get_server_info(),
                "data_freshness": self._get_data_freshness(),
                "connection_stats": self._get_connection_stats()
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"상태 조회 실패: {str(e)}")

    # ========== 헬퍼 메서드들 ==========

    def _get_server_info(self):
        """서버 정보 조회"""
        return {
            "redis_connected": getattr(self.redis_manager, 'available', False),
            "esp32_status": getattr(self.esp32_handler, 'esp32_status', 'unknown'),
            "esp32_ip": getattr(self.esp32_handler, 'esp32_ip', None),
            "active_websockets": len(getattr(self.websocket_manager, 'active_connections', {})),
            "uptime": "unknown"
        }

    def _get_data_freshness(self):
        """데이터 신선도 정보"""
        try:
            if self.redis_manager and hasattr(self.redis_manager, 'get_current_status'):
                latest = self.redis_manager.get_current_status()
                if latest and latest.get("timestamp"):
                    try:
                        last_update = datetime.fromisoformat(latest["timestamp"].replace('Z', '+00:00'))
                        age_seconds = (datetime.now(timezone.utc) - last_update).total_seconds()
                        return {
                            "last_update": latest["timestamp"],
                            "age_seconds": age_seconds,
                            "is_fresh": age_seconds < 300  # 5분 이내면 신선함
                        }
                    except:
                        pass
        except:
            pass
        
        return {
            "last_update": None,
            "age_seconds": None,
            "is_fresh": False
        }

    def _get_connection_stats(self):
        """연결 통계"""
        try:
            active_connections = getattr(self.websocket_manager, 'active_connections', {})
            return {
                "total_connections": len(active_connections),
                "mobile_apps": len([c for c in active_connections.values() 
                                   if isinstance(c, dict) and c.get("client_type") == "mobile_app"]),
                "web_clients": len([c for c in active_connections.values() 
                                   if isinstance(c, dict) and c.get("client_type") == "web"])
            }
        except:
            return {
                "total_connections": 0,
                "mobile_apps": 0,
                "web_clients": 0
            }

    def _get_default_notification_settings(self):
        """기본 알림 설정"""
        return {
            "baby_detected": True,
            "baby_not_detected": False,
            "environment_alert": True,
            "system_status": False,
            "quiet_hours": {
                "enabled": True,
                "start": "22:00",
                "end": "07:00"
            }
        }

    def _validate_notification_settings(self, settings):
        """알림 설정 유효성 검사"""
        # 기본 설정으로 시작
        validated = self._get_default_notification_settings()
        
        # 사용자 설정으로 업데이트
        if isinstance(settings, dict):
            for key in ["baby_detected", "baby_not_detected", "environment_alert", "system_status"]:
                if key in settings:
                    validated[key] = bool(settings[key])
            
            if "quiet_hours" in settings and isinstance(settings["quiet_hours"], dict):
                quiet_hours = settings["quiet_hours"]
                if "enabled" in quiet_hours:
                    validated["quiet_hours"]["enabled"] = bool(quiet_hours["enabled"])
                if "start" in quiet_hours:
                    validated["quiet_hours"]["start"] = str(quiet_hours["start"])
                if "end" in quiet_hours:
                    validated["quiet_hours"]["end"] = str(quiet_hours["end"])
        
        return validated

    def _get_default_stats(self):
        """기본 통계 데이터"""
        return {
            "total_detections": 0,
            "sleep_duration": "00:00:00",
            "average_temperature": 22.5,
            "average_humidity": 55.0,
            "alerts_count": 0,
            "images_captured": 0
        }

    def get_router(self):
        """FastAPI 라우터 반환"""
        return self.router