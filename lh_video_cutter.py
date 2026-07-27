import asyncio
import json
import os
import shutil
import subprocess

from aiohttp import web

import folder_paths
import server


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


def _resolve_video(path):
    value = str(path or "").strip()
    if not value:
        return ""
    candidates = [value]
    if not os.path.isabs(value):
        candidates.extend((
            os.path.join(folder_paths.get_input_directory(), value),
            os.path.join(folder_paths.get_output_directory(), value),
        ))
    for candidate in candidates:
        resolved = os.path.realpath(candidate)
        if os.path.isfile(resolved) and os.path.splitext(resolved)[1].lower() in VIDEO_EXTENSIONS:
            return resolved
    return ""


def _safe_name(value, fallback):
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(value or "").strip())
    return cleaned.strip("_") or fallback


def _render_waveform(source, width):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg was not found")
    command = [
        ffmpeg, "-v", "error", "-i", source,
        "-filter_complex", f"aformat=channel_layouts=mono,showwavespic=s={width}x120:colors=0x75d9ff",
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        return b""
    return result.stdout


def _project_path(directory, filename, create_directory=False):
    value = str(directory or "").strip()
    if value and os.path.isabs(value):
        target_directory = os.path.realpath(value)
    else:
        target_directory = os.path.realpath(os.path.join(
            folder_paths.get_output_directory(),
            value or "LH_Video_Cutter_Projects",
        ))
    if create_directory:
        os.makedirs(target_directory, exist_ok=True)
    name = _safe_name(os.path.splitext(str(filename or ""))[0], "LH_Video_Cutter_Task")
    return os.path.join(target_directory, f"{name}.json")


class LHVideoCutter:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video_path": ("STRING", {"default": ""}),
                "cuts_data": ("STRING", {"default": "{}", "multiline": True}),
                "output_subfolder": ("STRING", {"default": "LH_Video_Cutter"}),
                "filename_prefix": ("STRING", {"default": "segment"}),
                "cut_mode": (["accurate_h264", "fast_stream_copy"], {"default": "accurate_h264"}),
                "save_segments": ("BOOLEAN", {"default": False}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("segment_paths", "output_directory")
    FUNCTION = "cut_video"
    CATEGORY = "SQR/Video"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, video_path="", cuts_data="{}", output_subfolder="", filename_prefix="", cut_mode="accurate_h264", save_segments=False):
        if save_segments:
            return float("nan")
        return f"{video_path}:{cuts_data}:{output_subfolder}:{filename_prefix}:{cut_mode}"

    def cut_video(self, video_path, cuts_data, output_subfolder, filename_prefix, cut_mode, save_segments):
        source = _resolve_video(video_path)
        if not source:
            raise ValueError("LH Video Cutter: video file was not found")

        try:
            data = json.loads(cuts_data or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("LH Video Cutter: invalid cut data") from exc
        fps = max(0.001, float(data.get("fps") or 0.0))
        total_frames = max(1, int(data.get("total_frames") or 0))
        cuts = set()
        for value in data.get("cuts", []):
            try:
                frame = int(value)
            except (TypeError, ValueError):
                continue
            if 0 < frame < total_frames:
                cuts.add(frame)
        cuts = sorted(cuts)
        boundaries = [0, *cuts, total_frames]
        ranges = [(boundaries[index], boundaries[index + 1]) for index in range(len(boundaries) - 1)]
        if not save_segments:
            return (json.dumps(ranges, ensure_ascii=False), "")
        segment_meta = data.get("segment_meta") if isinstance(data.get("segment_meta"), dict) else {}
        selected_segment = data.get("save_selected_segment")
        export_audio = data.get("export_audio") is True
        audio_format = str(data.get("audio_format") or "mp3").lower()
        if audio_format not in ("mp3", "wav"):
            audio_format = "mp3"
        audio_bitrate = str(data.get("audio_bitrate") or "192k").lower()
        if audio_bitrate not in ("96k", "128k", "192k", "256k", "320k"):
            audio_bitrate = "192k"
        if type(selected_segment) is int and 0 <= selected_segment < len(ranges):
            selected_indices = [selected_segment]
        else:
            selected_indices = [
                index for index, (start_frame, _) in enumerate(ranges)
                if not isinstance(segment_meta.get(str(start_frame)), dict)
                or segment_meta[str(start_frame)].get("enabled", True) is not False
            ]
        if not selected_indices:
            raise ValueError("LH Video Cutter: no segments are selected for export")

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("LH Video Cutter: ffmpeg was not found")

        requested_output = str(output_subfolder or "LH_Video_Cutter").strip()
        if os.path.isabs(requested_output):
            output_dir = os.path.realpath(requested_output)
        else:
            subfolder = requested_output.replace("\\", "/").strip("/")
            output_root = os.path.realpath(folder_paths.get_output_directory())
            output_dir = os.path.realpath(os.path.join(output_root, subfolder))
            if os.path.commonpath((output_root, output_dir)) != output_root:
                raise ValueError("LH Video Cutter: output folder must stay inside ComfyUI output")
        os.makedirs(output_dir, exist_ok=True)
        prefix = _safe_name(filename_prefix, "segment")
        paths = []

        for range_index in selected_indices:
            start_frame, end_frame = ranges[range_index]
            segment_number = range_index + 1
            meta = segment_meta.get(str(start_frame)) if isinstance(segment_meta.get(str(start_frame)), dict) else {}
            segment_name = _safe_name(meta.get("name"), "")
            start_time = start_frame / fps
            duration = (end_frame - start_frame) / fps
            frame_count = end_frame - start_frame
            name_suffix = f"_{segment_name}" if segment_name else ""
            output_stem = f"{prefix}_{segment_number:03d}{name_suffix}_{start_frame}-{end_frame}"
            output_path = os.path.join(output_dir, f"{output_stem}.mp4")
            if cut_mode == "fast_stream_copy":
                command = [ffmpeg, "-y", "-ss", f"{start_time:.9f}", "-i", source, "-t", f"{duration:.9f}", "-map", "0:v:0", "-map", "0:a?"]
                command.extend(("-c", "copy", "-avoid_negative_ts", "make_zero"))
            else:
                command = [ffmpeg, "-y", "-i", source, "-ss", f"{start_time:.9f}", "-t", f"{duration:.9f}", "-map", "0:v:0", "-map", "0:a?"]
                command.extend(("-frames:v", str(frame_count), "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"))
            command.append(output_path)
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode != 0:
                raise RuntimeError(f"LH Video Cutter: segment {segment_number} failed: {result.stderr[-500:]}")
            paths.append(output_path)
            if export_audio:
                audio_path = os.path.join(output_dir, f"{output_stem}.{audio_format}")
                audio_command = [
                    ffmpeg, "-y", "-i", source, "-ss", f"{start_time:.9f}", "-t", f"{duration:.9f}",
                    "-map", "0:a:0", "-vn",
                ]
                if audio_format == "wav":
                    audio_command.extend(("-c:a", "pcm_s16le"))
                else:
                    audio_command.extend(("-c:a", "libmp3lame", "-b:a", audio_bitrate))
                audio_command.append(audio_path)
                audio_result = subprocess.run(audio_command, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if audio_result.returncode != 0:
                    raise RuntimeError(f"LH Video Cutter: audio segment {segment_number} failed; check that the source has an audio track: {audio_result.stderr[-500:]}")
                paths.append(audio_path)

        return ("\n".join(paths), output_dir)


@server.PromptServer.instance.routes.get("/sqr/video_file")
async def lh_video_cutter_file(request):
    path = _resolve_video(request.rel_url.query.get("file", ""))
    if not path:
        return web.Response(status=404)
    return web.FileResponse(path, headers={"Cache-Control": "no-store"})


@server.PromptServer.instance.routes.get("/sqr/audio_waveform")
async def lh_video_cutter_waveform(request):
    path = _resolve_video(request.rel_url.query.get("file", ""))
    if not path:
        return web.Response(status=404)
    try:
        width = max(320, min(4000, int(request.rel_url.query.get("width", "2400"))))
        image = await asyncio.to_thread(_render_waveform, path, width)
        if not image:
            return web.Response(status=204)
        return web.Response(body=image, content_type="image/png", headers={"Cache-Control": "no-store"})
    except (TypeError, ValueError):
        return web.Response(status=400)
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=500)


@server.PromptServer.instance.routes.post("/sqr/video_cutter/save_project")
async def lh_video_cutter_save_project(request):
    try:
        payload = await request.json()
        source = _resolve_video(payload.get("video_path"))
        if not source:
            raise ValueError("Target video was not found at its current path.")
        cuts_data = payload.get("cuts_data")
        if not isinstance(cuts_data, dict):
            raise ValueError("Invalid cutter task data.")
        ui_state = payload.get("ui_state") if isinstance(payload.get("ui_state"), dict) else {}
        project = {
            "schema": "lh_video_cutter_project",
            "version": 1,
            "video_path": os.path.normpath(source),
            "cuts_data": cuts_data,
            "output_subfolder": str(payload.get("output_subfolder") or "LH_Video_Cutter"),
            "filename_prefix": str(payload.get("filename_prefix") or "segment"),
            "cut_mode": str(payload.get("cut_mode") or "accurate_h264"),
            "ui_state": {
                "playhead": max(0, int(ui_state.get("playhead") or 0)),
                "selected_segment": max(0, int(ui_state.get("selected_segment") or 0)),
                "timeline_zoom": max(1, min(20, int(ui_state.get("timeline_zoom") or 1))),
                "view_start": max(0, int(ui_state.get("view_start") or 0)),
            },
        }
        path = _project_path(payload.get("directory"), payload.get("filename"), True)
        temporary_path = f"{path}.tmp"
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(project, handle, ensure_ascii=False, indent=2)
        os.replace(temporary_path, path)
        return web.json_response({"ok": True, "path": os.path.normpath(path)})
    except (OSError, TypeError, ValueError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)


@server.PromptServer.instance.routes.post("/sqr/video_cutter/load_project")
async def lh_video_cutter_load_project(request):
    try:
        payload = await request.json()
        path = _project_path(payload.get("directory"), payload.get("filename"))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Task file was not found: {path}")
        with open(path, "r", encoding="utf-8") as handle:
            project = json.load(handle)
        if not isinstance(project, dict) or project.get("schema") != "lh_video_cutter_project":
            raise ValueError("This is not an LH Video Cutter task file.")
        saved_source = _resolve_video(project.get("video_path"))
        if not saved_source:
            raise FileNotFoundError("The target video is no longer present at the saved path.")
        current_value = str(payload.get("current_video_path") or "").strip()
        if current_value:
            current_source = _resolve_video(current_value)
            if not current_source or os.path.normcase(current_source) != os.path.normcase(saved_source):
                raise ValueError("The current target video path does not match the saved task.")
        if not isinstance(project.get("cuts_data"), dict):
            raise ValueError("The task file contains invalid cut data.")
        return web.json_response({"ok": True, "path": os.path.normpath(path), "project": project})
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)


NODE_CLASS_MAPPINGS = {"LHVideoCutter": LHVideoCutter}
NODE_DISPLAY_NAME_MAPPINGS = {"LHVideoCutter": "LH Video Cutter"}
