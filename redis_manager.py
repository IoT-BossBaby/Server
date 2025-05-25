import redis
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any

class RedisManager:
    def __init__(self):
        self.client = None
        self.available = False
        self._connect()
    
    def _connect(self):
        """Redis 연결"""
        try:
            REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
            REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
            REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', None)
            REDIS_URL = os.getenv('REDIS_URL', None)
            
            print(f"🔍 Redis 연결 시도...")
            
            if REDIS_URL:
                self.client = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=10)
            elif REDIS_HOST and REDIS_HOST != 'localhost':
                self.client = redis.Redis(
                    host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD,
                    decode_responses=True, socket_timeout=10
                )
            
            if self.client:
                self.client.ping()
                self.available = True
                print(f"✅ Redis 연결 성공!")
        except Exception as e:
            print(f"⚠️ Redis 연결 실패: {e}")
            self.client = None
            self.available = False
    
    def store_esp32_data(self, data: Dict[str, Any]) -> bool:
        """ESP32 데이터 저장"""
        if not self.available:
            return False
        
        try:
            timestamp = datetime.now().isoformat()
            
            # 현재 상태 저장 (실시간 조회용)
            self.client.setex("current_esp32_data", 300, json.dumps(data))
            
            # 히스토리 저장
            hour_key = f"esp32_history:{datetime.now().strftime('%Y%m%d_%H')}"
            self.client.lpush(hour_key, json.dumps(data))
            self.client.expire(hour_key, 86400 * 7)  # 7일 보관
            
            return True
        except Exception as e:
            print(f"Redis 저장 실패: {e}")
            return False
    
    def store_image_data(self, image_data: Dict[str, Any]) -> bool:
        """이미지 데이터 저장"""
        if not self.available:
            return False
        
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            image_key = f"esp32_image:{timestamp}"
            
            # 이미지 데이터 저장 (1시간 보관)
            self.client.setex(image_key, 3600, json.dumps(image_data))
            
            # 최근 이미지 목록 관리
            self.client.lpush("recent_images", image_key)
            self.client.ltrim("recent_images", 0, 19)  # 최근 20개만
            
            return True
        except Exception as e:
            print(f"이미지 저장 실패: {e}")
            return False
    
    def get_current_status(self) -> Optional[Dict[str, Any]]:
        """현재 상태 조회"""
        if not self.available:
            return None
        
        try:
            current_data = self.client.get("current_esp32_data")
            return json.loads(current_data) if current_data else None
        except Exception as e:
            print(f"상태 조회 실패: {e}")
            return None
    
    def get_recent_images(self, count: int = 5) -> List[Dict[str, Any]]:
        """최근 이미지들 조회"""
        if not self.available:
            return []
        
        try:
            recent_keys = self.client.lrange("recent_images", 0, count - 1)
            images = []
            
            for key in recent_keys:
                image_data = self.client.get(key)
                if image_data:
                    images.append(json.loads(image_data))
            
            return images
        except Exception as e:
            print(f"이미지 조회 실패: {e}")
            return []
    
    def store_command(self, command: Dict[str, Any]) -> bool:
        """ESP32 명령 저장"""
        if not self.available:
            return False
        
        try:
            # ESP32가 읽을 수 있도록 저장
            self.client.setex("esp32_command", 60, json.dumps(command))
            self.client.publish("esp32_commands", json.dumps(command))
            return True
        except Exception as e:
            print(f"명령 저장 실패: {e}")
            return False
    
    def publish_alert(self, alert_data: Dict[str, Any]) -> bool:
        """알림 발행"""
        if not self.available:
            return False
        
        try:
            self.client.publish("baby_alerts", json.dumps(alert_data))
            return True
        except Exception as e:
            print(f"알림 발행 실패: {e}")
            return False
    
    def get_daily_stats(self, date: str = None) -> Dict[str, Any]:
        """일일 통계"""
        if not self.available:
            return {}
        
        if not date:
            date = datetime.now().strftime("%Y%m%d")
        
        try:
            stats = {
                "date": date,
                "total_readings": 0,
                "baby_detected_count": 0,
                "alerts_count": 0,
                "temperature_avg": 0,
                "humidity_avg": 0
            }
            
            # 해당 날짜의 모든 시간대 데이터 수집
            all_data = []
            for hour in range(24):
                hour_key = f"esp32_history:{date}_{hour:02d}"
                hour_data = self.client.lrange(hour_key, 0, -1)
                for data_str in hour_data:
                    all_data.append(json.loads(data_str))
            
            if all_data:
                stats["total_readings"] = len(all_data)
                stats["baby_detected_count"] = sum(1 for d in all_data if d.get("baby_detected", False))
                
                temps = [d.get("temperature", 0) for d in all_data if d.get("temperature")]
                humids = [d.get("humidity", 0) for d in all_data if d.get("humidity")]
                
                if temps:
                    stats["temperature_avg"] = round(sum(temps) / len(temps), 1)
                if humids:
                    stats["humidity_avg"] = round(sum(humids) / len(humids), 1)
            
            return stats
        except Exception as e:
            print(f"통계 조회 실패: {e}")
            return {}