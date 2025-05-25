import numpy as np
from PIL import Image
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List
import json

class MLProcessor:
    def __init__(self):
        self.ml_available = False
        self.yolo_model = None
        self.pose_model = None
        self.model_info = {}
        self._initialize_models()
    
    def _initialize_models(self):
        """ML 모델 초기화"""
        try:
            # YOLO 모델 로드 시도
            self._load_yolo_model()
            
            # Pose 모델 로드 시도
            self._load_pose_model()
            
            if self.yolo_model or self.pose_model:
                self.ml_available = True
                print("✅ ML 모델 초기화 완료")
            else:
                print("⚠️ ML 모델 없음 - 기본 분석 모드")
                
        except Exception as e:
            print(f"❌ ML 모델 초기화 실패: {e}")
            self.ml_available = False
    
    def _load_yolo_model(self):
        """YOLO 모델 로드"""
        try:
            import torch
            from ultralytics import YOLO
            
            print("🤖 YOLO 모델 로드 중...")
            self.yolo_model = YOLO('yolov8n.pt')  # nano 버전 (가벼움)
            
            self.model_info["yolo"] = {
                "version": "YOLOv8 nano",
                "loaded": True,
                "classes": ["person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat"],  # COCO classes 예시
                "confidence_threshold": 0.3
            }
            
            print("✅ YOLO 모델 로드 성공!")
            
        except ImportError:
            print("⚠️ YOLO 라이브러리 없음 (ultralytics, torch)")
            self.yolo_model = None
        except Exception as e:
            print(f"❌ YOLO 모델 로드 실패: {e}")
            self.yolo_model = None
    
    def _load_pose_model(self):
        """Pose estimation 모델 로드"""
        try:
            import mediapipe as mp
            
            print("🦴 MediaPipe Pose 모델 로드 중...")
            
            mp_pose = mp.solutions.pose
            self.pose_model = mp_pose.Pose(
                static_image_mode=True,
                model_complexity=1,
                enable_segmentation=False,
                min_detection_confidence=0.5
            )
            
            self.model_info["pose"] = {
                "version": "MediaPipe Pose",
                "loaded": True,
                "landmarks_count": 33,
                "confidence_threshold": 0.5
            }
            
            print("✅ MediaPipe Pose 모델 로드 성공!")
            
        except ImportError:
            print("⚠️ MediaPipe 라이브러리 없음")
            self.pose_model = None
        except Exception as e:
            print(f"❌ Pose 모델 로드 실패: {e}")
            self.pose_model = None
    
    def detect_objects(self, image: Image.Image) -> Dict[str, Any]:
        """YOLO 객체 탐지"""
        
        if not self.yolo_model:
            return {
                "model_available": False,
                "detections": [],
                "person_detected": False,
                "message": "YOLO model not available"
            }
        
        try:
            # PIL Image를 numpy 배열로 변환
            img_array = np.array(image)
            
            # YOLO 추론
            results = self.yolo_model(img_array)
            
            detections = []
            person_detected = False
            person_confidence = 0.0
            person_count = 0
            
            if results and len(results[0].boxes) > 0:
                for box in results[0].boxes:
                    class_id = int(box.cls[0])
                    confidence = float(box.conf[0])
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                    
                    # 클래스 이름 매핑 (COCO dataset)
                    class_names = {0: "person", 1: "bicycle", 2: "car", 16: "dog", 17: "cat"}
                    class_name = class_names.get(class_id, f"class_{class_id}")
                    
                    detection = {
                        "class_id": class_id,
                        "class_name": class_name,
                        "confidence": confidence,
                        "bbox": [float(x1), float(y1), float(x2), float(y2)],
                        "bbox_center": [float((x1 + x2) / 2), float((y1 + y2) / 2)],
                        "bbox_area": float((x2 - x1) * (y2 - y1))
                    }
                    detections.append(detection)
                    
                    # 사람 감지 확인
                    if class_id == 0:  # person
                        person_detected = True
                        person_confidence = max(person_confidence, confidence)
                        person_count += 1
            
            return {
                "model_available": True,
                "detections": detections,
                "detection_count": len(detections),
                "person_detected": person_detected,
                "person_confidence": person_confidence,
                "person_count": person_count,
                "image_size": img_array.shape[:2],  # (height, width)
                "processing_time": datetime.now().isoformat()
            }
            
        except Exception as e:
            print(f"❌ YOLO 객체 탐지 오류: {e}")
            return {
                "model_available": True,
                "error": str(e),
                "detections": [],
                "person_detected": False
            }
    
    def analyze_pose(self, image: Image.Image) -> Dict[str, Any]:
        """자세 분석"""
        
        if not self.pose_model:
            return {
                "model_available": False,
                "pose_detected": False,
                "message": "Pose model not available"
            }
        
        try:
            import mediapipe as mp
            
            # PIL Image를 RGB numpy 배열로 변환
            img_array = np.array(image)
            
            # MediaPipe는 RGB 포맷 필요
            if img_array.shape[2] == 3:  # RGB
                rgb_image = img_array
            else:  # RGBA 등
                rgb_image = img_array[:, :, :3]
            
            # Pose 추론
            results = self.pose_model.process(rgb_image)
            
            if not results.pose_landmarks:
                return {
                    "model_available": True,
                    "pose_detected": False,
                    "landmarks": [],
                    "message": "No pose detected"
                }
            
            # 랜드마크 추출
            landmarks = []
            landmark_names = [
                "nose", "left_eye_inner", "left_eye", "left_eye_outer",
                "right_eye_inner", "right_eye", "right_eye_outer",
                "left_ear", "right_ear", "mouth_left", "mouth_right",
                "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
                "left_wrist", "right_wrist", "left_pinky", "right_pinky",
                "left_index", "right_index", "left_thumb", "right_thumb",
                "left_hip", "right_hip", "left_knee", "right_knee",
                "left_ankle", "right_ankle", "left_heel", "right_heel",
                "left_foot_index", "right_foot_index"
            ]
            
            for i, landmark in enumerate(results.pose_landmarks.landmark):
                landmark_info = {
                    "id": i,
                    "name": landmark_names[i] if i < len(landmark_names) else f"landmark_{i}",
                    "x": landmark.x,
                    "y": landmark.y,
                    "z": landmark.z,
                    "visibility": landmark.visibility
                }
                landmarks.append(landmark_info)
            
            return {
                "model_available": True,
                "pose_detected": True,
                "landmarks": landmarks,
                "landmark_count": len(landmarks),
                "processing_time": datetime.now().isoformat()
            }
            
        except Exception as e:
            print(f"❌ Pose 분석 오류: {e}")
            return {
                "model_available": True,
                "error": str(e),
                "pose_detected": False,
                "landmarks": []
            }
    
    def analyze_baby_safety(self, object_detection: Dict[str, Any], pose_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """아기 안전 상태 종합 분석"""
        
        safety_analysis = {
            "timestamp": datetime.now().isoformat(),
            "risk_factors": [],
            "risk_score": 0,
            "risk_level": "unknown",
            "recommendations": []
        }
        
        # 1. 객체 탐지 결과 분석
        if object_detection.get("model_available"):
            if not object_detection.get("person_detected"):
                safety_analysis["risk_factors"].append("No person detected in view")
                safety_analysis["risk_score"] += 2
                safety_analysis["recommendations"].append("Check camera position and lighting")
            
            elif object_detection.get("person_confidence", 0) < 0.5:
                safety_analysis["risk_factors"].append("Low person detection confidence")
                safety_analysis["risk_score"] += 1
                safety_analysis["recommendations"].append("Improve lighting or camera focus")
            
            # 여러 사람 감지
            if object_detection.get("person_count", 0) > 1:
                safety_analysis["risk_factors"].append(f"Multiple persons detected ({object_detection['person_count']})")
                safety_analysis["risk_score"] += 1
        
        # 2. 자세 분석 결과
        if pose_analysis.get("model_available") and pose_analysis.get("pose_detected"):
            landmarks = pose_analysis.get("landmarks", [])
            
            if landmarks:
                # 얼굴 방향 분석
                nose = next((l for l in landmarks if l["name"] == "nose"), None)
                left_ear = next((l for l in landmarks if l["name"] == "left_ear"), None)
                right_ear = next((l for l in landmarks if l["name"] == "right_ear"), None)
                
                if nose and nose["visibility"] > 0.5:
                    # 얼굴이 아래를 향하는지 확인 (위험한 엎드린 자세)
                    if left_ear and right_ear:
                        if left_ear["visibility"] < 0.3 and right_ear["visibility"] < 0.3:
                            safety_analysis["risk_factors"].append("Possible face-down position (SIDS risk)")
                            safety_analysis["risk_score"] += 3
                            safety_analysis["recommendations"].append("Turn baby to back-sleeping position immediately")
                
                # 손이 얼굴 근처에 있는지 확인
                left_wrist = next((l for l in landmarks if l["name"] == "left_wrist"), None)
                right_wrist = next((l for l in landmarks if l["name"] == "right_wrist"), None)
                
                if nose and nose["visibility"] > 0.5:
                    for wrist in [left_wrist, right_wrist]:
                        if wrist and wrist["visibility"] > 0.5:
                            # 손과 얼굴의 거리 계산
                            distance = ((wrist["x"] - nose["x"])**2 + (wrist["y"] - nose["y"])**2)**0.5
                            if distance < 0.1:  # 임계값 조정 필요
                                safety_analysis["risk_factors"].append("Hand near face area")
                                safety_analysis["risk_score"] += 1
                                safety_analysis["recommendations"].append("Monitor for breathing obstruction")
                
                # 어깨 기울기로 옆으로 누운 자세 감지
                left_shoulder = next((l for l in landmarks if l["name"] == "left_shoulder"), None)
                right_shoulder = next((l for l in landmarks if l["name"] == "right_shoulder"), None)
                
                if left_shoulder and right_shoulder and left_shoulder["visibility"] > 0.5 and right_shoulder["visibility"] > 0.5:
                    shoulder_tilt = abs(left_shoulder["y"] - right_shoulder["y"])
                    if shoulder_tilt > 0.15:  # 임계값
                        safety_analysis["risk_factors"].append("Side sleeping position detected")
                        safety_analysis["risk_score"] += 1
                        safety_analysis["recommendations"].append("Back sleeping is safest for infants")
        
        # 3. 위험도 레벨 결정
        if safety_analysis["risk_score"] >= 3:
            safety_analysis["risk_level"] = "high"
        elif safety_analysis["risk_score"] >= 1:
            safety_analysis["risk_level"] = "medium"
        else:
            safety_analysis["risk_level"] = "low"
            safety_analysis["risk_factors"] = ["No immediate safety concerns detected"]
            safety_analysis["recommendations"] = ["Continue normal monitoring"]
        
        return safety_analysis
    
    def process_baby_image(self, image: Image.Image) -> Dict[str, Any]:
        """아기 이미지 전체 ML 분석 파이프라인"""
        
        print(f"🤖 ML 이미지 분석 시작: {image.size}")
        
        result = {
            "timestamp": datetime.now().isoformat(),
            "image_size": image.size,
            "ml_available": self.ml_available,
            "models_used": []
        }
        
        if not self.ml_available:
            result["message"] = "ML models not available - using basic analysis"
            return result
        
        try:
            # 1. 객체 탐지
            if self.yolo_model:
                print("🔍 YOLO 객체 탐지 수행")
                object_detection = self.detect_objects(image)
                result["object_detection"] = object_detection
                result["models_used"].append("YOLOv8")
            else:
                object_detection = {"model_available": False}
            
            # 2. 자세 분석
            if self.pose_model:
                print("🦴 Pose 자세 분석 수행")
                pose_analysis = self.analyze_pose(image)
                result["pose_analysis"] = pose_analysis
                result["models_used"].append("MediaPipe Pose")
            else:
                pose_analysis = {"model_available": False}
            
            # 3. 안전성 종합 분석
            safety_analysis = self.analyze_baby_safety(object_detection, pose_analysis)
            result["safety_analysis"] = safety_analysis
            
            # 4. 최종 결과 요약
            result["summary"] = {
                "baby_detected": object_detection.get("person_detected", False),
                "confidence": object_detection.get("person_confidence", 0.0),
                "pose_detected": pose_analysis.get("pose_detected", False),
                "risk_level": safety_analysis["risk_level"],
                "risk_score": safety_analysis["risk_score"],
                "primary_concerns": safety_analysis["risk_factors"][:3]  # 주요 우려사항 3개
            }
            
            print(f"✅ ML 분석 완료: 위험도={safety_analysis['risk_level']}, 점수={safety_analysis['risk_score']}")
            
            return result
            
        except Exception as e:
            print(f"❌ ML 이미지 분석 오류: {e}")
            result["error"] = str(e)
            result["message"] = "ML analysis failed"
            return result
    
    def get_model_status(self) -> Dict[str, Any]:
        """ML 모델 상태 정보"""
        return {
            "ml_available": self.ml_available,
            "models": self.model_info,
            "yolo_loaded": self.yolo_model is not None,
            "pose_loaded": self.pose_model is not None,
            "status": "ready" if self.ml_available else "models_not_loaded",
            "capabilities": {
                "object_detection": self.yolo_model is not None,
                "pose_estimation": self.pose_model is not None,
                "safety_analysis": self.ml_available,
                "baby_monitoring": self.ml_available
            }
        }
    
    def cleanup_models(self):
        """모델 메모리 정리"""
        try:
            if self.yolo_model:
                del self.yolo_model
                self.yolo_model = None
                
            if self.pose_model:
                self.pose_model.close()
                self.pose_model = None
                
            self.ml_available = False
            print("🗑️ ML 모델 메모리 정리 완료")
            
        except Exception as e:
            print(f"❌ 모델 정리 실패: {e}")

# 전역 ML 프로세서 인스턴스
ml_processor = MLProcessor()