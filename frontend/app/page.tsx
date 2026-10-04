"use client";

import { useEffect, useRef, useState } from "react";
import * as React from "react";
import {
  Maximize,
  Pause,
  Play,
  Upload,
  RotateCcw,
  CheckCircle2,
  Tv2,
  Layers,
  Radio,
  Sparkles,
  FileVideo,
  MonitorPlay,
  HardDrive,
  ArrowRight
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Slider } from "@/components/ui/slider";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import Image from "next/image";

const API_ENDPOINT = "http://localhost:8000/upload-mp4";
const YT_ENDPOINT = "http://localhost:8000/youtube-extract";
const START_ENDPOINT = "http://localhost:8000/start-broadcast";
const PROGRESS_ENDPOINT = "http://localhost:8000/progress";

interface Point {
  x: number;
  y: number;
  realX: number;
  realY: number;
}

const getImgSrc = (img: unknown): string => {
  if (!img) return "";
  if (typeof img === "string") return img;
  if (typeof img === "object" && "src" in img && typeof (img as { src: unknown }).src === "string") {
    return (img as { src: string }).src;
  }
  return "";
};

const parseTimeToSeconds = (timeStr: string) => {
  const parts = timeStr.split(":");
  if (parts.length !== 2) return null;
  const m = parseInt(parts[0], 10);
  const s = parseInt(parts[1], 10);
  if (isNaN(m) || isNaN(s)) return null;
  return m * 60 + s;
};

const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className = "", ...props }, ref) => (
    <input
      ref={ref}
      className={`flex h-9 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-1 text-sm text-zinc-100 shadow-sm transition-colors placeholder:text-zinc-600 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-sky-500 disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
      {...props}
    />
  )
);
Input.displayName = "Input";

export default function StormpuckStudio() {
  const [hasMounted, setHasMounted] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [currentTimecode, setCurrentTimecode] = useState("00:00.0");
  const [totalTimecode, setTotalTimecode] = useState("00:00.0");

  const [pipelineProgress, setPipelineProgress] = useState(0);
  const [pipelineStage, setPipelineStage] = useState<string>("Ready to ingest tape");
  const [isProcessing, setIsProcessing] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const [ingestMethod, setIngestMethod] = useState<"upload" | "youtube">("youtube");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [ytUrl, setYtUrl] = useState("");
  const [ytStart, setYtStart] = useState("");
  const [ytEnd, setYtEnd] = useState("");

  // --- 2-STEP CALIBRATION STATE ---
  const [calibrationStep, setCalibrationStep] = useState<"minimap" | "video" | null>(null);
  const [calibrationFrameUrl, setCalibrationFrameUrl] = useState<string | null>(null);
  const [calibrationPoints, setCalibrationPoints] = useState<Point[]>([]); // Points on video
  const [dstPoints, setDstPoints] = useState<Point[]>([]); // Points on minimap
  const calibrationImgRef = useRef<HTMLImageElement | null>(null);

  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const pollingIntervalRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    setHasMounted(true);
    return () => {
      if (pollingIntervalRef.current) clearInterval(pollingIntervalRef.current);
    };
  }, []);

  const formatSeconds = (sec: number) => {
    const mins = Math.floor(sec / 60);
    const secs = (sec % 60).toFixed(1);
    return `${String(mins).padStart(2, "0")}:${secs.padStart(4, "0")}`;
  };

  const startProgressPolling = () => {
    if (pollingIntervalRef.current) clearInterval(pollingIntervalRef.current);
    pollingIntervalRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${PROGRESS_ENDPOINT}?t=${Date.now()}`, { cache: "no-store" });
        if (res.ok) {
          const data = await res.json();
          if (data.percent !== undefined && data.percent > 0) setPipelineProgress(data.percent);
          if (data.stage) setPipelineStage(data.stage);
        }
      } catch { }
    }, 400);
  };

  const stopProgressPolling = () => {
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current);
      pollingIntervalRef.current = null;
    }
  };

  const handleFileSelection = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null;
    if (!file) return;

    if (!file.name.toLowerCase().endsWith(".mp4") && file.type !== "video/mp4") {
      setErrorMessage("Unsupported container. Please provide a standard .mp4 video file.");
      setSelectedFile(null);
      return;
    }

    setSelectedFile(file);
    setErrorMessage(null);
    setPipelineStage(`Loaded: ${file.name}`);
  };

  const handleUploadAndExtract = async () => {
    if (!selectedFile || isProcessing) return;

    setIsProcessing(true);
    setPipelineStage("Ingesting footage and capturing keyframe...");
    setPipelineProgress(10);
    setErrorMessage(null);

    const formData = new FormData();
    formData.append("file", selectedFile);

    try {
      const res = await fetch(API_ENDPOINT, { method: "POST", body: formData });
      if (!res.ok) throw new Error("Upload failed. Verify backend service is running on :8000.");
      const data = await res.json();

      setCalibrationFrameUrl(`${data.frame_url}?t=${Date.now()}`);
      setCalibrationPoints([]);
      setDstPoints([]);
      setCalibrationStep("minimap"); // Start at step 1
      setIsProcessing(false);
      setPipelineStage("Awaiting 2-step homography calibration");
    } catch (err: any) {
      setIsProcessing(false);
      setErrorMessage(err.message || "Failed to communicate with ingestion service.");
    }
  };

  const handleYouTubeExtract = async () => {
    setErrorMessage(null);

    if (!ytUrl || !ytStart || !ytEnd) {
      setErrorMessage("Please fill in the URL, Start Time, and End Time.");
      return;
    }

    const startSec = parseTimeToSeconds(ytStart);
    const endSec = parseTimeToSeconds(ytEnd);

    if (startSec === null || endSec === null) {
      setErrorMessage("Invalid time format. Please use MM:SS (e.g. 05:12).");
      return;
    }
    if (endSec <= startSec) {
      setErrorMessage("End time must be greater than start time.");
      return;
    }
    if (endSec - startSec > 300) {
      setErrorMessage("Video segment cannot exceed 5 minutes (300 seconds).");
      return;
    }

    setIsProcessing(true);
    setPipelineStage("Downloading YouTube segment...");
    setPipelineProgress(5);
    startProgressPolling();

    try {
      const res = await fetch(YT_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: ytUrl, start: ytStart, end: ytEnd }),
      });

      stopProgressPolling();

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Failed to fetch YouTube video.");
      }

      const data = await res.json();

      setCalibrationFrameUrl(`${data.frame_url}?t=${Date.now()}`);
      setCalibrationPoints([]);
      setDstPoints([]);
      setCalibrationStep("minimap"); // Start at step 1
      setIsProcessing(false);
      setPipelineStage("Awaiting 2-step homography calibration");
    } catch (err: any) {
      stopProgressPolling();
      setIsProcessing(false);
      setErrorMessage(err.message || "Failed to process YouTube link.");
    }
  };

  // Click handler that handles BOTH minimap and video clicks dynamically
  const handleFrameClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!calibrationImgRef.current || !calibrationStep) return;
    
    const isMinimap = calibrationStep === "minimap";
    const currentPoints = isMinimap ? dstPoints : calibrationPoints;

    if (currentPoints.length >= 4) return;

    const rect = calibrationImgRef.current.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;

    const scaleX = calibrationImgRef.current.naturalWidth / rect.width;
    const scaleY = calibrationImgRef.current.naturalHeight / rect.height;

    const realX = Math.round(clickX * scaleX);
    const realY = Math.round(clickY * scaleY);

    const pctX = (clickX / rect.width) * 100;
    const pctY = (clickY / rect.height) * 100;

    const newPoint = { x: pctX, y: pctY, realX, realY };

    if (isMinimap) {
      setDstPoints([...dstPoints, newPoint]);
    } else {
      setCalibrationPoints([...calibrationPoints, newPoint]);
    }
  };

  const handleConfirmCalibration = async () => {
    if (calibrationPoints.length !== 4 || dstPoints.length !== 4) return;

    setIsProcessing(true);
    setCalibrationStep(null);
    setCalibrationFrameUrl(null);
    setPipelineStage("Initializing Neural Engine & commentary booth...");
    startProgressPolling();

    const srcPayload = calibrationPoints.map((p) => [p.realX, p.realY]);
    const dstPayload = dstPoints.map((p) => [p.realX, p.realY]);

    try {
      const res = await fetch(START_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // Sending BOTH source video points AND destination minimap points
        body: JSON.stringify({ points: srcPayload, dst_points: dstPayload }), 
      });

      stopProgressPolling();
      setIsProcessing(false);

      if (!res.ok) throw new Error("Broadcast synthesis failed");
      const data = await res.json();

      setPipelineProgress(100);
      setPipelineStage("Broadcast generated successfully");
      setVideoUrl(`${data.video_url}?t=${Date.now()}`);
      setIsPlaying(true);
    } catch (err: any) {
      stopProgressPolling();
      setIsProcessing(false);
      setErrorMessage(err.message || "Pipeline execution failed.");
    }
  };

  const togglePlay = () => {
    if (!videoRef.current) return;
    if (isPlaying) {
      videoRef.current.pause();
      setIsPlaying(false);
    } else {
      videoRef.current.play();
      setIsPlaying(true);
    }
  };

  const handleTimeUpdate = () => {
    if (!videoRef.current) return;
    const current = videoRef.current.currentTime;
    const total = videoRef.current.duration;
    if (total > 0) {
      setProgress((current / total) * 100);
      setCurrentTimecode(formatSeconds(current));
      setTotalTimecode(formatSeconds(total));
    }
  };

  const handleSliderSeek = (val: number[]) => {
    if (!videoRef.current) return;
    const total = videoRef.current.duration;
    if (total > 0) {
      videoRef.current.currentTime = (val[0] / 100) * total;
      setProgress(val[0]);
    }
  };

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 selection:bg-zinc-800">
      <header className="border-b border-zinc-800 bg-zinc-950 sticky top-0 z-40">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-2">
              <Image
                src="/stormpucks.png"
                alt="StormPucks"
                width={140}
                height={40}
                className="h-12 w-auto object-contain"
                priority
              />
              <span className="text-sm font-bold text-zinc-100">StormPucks</span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <div className="grid grid-cols-1 gap-8 lg:grid-cols-12">
          <div className="lg:col-span-8 flex flex-col gap-4 border-b-6 border-white">
            <Card className="overflow-hidden border-zinc-800/80 bg-zinc-900/40 p-0 shadow-2xl">
              <div className="relative aspect-video w-full overflow-hidden bg-black">
                {videoUrl ? (
                  <video
                    ref={videoRef}
                    src={videoUrl}
                    className="h-full w-full object-contain"
                    playsInline
                    autoPlay
                    onTimeUpdate={handleTimeUpdate}
                    onEnded={() => setIsPlaying(false)}
                    onClick={togglePlay}
                  />
                ) : (
                  <div className="absolute inset-0 flex flex-col items-center justify-center bg-zinc-950 p-6 text-center">
                    <h3 className="text-base font-semibold text-zinc-200">NO FEED</h3>
                    <p className="mt-1 max-w-sm text-xs text-zinc-500">
                      upload something
                    </p>
                  </div>
                )}

                {videoUrl && !isPlaying && (
                  <div className="absolute inset-0 flex items-center justify-center bg-black/40 backdrop-blur-[2px] transition-all">
                    <Button
                      size="icon"
                      onClick={togglePlay}
                      className="h-16 w-16 rounded-full bg-zinc-100/90 text-zinc-950 shadow-2xl hover:scale-105 hover:bg-zinc-100"
                    >
                      <Play className="ml-1 h-7 w-7 fill-current" />
                    </Button>
                  </div>
                )}

                <div className="absolute inset-x-0 bottom-0 z-20 bg-gradient-to-t from-black/90 via-black/50 to-transparent p-4">
                  {hasMounted && (
                    <Slider
                      aria-label="Seek timecode"
                      value={[progress]}
                      max={100}
                      step={0.1}
                      onValueChange={handleSliderSeek}
                      className="mb-3 cursor-pointer"
                    />
                  )}

                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={togglePlay}
                        disabled={!videoUrl}
                        className="h-8 w-8 text-zinc-200 hover:text-white hover:bg-white/10"
                      >
                        {isPlaying ? <Pause className="h-4 w-4 fill-current" /> : <Play className="h-4 w-4 fill-current" />}
                      </Button>

                      <div className="font-mono text-xs text-zinc-400">
                        <span className="text-zinc-100">{currentTimecode}</span>
                        <span className="mx-1 text-zinc-600">/</span>
                        <span>{totalTimecode}</span>
                      </div>
                    </div>

                    <div className="flex items-center gap-2">
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => videoRef.current?.requestFullscreen()}
                        disabled={!videoUrl}
                        className="h-8 w-8 text-zinc-400 hover:text-white hover:bg-white/10"
                      >
                        <Maximize className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                </div>
              </div>
            </Card>
          </div>

          <div className="lg:col-span-4 flex flex-col gap-6">
            <Card className="border-zinc-800 bg-zinc-900/50">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Footage Input</CardTitle>
                <CardDescription className="text-xs">
                  import YouTube videos to add commentary! max 5 minutes. or upload a local mp4 clip. 1080p 60fps recommended.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">

                <div className="flex gap-2 p-1 bg-zinc-950 rounded-lg border border-zinc-800">
                  <button
                    onClick={() => setIngestMethod("youtube")}
                    className={`flex-1 py-1.5 text-xs font-medium rounded-md transition-colors flex items-center justify-center gap-1.5 ${ingestMethod === "youtube" ? "bg-zinc-800 text-zinc-100" : "text-zinc-500 hover:text-zinc-300"
                      }`}
                  >
                    <MonitorPlay className="h-3.5 w-3.5" /> YouTube
                  </button>
                  <button
                    onClick={() => setIngestMethod("upload")}
                    className={`flex-1 py-1.5 text-xs font-medium rounded-md transition-colors flex items-center justify-center gap-1.5 ${ingestMethod === "upload" ? "bg-zinc-800 text-zinc-100" : "text-zinc-500 hover:text-zinc-300"
                      }`}
                  >
                    <HardDrive className="h-3.5 w-3.5" /> Local File
                  </button>
                </div>

                {ingestMethod === "youtube" && (
                  <div className="space-y-3 animate-in fade-in slide-in-from-left-2 duration-300">
                    <div className="space-y-1">
                      <label className="text-xs text-zinc-500 font-medium ml-1">Video URL</label>
                      <Input
                        placeholder="..."
                        value={ytUrl}
                        onChange={(e) => setYtUrl(e.target.value)}
                        disabled={isProcessing}
                      />
                    </div>
                    <div className="flex gap-3">
                      <div className="space-y-1 flex-1">
                        <label className="text-xs text-zinc-500 font-medium ml-1">Start (MM:SS)</label>
                        <Input
                          placeholder="00:00"
                          value={ytStart}
                          onChange={(e) => setYtStart(e.target.value)}
                          disabled={isProcessing}
                        />
                      </div>
                      <div className="space-y-1 flex-1">
                        <label className="text-xs text-zinc-500 font-medium ml-1">End (MM:SS)</label>
                        <Input
                          placeholder="01:30"
                          value={ytEnd}
                          onChange={(e) => setYtEnd(e.target.value)}
                          disabled={isProcessing}
                        />
                      </div>
                    </div>
                    <Button
                      onClick={handleYouTubeExtract}
                      disabled={isProcessing}
                      className="w-full gap-2 text-xs font-semibold mt-1"
                    >
                      <Upload className="h-3.5 w-3.5" />
                      Download and Start
                    </Button>
                  </div>
                )}

                {ingestMethod === "upload" && (
                  <div className="space-y-4 animate-in fade-in slide-in-from-right-2 duration-300">
                    <div
                      role="button"
                      tabIndex={0}
                      onClick={() => !isProcessing && fileInputRef.current?.click()}
                      className={`flex flex-col items-center justify-center rounded-xl border border-dashed border-zinc-800 bg-zinc-950/60 p-6 text-center transition ${isProcessing ? "opacity-50 cursor-not-allowed" : "hover:border-zinc-700 hover:bg-zinc-900/50 cursor-pointer"}`}
                    >
                      <input
                        ref={fileInputRef}
                        type="file"
                        accept="video/mp4,.mp4"
                        onChange={handleFileSelection}
                        className="hidden"
                        disabled={isProcessing}
                      />
                      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-zinc-900 border border-zinc-800 text-zinc-400 mb-3">
                        <FileVideo className="h-5 w-5 text-sky-400" />
                      </div>
                      <p className="text-xs font-medium text-zinc-200">
                        {selectedFile ? selectedFile.name : "Select MP4 clip"}
                      </p>
                      <p className="text-[11px] text-zinc-500 mt-1">1080p 60fps recommended</p>
                    </div>

                    <Button
                      onClick={handleUploadAndExtract}
                      disabled={!selectedFile || isProcessing}
                      className="w-full gap-2 text-xs font-semibold"
                    >
                      <Upload className="h-3.5 w-3.5" />
                      Upload and Start
                    </Button>
                  </div>
                )}

                {errorMessage && (
                  <div className="rounded-lg border border-red-500/20 bg-red-500/10 p-3 text-xs text-red-400 animate-in fade-in duration-200">
                    {errorMessage}
                  </div>
                )}

              </CardContent>
            </Card>

            <Card className="border-zinc-800 bg-zinc-900/50">
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-base">Status</CardTitle>
                  <Badge variant={pipelineProgress === 100 ? "success" : isProcessing ? "warning" : "secondary"}>
                    {pipelineProgress === 100 ? "Complete" : isProcessing ? "Running" : "Idle"}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="space-y-1.5">
                  <div className="flex justify-between text-xs font-mono text-zinc-400">
                    <span>Progress</span>
                    <span>{pipelineProgress}%</span>
                  </div>
                  <Progress value={pipelineProgress} />
                </div>
                <p className="text-xs text-zinc-400 font-mono flex items-center gap-2">
                  <Sparkles className="h-3.5 w-3.5 text-sky-400 shrink-0" />
                  <span className="truncate">{pipelineStage}</span>
                </p>
              </CardContent>
            </Card>
          </div>
        </div>

        {/* --- 2-STEP CALIBRATION MODAL --- */}
        {calibrationStep && calibrationFrameUrl && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm animate-in fade-in duration-200">
            <Card className="max-w-4xl border-zinc-800 bg-zinc-950 p-6 shadow-2xl w-full">
              
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h3 className="text-base font-semibold text-zinc-100 flex items-center gap-2">
                    <Layers className="h-4 w-4 text-sky-400" />
                    {calibrationStep === "minimap" ? "Step 1: Map the Minimap Anchors" : "Step 2: Map the Video Anchors"}
                  </h3>
                  <p className="text-xs text-zinc-400 mt-1">
                    {calibrationStep === "minimap" 
                      ? `Click 4 reference points on this tactical minimap (e.g., faceoff dots, line intersections). You will match these exact points on the video next. (Points: ${dstPoints.length}/4)`
                      : `Click the exact same 4 reference points on the video frame in the SAME order. (Points: ${calibrationPoints.length}/4)`}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => calibrationStep === "minimap" ? setDstPoints([]) : setCalibrationPoints([])}
                  className="gap-1.5 text-xs border-zinc-800 hover:bg-zinc-900"
                >
                  <RotateCcw className="h-3 w-3" /> Reset {calibrationStep === "minimap" ? "Minimap" : "Video"}
                </Button>
              </div>

              <div
                className="relative cursor-crosshair overflow-hidden rounded-lg border border-zinc-800 bg-black select-none"
                onClick={handleFrameClick}
              >
                {/* Dynamically show either /rink.webp or the extracted video frame */}
                <img
                  ref={calibrationImgRef}
                  key={calibrationStep} 
                  src={calibrationStep === "minimap" ? "/rink.webp" : calibrationFrameUrl}
                  alt="Calibration Reference"
                  className="w-full object-contain pointer-events-none"
                  onDragStart={(e) => e.preventDefault()}
                />

                {/* Render numbered pin markers based on current step */}
                {(calibrationStep === "minimap" ? dstPoints : calibrationPoints).map((pt, i) => (
                  <div
                    key={i}
                    className="absolute flex h-6 w-6 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-sky-500 font-mono text-xs font-bold text-zinc-950 shadow-lg ring-4 ring-sky-500/20"
                    style={{ left: `${pt.x}%`, top: `${pt.y}%` }}
                  >
                    {i + 1}
                  </div>
                ))}
              </div>

              <div className="mt-4 flex items-center justify-between pt-2">
                <span className="text-xs font-mono text-zinc-500">
                  {calibrationStep === "minimap"
                    ? dstPoints.length === 4 ? "✓ Minimap anchors set" : `Select ${4 - dstPoints.length} minimap points`
                    : calibrationPoints.length === 4 ? "✓ Video anchors set" : `Select ${4 - calibrationPoints.length} video points`}
                </span>
                
                <div className="flex gap-2">
                  {calibrationStep === "minimap" ? (
                    <>
                      <Button variant="ghost" size="sm" onClick={() => {
                        setCalibrationStep(null);
                        setIsProcessing(false);
                        setPipelineStage("Calibration cancelled");
                      }}>
                        Cancel
                      </Button>
                      <Button
                        size="sm"
                        disabled={dstPoints.length !== 4}
                        onClick={() => setCalibrationStep("video")}
                        className="gap-2 bg-zinc-100 text-zinc-950 font-semibold hover:bg-zinc-300"
                      >
                        Next Step <ArrowRight className="h-4 w-4" />
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button variant="ghost" size="sm" onClick={() => setCalibrationStep("minimap")}>
                        Back
                      </Button>
                      <Button
                        size="sm"
                        disabled={calibrationPoints.length !== 4}
                        onClick={handleConfirmCalibration}
                        className="gap-2 bg-sky-500 text-zinc-950 font-semibold hover:bg-sky-400"
                      >
                        <CheckCircle2 className="h-4 w-4" /> Start
                      </Button>
                    </>
                  )}
                </div>
              </div>
            </Card>
          </div>
        )}
      </main>
    </div>
  );
}