# esp32_handler.py 
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
            "playLullaby": str(raw_data.get("playLullaby", "")).lower() == "true",
            
            # 움직임 감지
            "movement_detected": raw_data.get("movement", False),
            "motion_level": float(raw_data.get("motion_level", 0.0)),
            
            # 소음 레벨
            "sound_level": float(raw_data.get("sound", 0.0)),
            "noise_detected": raw_data.get("noise_detected", False),
            
            # 시스템 상태
            "battery_level": raw_data.get("battery", None),
            "wifi_signal": raw_data.get("wifi_signal", None),
            "memory_free": raw_data.get("memory_free", None),
            "uptime": raw_data.get("uptime", None),
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
        
        # 움직임/소음 감지
        if processed_data["movement_detected"]:
            alert_factors.append("움직임 감지됨")
            alert_score += 1
        
        if processed_data["noise_detected"]:
            alert_factors.append("소음 감지됨")
            alert_score += 1
        
        # 시스템 경고
        if processed_data.get("battery_level") and processed_data["battery_level"] < 20:
            alert_factors.append("배터리 부족")
            alert_score += 1
        
        # 알림 레벨 결정
        if alert_score >= 3:
            alert_level = "high"
        elif alert_score >= 1:
            alert_level = "medium"
        else:
            alert_level = "low"
        
        processed_data["alert_factors"] = alert_factors
        processed_data["alert_score"] = alert_score
        processed_data["alert_level"] = alert_level
        
        return processed_data
    
    def process_esp_eye_data(self, raw_data: Dict[str, Any], client_ip: str) -> Dict[str, Any]:
        """ESP Eye 이미지 데이터 처리 (단순화 버전)"""
        
        # 디바이스 상태 업데이트
        self.update_device_status("esp_eye", client_ip)
        
        timestamp = get_korea_time().isoformat()
        
        # ESP Eye 데이터 정규화 (이미지만)
        processed_data = {
            "device_type": "esp_eye",
            "timestamp": timestamp,
            "esp_eye_ip": client_ip,
            
            # 이미지 데이터
            "has_image": "image" in raw_data and raw_data["image"],
            "image_base64": raw_data.get("image", ""),
            "image_size": len(raw_data.get("image", "")),
            
            # 이미지 메타데이터 (기본값 설정)
            "image_width": raw_data.get("width", 640),
            "image_height": raw_data.get("height", 480),
            "image_format": raw_data.get("format", "jpeg"),
            "compression_quality": raw_data.get("quality", 80),
            
            # 아기 감지 관련 필드 (기본값)
            "baby_detected": False,
            "detection_confidence": 0.0,
            "face_detected": False,
            "face_count": 0,
        }
        
        # 단순한 품질 검사만
        vision_alerts = []
        vision_score = 0
        
        # 이미지 크기 검사
        if processed_data["image_size"] < 1000:
            vision_alerts.append("이미지 크기가 작음")
            vision_score += 1
        elif processed_data["image_size"] > 100000:  # 100KB 초과
            vision_alerts.append("이미지 크기가 큼")
            vision_score += 1
        
        # 이미지 없음
        if not processed_data["has_image"]:
            vision_alerts.append("이미지 없음")
            vision_score += 2
        
        # 알림 레벨 결정 (단순화)
        if vision_score >= 2:
            alert_level = "medium"
        elif vision_score >= 1:
            alert_level = "low"
        else:
            alert_level = "normal"
        
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
                  f"습도={processed_data['humidity']}%, 알림레벨={processed_data['alert_level']}")
            
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
            
            return {
                "status": "success",
                "message": "ESP32 센서 데이터 처리 완료",
                "device_type": "esp32",
                "timestamp": processed_data["timestamp"],
                "korea_time": get_korea_time().strftime("%Y년 %m월 %d일 %H:%M:%S"),
                "processing_results": {
                    "redis_stored": redis_stored,
                    "apps_notified": apps_notified,
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
        
            print(f"👁️ ESP Eye 데이터: 이미지크기={processed_data['image_size']}bytes")
        
            # 🔥 2. Base64 데이터 정리 및 검증
            raw_image = raw_data.get("image", "")
        
            # Base64 데이터 정리 (공백, 줄바꿈 제거)
            clean_image = raw_image.replace(" ", "").replace("\n", "").replace("\r", "").strip()
        
            # data:image/jpeg;base64, 접두사 제거 (있다면)
            if clean_image.startswith("data:"):
                clean_image = clean_image.split(",", 1)[-1]
        
            print(f"🔍 원본 이미지 길이: {len(raw_image)}")
            print(f"🔍 정리된 이미지 길이: {len(clean_image)}")
            print(f"🔍 이미지 시작: {clean_image[:30]}...")
        
            # 3. Base64 유효성 검사
            try:
                import base64
                # Base64 디코딩 테스트
                decoded_data = base64.b64decode(clean_image)
                print(f"✅ Base64 디코딩 성공: {len(decoded_data)} bytes")
            
                # JPEG 헤더 확인
                if decoded_data.startswith(b'\xff\xd8\xff'):
                    print("✅ JPEG 이미지 확인됨")
                else:
                    print("⚠️ JPEG 헤더가 아님")
                
            except Exception as decode_error:
                print(f"❌ Base64 디코딩 실패: {decode_error}")
                clean_image = ""  # 잘못된 데이터면 빈 문자열로
        
            # 4. Redis에 이미지 데이터 저장
            image_stored = False
            if self.redis_manager and hasattr(self.redis_manager, 'store_image_data') and clean_image:
                try:
                    image_data = {
                        "timestamp": processed_data["timestamp"],
                        "image_base64": clean_image,  # 🔥 정리된 데이터 저장
                        "has_image": bool(clean_image),
                        "alert_level": processed_data["alert_level"],
                        "metadata": {
                            "width": processed_data["image_width"],
                            "height": processed_data["image_height"],
                            "format": "jpeg",
                            "size": len(clean_image),
                            "decoded_size": len(decoded_data) if 'decoded_data' in locals() else 0
                        }
                    }
                    image_stored = self.redis_manager.store_image_data(image_data)
                    print(f"✅ 이미지 Redis 저장: {image_stored}")
                except Exception as e:
                    print(f"⚠️ 이미지 저장 실패: {e}")
        
            # 나머지 코드는 동일...
            return {
                "status": "success",
                "message": "ESP Eye 이미지 처리 완료",
                "device_type": "esp_eye",
                "timestamp": processed_data["timestamp"],
                "processing_results": {
                    "image_stored": image_stored,
                    "image_size": len(clean_image),
                    "image_valid": bool(clean_image)
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