import asyncio
import json
from fastapi import WebSocket
from typing import List, Dict, Any
from datetime import datetime, timezone
import time

class WebSocketManager:
    def __init__(self):
        # 연결 관리를 Dict 기반으로 통일
        self.active_connections: Dict[str, Dict[str, Any]] = {}
        self.connection_counter = 0
    
    async def connect(self, websocket: WebSocket, client_type: str = "unknown", client_info: Dict[str, Any] = None) -> str:
        """WebSocket 연결"""
        await websocket.accept()
        
        # 고유한 클라이언트 ID 생성
        self.connection_counter += 1
        client_id = f"{client_type}_{int(time.time())}_{self.connection_counter}"
        
        # 연결 정보 저장
        self.active_connections[client_id] = {
            "websocket": websocket,
            "client_type": client_type,
            "connected_at": datetime.now(timezone.utc).isoformat(),
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "client_info": client_info or {},
            "message_count": 0
        }
        
        print(f"📱 {client_type} 연결됨 ({client_id}). 총 연결: {len(self.active_connections)}")
        
        # 연결 즉시 환영 메시지
        await self.send_to_connection(client_id, {
            "type": "connection_established",
            "message": "Baby Monitor에 연결되었습니다",
            "client_id": client_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "server_status": "online"
        })
        
        return client_id
    
    def disconnect(self, client_id: str):
        """WebSocket 연결 해제"""
        if client_id in self.active_connections:
            client_info = self.active_connections[client_id]
            client_type = client_info.get("client_type", "unknown")
            del self.active_connections[client_id]
            print(f"📱 {client_type} 연결 해제 ({client_id}). 남은 연결: {len(self.active_connections)}")
    
    async def send_to_connection(self, client_id: str, data: Dict[str, Any]):
        """특정 연결에 메시지 전송"""
        if client_id not in self.active_connections:
            return False
        
        try:
            websocket = self.active_connections[client_id]["websocket"]
            await websocket.send_json(data)
            
            # 메시지 카운트 및 활동 시간 업데이트
            self.active_connections[client_id]["message_count"] += 1
            self.active_connections[client_id]["last_seen"] = datetime.now(timezone.utc).isoformat()
            
            return True
                
        except Exception as e:
            print(f"❌ WebSocket 전송 실패 ({client_id}): {e}")
            self.disconnect(client_id)
            return False
    
    async def broadcast_to_all(self, data: Dict[str, Any]) -> int:
        """모든 연결에 브로드캐스트"""
        if not self.active_connections:
            return 0
        
        # 타임스탬프 추가
        message_data = {
            **data,
            "broadcast_timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        disconnected = []
        sent_count = 0
        
        for client_id, client_info in self.active_connections.items():
            try:
                websocket = client_info["websocket"]
                await websocket.send_json(message_data)
                
                # 메시지 카운트 및 활동 시간 업데이트
                client_info["message_count"] += 1
                client_info["last_seen"] = datetime.now(timezone.utc).isoformat()
                sent_count += 1
                    
            except Exception as e:
                print(f"❌ 브로드캐스트 실패 ({client_id}): {e}")
                disconnected.append(client_id)
        
        # 끊어진 연결 제거
        for client_id in disconnected:
            self.disconnect(client_id)
        
        if sent_count > 0:
            print(f"📡 {sent_count}개 클라이언트에 데이터 전송")
        
        return sent_count
    
    async def broadcast_to_apps(self, data: Dict[str, Any]) -> int:
        """모바일 앱들에게만 브로드캐스트"""
        if not self.active_connections:
            return 0
        
        # 앱 클라이언트들 필터링
        app_clients = {
            client_id: client_info 
            for client_id, client_info in self.active_connections.items()
            if client_info.get("client_type") == "mobile_app"
        }
        
        if not app_clients:
            return 0
        
        # 메시지에 타입과 타임스탬프 추가
        message_data = {
            "type": "esp32_data",
            **data,
            "app_broadcast_timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        disconnected = []
        sent_count = 0
        
        for client_id, client_info in app_clients.items():
            try:
                websocket = client_info["websocket"]
                await websocket.send_json(message_data)
                
                # 메시지 카운트 및 활동 시간 업데이트
                client_info["message_count"] += 1
                client_info["last_seen"] = datetime.now(timezone.utc).isoformat()
                sent_count += 1
                    
            except Exception as e:
                print(f"❌ 앱 브로드캐스트 실패 ({client_id}): {e}")
                disconnected.append(client_id)
        
        # 끊어진 연결 제거
        for client_id in disconnected:
            self.disconnect(client_id)
        
        if sent_count > 0:
            print(f"📱 {sent_count}개 앱에 ESP32 데이터 전송")
        
        return sent_count
    
    async def handle_app_message(self, client_id: str, message: str, esp32_handler=None):
        """앱에서 온 메시지 처리 (자장가 제어 포함)"""
        try:
            data = json.loads(message)
            message_type = data.get("type")
            
            print(f"📱 앱 메시지 수신 ({client_id}): {message_type}")
            
            if message_type == "command":
                # ESP32에 명령 전달
                command_name = data.get("command")
                params = data.get("params", {})
                
                print(f"🎵 앱 명령: {command_name}, 파라미터: {params}")
                
                if esp32_handler:
                    # ESP32로 전송할 명령 구성
                    esp32_command = {
                        "command": command_name,
                        "params": params,
                        "source": "mobile_app",
                        "client_id": client_id,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                    
                    # 자장가 제어 명령 특별 처리
                    if command_name == "lullaby_control":
                        enabled = params.get("enabled")  # true/false
                        song = params.get("song", "default")
                        volume = params.get("volume", 50)
                        
                        esp32_command["params"] = {
                            "action": "start" if enabled else "stop",
                            "song": song,
                            "volume": volume
                        }
                        
                        print(f"🎵 자장가 제어: {'켜기' if enabled else '끄기'} (곡: {song}, 볼륨: {volume})")
                    
                    # ESP32로 명령 전송
                    success = await esp32_handler.send_command_to_esp32(esp32_command)
                    
                    # 앱에 응답 전송
                    response = {
                        "type": "command_response",
                        "original_command": command_name,
                        "params": params,
                        "status": "sent" if success else "failed",
                        "esp32_status": getattr(esp32_handler, 'esp32_status', 'unknown'),
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                    
                    await self.send_to_connection(client_id, response)
                    
                    # 다른 앱들에게도 상태 변경 알림
                    if success and command_name == "lullaby_control":
                        notification = {
                            "type": "lullaby_status_changed",
                            "enabled": params.get("enabled"),
                            "song": params.get("song"),
                            "changed_by": client_id,
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        }
                        await self.broadcast_to_apps(notification)
                
                else:
                    # ESP32 핸들러가 없는 경우
                    response = {
                        "type": "command_response",
                        "original_command": command_name,
                        "status": "failed",
                        "error": "ESP32 handler not available",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                    await self.send_to_connection(client_id, response)
                
            elif message_type == "request_status":
                # 현재 상태 요청
                response = {
                    "type": "status_response",
                    "server_status": "online",
                    "esp32_connected": getattr(esp32_handler, 'esp32_status', 'unknown') == 'connected' if esp32_handler else False,
                    "active_connections": len(self.active_connections),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                await self.send_to_connection(client_id, response)
                
            elif message_type == "request_image":
                # 최신 이미지 요청
                response = {
                    "type": "image_request_response",
                    "message": "Use /app/images/latest API endpoint for image data",
                    "api_endpoint": "/app/images/latest",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                await self.send_to_connection(client_id, response)
                
            elif message_type == "ping":
                # 연결 상태 확인
                response = {
                    "type": "pong",
                    "client_id": client_id,
                    "server_time": datetime.now(timezone.utc).isoformat(),
                    "server_status": "online"
                }
                await self.send_to_connection(client_id, response)
                
            else:
                # 알 수 없는 메시지 타입
                response = {
                    "type": "error",
                    "message": f"Unknown message type: {message_type}",
                    "supported_types": ["command", "request_status", "request_image", "ping"],
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                await self.send_to_connection(client_id, response)
                
        except json.JSONDecodeError:
            # JSON 파싱 오류
            error_response = {
                "type": "error",
                "message": "Invalid JSON format",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            await self.send_to_connection(client_id, error_response)
            
        except Exception as e:
            # 기타 오류
            error_response = {
                "type": "error",
                "message": f"Message processing error: {str(e)}",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            await self.send_to_connection(client_id, error_response)
    
    async def send_alert(self, alert_data: Dict[str, Any]) -> int:
        """긴급 알림 전송"""
        alert_message = {
            "type": "emergency_alert",
            "priority": "high",
            **alert_data,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return await self.broadcast_to_apps(alert_message)
    
    async def send_image_update(self, image_data: Dict[str, Any]) -> int:
        """이미지 업데이트 전송"""
        # 이미지 데이터는 크니까 메타데이터만 전송
        image_message = {
            "type": "image_update",
            "timestamp": image_data.get("timestamp"),
            "baby_detected": image_data.get("baby_detected"),
            "confidence": image_data.get("confidence"),
            "alert_level": image_data.get("alert_level"),
            "has_image": True,
            "image_available": True,
            "download_url": "/app/images/latest"
        }
        
        return await self.broadcast_to_apps(image_message)
    
    async def send_system_status(self, status_data: Dict[str, Any]) -> int:
        """시스템 상태 업데이트 전송"""
        message = {
            "type": "system_status",
            **status_data,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return await self.broadcast_to_all(message)
    
    def get_connection_stats(self) -> Dict[str, Any]:
        """연결 통계"""
        total_messages = sum(
            info.get("message_count", 0) 
            for info in self.active_connections.values()
        )
        
        by_type = {}
        for info in self.active_connections.values():
            client_type = info.get("client_type", "unknown")
            by_type[client_type] = by_type.get(client_type, 0) + 1
        
        return {
            "active_connections": len(self.active_connections),
            "total_messages_sent": total_messages,
            "connections_by_type": by_type,
            "connections_info": [
                {
                    "client_id": client_id,
                    "client_type": info["client_type"],
                    "connected_at": info["connected_at"],
                    "last_seen": info["last_seen"],
                    "message_count": info["message_count"],
                    "client_info": info["client_info"]
                }
                for client_id, info in self.active_connections.items()
            ]
        }