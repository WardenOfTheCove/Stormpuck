import os
import sys
import warnings
import logging

warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"
logging.getLogger().setLevel(logging.ERROR)
logging.getLogger("google").setLevel(logging.ERROR)

import argparse
import subprocess
import json
import threading
import queue
from pathlib import Path

from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from google import genai
from google.genai import errors, types

load_dotenv(Path(__file__).with_name(".env"), override=True) 

START_TIME_SEC = 0.0

def set_progress(percent, stage):
    try:
        with open("progress.json", "w") as f:
            json.dump({"percent": int(percent), "stage": stage}, f)
    except Exception:
        pass

def main() -> None:
    parser = argparse.ArgumentParser(description="Live sports commentary with Gemini and ElevenLabs.") 
    parser.add_argument("--pbp-voice-id", default=os.getenv("ELEVENLABS_VOICE_ID"), help="Play-by-Play Voice ID") 
    parser.add_argument("--color-voice-id", default=os.getenv("ELEVENLABS_COLOR_VOICE_ID"), help="Color Commentator Voice ID") 
    args = parser.parse_args()

    elevenlabs_key = os.getenv("ELEVENLABS_API_KEY") 
    gemini_key = os.getenv("GEMINI_API_KEY") 
    
    if not elevenlabs_key or not gemini_key:
        raise SystemExit("Missing API keys in .env file.")
        
    if not args.color_voice_id:
        raise SystemExit("Missing ELEVENLABS_COLOR_VOICE_ID in .env file.")

    gemini = genai.Client(api_key=gemini_key)
    elevenlabs = ElevenLabs(api_key=elevenlabs_key)

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        system_instruction=(
            "You are the director of an elite NHL broadcast booth. You control two distinct personas:\n\n"
            "1. 'PbP' (John Shorthouse): The play-by-play voice. Calls 70% of the action. High energy on hits/turnovers, smooth narration on puck movement.\n"
            "2. 'Color' (Ray Ferraro): The color commentator. Speaks 30% of the time. Adds quick tactical analysis.\n\n"
            
            "CRITICAL ZONE & ACCURACY RULES:\n"
            "1. ACCURATE ICE GEOGRAPHY: Understand the rink layout:\n"
            "   - 'Left Zone' and 'Right Zone' are the END ZONES (Offensive / Defensive ends). They are NEVER the neutral zone!\n"
            "   - If the play is in the 'Left Zone', 'Right Zone', or 'behind the net', refer to it as the 'offensive zone', 'defensive end', or 'deep in the zone'.\n"
            "   - DO NOT say 'neutral zone' unless the data explicitly says 'Neutral Zone'!\n"
            "2. FOCUS ON LITERAL PLAY-BY-PLAY: Narrate the players and the puck. Who passed to whom? Who took the hit? Avoid vague generalities.\n"
            "3. CONVERSATIONAL FRAGMENTS: Use natural pauses, dashes (-), 'And...', 'Well...', or 'Yeah...'. Do not speak in robotic, formal paragraphs.\n"
            "4. PACING: If the play is uneventful, return an empty array [] to let the broadcast breathe.\n\n"
            
            "OUTPUT FORMAT:\n"
            "Output a valid JSON array of objects. Example:\n"
            "[{\"speaker\": \"PbP\", \"text\": \"Eklund works it deep in the offensive zone... slides it to the point.\"}]\n"
            "Example of staying silent: []"
        )
    )

    print("generating commentary\n")

    os.makedirs("audio_clips", exist_ok=True)
    
    event_queue = queue.Queue()
    audio_clip_timeline = []
    
    def audio_worker():
        voice_occupied_until = 0.0
        local_batch = []
        conversation_history = [] 
        
        last_known_location = "in the zone"
        last_known_team = "Unknown"
        
        while True:
            item = event_queue.get()
            if item is None: 
                break
                
            local_batch.append(item)
            raw_ts_sec = float(item["timestamp"])
            
            if raw_ts_sec < voice_occupied_until:
                event_queue.task_done()
                continue
                
            try:
                while True:
                    extra = event_queue.get_nowait()
                    if extra is None:
                        event_queue.put(None) 
                        break
                    local_batch.append(extra)
                    event_queue.task_done()
            except queue.Empty:
                pass
                
            for ev in local_batch:
                if ev.get("team"):
                    last_known_team = ev["team"]
                if ev.get("additional_data") and ev["additional_data"].get("location"):
                    last_known_location = ev["additional_data"]["location"]
                elif ev.get("additional_data") and ev["additional_data"].get("to_zone"):
                    last_known_location = f"in the {ev['additional_data']['to_zone']}"
            
            prompt_lines = [f"CURRENT RINK LOCATION: {last_known_location}. Team in possession: {last_known_team}."]
            prompt_lines.append("RECENT EVENTS:")
            
            for ev in local_batch:
                context = ""
                if ev.get("additional_data"):
                    context = f" - Location/Context: {ev['additional_data']}"
                prompt_lines.append(f"Time {ev['timestamp']:.1f}s: {ev['event']} by {ev['team']} ({ev['player_name']}){context}")
            
            prompt_lines.append("\nBOOTH CONVERSATION HISTORY (DO NOT repeat these):")
            if not conversation_history:
                prompt_lines.append("(None. Starting fresh.)")
            else:
                for hist in conversation_history:
                    prompt_lines.append(f"{hist['speaker']}: {hist['text']}")
                    
            prompt_lines.append("\nDECISION: Output JSON array. (PbP calls the action. Match the exact location provided above).")
                
            prompt_text = "\n".join(prompt_lines)
            
            try:
                response = gemini.models.generate_content(
                    model="gemini-3.5-flash-lite",
                    contents=prompt_text,
                    config=config, 
                )
                
                raw_text = ""
                if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                    for part in response.candidates[0].content.parts:
                        if hasattr(part, 'text') and part.text:
                            raw_text += part.text
                            
                booth_script = json.loads(raw_text.strip())
                
                if not booth_script or len(booth_script) == 0:
                    pass 
                
                for dialogue in booth_script:
                    speaker = dialogue.get("speaker", "PbP")
                    speech_text = dialogue.get("text", "").strip()
                    
                    if not speech_text: continue
                    
                    conversation_history.append({"speaker": speaker, "text": speech_text})
                    if len(conversation_history) > 6: 
                        conversation_history.pop(0)
                    
                    voice_id = args.pbp_voice_id.strip() if speaker == "PbP" else args.color_voice_id.strip()
                    current_line_start_ts = max(raw_ts_sec, voice_occupied_until)
                    
                    minutes = int(current_line_start_ts // 60)
                    seconds = int(current_line_start_ts % 60)
                    ts_prefix = f"[{minutes:02d}:{seconds:02d}] "
                    
                    if speaker == "PbP":
                        print(f"\nPbP {ts_prefix}{speech_text}")
                    else:
                        print(f"\nColour {ts_prefix}{speech_text}")
                    
                    audio = elevenlabs.text_to_speech.convert(
                        voice_id=voice_id, 
                        text="[excited] " + speech_text if speaker == "PbP" else speech_text,
                        model_id="eleven_v4", 
                        output_format="mp3_44100_128", 
                    )
                    
                    clip_path = f"audio_clips/clip_{len(audio_clip_timeline):03d}.mp3"
                    with open(clip_path, "wb") as output_file:
                        for chunk in audio: 
                            if chunk: output_file.write(chunk) 
                    
                    file_size_bytes = os.path.getsize(clip_path)
                    clip_duration_sec = file_size_bytes / 16000.0
                    
                    audio_clip_timeline.append((current_line_start_ts, clip_path))
                    voice_occupied_until = current_line_start_ts + clip_duration_sec + 0.4 
                    
            except Exception as e:
                pass 
                
            local_batch = []
            event_queue.task_done()

    worker = threading.Thread(target=audio_worker, daemon=True)
    worker.start()

    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        
        if not line.startswith("{"):
            continue

        try:
            event_data = json.loads(line)
            if "timestamp" in event_data:
                event_queue.put(event_data)
        except Exception:
            pass

    print("\n\nrendering complete, waiting for commentary...")
    set_progress(82, "Generating commentary...")
    event_queue.put(None)
    worker.join()

    print("\ncommentary complete! combining...")
    
    set_progress(92, "Mixing...")
    annotated_video = "annotated_video.mp4"
    original_video = os.getenv("INPUT_VIDEO", "hockey_clip_226_420.mp4") 
    output_final = "final_broadcast.mp4"
    
    if not os.path.exists(annotated_video):
        print("Error: annotated_video.mp4 not found.")
        return

    cmd = ["ffmpeg", "-y", "-i", annotated_video, "-ss", str(START_TIME_SEC), "-i", original_video]
    
    for _, path in audio_clip_timeline:
        cmd.extend(["-i", path])
        
    filter_parts = []
    mix_inputs = ["[bg_audio]"]
    
    filter_parts.append("[1:a]volume=0.3,aresample=44100,aformat=channel_layouts=stereo[bg_audio]")
    
    for i, (ts, _) in enumerate(audio_clip_timeline):
        delay_sec = max(0.0, ts - START_TIME_SEC)
        delay_ms = int(delay_sec * 1000)
        
        audio_idx = i + 2 
        filter_parts.append(f"[{audio_idx}:a]aresample=44100,aformat=channel_layouts=stereo,highpass=f=250,lowpass=f=4000,acompressor=ratio=4,adelay={delay_ms}|{delay_ms}[a{audio_idx}]")
        mix_inputs.append(f"[a{audio_idx}]")
        
    amix_str = "".join(mix_inputs)
    num_inputs = len(mix_inputs)
    filter_parts.append(f"{amix_str}amix=inputs={num_inputs}:duration=first:dropout_transition=0:normalize=0[aout]")
    
    filter_complex = "; ".join(filter_parts)
    
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "0:v",                 
        "-map", "[aout]",              
        "-c:v", "libx264",             
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",                 
        "-shortest",                  
        output_final
    ])
    
    try:
        if len(audio_clip_timeline) > 0:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"success! saved as: {output_final}")
            subprocess.Popen(["open", output_final])
        else:
            print("no events detected, no final video generated")
    except Exception as e:
        print(f"failed to run ffmpeg: {e}")

if __name__ == "__main__":
    main()