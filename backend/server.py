import os
import sys
import subprocess
import shutil
import json
import cv2
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROGRESS_FILE = "progress.json"

def write_progress(percent: int, stage: str):
    try:
        with open(PROGRESS_FILE, "w") as f:
            json.dump({"percent": percent, "stage": stage}, f)
    except Exception:
        pass

class CalibrationData(BaseModel):
    points: List[List[float]]
    dst_points: List[List[float]]

class YouTubeRequest(BaseModel):
    url: str
    start: str
    end: str

@app.get("/video")
def get_video():
    video_path = "final_broadcast.mp4"
    if not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail="Video not generated yet.")
    return FileResponse(video_path, media_type="video/mp4")

@app.get("/first-frame")
def get_first_frame():
    frame_path = "first_frame.jpg"
    if not os.path.exists(frame_path):
        raise HTTPException(status_code=404, detail="First frame not extracted.")
    return FileResponse(frame_path, media_type="image/jpeg")

@app.get("/progress")
def get_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"percent": 0, "stage": "Initializing..."}

@app.post("/upload-mp4")
def upload_mp4(file: UploadFile = File(...)):
    if not file.filename.lower().endswith('.mp4'):
        raise HTTPException(status_code=400, detail="Only MP4 files are allowed.")
        
    file_path = "uploaded_video.mp4"
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    cap = cv2.VideoCapture(file_path)
    ret, frame = cap.read()
    cap.release()
    
    if not ret:
        raise HTTPException(status_code=500, detail="Could not read video frames.")
        
    cv2.imwrite("first_frame.jpg", frame)
    return {"message": "Frame extracted", "frame_url": "http://localhost:8000/first-frame"}

@app.post("/youtube-extract")
def youtube_extract(req: YouTubeRequest):
    write_progress(5, "Downloading video in 1080p...")
    file_path = "uploaded_video.mp4"
    
    if os.path.exists(file_path):
        os.remove(file_path)

    ytdlp_bin = "/opt/homebrew/bin/yt-dlp" if os.path.exists("/opt/homebrew/bin/yt-dlp") else "yt-dlp"
    
    cmd = [
        ytdlp_bin,
        "--download-sections", f"*{req.start}-{req.end}",
        "-f", "bestvideo[height>=1080]+bestaudio/best[height>=1080]",
        "--merge-output-format", "mp4",
        "-o", file_path,
        "--force-overwrites",
        req.url
    ]
    
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except subprocess.CalledProcessError:
        raise HTTPException(status_code=500, detail="Failed to download YouTube video. Ensure the URL is public and the timestamps are valid.")

    write_progress(12, "Getting first frame...")
    cap = cv2.VideoCapture(file_path)
    ret, frame = cap.read()
    cap.release()
    
    if not ret:
        raise HTTPException(status_code=500, detail="Failed to extract frame from downloaded video.")
        
    cv2.imwrite("first_frame.jpg", frame)
    return {"message": "Frame extracted", "frame_url": "http://localhost:8000/first-frame"}

@app.post("/start-broadcast")
def start_broadcast(data: CalibrationData):
    if len(data.points) != 4:
        raise HTTPException(status_code=400, detail="Exactly 4 points are required.")
        
    with open("calibration_points.json", "w") as f:
        json.dump({"src": data.points, "dst": data.dst_points}, f)
        
    write_progress(15, "Beginning video analysis...")
    
    env = os.environ.copy()
    env["INPUT_VIDEO"] = "uploaded_video.mp4"
    env["SKIP_YOUTUBE"] = "1"
    
    py_bin = sys.executable
    cmd = f'"{py_bin}" -u main.py | "{py_bin}" tts.py'
    
    try:
        subprocess.run(cmd, shell=True, env=env, check=True)
        write_progress(100, "Broadcast ready!")
        return {"message": "Broadcast generated successfully!", "video_url": "http://localhost:8000/video"}
    except subprocess.CalledProcessError:
        write_progress(0, "Processing failed")
        raise HTTPException(status_code=500, detail="AI processing pipeline failed.")

if __name__ == "__main__":
    import uvicorn
    import logging

    class EndpointFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return record.getMessage().find("/progress") == -1

    logging.getLogger("uvicorn.access").addFilter(EndpointFilter())
    uvicorn.run(app, host="0.0.0.0", port=8000)