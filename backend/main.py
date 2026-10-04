import os
import subprocess
import cv2
import numpy as np
import torch
import json
import random
import sys
from ultralytics import YOLO

YOUTUBE_URL = "https://www.youtube.com/watch?v=YXKVkd39ZSw"
CLIP_START = "00:05"
CLIP_END = "00:55"
LOCAL_CLIP_FILE = "hockey_clip_226_420.mp4"

START_TIME_SECONDS = 0.0 
END_TIME_SECONDS = 50.0 

dst_pts = np.array([
    [302, 129],
    [348, 80],
    [348, 180],
    [387, 116],
], dtype="float32")

RINK_MIN_X = 28
RINK_MAX_X = 412
RINK_MIN_Y = 45
RINK_MAX_Y = 275

CALIBRATION_WINDOW = "Calibration: Click 4 Points"
TEAM_COLORS = {
    "Sharks": (200, 200, 200),
    "Wild": (24, 80, 50),
}
HOCKEY_MODEL_PATH = "HockeyAI_model_weight.mlpackage"
MODEL_IMAGE_SIZE = 1088
PLAYER_DETECTION_CONFIDENCE = 0.60
TEAM_REASSIGN_SECONDS = 3.0
POSSESSION_CONFIRM_SECONDS = 0.25 
POSSESSION_LOST_SECONDS = 1.50

CONTEST_RADIUS_PIXELS = 75     
HIT_RADIUS_PIXELS = 28         
HIT_SPEED_MINIMAP = 1.8 

EVENT_COOLDOWN_SECONDS = 0.5
PUCK_ACQUIRE_FRAMES = 1
PUCK_MAX_JUMP_RINK = 45.0  
PUCK_LOST_FRAMES = 45

WILD_ROSTER = ["Kaprizov", "Zuccarello", "Eriksson Ek", "Faber", "Brodin", "Spurgeon", "Boldy"]
SHARKS_ROSTER = ["Hertl", "Couture", "Eklund", "Ferraro", "Vlasic", "Zetterlund", "Granlund"]

def fetch_youtube_clip(url, start_time, end_time, output_path):
    if os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
        test_cap = cv2.VideoCapture(output_path)
        cached_h = test_cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        test_cap.release()
        if cached_h >= 1080:
            return output_path
        else:
            os.remove(output_path)
    ytdlp_bin = "/opt/homebrew/bin/yt-dlp" if os.path.exists("/opt/homebrew/bin/yt-dlp") else "yt-dlp"
    strategies = [
        ["--cookies-from-browser", "safari"],
        ["--extractor-args", "youtube:player_client=tv,tv_embedded"],
        ["--extractor-args", "youtube:player_client=ios;formats=missing_pot"],
        ["--cookies-from-browser", "chrome"],
    ]
    for strat in strategies:
        cmd = [
            ytdlp_bin, "--download-sections", f"*{start_time}-{end_time}",
            *strat, "-f", "bestvideo[height>=1080]+bestaudio/best[height>=1080]",
            "--merge-output-format", "mp4", "-o", output_path, "--force-overwrites", url
        ]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0 and os.path.exists(output_path):
            return output_path
    raise RuntimeError("Could not download in 1080p automatically.")

def get_rink_zone(map_x, current_zone="Neutral Zone"):
    left_blue_line = 183
    right_blue_line = 257
    buffer = 15  
    if current_zone == "Neutral Zone":
        if map_x < (left_blue_line - buffer): return "Left Zone"
        if map_x > (right_blue_line + buffer): return "Right Zone"
    elif current_zone == "Left Zone":
        if map_x > (left_blue_line + buffer): return "Neutral Zone"
    elif current_zone == "Right Zone":
        if map_x < (right_blue_line - buffer): return "Neutral Zone"
    return current_zone

def get_rink_location(map_x, map_y):
    zone = get_rink_zone(map_x).lower()
    if map_x < 90: return "behind the net in the left zone"
    if map_x > 350: return "behind the net in the right zone"
    if map_y < 85: return f"along the top boards in the {zone}"
    if map_y > 235: return f"along the bottom boards in the {zone}"
    return f"in the {zone}"

def draw_calibration(frame, points):
    display = frame.copy()
    for index, (x, y) in enumerate(points):
        cv2.circle(display, (x, y), 7, (0, 255, 0), -1)
    return display

def click_event(event, x, y, flags, param):
    if event != cv2.EVENT_LBUTTONDOWN: return
    points = param['points']
    if len(points) >= len(dst_pts): return
    points.append([x, y])
    cv2.imshow(CALIBRATION_WINDOW, draw_calibration(param['frame'], points))

def calibrate(frame):
    points = []
    cv2.namedWindow(CALIBRATION_WINDOW)
    cv2.setMouseCallback(CALIBRATION_WINDOW, click_event, param={'frame': frame, 'points': points})
    while True:
        cv2.imshow(CALIBRATION_WINDOW, draw_calibration(frame, points))
        key = cv2.waitKey(20) & 0xFF
        if key in (ord('q'), 27): return None
        if len(points) == len(dst_pts):
            source = np.array(points, dtype="float32")
            return cv2.getPerspectiveTransform(source, dst_pts), source

def recognize_team(frame, x1, y1, x2, y2):
    box_width, box_height = x2 - x1, y2 - y1
    torso = frame[y1 + int(box_height * 0.25):y1 + int(box_height * 0.60), x1 + int(box_width * 0.30):x1 + int(box_width * 0.70)]
    if torso.size == 0: return "Sharks" 
    hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
    green_mask = cv2.inRange(hsv, np.array([30, 25, 20]), np.array([85, 255, 255]))
    if (cv2.countNonZero(green_mask) / (torso.shape[0] * torso.shape[1])) > 0.04: return "Wild"
    return "Sharks"

def set_progress(percent, stage):
    try:
        with open("progress.json", "w") as f:
            json.dump({"percent": int(percent), "stage": stage}, f)
    except Exception:
        pass

def main():
    device = "cpu"
    use_half = False
    
    model = YOLO(HOCKEY_MODEL_PATH)
    class_ids = {str(name).lower(): int(cid) for cid, name in (model.names if isinstance(model.names, dict) else dict(enumerate(model.names))).items()}
    
    player_class_id = class_ids["player"]
    puck_class_id = class_ids["puck"]
    
    if os.getenv("SKIP_YOUTUBE") == "1":
        video_source = os.getenv("INPUT_VIDEO")
        print(f"Using uploaded video: {video_source}")
    else:
        video_source = fetch_youtube_clip(YOUTUBE_URL, CLIP_START, CLIP_END, LOCAL_CLIP_FILE)
    
    cap = cv2.VideoCapture(video_source)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(START_TIME_SECONDS * fps)))
    ret, first_frame = cap.read()
    if not ret: return
        
    minimap_base = cv2.imread('rink.webp')
    points_file = "calibration_points.json"
    if os.path.exists(points_file):
        with open(points_file, "r") as f:
            calib_data = json.load(f)
        global dst_pts
        if isinstance(calib_data, dict):
            source_points = np.array(calib_data["src"], dtype="float32")
            dst_pts = np.array(calib_data["dst"], dtype="float32") 
        else:
            source_points = np.array(calib_data, dtype="float32")
        
        base_matrix = cv2.getPerspectiveTransform(source_points, dst_pts)
    else:
        calibration = calibrate(first_frame)
        cv2.destroyAllWindows()
        if calibration is None: return
        base_matrix, source_points = calibration

    output_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    output_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out_video = cv2.VideoWriter('annotated_video.mp4', fourcc, fps, (output_w, output_h))

    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
    
    # --- ANCHOR FRAME INITIALIZATION ---
    anchor_gray = clahe.apply(cv2.cvtColor(first_frame, cv2.COLOR_BGR2GRAY))
    h, w = anchor_gray.shape
    anchor_mask = np.ones(anchor_gray.shape, dtype=np.uint8) * 255 
    cv2.rectangle(anchor_mask, (0, 0), (w, int(h * 0.12)), 0, -1) 
    cv2.rectangle(anchor_mask, (0, int(h * 0.85)), (w, h), 0, -1) 
    anchor_pts = cv2.goodFeaturesToTrack(anchor_gray, maxCorners=1000, qualityLevel=0.01, minDistance=15, mask=anchor_mask)
    
    cumulative_base_motion = np.eye(3, dtype=np.float32)
    current_frame_homography = np.eye(3, dtype=np.float32)
    # -----------------------------------
    
    player_team_votes, player_names_map, smoothed_skates, player_zones, player_recent_positions, player_map_velocities, ghost_players = {}, {}, {}, {}, {}, {}, {}
    persistent_player_boxes = {}
    player_speed_history = {} 
    
    current_possessor_id, current_possessor_team = None, None
    possession_candidate_id, possession_candidate_frames, possession_missing_frames = None, 0, 0
    possession_confirm_frames = max(1, int(round(POSSESSION_CONFIRM_SECONDS * fps)))
    possession_lost_frames = max(1, int(round(POSSESSION_LOST_SECONDS * fps)))
    
    puck_rink_pos = None
    puck_conf = 0.0
    puck_candidate_rink, puck_candidate_frames, puck_missing_frames = None, 0, 0
    
    kf_rink = cv2.KalmanFilter(4, 2)
    kf_rink.measurementMatrix = np.array([[1, 0, 0, 0], 
                                          [0, 1, 0, 0]], np.float32)
    kf_rink.transitionMatrix = np.array([[1, 0, 1, 0], 
                                         [0, 1, 0, 1], 
                                         [0, 0, 1, 0], 
                                         [0, 0, 0, 1]], np.float32)
    cv2.setIdentity(kf_rink.processNoiseCov, 1e-2) 
    cv2.setIdentity(kf_rink.measurementNoiseCov, 1e-1)
    cv2.setIdentity(kf_rink.errorCovPost, 1.0)
    
    frames_since_last_event = 0
    event_cooldown_frames = int(round(EVENT_COOLDOWN_SECONDS * fps))
    frames_since_last_hit = event_cooldown_frames 
    frames_carrying = 0
    last_pass_y = 150
    pressure_frames, pressure_team, pressure_zone = 0, None, None

    def emit_event(event_type, team, player_id, additional_data=None, force=False):
        nonlocal frames_since_last_event
        if force or frames_since_last_event > event_cooldown_frames:
            current_frame = cap.get(cv2.CAP_PROP_POS_FRAMES)
            video_time_sec = current_frame / fps
            payload = {
                "timestamp": video_time_sec,
                "event": event_type,
                "team": team,
                "player_name": player_names_map.get(player_id, f"Player {player_id}")
            }
            if additional_data: payload.update(additional_data)
            print(json.dumps(payload), flush=True) 
            frames_since_last_event = 0

    total_frames = int((END_TIME_SECONDS - START_TIME_SECONDS) * fps)
    
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret: break

        current_frame = cap.get(cv2.CAP_PROP_POS_FRAMES)
        video_time_sec = current_frame / fps
        
        if video_time_sec > END_TIME_SECONDS:
            break

        if int(current_frame) % 5 == 0:
            frames_processed = current_frame - int(START_TIME_SECONDS * fps)
            progress = min(1.0, max(0.0, frames_processed / total_frames))
            overall_pct = 15 + int(progress * 60)
            set_progress(overall_pct, f"Tracking the game ({video_time_sec:.1f}s / {END_TIME_SECONDS:.1f}s)...")
            
        frames_since_last_event += 1
        frames_since_last_hit += 1 
        current_gray = clahe.apply(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        display_map = minimap_base.copy()

        with torch.inference_mode():
            results = model.track(frame, persist=True, verbose=False, classes=[player_class_id, puck_class_id], conf=0.02, iou=0.45, agnostic_nms=True, imgsz=MODEL_IMAGE_SIZE, device=device, quantize="fp16" if use_half else "fp32", tracker="bytetrack.yaml")

        detected_pucks = []
        if results[0].boxes is not None:
            for box in results[0].boxes:
                if int(box.cls[0]) == puck_class_id and float(box.conf[0]) >= 0.02:
                    detected_pucks.append((*map(int, box.xyxy[0]), float(box.conf[0])))

        current_mask = np.ones(current_gray.shape, dtype=np.uint8) * 255
        cv2.rectangle(current_mask, (0, 0), (w, int(h * 0.12)), 0, -1)
        cv2.rectangle(current_mask, (0, int(h * 0.85)), (w, h), 0, -1)
        
        if results[0].boxes is not None:
            for box in results[0].boxes:
                if int(box.cls[0]) == player_class_id and float(box.conf[0]) >= PLAYER_DETECTION_CONFIDENCE:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    cv2.rectangle(current_mask, (max(0, x1 - 40), max(0, y1 - 40)), (min(w, x2 + 40), min(h, y2 + 40)), 0, -1)

        # --- NEW ANCHOR-BASED MOTION ENGINE ---
        need_reset = False
        if anchor_pts is not None and len(anchor_pts) > 20:
            p1, st, err = cv2.calcOpticalFlowPyrLK(anchor_gray, current_gray, anchor_pts, None, winSize=(31, 31), maxLevel=3)
            good_new = p1[st == 1]
            good_old = anchor_pts[st == 1]

            if len(good_new) > 20:
                matrix_homo, inliers = cv2.findHomography(good_old, good_new, cv2.RANSAC, 3.0)
                if matrix_homo is not None:
                    det = np.linalg.det(matrix_homo[0:2, 0:2])
                    if 0.7 < det < 1.3:
                        current_frame_homography = matrix_homo
                    else:
                        need_reset = True
                else:
                    need_reset = True
            else:
                need_reset = True
        else:
            need_reset = True

        cumulative_motion = current_frame_homography @ cumulative_base_motion
        cumulative_motion /= cumulative_motion[2, 2]

        if need_reset or abs(current_frame_homography[0, 2]) > 150 or abs(current_frame_homography[1, 2]) > 50:
            anchor_gray = current_gray.copy()
            anchor_mask = current_mask.copy()
            anchor_pts = cv2.goodFeaturesToTrack(anchor_gray, maxCorners=1000, qualityLevel=0.01, minDistance=15, mask=anchor_mask)
            cumulative_base_motion = cumulative_motion.copy()
            current_frame_homography = np.eye(3, dtype=np.float32)

        current_to_map = base_matrix @ np.linalg.inv(cumulative_motion)
        current_to_map /= current_to_map[2, 2] 
        map_to_current = np.linalg.inv(current_to_map)
        # --------------------------------------

        live_players_on_map = {}
        active_track_ids = set()

        if results[0].boxes is not None:
            for box in results[0].boxes:
                if int(box.cls[0]) != player_class_id or float(box.conf[0]) < PLAYER_DETECTION_CONFIDENCE: continue
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                track_id = int(box.id[0]) if box.id is not None else None
                
                if track_id is None:
                    continue

                active_track_ids.add(track_id)
                raw_team_guess = recognize_team(frame, x1, y1, x2, y2)
                if track_id not in player_team_votes: player_team_votes[track_id] = []
                player_team_votes[track_id].append(raw_team_guess)
                if len(player_team_votes[track_id]) > 30: player_team_votes[track_id].pop(0)
                team = max(set(player_team_votes[track_id]), key=player_team_votes[track_id].count)
                
                if track_id not in player_names_map: player_names_map[track_id] = random.choice(WILD_ROSTER) if team == "Wild" else random.choice(SHARKS_ROSTER)

                cur_box = np.array([x1, y1, x2, y2], dtype=np.float32)
                if track_id in persistent_player_boxes:
                    persistent_player_boxes[track_id]['coords'] = (
                        0.45 * cur_box + 0.55 * persistent_player_boxes[track_id]['coords']
                    )
                    persistent_player_boxes[track_id]['team'] = team
                    persistent_player_boxes[track_id]['lost_frames'] = 0
                else:
                    persistent_player_boxes[track_id] = {
                        'coords': cur_box,
                        'team': team,
                        'lost_frames': 0
                    }

                raw_skate = np.array([(x1 + x2) / 2, y2], dtype="float32")
                smoothed_skates[track_id] = 0.2 * raw_skate + 0.8 * smoothed_skates[track_id] if track_id in smoothed_skates else raw_skate
                    
                skate_x, skate_y = np.round(smoothed_skates[track_id]).astype(int)
                map_point = cv2.perspectiveTransform(np.array([[[skate_x, skate_y]]], dtype="float32"), current_to_map)
                map_x, map_y = int(map_point[0][0][0]), int(map_point[0][0][1])
                
                history = player_recent_positions.setdefault(track_id, [])
                history.append(np.array([map_x, map_y], dtype=np.float32))
                if len(history) > 5: history.pop(0)
                
                current_vel = (history[-1] - history[0]) / 5.0 if len(history) == 5 else np.zeros(2, dtype=np.float32)
                player_map_velocities[track_id] = current_vel
                
                cur_speed = float(np.linalg.norm(current_vel))
                s_hist = player_speed_history.setdefault(track_id, [])
                s_hist.append(cur_speed)
                if len(s_hist) > 8:
                    s_hist.pop(0)
                
                previous_zone = player_zones.get(track_id, "Neutral Zone")
                current_zone = get_rink_zone(map_x, previous_zone) 
                live_players_on_map[track_id] = {'x': map_x, 'y': map_y, 'team': team, 'zone': current_zone}

                if current_zone != previous_zone:
                    if track_id == current_possessor_id:
                        action = "carries_puck_out_of_zone" if current_zone == "Neutral Zone" else "carries_puck_into_zone"
                        attackers = sum(1 for p in live_players_on_map.values() if p['team'] == team and p['zone'] == current_zone)
                        defenders = sum(1 for p in live_players_on_map.values() if p['team'] != team and p['zone'] == current_zone)
                        emit_event(action, team, track_id, {"from_zone": previous_zone, "to_zone": current_zone, "odd_man_rush": (attackers > defenders and attackers > 1)}, force=True)
                player_zones[track_id] = current_zone
                
                box_color = (0, 255, 255) if track_id == current_possessor_id else TEAM_COLORS[team]
                cv2.circle(display_map, (map_x, map_y), 8, box_color, -1)

        to_delete_boxes = []
        for tid, pbox in persistent_player_boxes.items():
            if tid not in active_track_ids:
                pbox['lost_frames'] += 1
                if pbox['lost_frames'] > 8:
                    to_delete_boxes.append(tid)
                    continue
            
            bx1, by1, bx2, by2 = pbox['coords'].astype(int)
            b_color = (0, 255, 255) if tid == current_possessor_id else TEAM_COLORS[pbox['team']]
            cv2.rectangle(frame, (bx1, by1), (bx2, by2), b_color, 2)
            
        for tid in to_delete_boxes:
            persistent_player_boxes.pop(tid, None)

        for pid in [pid for pid in player_recent_positions.keys() if pid not in active_track_ids]:
            ghost_players.setdefault(pid, {'team': max(set(player_team_votes[pid]), key=player_team_votes[pid].count) if pid in player_team_votes and player_team_votes[pid] else "Sharks", 'x': player_recent_positions[pid][-1][0], 'y': player_recent_positions[pid][-1][1], 'velocity': player_map_velocities.get(pid, np.zeros(2)), 'frames_missing': 0})
            player_recent_positions.pop(pid, None); player_map_velocities.pop(pid, None); player_team_votes.pop(pid, None); player_speed_history.pop(pid, None)

        for pid in list(ghost_players.keys()):
            if pid in active_track_ids: ghost_players.pop(pid, None)
            else:
                ghost_players[pid]['frames_missing'] += 1
                if ghost_players[pid]['frames_missing'] > int(fps * 1.0): ghost_players.pop(pid, None)

        hit_detected = False
        player_ids = list(live_players_on_map.keys())
        
        for i in range(len(player_ids)):
            if hit_detected: break
            for j in range(i + 1, len(player_ids)):
                pid1, pid2 = player_ids[i], player_ids[j]
                p1, p2 = live_players_on_map[pid1], live_players_on_map[pid2]

                if p1['team'] != p2['team']:
                    dist = np.hypot(p1['x'] - p2['x'], p1['y'] - p2['y'])
                    if dist < HIT_RADIUS_PIXELS:
                        hist1 = player_speed_history.get(pid1, [0.0])
                        hist2 = player_speed_history.get(pid2, [0.0])

                        pre_spd1 = max(hist1[:3]) if len(hist1) >= 4 else hist1[-1]
                        pre_spd2 = max(hist2[:3]) if len(hist2) >= 4 else hist2[-1]

                        cur_spd1 = hist1[-1]
                        cur_spd2 = hist2[-1]
                        
                        decel1 = pre_spd1 - cur_spd1
                        decel2 = pre_spd2 - cur_spd2
                        
                        had_momentum = (pre_spd1 > 1.45) or (pre_spd2 > 1.45)
                        impact_impulse = (decel1 > 0.55 and (decel1 / (pre_spd1 + 1e-4)) > 0.35) or \
                                         (decel2 > 0.55 and (decel2 / (pre_spd2 + 1e-4)) > 0.35)
                        
                        if had_momentum and impact_impulse:
                            if frames_since_last_hit > event_cooldown_frames:
                                hitting_id = pid1 if pre_spd1 >= pre_spd2 else pid2
                                hitting_team = p1['team'] if pre_spd1 >= pre_spd2 else p2['team']
                                
                                emit_event("hit", hitting_team, hitting_id, {"location": get_rink_location(p1['x'], p1['y'])}, force=True)
                                frames_since_last_hit, hit_detected = 0, True
                                break

        rink_puck_candidates = []
        if detected_pucks:
            for p in detected_pucks:
                cx, cy = (p[0] + p[2]) / 2.0, (p[1] + p[3]) / 2.0
                screen_pt = np.array([[[cx, cy]]], dtype=np.float32)
                rink_pt = cv2.perspectiveTransform(screen_pt, current_to_map)[0][0]
                if -20 <= rink_pt[0] <= 450 and -20 <= rink_pt[1] <= 320:
                    rink_puck_candidates.append({
                        "rink_pos": rink_pt,
                        "screen_box": p[:4],
                        "conf": p[4]
                    })

        accepted_puck = None
        if rink_puck_candidates:
            if puck_rink_pos is None:
                best_cand = max(rink_puck_candidates, key=lambda c: c["conf"])
                cand_pos = best_cand["rink_pos"]
                
                if puck_candidate_rink is not None and np.linalg.norm(cand_pos - puck_candidate_rink) < PUCK_MAX_JUMP_RINK:
                    puck_candidate_frames += 1
                else:
                    puck_candidate_frames = 1
                puck_candidate_rink = cand_pos
                
                if puck_candidate_frames >= PUCK_ACQUIRE_FRAMES:
                    puck_rink_pos = cand_pos
                    puck_conf = float(best_cand["conf"])
                    kf_rink.statePost = np.array([[puck_rink_pos[0]], [puck_rink_pos[1]], [0.0], [0.0]], dtype=np.float32)
                    accepted_puck = best_cand["screen_box"]
                    puck_candidate_rink, puck_candidate_frames = None, 0
            else:
                kf_rink.statePost[2] *= 0.96 
                kf_rink.statePost[3] *= 0.96 
                predicted_state = kf_rink.predict()
                pred_rink_center = np.array([predicted_state[0, 0], predicted_state[1, 0]], dtype=np.float32)
                
                best_cand = min(rink_puck_candidates, key=lambda c: np.linalg.norm(c["rink_pos"] - pred_rink_center))
                cand_pos = best_cand["rink_pos"]
                
                if np.linalg.norm(cand_pos - pred_rink_center) <= PUCK_MAX_JUMP_RINK:
                    measured = np.array([[cand_pos[0]], [cand_pos[1]]], dtype=np.float32)
                    corrected_state = kf_rink.correct(measured)
                    puck_rink_pos = np.array([corrected_state[0, 0], corrected_state[1, 0]], dtype=np.float32)
                    puck_conf = float(best_cand["conf"])
                    puck_missing_frames, accepted_puck = 0, best_cand["screen_box"]
                else:
                    puck_rink_pos = pred_rink_center
                    puck_conf = max(0.01, puck_conf * 0.95)
                    puck_missing_frames += 1
        else:
            puck_candidate_rink, puck_candidate_frames = None, 0
            if puck_rink_pos is not None:
                kf_rink.statePost[2] *= 0.96 
                kf_rink.statePost[3] *= 0.96 
                predicted_state = kf_rink.predict()
                puck_rink_pos = np.array([predicted_state[0, 0], predicted_state[1, 0]], dtype=np.float32)
                puck_conf = max(0.01, puck_conf * 0.95)
                puck_missing_frames += 1

        if puck_rink_pos is not None:
            if puck_rink_pos[0] < RINK_MIN_X:
                puck_rink_pos[0] = RINK_MIN_X
                kf_rink.statePost[0] = RINK_MIN_X
                kf_rink.statePost[2] = abs(kf_rink.statePost[2]) * 0.75
            elif puck_rink_pos[0] > RINK_MAX_X:
                puck_rink_pos[0] = RINK_MAX_X
                kf_rink.statePost[0] = RINK_MAX_X
                kf_rink.statePost[2] = -abs(kf_rink.statePost[2]) * 0.75

            if puck_rink_pos[1] < RINK_MIN_Y:
                puck_rink_pos[1] = RINK_MIN_Y
                kf_rink.statePost[1] = RINK_MIN_Y
                kf_rink.statePost[3] = abs(kf_rink.statePost[3]) * 0.75
            elif puck_rink_pos[1] > RINK_MAX_Y:
                puck_rink_pos[1] = RINK_MAX_Y
                kf_rink.statePost[1] = RINK_MAX_Y
                kf_rink.statePost[3] = -abs(kf_rink.statePost[3]) * 0.75

        if puck_rink_pos is not None and puck_missing_frames >= PUCK_LOST_FRAMES: 
            puck_rink_pos, puck_missing_frames = None, 0

        if puck_rink_pos is not None:
            p_x, p_y = int(puck_rink_pos[0]), int(puck_rink_pos[1])
            
            cv2.circle(display_map, (p_x, p_y), 5, (0, 255, 255), -1)

            rink_pt_arr = np.array([[[p_x, p_y]]], dtype=np.float32)
            screen_pt = cv2.perspectiveTransform(rink_pt_arr, map_to_current)[0][0]
            screen_x, screen_y = int(screen_pt[0]), int(screen_pt[1])

            if 0 <= screen_x < w and 0 <= screen_y < h:
                cv2.rectangle(frame, (screen_x - 7, screen_y - 7), (screen_x + 7, screen_y + 7), (0, 255, 255), 2)
                
                label = f"{puck_conf:.2f}"
                (text_w, text_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
                
                pill_top = max(0, screen_y - 10 - text_h - 2)
                pill_bottom = max(text_h + 2, screen_y - 8)
                cv2.rectangle(frame, (screen_x - 7, pill_top), (screen_x - 7 + text_w + 4, pill_bottom), (0, 0, 0), -1)
                cv2.putText(frame, label, (screen_x - 5, pill_bottom - 2), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 255), 1, cv2.LINE_AA)

            puck_zone, puck_location = get_rink_zone(p_x), get_rink_location(p_x, p_y)
            puck_vel = np.array([kf_rink.statePost[2, 0], kf_rink.statePost[3, 0]], dtype=np.float32)
            puck_speed = np.linalg.norm(puck_vel)

            closest_dist, closest_id = float('inf'), None
            
            for pid, pdata in live_players_on_map.items():
                dist = np.hypot(p_x - pdata['x'], p_y - pdata['y'])
                
                if dist > 38.0:
                    continue
                    
                p_vel = player_map_velocities.get(pid, np.zeros(2, dtype=np.float32))
                p_speed = np.linalg.norm(p_vel)
                
                rel_vel = puck_vel - p_vel
                rel_speed = np.linalg.norm(rel_vel)
                
                is_vector_match = False
                if puck_speed < 2.0:
                    is_vector_match = True
                else:
                    if rel_speed < 4.2:
                        is_vector_match = True
                    elif p_speed > 0.6:
                        cos_sim = np.dot(puck_vel, p_vel) / (puck_speed * p_speed)
                        if cos_sim > 0.65 and rel_speed < 5.5:
                            is_vector_match = True
                
                if is_vector_match and dist < closest_dist:
                    closest_dist, closest_id = dist, pid
            
            if closest_id is not None and closest_dist < 38: 
                new_team, possession_missing_frames = live_players_on_map[closest_id]['team'], 0
                if closest_id == current_possessor_id:
                    possession_candidate_id, possession_candidate_frames, frames_carrying, last_pass_y = None, 0, frames_carrying + 1, p_y
                    nearest_enemy = min([np.hypot(p_x - p['x'], p_y - p['y']) for pid, p in live_players_on_map.items() if p['team'] != new_team] + [999])
                    if frames_carrying % int(fps * 3.0) == 0:
                        emit_event("carrying_puck", current_possessor_team, current_possessor_id, {"location": puck_location, "pressure": "heavy pressure" if nearest_enemy < 40 else "isolated breakaway" if nearest_enemy > 120 else "moderate pressure"})
                else:
                    frames_carrying = 0
                    if possession_candidate_id != closest_id: possession_candidate_id, possession_candidate_frames = closest_id, 0
                    possession_candidate_frames += 1
                    if possession_candidate_frames >= possession_confirm_frames:
                        action = "completes_pass" if current_possessor_team == new_team else "recovers_loose_puck" if current_possessor_team is None else "forces_turnover"
                        event_data = {"zone": puck_zone, "location": puck_location}
                        if action == "completes_pass" and abs(p_y - last_pass_y) > 70: event_data["cross_ice_pass"] = True
                        emit_event(action, new_team, closest_id, event_data, force=True)
                        current_possessor_id, current_possessor_team, possession_candidate_id, possession_candidate_frames = closest_id, new_team, None, 0
            else:
                if current_possessor_id is not None:
                    possession_missing_frames += 1
                    if possession_missing_frames >= possession_lost_frames:
                        emit_event("dump_in_or_corner_play" if (p_x < 60 or p_x > 340) else "puck_knocked_loose", current_possessor_team, current_possessor_id, {"location": puck_location}, force=True)
                        current_possessor_id, current_possessor_team, possession_missing_frames = None, None, 0
        
        out_video.write(frame)

        preview_frame = cv2.resize(frame, (960, 540)) 
        cv2.imshow('Feed', preview_frame)
        cv2.imshow('Minimap', display_map)
        
        if cv2.waitKey(1) & 0xFF == ord('q'): 
            break
        
    set_progress(75, "Video rendered, commentary in progress...") 
    cap.release()
    out_video.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()