import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
import subprocess
import os
import signal

def get_korea_time():
    """한국 시간 반환"""
    utc_now = datetime.now(timezone.utc)
    korea_offset = timedelta(hours=9)
    korea_tz = timezone(korea_offset)
    return utc_now.astimezone(korea_tz)

class MJPEGStreamHandler:
    def __init__(self, redis_manager, websocket_manager):
        self.redis_manager = redis_manager
        self.websocket_manager = websocket_manager
        
        # Node.js 스트리밍 서버 설정
        self.stream_server_port = 3001
        self.stream_server_process = None
        self.stream_server_url = f"http://localhost:{self.stream_server_port}"
        
        # ESP Eye 상태
        self.esp_eye_streaming = False
        self.stream_viewers = 0
        self.last_frame_time = None
        
        print("🎥 MJPEG 스트리밍 핸들러 초기화")
    
    async def start_stream_server(self):
        """Node.js MJPEG 스트리밍 서버 시작"""
        try:
            # Node.js 스크립트 생성
            nodejs_script = f"""
                const express = require('express');
                const app = express();
                const PORT = {self.stream_server_port};
                const BOUNDARY = 'frame';

                let clients = [];
                let frameCount = 0;
                let lastFrameTime = Date.now();

                // CORS 설정
                app.use((req, res, next) => {{
                    res.header('Access-Control-Allow-Origin', '*');
                    res.header('Access-Control-Allow-Headers', 'Origin, X-Requested-With, Content-Type, Accept');
                    next();
                }});

                // 1) ESP Eye가 푸시하는 엔드포인트
                app.post('/video', (req, res) => {{
                    console.log('🚀 ESP Eye MJPEG connected');
    
                    req.on('data', chunk => {{
                        frameCount++;
                        lastFrameTime = Date.now();
        
                        // 모든 스트리밍 클라이언트에게 전송
                        clients.forEach((clientRes, index) => {{
                            try {{
                                clientRes.write(chunk);
                            }} catch (err) {{
                                console.log(`❌ Client ${{index}} write error:`, err.message);
                                clients = clients.filter(r => r !== clientRes);
                            }}
                        }});
        
                        // Python 서버에 프레임 통계 전송 (선택사항)
                        if (frameCount % 30 === 0) {{
                            // 30프레임마다 한 번씩 통계 전송
                            fetch('http://localhost:8000/mjpeg/stats', {{
                                method: 'POST',
                                headers: {{ 'Content-Type': 'application/json' }},
                                body: JSON.stringify({{
                                    frameCount,
                                    viewers: clients.length,
                                    lastFrameTime: new Date().toISOString()
                                }})
                            }}).catch(err => console.log('Stats send failed:', err.message));
                        }}
                    }});
    
                    req.on('end', () => {{
                        console.log('📴 ESP Eye MJPEG disconnected');
                        res.sendStatus(200);
                    }});
    
                    req.on('error', (err) => {{
                        console.log('❌ ESP Eye connection error:', err.message);
                        res.sendStatus(500);
                    }});
                }});

                // 2) 클라이언트가 연결하는 스트림 엔드포인트
                app.get('/stream', (req, res) => {{
                    console.log('🔗 New viewer connected from:', req.ip);
    
                    res.writeHead(200, {{
                        'Content-Type': `multipart/x-mixed-replace;boundary=${{BOUNDARY}}`,
                        'Connection': 'keep-alive',
                        'Cache-Control': 'no-cache, no-store, must-revalidate',
                        'Pragma': 'no-cache',
                        'Expires': '0',
                        'Access-Control-Allow-Origin': '*'
                    }});
    
                    // 신규 클라이언트 등록
                    clients.push(res);
    
                    req.on('close', () => {{
                        console.log('❌ Viewer disconnected');
                        clients = clients.filter(r => r !== res);
                    }});
    
                    req.on('error', (err) => {{
                        console.log('❌ Viewer error:', err.message);
                        clients = clients.filter(r => r !== res);
                    }});
                }});

                // 3) 스트림 상태 확인 엔드포인트
                app.get('/status', (req, res) => {{
                    res.json({{
                        active: true,
                        viewers: clients.length,
                        frameCount,
                        lastFrameTime: new Date(lastFrameTime).toISOString(),
                        uptime: process.uptime()
                    }});
                }});

                // 서버 시작
                app.listen(PORT, '0.0.0.0', () => {{
                    console.log(`🎥 MJPEG Stream server running on http://0.0.0.0:${{PORT}}/stream`);
                    console.log(`📊 Status: http://0.0.0.0:${{PORT}}/status`);
                }});
                """
            
            # 임시 파일에 스크립트 저장
            script_path = "/tmp/mjpeg_server.js"
            with open(script_path, 'w') as f:
                f.write(nodejs_script)
            
            # Node.js 서버 시작
            self.stream_server_process = subprocess.Popen([
                'node', script_path
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # 서버 시작 확인
            await asyncio.sleep(2)
            if self.stream_server_process.poll() is None:
                print(f"✅ MJPEG 스트리밍 서버 시작됨: {self.stream_server_url}/stream")
                return True
            else:
                print("❌ MJPEG 서버 시작 실패")
                return False
                
        except Exception as e:
            print(f"❌ MJPEG 서버 시작 오류: {e}")
            return False
    
    async def stop_stream_server(self):
        """Node.js 스트리밍 서버 종료"""
        if self.stream_server_process:
            try:
                self.stream_server_process.terminate()
                await asyncio.sleep(1)
                if self.stream_server_process.poll() is None:
                    self.stream_server_process.kill()
                print("🛑 MJPEG 스트리밍 서버 종료됨")
            except Exception as e:
                print(f"❌ MJPEG 서버 종료 오류: {e}")
    
    async def handle_mjpeg_stats(self, stats_data: Dict[str, Any]):
        """Node.js 서버로부터 스트리밍 통계 수신"""
        try:
            self.stream_viewers = stats_data.get("viewers", 0)
            self.last_frame_time = stats_data.get("lastFrameTime")
            frame_count = stats_data.get("frameCount", 0)
            
            print(f"📊 MJPEG 통계: 시청자={self.stream_viewers}, 프레임={frame_count}")
            
            # Redis에 스트리밍 상태 저장
            if self.redis_manager and hasattr(self.redis_manager, 'update_stream_status'):
                stream_status = {
                    "streaming_active": True,
                    "viewers": self.stream_viewers,
                    "frame_count": frame_count,
                    "last_frame_time": self.last_frame_time,
                    "stream_url": f"{self.stream_server_url}/stream",
                    "timestamp": get_korea_time().isoformat()
                }
                self.redis_manager.update_stream_status(stream_status)
            
            # 앱에 스트리밍 상태 브로드캐스트
            if self.websocket_manager and hasattr(self.websocket_manager, 'broadcast_to_apps'):
                stream_update = {
                    "type": "mjpeg_stream_status",
                    "data": {
                        "streaming": True,
                        "viewers": self.stream_viewers,
                        "stream_url": f"{self.stream_server_url}/stream",
                        "last_update": get_korea_time().strftime("%Y년 %m월 %d일 %H:%M:%S")
                    },
                    "timestamp": get_korea_time().isoformat()
                }
                await self.websocket_manager.broadcast_to_apps(stream_update)
            
            return {
                "status": "success",
                "message": "스트리밍 통계 업데이트 완료"
            }
            
        except Exception as e:
            print(f"❌ MJPEG 통계 처리 오류: {e}")
            return {
                "status": "error",
                "message": f"통계 처리 실패: {str(e)}"
            }
    
    async def get_stream_status(self):
        """현재 스트리밍 상태 조회"""
        try:
            # Node.js 서버 상태 확인
            server_active = False
            if self.stream_server_process and self.stream_server_process.poll() is None:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            f"{self.stream_server_url}/status",
                            timeout=aiohttp.ClientTimeout(total=3)
                        ) as response:
                            if response.status == 200:
                                status_data = await response.json()
                                server_active = True
                                self.stream_viewers = status_data.get("viewers", 0)
                except:
                    server_active = False
            
            return {
                "server_active": server_active,
                "server_url": f"{self.stream_server_url}/stream",
                "viewers": self.stream_viewers,
                "esp_eye_streaming": self.esp_eye_streaming,
                "last_frame_time": self.last_frame_time,
                "timestamp": get_korea_time().isoformat()
            }
            
        except Exception as e:
            print(f"❌ 스트리밍 상태 조회 오류: {e}")
            return {
                "server_active": False,
                "error": str(e)
            }
    
    def get_stream_url_for_app(self):
        """앱에서 사용할 스트림 URL 반환"""
        if self.stream_server_process and self.stream_server_process.poll() is None:
            return f"{self.stream_server_url}/stream"
        return None