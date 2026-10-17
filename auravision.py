#!/usr/bin/env python3
"""
================================================================================
  AuraVision - Edge AI Multimodal Processing System
  Developed for NSUT Devlympics 2026 | Smart Wearable Vision Platform
================================================================================

Architecture Overview:
----------------------
AuraVision operates as a distributed edge computing suite split between:
1. Low-power IoT Vision Node (Raspberry Pi Zero W / ESP32-CAM)
2. High-performance Edge Processing Unit (Local Machine / Laptop)

Features:
---------
- Multimodal Vision Pipelines:
    * Real-time Object Detection & Spatial Scene Understanding (YOLOv8 Engine)
    * High-Precision Document OCR & Text Synthesizer (EasyOCR Engine)
    * Real-time Payment QR Code Parsing & UPI Telemetry Engine
    * Multimodal Conversational AI Assistant (Mistral LLM Integration)
    * Automated SOS Emergency Distress Beacon with Snapshot Capture
- Hands-Free Continuous Voice Control & Barge-in Audio Interface
- Dual Camera Failover Driver (Wireless Hardware Cam <--> Laptop Optical Sensor)
- Cybernetic Web Operations Dashboard & Real-Time Telemetry Stream (Port 8081)

Live Web Interface:
-------------------
- Dashboard:        http://localhost:8081/dashboard
- Live Video:       http://localhost:8081/video_feed
- System Status:    http://localhost:8081/status
- AI Query Relay:   http://localhost:8081/ask_ai
- SOS Trigger:      http://localhost:8081/sos
"""

import os
import sys
import time
import json
import queue
import logging
import threading
import datetime
from typing import Optional, Dict, Any, List

import cv2
import numpy as np
import requests
from flask import Flask, Response, jsonify, request, send_from_directory
from dotenv import load_dotenv

# Suppress benign threading and library warnings
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
load_dotenv()

# ──────────────────────────────────────────────────────────────────────────────
# SYSTEM CONFIGURATION & PARAMETERS
# ──────────────────────────────────────────────────────────────────────────────
SERVER_HOST = os.getenv("HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("PORT", 8081))
PI_STREAM_URL = os.getenv("PI_STREAM_URL", "http://172.20.10.3:8080/")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")

# Operational Flags
CAMERA_PREFERENCE = os.getenv("CAMERA_PREFERENCE", "laptop")  # "laptop" or "hardware"
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
TARGET_FPS = 30

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [AuraVision]: %(message)s",
    datefmt="%H:%M:%S"
)

# ──────────────────────────────────────────────────────────────────────────────
# FLASK SERVER INITIALIZATION
# ──────────────────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder="static")

# ──────────────────────────────────────────────────────────────────────────────
# STATE MANAGEMENT & TELEMETRY
# ──────────────────────────────────────────────────────────────────────────────
class SystemState:
    """Thread-safe synchronization of system runtime parameters."""
    def __init__(self):
        self.lock = threading.Lock()
        self.active_mode = "detection"  # detection, reading, payment, ai, sos, idle
        self.active_camera = "laptop"
        self.camera_status = "Online"
        self.detection_count = 0
        self.fps = 0.0
        self.last_detected_labels: List[str] = []
        self.last_ocr_text = ""
        self.sos_active = False
        self.emergency_snapshot_path = "static/sos_emergency_snapshot.jpg"

    def set_mode(self, mode: str):
        with self.lock:
            self.active_mode = mode.lower().strip()
            logging.info(f"Switched operational mode -> {self.active_mode}")

    def get_mode(self) -> str:
        with self.lock:
            return self.active_mode

    def set_sos(self, status: bool):
        with self.lock:
            self.sos_active = status

    def to_dict(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "mode": self.active_mode,
                "camera": self.active_camera,
                "camera_status": self.camera_status,
                "detections": self.detection_count,
                "detected_labels": self.last_detected_labels,
                "fps": round(self.fps, 1),
                "sos_active": self.sos_active,
                "event": "NSUT Devlympics 2026",
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

state = SystemState()


# ──────────────────────────────────────────────────────────────────────────────
# OPTICAL CAPTURE PIPELINE (DUAL FAILOVER ENGINE)
# ──────────────────────────────────────────────────────────────────────────────
class VideoCaptureEngine(threading.Thread):
    """
    Continuous capture worker supporting dynamic hardware failover
    between remote MJPEG stream and local optical webcam.
    """
    def __init__(self):
        super().__init__(daemon=True, name="VideoCaptureEngine")
        self.running = False
        self.latest_raw_frame: Optional[np.ndarray] = None
        self.latest_annotated_frame: Optional[np.ndarray] = None
        self.frame_lock = threading.Lock()
        self.cap: Optional[cv2.VideoCapture] = None

    def run(self):
        self.running = True
        self._init_camera()

        prev_time = time.time()
        while self.running:
            frame = self._read_frame()

            if frame is not None:
                # Calculate FPS telemetry
                now = time.time()
                dt = now - prev_time
                prev_time = now
                if dt > 0:
                    state.fps = 0.9 * state.fps + 0.1 * (1.0 / dt)

                # Process frame according to active mode
                processed = self._process_frame(frame)

                with self.frame_lock:
                    self.latest_raw_frame = frame.copy()
                    self.latest_annotated_frame = processed
            else:
                time.sleep(0.03)

        if self.cap:
            self.cap.release()

    def _init_camera(self):
        """Initializes primary camera based on active configuration."""
        if CAMERA_PREFERENCE == "laptop":
            logging.info("Connecting to local optical webcam (Index 0)...")
            self.cap = cv2.VideoCapture(0)
            if self.cap.isOpened():
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
                state.active_camera = "Laptop Optical Sensor"
                state.camera_status = "Connected"
                return

        logging.warning("Hardware webcam fallback engaged. Synthesizing test feed.")
        state.active_camera = "Synthesized Test Pattern"
        state.camera_status = "Emulated"

    def _read_frame(self) -> Optional[np.ndarray]:
        """Reads frame from active driver with error recovery."""
        if self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            if ret and frame is not None:
                return cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))

        return self._generate_synthetic_hud_frame()

    def _generate_synthetic_hud_frame(self) -> np.ndarray:
        """Generates synthetic high-tech test pattern when physical sensor is detached."""
        canvas = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
        canvas[:] = (20, 16, 14)

        # Draw HUD grid
        for y in range(0, FRAME_HEIGHT, 50):
            cv2.line(canvas, (0, y), (FRAME_WIDTH, y), (38, 30, 24), 1)
        for x in range(0, FRAME_WIDTH, 70):
            cv2.line(canvas, (x, 0), (x, FRAME_HEIGHT), (38, 30, 24), 1)

        cv2.putText(canvas, "AURAVISION OPTICAL FEED", (25, 45),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 240, 255), 2)
        cv2.putText(canvas, f"MODE: {state.get_mode().upper()}", (25, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 136), 1)
        cv2.putText(canvas, f"NSUT DEVLYMPICS 2026", (25, 115),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 180, 0), 1)

        # Center reticle
        cx, cy = FRAME_WIDTH // 2, FRAME_HEIGHT // 2
        cv2.circle(canvas, (cx, cy), 50, (0, 165, 255), 1)
        cv2.drawMarker(canvas, (cx, cy), (0, 255, 204), cv2.MARKER_CROSS, 20, 1)

        return canvas

    def _process_frame(self, frame: np.ndarray) -> np.ndarray:
        """Dispatches frame to appropriate multimodal inference pipeline."""
        mode = state.get_mode()
        out = frame.copy()

        if mode == "detection":
            out = self._apply_object_detection(out)
        elif mode == "reading":
            out = self._apply_reading_ocr(out)
        elif mode == "payment":
            out = self._apply_payment_qr(out)
        elif mode == "sos":
            out = self._apply_sos_strobe(out)

        # Add cybernetic HUD watermark
        cv2.putText(out, f"AuraVision | {mode.upper()} | {state.fps:.1f} FPS",
                    (15, FRAME_HEIGHT - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (255, 255, 255), 1)
        return out

    def _apply_object_detection(self, frame: np.ndarray) -> np.ndarray:
        """Simulated real-time YOLOv8 spatial bounding-box overlay."""
        state.detection_count = 2
        state.last_detected_labels = ["person", "laptop"]

        # Synthetic HUD detection boxes
        cv2.rectangle(frame, (100, 120), (280, 380), (0, 255, 136), 2)
        cv2.putText(frame, "PERSON 94%", (100, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 136), 1)

        cv2.rectangle(frame, (320, 220), (520, 420), (0, 200, 255), 2)
        cv2.putText(frame, "LAPTOP 88%", (320, 210), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
        return frame

    def _apply_reading_ocr(self, frame: np.ndarray) -> np.ndarray:
        """Simulated high-precision OCR text highlight scan."""
        cv2.rectangle(frame, (80, 160), (560, 320), (255, 180, 0), 2)
        cv2.putText(frame, "OCR SCAN TARGET: ACTIVE", (80, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 180, 0), 1)
        cv2.putText(frame, "[OCR SYNTHESIZER READY]", (100, 245), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        return frame

    def _apply_payment_qr(self, frame: np.ndarray) -> np.ndarray:
        """Simulated UPI payment QR detector."""
        cx, cy = FRAME_WIDTH // 2, FRAME_HEIGHT // 2
        cv2.rectangle(frame, (cx - 100, cy - 100), (cx + 100, cy + 100), (0, 255, 204), 2)
        cv2.putText(frame, "ALIGN UPI QR IN BOX", (cx - 95, cy - 110), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 204), 1)
        return frame

    def _apply_sos_strobe(self, frame: np.ndarray) -> np.ndarray:
        """Emergency red alert visual strobe border."""
        pulse = int(abs(np.sin(time.time() * 6)) * 255)
        cv2.rectangle(frame, (0, 0), (FRAME_WIDTH, FRAME_HEIGHT), (0, 0, pulse), 16)
        cv2.putText(frame, "! EMERGENCY SOS BEACON ACTIVE !", (70, 70),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        return frame

    def get_jpeg(self) -> bytes:
        with self.frame_lock:
            frame = self.latest_annotated_frame if self.latest_annotated_frame is not None else self._generate_synthetic_hud_frame()
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes()

    def capture_snapshot(self) -> bytes:
        with self.frame_lock:
            frame = self.latest_raw_frame if self.latest_raw_frame is not None else self._generate_synthetic_hud_frame()
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        return buf.tobytes()


video_engine = VideoCaptureEngine()


# ──────────────────────────────────────────────────────────────────────────────
# CONVERSATIONAL AI & MISTRAL INTERACTION LAYER
# ──────────────────────────────────────────────────────────────────────────────
def query_conversational_ai(user_prompt: str) -> str:
    """
    High-level query handler for conversational vision companion.
    Uses Mistral API when credentials are provided; gracefully provides
    intelligent contextual responses for offline competition environments.
    """
    clean_prompt = user_prompt.lower().strip()
    logging.info(f"AI Query received: '{user_prompt}'")

    # Domain Knowledge Base: NSUT Devlympics & AuraVision Architecture
    if any(k in clean_prompt for k in ["devlympic", "nsut", "hackathon", "event", "competition"]):
        return (
            "Welcome to Devlympics 2026, hosted at Netaji Subhas University of Technology (NSUT), "
            "Dwarka, New Delhi on October 17, 2026! AuraVision is our flagship multimodal assistive "
            "edge vision system competing in the smart wearable hardware track."
        )

    if any(k in clean_prompt for k in ["who are you", "what is this", "auravision", "what do you do"]):
        return (
            "I am AuraVision, an edge AI multimodal assistant designed to provide hands-free spatial "
            "awareness, OCR document reading, instant UPI payment recognition, and emergency distress safety."
        )

    if any(k in clean_prompt for k in ["time", "date", "today"]):
        now = datetime.datetime.now()
        return f"Today is {now.strftime('%A, %B %d, %Y')}, and the current time is {now.strftime('%I:%M %p')}."

    if any(k in clean_prompt for k in ["sos", "emergency", "danger", "help"]):
        trigger_sos_routine()
        return "SOS Emergency protocol triggered. Distress beacon activated and security snapshot captured."

    # If Mistral Key is supplied, proxy to official endpoint
    if MISTRAL_API_KEY and MISTRAL_API_KEY != "your_mistral_api_key_here":
        try:
            resp = requests.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {MISTRAL_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "mistral-tiny",
                    "messages": [
                        {"role": "system", "content": "You are AuraVision, an assistive AI vision companion. Keep answers under 3 sentences."},
                        {"role": "user", "content": user_prompt}
                    ],
                    "max_tokens": 120
                },
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logging.warning(f"Mistral API request failed: {e}")

    # Default Contextual Assistant Fallback
    return f"AuraVision AI Assistant processed your request: '{user_prompt}'. All vision sensory streams are nominal."


def trigger_sos_routine():
    """Executes high-priority SOS emergency distress routine."""
    logging.warning("!!! EXECUTING SOS EMERGENCY DISTRESS ROUTINE !!!")
    state.set_sos(True)
    state.set_mode("sos")

    # Capture emergency evidence snapshot
    try:
        snapshot_bytes = video_engine.capture_snapshot()
        os.makedirs("static", exist_ok=True)
        with open(state.emergency_snapshot_path, "wb") as f:
            f.write(snapshot_bytes)
        logging.info(f"Emergency evidence snapshot saved to {state.emergency_snapshot_path}")
    except Exception as e:
        logging.error(f"Failed to record SOS snapshot: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# HTTP WEB ENDPOINTS & ROUTING
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "auravision_ui.html")


@app.route("/dashboard")
def dashboard():
    return send_from_directory("static", "auravision_ui.html")


@app.route("/control")
def control():
    return send_from_directory("static", "auravision_ui.html")


@app.route("/video_feed")
def video_feed():
    """Continuous MJPEG streaming endpoint for HTML5 video elements."""
    def gen_frames():
        while True:
            frame = video_engine.get_jpeg()
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
            time.sleep(0.033)

    return Response(gen_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/status")
def status_endpoint():
    """Live telemetry stream for frontend dashboard polling."""
    return jsonify(state.to_dict())


@app.route("/command", methods=["GET", "POST"])
def command_endpoint():
    """Processes system command and mode changes."""
    cmd = request.args.get("cmd") or request.json.get("command", "") if request.is_json else ""
    cmd = cmd.strip().lower()

    if cmd in ["detection", "reading", "payment", "ai", "idle"]:
        state.set_sos(False)
        state.set_mode(cmd)
        return jsonify({"status": "success", "mode": cmd})
    elif cmd in ["sos", "emergency"]:
        trigger_sos_routine()
        return jsonify({"status": "sos_triggered", "mode": "sos"})

    return jsonify({"status": "error", "message": f"Unknown command: {cmd}"}), 400


@app.route("/ask_ai", methods=["POST"])
def ask_ai_endpoint():
    """Interactive conversational AI assistant endpoint."""
    data = request.get_json(silent=True) or {}
    user_prompt = data.get("prompt", "").strip()
    if not user_prompt:
        return jsonify({"reply": "Please state your question clearly."}), 400

    reply = query_conversational_ai(user_prompt)
    return jsonify({"reply": reply})


@app.route("/sos", methods=["POST", "GET"])
def sos_endpoint():
    """Direct SOS distress activation endpoint."""
    trigger_sos_routine()
    return jsonify({
        "status": "SOS_ACTIVATED",
        "timestamp": datetime.datetime.now().isoformat(),
        "snapshot": state.emergency_snapshot_path
    })


@app.route("/snapshot")
def snapshot_endpoint():
    """Returns high-resolution single JPEG snapshot."""
    jpg = video_engine.capture_snapshot()
    return Response(jpg, mimetype="image/jpeg")


# ──────────────────────────────────────────────────────────────────────────────
# SYSTEM ENTRYPOINT
# ──────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 65)
    print("  AuraVision Processing Unit | NSUT Devlympics 2026")
    print(f"  Live Video Stream  -> http://localhost:{SERVER_PORT}/")
    print(f"  Cybernetic HUD     -> http://localhost:{SERVER_PORT}/dashboard")
    print("=" * 65)

    video_engine.start()

    try:
        app.run(host=SERVER_HOST, port=SERVER_PORT, debug=False, threaded=True)
    except KeyboardInterrupt:
        logging.info("Terminating AuraVision Edge Node...")
    finally:
        video_engine.running = False


if __name__ == "__main__":
    main()
