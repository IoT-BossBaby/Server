import base64
import io
from PIL import Image
import os
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

class ImageHandler:
    def __init__(self, upload_folder: str = "uploaded_images"):
        self.upload_folder = upload_folder
        self._ensure_upload_folder()
    
    def _ensure_upload_folder(self):
        """업로드 폴더 생성"""
        if not os.path.exists(self.upload_folder):
            os.makedirs(self.upload_folder)
            print(f"📁 이미지 폴더 생성: {self.upload_folder}")
    
    def decode_base64_image(self, base64_string: str) -> Optional[Image.Image]:
        """Base64 문자열을 PIL Image로 디코드"""
        try:
            # Base64 헤더 제거 (data:image/jpeg;base64, 등)
            if ',' in base64_string:
                base64_string = base64_string.split(',')[1]
            
            # Base64 디코드
            image_data = base64.b64decode(base64_string)
            
            # PIL Image로 변환
            image = Image.open(io.BytesIO(image_data))
            
            return image
        except Exception as e:
            print(f"❌ Base64 이미지 디코드 실패: {e}")
            return None
    
    def encode_image_to_base64(self, image: Image.Image, format: str = "JPEG", quality: int = 85) -> str:
        """PIL Image를 Base64 문자열로 인코드"""
        try:
            buffer = io.BytesIO()
            image.save(buffer, format=format, quality=quality)
            buffer.seek(0)
            
            # Base64 인코딩
            base64_string = base64.b64encode(buffer.getvalue()).decode('utf-8')
            
            return base64_string
        except Exception as e:
            print(f"❌ Base64 이미지 인코드 실패: {e}")
            return ""
    
    def save_image_from_base64(self, base64_string: str, filename: str = None) -> Optional[Dict[str, Any]]:
        """Base64 이미지를 파일로 저장"""
        try:
            # 이미지 디코드
            image = self.decode_base64_image(base64_string)
            if not image:
                return None
            
            # 파일명 생성
            if not filename:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"baby_image_{timestamp}.jpg"
            
            # 파일 경로
            filepath = os.path.join(self.upload_folder, filename)
            
            # 이미지 저장 (JPEG 형식으로 통일)
            if image.mode in ('RGBA', 'LA', 'P'):
                # 투명도가 있는 이미지는 RGB로 변환
                rgb_image = Image.new('RGB', image.size, (255, 255, 255))
                if image.mode == 'P':
                    image = image.convert('RGBA')
                rgb_image.paste(image, mask=image.split()[-1] if image.mode == 'RGBA' else None)
                image = rgb_image
            
            image.save(filepath, "JPEG", quality=90)
            
            # 이미지 정보 반환
            return {
                "filename": filename,
                "filepath": filepath,
                "size": image.size,
                "mode": image.mode,
                "format": "JPEG",
                "file_size_bytes": os.path.getsize(filepath),
                "file_size_kb": os.path.getsize(filepath) // 1024,
                "saved_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            print(f"❌ 이미지 저장 실패: {e}")
            return None
    
    def create_thumbnail(self, image: Image.Image, size: Tuple[int, int] = (320, 240)) -> Image.Image:
        """썸네일 생성"""
        try:
            # 비율 유지하면서 리사이즈
            image.thumbnail(size, Image.Resampling.LANCZOS)
            return image
        except Exception as e:
            print(f"❌ 썸네일 생성 실패: {e}")
            return image
    
    def analyze_image_basic(self, image: Image.Image) -> Dict[str, Any]:
        """기본 이미지 분석 (ML 없이)"""
        try:
            width, height = image.size
            
            # 기본 정보
            analysis = {
                "width": width,
                "height": height,
                "aspect_ratio": round(width / height, 2),
                "total_pixels": width * height,
                "orientation": "landscape" if width > height else "portrait" if height > width else "square"
            }
            
            # 이미지 품질 분석
            quality_factors = []
            quality_score = 100
            
            # 해상도 체크
            if width < 320 or height < 240:
                quality_factors.append("Low resolution")
                quality_score -= 20
            elif width > 1920 or height > 1080:
                quality_factors.append("Very high resolution")
            
            # 이미지를 numpy 배열로 변환해서 밝기 분석
            try:
                import numpy as np
                img_array = np.array(image)
                
                if len(img_array.shape) == 3:  # 컬러 이미지
                    # 평균 밝기
                    avg_brightness = np.mean(img_array)
                    analysis["average_brightness"] = round(float(avg_brightness), 2)
                    
                    if avg_brightness < 50:
                        quality_factors.append("Too dark")
                        quality_score -= 30
                    elif avg_brightness > 200:
                        quality_factors.append("Overexposed")
                        quality_score -= 20
                    
                    # 색상 채널 분석
                    if img_array.shape[2] >= 3:
                        r_avg = np.mean(img_array[:, :, 0])
                        g_avg = np.mean(img_array[:, :, 1])
                        b_avg = np.mean(img_array[:, :, 2])
                        
                        analysis["color_channels"] = {
                            "red": round(float(r_avg), 2),
                            "green": round(float(g_avg), 2),
                            "blue": round(float(b_avg), 2)
                        }
                        
                        # 색상 균형 체크
                        color_diff = max(r_avg, g_avg, b_avg) - min(r_avg, g_avg, b_avg)
                        if color_diff > 50:
                            quality_factors.append("Color imbalance")
                            quality_score -= 10
                
            except ImportError:
                # numpy가 없으면 기본 분석만
                analysis["note"] = "Basic analysis only (numpy not available)"
            except Exception as e:
                print(f"이미지 고급 분석 실패: {e}")
            
            # 파일 크기 기반 품질 추정
            analysis["quality_factors"] = quality_factors if quality_factors else ["Good quality"]
            analysis["quality_score"] = max(0, min(100, quality_score))
            analysis["quality_level"] = (
                "excellent" if quality_score >= 90 else
                "good" if quality_score >= 70 else
                "fair" if quality_score >= 50 else
                "poor"
            )
            
            return analysis
            
        except Exception as e:
            print(f"❌ 이미지 분석 실패: {e}")
            return {"error": str(e)}
    
    def process_esp32_image(self, base64_string: str, save_to_disk: bool = True) -> Dict[str, Any]:
        """ESP32에서 받은 이미지 전체 처리"""
        try:
            result = {
                "processed_at": datetime.now().isoformat(),
                "success": False
            }
            
            # 1. Base64 디코드
            image = self.decode_base64_image(base64_string)
            if not image:
                result["error"] = "Failed to decode base64 image"
                return result
            
            # 2. 기본 분석
            analysis = self.analyze_image_basic(image)
            result["analysis"] = analysis
            
            # 3. 썸네일 생성
            thumbnail = self.create_thumbnail(image.copy(), (320, 240))
            thumbnail_base64 = self.encode_image_to_base64(thumbnail, quality=70)
            result["thumbnail_base64"] = thumbnail_base64
            
            # 4. 파일 저장 (옵션)
            if save_to_disk:
                save_info = self.save_image_from_base64(base64_string)
                if save_info:
                    result["saved_file"] = save_info
                else:
                    result["save_error"] = "Failed to save image to disk"
            
            # 5. 이미지 메타데이터
            result["metadata"] = {
                "original_size": image.size,
                "thumbnail_size": thumbnail.size,
                "original_mode": image.mode,
                "base64_size_chars": len(base64_string),
                "thumbnail_base64_size": len(thumbnail_base64)
            }
            
            result["success"] = True
            result["message"] = "Image processed successfully"
            
            return result
            
        except Exception as e:
            print(f"❌ ESP32 이미지 처리 실패: {e}")
            return {
                "processed_at": datetime.now().isoformat(),
                "success": False,
                "error": str(e)
            }
    
    def get_image_by_filename(self, filename: str) -> Optional[Dict[str, Any]]:
        """파일명으로 저장된 이미지 정보 조회"""
        try:
            filepath = os.path.join(self.upload_folder, filename)
            
            if not os.path.exists(filepath):
                return None
            
            # 파일 정보
            file_stat = os.stat(filepath)
            
            # 이미지 로드
            image = Image.open(filepath)
            
            return {
                "filename": filename,
                "filepath": filepath,
                "size": image.size,
                "mode": image.mode,
                "format": image.format,
                "file_size_bytes": file_stat.st_size,
                "file_size_kb": file_stat.st_size // 1024,
                "created_at": datetime.fromtimestamp(file_stat.st_ctime).isoformat(),
                "modified_at": datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            }
            
        except Exception as e:
            print(f"❌ 이미지 정보 조회 실패: {e}")
            return None
    
    def list_recent_images(self, count: int = 10) -> list:
        """최근 저장된 이미지 목록"""
        try:
            if not os.path.exists(self.upload_folder):
                return []
            
            # 폴더의 모든 이미지 파일
            image_files = []
            for filename in os.listdir(self.upload_folder):
                if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp')):
                    filepath = os.path.join(self.upload_folder, filename)
                    file_stat = os.stat(filepath)
                    image_files.append({
                        "filename": filename,
                        "created_at": file_stat.st_ctime,
                        "size_bytes": file_stat.st_size
                    })
            
            # 생성 시간순 정렬 (최신순)
            image_files.sort(key=lambda x: x["created_at"], reverse=True)
            
            # 상위 count개만 반환
            return image_files[:count]
            
        except Exception as e:
            print(f"❌ 이미지 목록 조회 실패: {e}")
            return []
    
    def cleanup_old_images(self, days_old: int = 7) -> int:
        """오래된 이미지 파일 정리"""
        try:
            if not os.path.exists(self.upload_folder):
                return 0
            
            cutoff_time = datetime.now().timestamp() - (days_old * 24 * 3600)
            deleted_count = 0
            
            for filename in os.listdir(self.upload_folder):
                filepath = os.path.join(self.upload_folder, filename)
                
                if os.path.isfile(filepath):
                    file_stat = os.stat(filepath)
                    
                    if file_stat.st_ctime < cutoff_time:
                        os.remove(filepath)
                        deleted_count += 1
                        print(f"🗑️ 오래된 이미지 삭제: {filename}")
            
            if deleted_count > 0:
                print(f"✅ {deleted_count}개 오래된 이미지 정리 완료")
            
            return deleted_count
            
        except Exception as e:
            print(f"❌ 이미지 정리 실패: {e}")
            return 0