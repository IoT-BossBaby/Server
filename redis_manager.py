# Upstash Redis 최적화된 연결 설정

import os
import json
import redis
from redis.exceptions import ConnectionError, TimeoutError, RedisError
from datetime import datetime
from typing import Dict, Any, Optional
import time
import ssl

class RedisManager:
    def __init__(self):
        self.redis_client = None
        self.available = False
        self.in_memory_storage = {}
        self._connect_to_upstash()
    
    def _connect_to_upstash(self):
        """Upstash Redis 연결 (최적화)"""
        redis_url = os.getenv("REDIS_URL")
        
        if not redis_url:
            print("⚠️ REDIS_URL 환경변수 없음")
            self.available = False
            return
        
        try:
            print(f"🔍 Upstash Redis 연결 시도...")
            
            # Upstash 최적화 설정
            self.redis_client = redis.from_url(
                redis_url,
                socket_connect_timeout=10,     # 연결 타임아웃 늘림
                socket_timeout=10,             # 읽기 타임아웃 늘림
                socket_keepalive=True,         # Keep-alive 활성화
                socket_keepalive_options={},
                retry_on_timeout=True,         # 타임아웃 시 재시도
                retry_on_error=[ConnectionError, TimeoutError],  # 특정 오류 시 재시도
                decode_responses=True,         # 자동 디코딩
                health_check_interval=60,      # 헬스체크 간격
                max_connections=3,             # 연결 수 제한
                
                # SSL 설정 (Upstash TLS용)
                ssl_cert_reqs=ssl.CERT_NONE,   # SSL 인증서 검증 안함
                ssl_check_hostname=False       # 호스트명 검증 안함
            )
            
            # 간단한 연결 테스트
            result = self.redis_client.ping()
            if result:
                self.available = True
                print("✅ Upstash Redis 연결 성공!")
                
                # 연결 정보 출력
                info = self.redis_client.info('server')
                redis_version = info.get('redis_version', 'unknown')
                print(f"📊 Redis 버전: {redis_version}")
                return
                
        except ConnectionError as e:
            print(f"❌ Upstash 연결 오류: {e}")
            print("💡 Upstash 데이터베이스가 활성화되어 있는지 확인하세요")
            
        except redis.AuthenticationError as e:
            print(f"❌ Upstash 인증 오류: {e}")
            print("💡 Redis URL의 패스워드가 올바른지 확인하세요")
            
        except TimeoutError as e:
            print(f"❌ Upstash 타임아웃: {e}")
            print("💡 네트워크 연결 상태를 확인하세요")
            
        except Exception as e:
            print(f"❌ Upstash 연결 실패: {type(e).__name__} - {e}")
        
        # 연결 실패 시 fallback
        print("📦 메모리 기반 저장소로 fallback")
        self.available = False
        self.redis_client = None
    
    def store_esp32_data(self, data: Dict[str, Any]) -> bool:
        """ESP32 데이터 저장"""
        timestamp = datetime.now().isoformat()
        data_with_timestamp = {**data, "stored_at": timestamp}
        
        # Redis 시도
        if self.available and self.redis_client:
            try:
                result = self.redis_client.setex(
                    "current_esp32_data", 
                    300,  # 5분 TTL
                    json.dumps(data_with_timestamp, ensure_ascii=False)
                )
                if result:
                    return True
            except Exception as e:
                print(f"⚠️ Upstash 저장 실패: {e}")
                self._handle_connection_error()
        
        # 메모리 fallback
        self.in_memory_storage["current_esp32_data"] = data_with_timestamp
        return True
    
    def get_current_status(self) -> Optional[Dict[str, Any]]:
        """현재 상태 조회"""
        # Redis 시도
        if self.available and self.redis_client:
            try:
                data = self.redis_client.get("current_esp32_data")
                if data:
                    return json.loads(data)
            except Exception as e:
                print(f"⚠️ Upstash 조회 실패: {e}")
                self._handle_connection_error()
        
        # 메모리 fallback
        return self.in_memory_storage.get("current_esp32_data")
    
    def _handle_connection_error(self):
        """연결 오류 처리"""
        self.available = False
        print("📦 Upstash 연결 끊어짐 - 메모리 모드로 전환")
    
    def test_connection(self):
        """연결 테스트"""
        if self.available and self.redis_client:
            try:
                result = self.redis_client.ping()
                return {"status": "connected", "ping": result}
            except Exception as e:
                return {"status": "failed", "error": str(e)}
        else:
            return {"status": "not_available", "mode": "memory"}
    
    def get_stats(self):
        """Redis 통계"""
        stats = {
            "available": self.available,
            "storage_mode": "upstash" if self.available else "memory",
            "memory_items": len(self.in_memory_storage)
        }
        
        if self.available and self.redis_client:
            try:
                info = self.redis_client.info()
                stats.update({
                    "redis_version": info.get('redis_version'),
                    "connected_clients": info.get('connected_clients'),
                    "used_memory_human": info.get('used_memory_human')
                })
            except:
                pass
                
        return stats