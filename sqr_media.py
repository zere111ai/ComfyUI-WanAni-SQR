"""Media helpers shared by the segmented queue runtime.

This module intentionally has no ComfyUI imports so its ffmpeg and preview
behavior can be tested outside a running ComfyUI installation.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from typing import Any


RunCommand = Callable[..., Any]


def _usable_executable(value: str | None) -> str | None:
    value = str(value or "").strip()
    if not value:
        return None
    if os.path.isdir(value):
        value = os.path.join(
            value,
            "ffmpeg.exe" if os.name == "nt" else "ffmpeg",
        )
    if os.path.isfile(value):
        return os.path.abspath(value)
    resolved = shutil.which(value)
    return os.path.abspath(resolved) if resolved else None


def resolve_ffmpeg_path() -> str | None:
    """Resolve the same ffmpeg installation that VHS is likely to use.

    VideoHelperSuite can run with imageio-ffmpeg or VHS_FORCE_FFMPEG_PATH even
    when ``ffmpeg`` is absent from PATH. Reusing its loaded module first avoids
    the queue incorrectly classifying an audio-bearing source as silent.
    """

    forced = _usable_executable(os.environ.get("VHS_FORCE_FFMPEG_PATH"))
    if forced:
        return forced

    for module_name, module in tuple(sys.modules.items()):
        if module_name == "videohelpersuite.utils" or module_name.endswith(
            ".videohelpersuite.utils"
        ):
            candidate = _usable_executable(getattr(module, "ffmpeg_path", None))
            if candidate:
                return candidate

    system_ffmpeg = _usable_executable("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    for local_name in ("ffmpeg", "ffmpeg.exe"):
        candidate = _usable_executable(os.path.abspath(local_name))
        if candidate:
            return candidate

    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        return _usable_executable(get_ffmpeg_exe())
    except Exception:
        return None


def resolve_ffprobe_path(ffmpeg_path: str | None = None) -> str | None:
    ffmpeg_path = _usable_executable(ffmpeg_path) or resolve_ffmpeg_path()
    if ffmpeg_path:
        sibling = os.path.join(
            os.path.dirname(ffmpeg_path),
            "ffprobe.exe" if os.name == "nt" else "ffprobe",
        )
        sibling = _usable_executable(sibling)
        if sibling:
            return sibling
    return _usable_executable("ffprobe")


def probe_audio_stream(
    path: str | None,
    *,
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
    runner: RunCommand = subprocess.run,
) -> tuple[bool | None, str]:
    """Return ``(has_audio, detail)``.

    ``has_audio`` is ``None`` when the source cannot be inspected reliably.
    Callers should not silently strip an existing audio input in that case.
    """

    if not path or not os.path.isfile(path):
        return False, "source media is missing"

    ffmpeg_path = _usable_executable(ffmpeg_path) or resolve_ffmpeg_path()
    ffprobe_path = _usable_executable(ffprobe_path) or resolve_ffprobe_path(
        ffmpeg_path
    )

    try:
        if ffprobe_path:
            result = runner(
                [
                    ffprobe_path,
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=index",
                    "-of",
                    "csv=p=0",
                    path,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
            if result.returncode == 0:
                return bool((result.stdout or "").strip()), "ffprobe"

        if not ffmpeg_path:
            return None, "ffmpeg/ffprobe is unavailable"

        result = runner(
            [ffmpeg_path, "-hide_banner", "-i", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        stderr = result.stderr or ""
        return ("Audio:" in stderr), "ffmpeg fallback"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _ffconcat_line(path: str) -> str:
    normalized = os.path.abspath(path).replace("\\", "/")
    escaped = normalized.replace("'", "'\\''")
    return f"file '{escaped}'\n"


def _temporary_path(suffix: str) -> str:
    descriptor, path = tempfile.mkstemp(suffix=suffix)
    os.close(descriptor)
    os.unlink(path)
    return path


def merge_videos(
    video_paths: Sequence[str],
    output_path: str,
    target_fps: float | None = None,
    source_audio_path: str | None = None,
    total_frames: int | None = None,
    source_fps: float | None = None,
    *,
    ffmpeg_path: str | None = None,
    runner: RunCommand = subprocess.run,
    logger: Callable[[str], Any] | None = None,
) -> bool:
    def log(message: str) -> None:
        if logger:
            logger(message)
        else:
            print(f"[SQR] {message}")

    if not video_paths:
        return False

    ffmpeg_path = _usable_executable(ffmpeg_path) or resolve_ffmpeg_path()
    if not ffmpeg_path:
        log("✗ 找不到可用的 ffmpeg，无法执行最终视频合并")
        return False

    replace_audio = bool(
        source_audio_path
        and os.path.isfile(source_audio_path)
        and source_fps
        and source_fps > 0
        and total_frames
        and total_frames > 0
    )
    concat_output = _temporary_path(".mp4") if replace_audio else output_path
    list_path = None
    converted: list[str] = []
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as file_list:
            file_list.writelines(_ffconcat_line(path) for path in video_paths)
            list_path = file_list.name

        if target_fps and target_fps > 0:
            fps_str = f"{target_fps:.6f}".rstrip("0").rstrip(".")
            for video_path in video_paths:
                converted_path = _temporary_path(".mp4")
                convert_command = [
                    ffmpeg_path,
                    "-y",
                    "-i",
                    video_path,
                    "-r",
                    fps_str,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "18",
                    "-c:a",
                    "copy",
                    converted_path,
                ]
                result = runner(
                    convert_command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if result.returncode == 0:
                    converted.append(converted_path)
                else:
                    try:
                        if os.path.exists(converted_path):
                            os.unlink(converted_path)
                    except Exception:
                        pass
                    converted.append(video_path)
            with open(list_path, "w", encoding="utf-8") as file_list:
                file_list.writelines(_ffconcat_line(path) for path in converted)

        concat_command = [
            ffmpeg_path,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c",
            "copy",
            concat_output,
        ]
        result = runner(
            concat_command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            log(f"✗ 视频分段拼接失败: {(result.stderr or '')[-300:]}")
            return False

        if not replace_audio:
            return True

        duration = total_frames / source_fps
        remux_command = [
            ffmpeg_path,
            "-y",
            "-i",
            concat_output,
            "-i",
            source_audio_path,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            f"{duration:.9f}",
            "-movflags",
            "+faststart",
            output_path,
        ]
        remux_result = runner(
            remux_command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if remux_result.returncode == 0:
            has_audio, detail = probe_audio_stream(
                output_path,
                ffmpeg_path=ffmpeg_path,
                runner=runner,
            )
            if has_audio is True:
                log(f"✓ 最终视频音轨校验通过 ({detail})")
                return True
            if has_audio is False:
                log(
                    "✗ 最终视频已生成，但音轨校验失败：成品中未检测到音频流；"
                    "任务将保留 checkpoint"
                )
            else:
                log(
                    f"✗ 最终视频音轨校验失败：无法可靠读取成品音轨 "
                    f"({detail})；任务将保留 checkpoint"
                )
            return False
        log(
            f"✗ 最终音轨写入失败：ffmpeg 无法将源音轨写入成品；"
            f"任务将保留 checkpoint。详情: {(remux_result.stderr or '')[-300:]}"
        )
        return False
    except FileNotFoundError:
        log("✗ ffmpeg 执行文件不可用，最终视频合并未完成")
        return False
    except Exception as exc:
        log(f"✗ 最终视频合并异常: {type(exc).__name__}: {exc}")
        return False
    finally:
        for temporary_path in (
            list_path,
            concat_output if replace_audio else None,
        ):
            try:
                if temporary_path and os.path.exists(temporary_path):
                    os.unlink(temporary_path)
            except Exception:
                pass
        for temporary_path in converted:
            try:
                if (
                    temporary_path not in video_paths
                    and os.path.exists(temporary_path)
                ):
                    os.unlink(temporary_path)
            except Exception:
                pass


def build_video_preview(
    video_path: str,
    output_directory: str,
    frame_rate: float,
) -> dict[str, Any] | None:
    """Build a VHS-compatible preview record for a completed output file."""

    if not video_path or not os.path.isfile(video_path):
        return None
    output_root = os.path.realpath(output_directory)
    resolved_path = os.path.realpath(video_path)
    try:
        if os.path.commonpath([output_root, resolved_path]) != output_root:
            return None
    except ValueError:
        return None

    relative_path = os.path.relpath(resolved_path, output_root)
    subfolder = os.path.dirname(relative_path).replace(os.sep, "/")
    return {
        "filename": os.path.basename(relative_path),
        "subfolder": "" if subfolder == "." else subfolder,
        "type": "output",
        "format": "video/h264-mp4",
        "frame_rate": float(frame_rate),
        "workflow": "",
        "fullpath": resolved_path,
    }
