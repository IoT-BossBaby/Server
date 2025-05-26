# esp32_handler.py (POST 방식으로 수정된 버전)
import asyncio
import json
import aiohttp
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

def get_korea_time():
    """한국 시간 반환"""
    utc_now = datetime.now(timezone.utc)
    korea_offset = timedelta(hours=9)
    korea_tz = timezone(korea_offset)
    return utc_now.astimezone(korea_tz)

class ESP32Handler:
    def __init__(self, redis_manager, websocket_manager):
        self.redis_manager = redis_manager
        self.websocket_manager = websocket_manager
        
        # ESP32와 ESP Eye 상태 관리
        self.esp32_ip = "172.25.83.227"
        self.esp32_status = "disconnected"
        self.esp32_last_seen = None
        
        self.esp_eye_ip = "172.25.85.66"
        self.esp_eye_status = "disconnected"
        self.esp_eye_last_seen = None
        
        # 전체 상태
        self.last_heartbeat = None
        
        print("🔧 ESP32Handler 초기화 완료 (POST 수신 모드)")
    
    def update_device_status(self, device_type: str, client_ip: str):
        """디바이스 상태 업데이트"""
        current_time = get_korea_time().isoformat()
        
        if device_type == "esp32":
            self.esp32_ip = client_ip
            self.esp32_status = "connected"
            self.esp32_last_seen = current_time
            print(f"📡 ESP32 상태 업데이트: {client_ip}")
        elif device_type == "esp_eye":
            self.esp_eye_ip = client_ip
            self.esp_eye_status = "connected"
            self.esp_eye_last_seen = current_time
            print(f"👁️ ESP Eye 상태 업데이트: {client_ip}")
        
        self.last_heartbeat = current_time
    
    def process_esp32_sensor_data(self, raw_data: Dict[str, Any], client_ip: str) -> Dict[str, Any]:
        """ESP32 센서 데이터 처리"""
        
        # 디바이스 상태 업데이트
        self.update_device_status("esp32", client_ip)
        
        timestamp = get_korea_time().isoformat()
        
        # ESP32 센서 데이터 정규화
        processed_data = {
            "device_type": "esp32",
            "timestamp": timestamp,
            "esp32_ip": client_ip,
            
            # 환경 센서 데이터
            "temperature": float(raw_data.get("temperature", 0.0)),
            "humidity": float(raw_data.get("humidity", 0.0)),
            "playLullaby": str(raw_data.get("playLullaby", "")).lower() == "true"
        }
        
        # 환경 상태 분석
        temp_status = "optimal" if 20 <= processed_data["temperature"] <= 24 else "warning"
        humidity_status = "optimal" if 40 <= processed_data["humidity"] <= 60 else "warning"
        
        processed_data["temperature_status"] = temp_status
        processed_data["humidity_status"] = humidity_status
        processed_data["environment_status"] = "optimal" if temp_status == "optimal" and humidity_status == "optimal" else "warning"
        
        # 알림 분석
        alert_factors = []
        alert_score = 0
        
        # 환경 경고
        if temp_status == "warning":
            if processed_data["temperature"] < 18 or processed_data["temperature"] > 26:
                alert_factors.append(f"극한 온도: {processed_data['temperature']}°C")
                alert_score += 2
            else:
                alert_factors.append(f"부적절한 온도: {processed_data['temperature']}°C")
                alert_score += 1
        
        if humidity_status == "warning":
            if processed_data["humidity"] < 30 or processed_data["humidity"] > 80:
                alert_factors.append(f"극한 습도: {processed_data['humidity']}%")
                alert_score += 2
            else:
                alert_factors.append(f"부적절한 습도: {processed_data['humidity']}%")
                alert_score += 1
        
        return processed_data
    
    def process_esp_eye_data(self, raw_data: Dict[str, Any], client_ip: str) -> Dict[str, Any]:
        """ESP Eye 이미지 데이터 처리"""
        
        # 디바이스 상태 업데이트
        self.update_device_status("esp_eye", client_ip)
        
        timestamp = get_korea_time().isoformat()
        
        # ESP Eye 데이터 정규화
        processed_data = {
            "device_type": "esp_eye",
            "timestamp": timestamp,
            "esp_eye_ip": client_ip,
            
            # 이미지 데이터
            "has_image": "image" in raw_data and raw_data["image"],
            "image_base64": raw_data.get("image", ""),
            "image_size": len(raw_data.get("image", "")),
            
            # 이미지 메타데이터
            "image_width": raw_data.get("width", 0),
            "image_height": raw_data.get("height", 0),
            "image_format": raw_data.get("format", "jpeg"),
            "compression_quality": raw_data.get("quality", 80),
        }
        
        # 비전 분석
        vision_alerts = []
        vision_score = 0
        
        # 이미지 품질 검사
        if processed_data["image_size"] < 1000:  # 너무 작은 이미지
            vision_alerts.append("이미지 품질 불량")
            vision_score += 1
        
        
        if processed_data["camera_status"] != "ok":
            vision_alerts.append("카메라 오류")
            vision_score += 2
        
        # 알림 레벨 결정
        if vision_score >= 3:
            alert_level = "high"
        elif vision_score >= 1:
            alert_level = "medium"
        else:
            alert_level = "low"
        
        processed_data["vision_alerts"] = vision_alerts
        processed_data["vision_score"] = vision_score
        processed_data["alert_level"] = alert_level
        
        return processed_data
    
    async def handle_esp32_data(self, raw_data: Dict[str, Any], client_ip: str = "unknown") -> Dict[str, Any]:
        """ESP32 센서 데이터 처리 파이프라인"""
        
        try:
            # 1. 데이터 처리
            processed_data = self.process_esp32_sensor_data(raw_data, client_ip)
            
            print(f"📡 ESP32 데이터: 온도={processed_data['temperature']}°C, "
                  f"습도={processed_data['humidity']}%")
            
            # 2. Redis 저장
            redis_stored = False
            if self.redis_manager and hasattr(self.redis_manager, 'store_esp32_data'):
                try:
                    redis_stored = self.redis_manager.store_esp32_data(processed_data)
                except Exception as e:
                    print(f"⚠️ Redis 저장 실패: {e}")
            
            # 3. 실시간 앱 전송
            apps_notified = 0
            if self.websocket_manager and hasattr(self.websocket_manager, 'broadcast_to_apps'):
                try:
                    # 앱용 데이터 준비
                    app_data = {
                        "type": "esp32_sensor_data",
                        "source": "esp32",
                        "data": processed_data,
                        "korea_time": get_korea_time().strftime("%Y년 %m월 %d일 %H:%M:%S"),
                        "timestamp": processed_data["timestamp"]
                    }
                    apps_notified = await self.websocket_manager.broadcast_to_apps(app_data)
                except Exception as e:
                    print(f"⚠️ 앱 브로드캐스트 실패: {e}")
            
            # 4. 고위험 알림 처리
            alert_sent = False
            if processed_data["alert_level"] == "high":
                try:
                    alert_data = {
                        "type": "high_risk_alert",
                        "device": "esp32",
                        "message": f"환경 경고: {', '.join(processed_data['alert_factors'])}",
                        "timestamp": processed_data["timestamp"],
                        "korea_time": get_korea_time().strftime("%Y년 %m월 %d일 %H:%M:%S"),
                        "alert_score": processed_data["alert_score"],
                        "data": processed_data
                    }
                    
                    # Redis 알림 저장
                    if self.redis_manager and hasattr(self.redis_manager, 'publish_alert'):
                        alert_sent = self.redis_manager.publish_alert(alert_data)
                    
                    # 앱에 긴급 알림
                    if self.websocket_manager and hasattr(self.websocket_manager, 'send_alert'):
                        await self.websocket_manager.send_alert(alert_data)
                        
                except Exception as e:
                    print(f"⚠️ 알림 전송 실패: {e}")
            
            return {
                "status": "success",
                "message": "ESP32 센서 데이터 처리 완료",
                "device_type": "esp32",
                "timestamp": processed_data["timestamp"],
                "korea_time": get_korea_time().strftime("%Y년 %m월 %d일 %H:%M:%S"),
                "processing_results": {
                    "redis_stored": redis_stored,
                    "apps_notified": apps_notified,
                    "alert_sent": alert_sent,
                    "alert_level": processed_data["alert_level"]
                }
            }
            
        except Exception as e:
            print(f"❌ ESP32 데이터 처리 오류: {e}")
            return {
                "status": "error",
                "message": f"ESP32 데이터 처리 실패: {str(e)}",
                "device_type": "esp32",
                "timestamp": get_korea_time().isoformat()
            }
    
    async def handle_esp_eye_data(self, raw_data: Dict[str, Any], client_ip: str = "unknown") -> Dict[str, Any]:
        """ESP Eye 이미지 데이터 처리 파이프라인"""
        
        try:
            # 1. 데이터 처리
            processed_data = self.process_esp_eye_data(raw_data, client_ip)
            
            print(f"👁️ ESP Eye 데이터: 이미지크기={processed_data['image_size']}bytes, "
                  f"아기감지={processed_data['baby_detected']}, 알림레벨={processed_data['alert_level']}")
            
            # 2. Redis에 이미지 데이터 저장
            image_stored = False
            if self.redis_manager and hasattr(self.redis_manager, 'store_image_data'):
                try:
                    image_data = {
                        "timestamp": processed_data["timestamp"],
                        "image_base64": processed_data["image_base64"],
                        "baby_detected": processed_data["baby_detected"],
                        "confidence": processed_data["detection_confidence"],
                        "alert_level": processed_data["alert_level"],
                        "metadata": {
                            "width": processed_data["image_width"],
                            "height": processed_data["image_height"],
                            "format": processed_data["image_format"],
                            "size": processed_data["image_size"]
                        }
                    }
                    image_stored = self.redis_manager.store_image_data(image_data)
                except Exception as e:
                    print(f"⚠️ 이미지 저장 실패: {e}")
            
            # 3. 센서 데이터도 저장 (이미지 정보 포함)
            redis_stored = False
            if self.redis_manager and hasattr(self.redis_manager, 'store_esp32_data'):
                try:
                    # 이미지는 제외하고 메타데이터만 저장
                    sensor_data = processed_data.copy()
                    sensor_data.pop("image_base64", None)  # 용량 절약
                    redis_stored = self.redis_manager.store_esp32_data(sensor_data)
                except Exception as e:
                    print(f"⚠️ Redis 저장 실패: {e}")
            
            # 4. 실시간 앱 전송 (이미지 미포함)
            apps_notified = 0
            if self.websocket_manager and hasattr(self.websocket_manager, 'broadcast_to_apps'):
                try:
                    # 앱용 데이터 (이미지 제외, 메타데이터만)
                    app_data = {
                        "type": "esp_eye_data",
                        "source": "esp_eye",
                        "data": {
                            "timestamp": processed_data["timestamp"],
                            "baby_detected": processed_data["baby_detected"],
                            "detection_confidence": processed_data["detection_confidence"],
                            "face_detected": processed_data["face_detected"],
                            "face_count": processed_data["face_count"],
                            "has_new_image": processed_data["has_image"],
                            "image_size": processed_data["image_size"],
                            "alert_level": processed_data["alert_level"],
                            "vision_alerts": processed_data["vision_alerts"]
                        },
                        "korea_time": get_korea_time().strftime("%Y년 %m월 %d일 %H:%M:%S"),
                        "timestamp": processed_data["timestamp"]
                    }
                    apps_notified = await self.websocket_manager.broadcast_to_apps(app_data)
                except Exception as e:
                    print(f"⚠️ 앱 브로드캐스트 실패: {e}")
            
            # 5. 고위험 알림 처리
            alert_sent = False
            if processed_data["alert_level"] == "high":
                try:
                    alert_data = {
                        "type": "high_risk_alert",
                        "device": "esp_eye",
                        "message": f"비전 경고: {', '.join(processed_data['vision_alerts'])}",
                        "timestamp": processed_data["timestamp"],
                        "korea_time": get_korea_time().strftime("%Y년 %m월 %d일 %H:%M:%S"),
                        "alert_score": processed_data["vision_score"],
                        "baby_detected": processed_data["baby_detected"],
                        "has_image": processed_data["has_image"]
                    }
                    
                    # Redis 알림 저장
                    if self.redis_manager and hasattr(self.redis_manager, 'publish_alert'):
                        alert_sent = self.redis_manager.publish_alert(alert_data)
                    
                    # 앱에 긴급 알림
                    if self.websocket_manager and hasattr(self.websocket_manager, 'send_alert'):
                        await self.websocket_manager.send_alert(alert_data)
                        
                except Exception as e:
                    print(f"⚠️ 알림 전송 실패: {e}")
            
            return {
                "status": "success",
                "message": "ESP Eye 데이터 처리 완료",
                "device_type": "esp_eye",
                "timestamp": processed_data["timestamp"],
                "korea_time": get_korea_time().strftime("%Y년 %m월 %d일 %H:%M:%S"),
                "processing_results": {
                    "redis_stored": redis_stored,
                    "image_stored": image_stored,
                    "apps_notified": apps_notified,
                    "alert_sent": alert_sent,
                    "alert_level": processed_data["alert_level"],
                    "baby_detected": processed_data["baby_detected"]
                }
            }
            
        except Exception as e:
            print(f"❌ ESP Eye 데이터 처리 오류: {e}")
            return {
                "status": "error",
                "message": f"ESP Eye 데이터 처리 실패: {str(e)}",
                "device_type": "esp_eye",
                "timestamp": get_korea_time().isoformat()
            }
    
    async def send_command_to_esp32(self, command: Dict[str, Any]) -> bool:
        """ESP32에 명령 전송 (IP가 있는 경우에만)"""
        
        if not self.esp32_ip:
            print("❌ ESP32 IP 주소를 알 수 없음 (데이터를 먼저 받아야 함)")
            return False
        
        try:
            command_data = {
                "command": command.get("command"),
                "params": command.get("params", {}),
                "timestamp": get_korea_time().isoformat(),
                "source": "server"
            }
            
            # Redis에 명령 기록
            if self.redis_manager and hasattr(self.redis_manager, 'store_command'):
                try:
                    self.redis_manager.store_command(command_data)
                except Exception as e:
                    print(f"⚠️ 명령 기록 저장 실패: {e}")
            
            # ESP32에 HTTP POST 전송
            url = f"http://{self.esp32_ip}/command"
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, 
                    json=command_data,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    if response.status == 200:
                        print(f"✅ ESP32 명령 전송 성공: {command_data['command']} → {self.esp32_ip}")
                        return True
                    else:
                        print(f"❌ ESP32 명령 전송 실패: HTTP {response.status}")
                        return False
                        
        except asyncio.TimeoutError:
            print(f"⏰ ESP32 명령 전송 타임아웃: {self.esp32_ip}")
            self.esp32_status = "timeout"
            return False
        except Exception as e:
            print(f"❌ ESP32 명령 전송 오류: {e}")
            self.esp32_status = "error"
            return False
    
    def get_esp32_status(self) -> Dict[str, Any]:
        """전체 ESP32 시스템 상태 정보"""
        return {
            "esp32": {
                "ip": self.esp32_ip,
                "status": self.esp32_status,
                "last_seen": self.esp32_last_seen,
                "connected": self.esp32_status == "connected"
            },
            "esp_eye": {
                "ip": self.esp_eye_ip,
                "status": self.esp_eye_status,
                "last_seen": self.esp_eye_last_seen,
                "connected": self.esp_eye_status == "connected"
            },
            "overall": {
                "last_heartbeat": self.last_heartbeat,
                "any_connected": self.esp32_status == "connected" or self.esp_eye_status == "connected",
                "both_connected": self.esp32_status == "connected" and self.esp_eye_status == "connected"
            }
        }