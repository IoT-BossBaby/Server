# redis_manager.py 

import os
import json
import redis
from datetime import datetime
from typing import Dict, Any, Optional

class RedisManager:
    def __init__(self):
        self.redis_client = None
        self.available = False
        self.in_memory_storage = {}
        self._connect()
    
    def _connect(self):
        """초간단 Redis 연결 (SSL 설정 없음)"""
        redis_url = os.getenv("REDIS_URL")
        
        if not redis_url:
            print("⚠️ REDIS_URL 없음")
            return
        
        try:
            print(f"🔍 Redis 연결 시도...")
            
            # 🔥 최소한의 설정만 사용
            self.redis_client = redis.from_url(redis_url)
            
            # 연결 테스트
            result = self.redis_client.ping()
            
            if result:
                self.available = True
                print("✅ Redis 연결 성공!")
            else:
                print("❌ Redis ping 실패")
                
        except Exception as e:
            print(f"❌ Redis 연결 실패: {e}")
            self.available = False
            self.redis_client = None
    
    def store_esp32_data(self, data: Dict[str, Any]) -> bool:
        """ESP32 데이터 저장"""
        timestamp = datetime.now().isoformat()
        data_with_timestamp = {**data, "stored_at": timestamp}
        
        # Redis 시도
        if self.available and self.redis_client:
            try:
                self.redis_client.setex(
                    "current_esp32_data", 
                    300,  # 5분
                    json.dumps(data_with_timestamp)
                )
                return True
            except Exception as e:
                print(f"⚠️ Redis 저장 실패: {e}")
                self.available = False
        
        # 메모리 저장
        self.in_memory_storage["current_esp32_data"] = data_with_timestamp
        return True
    
    def store_image_data(self, image_data: Dict[str, Any]) -> bool:
        """이미지 데이터 저장"""
        try:
            if self.available and self.redis_client:
                try:
                    # Redis에 저장 (10분 TTL)
                    result = self.redis_client.setex(
                        "latest_image", 
                        600,  # 10분 TTL
                        json.dumps(image_data, ensure_ascii=False)
                    )
                    print(f"📦 Redis 이미지 저장: {result}")
                    return bool(result)
                except Exception as e:
                    print(f"⚠️ Redis 이미지 저장 실패: {e}")
                    self.available = False
        
            # 메모리 저장 (fallback)
            self.in_memory_storage["latest_image"] = image_data
            print(f"📦 메모리에 이미지 저장: {len(image_data.get('image_base64', ''))} bytes")
            return True
        
        except Exception as e:
            print(f"❌ 이미지 저장 총 오류: {e}")
            return False

    def get_latest_image(self) -> Optional[Dict[str, Any]]:
        """최신 이미지 조회"""
        try:
            # Redis 시도
            if self.available and self.redis_client:
                try:
                    data = self.redis_client.get("latest_image")
                    if data:
                        result = json.loads(data)
                        print(f"📦 Redis에서 이미지 조회: {len(result.get('image_base64', ''))} bytes")
                        return result
                except Exception as e:
                    print(f"⚠️ Redis 이미지 조회 실패: {e}")
                    self.available = False
        
            # 메모리 조회 (fallback)
            result = self.in_memory_storage.get("latest_image")
            if result:
                print(f"📦 메모리에서 이미지 조회: {len(result.get('image_base64', ''))} bytes")
            else:
                print("📦 저장된 이미지 없음")
            return result
        
        except Exception as e:
            print(f"❌ 이미지 조회 총 오류: {e}")
            return None
    
    def get_current_status(self) -> Optional[Dict[str, Any]]:
        """현재 상태 조회"""
        try:
            # Redis 시도
            if self.available and self.redis_client:
                try:
                    data = self.redis_client.get("current_esp32_data")
                    if data:
                        return json.loads(data)
                except Exception as e:
                    print(f"⚠️ Redis 조회 실패: {e}")
                    self.available = False
        
            # 메모리 조회
            return self.in_memory_storage.get("current_esp32_data")
        
        except Exception as e:
            print(f"❌ 상태 조회 총 오류: {e}")
            return None
    
    def reconnect(self):
        """재연결 시도"""
        self._connect()
        return self.available