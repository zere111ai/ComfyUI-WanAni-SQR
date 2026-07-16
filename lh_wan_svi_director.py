import json
import math
import os

import numpy as np
import torch
import types
from PIL import Image, ImageOps

import folder_paths
import comfy.ldm.modules.attention
import comfy.latent_formats
import comfy.model_management
import comfy.utils
import node_helpers

MIN_REF_FRAMES = 5
MIN_CW_REF_FRAMES = 4
MAX_REF_FRAMES = 81
MAX_CW_SECONDS = 60.0


def _safe_int(value, default=0):
    try:
        return int(round(float(value)))
    except Exception:
        return default


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _safe_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return default


def _round_to_multiple(value, multiple=16):
    value = max(multiple, _safe_int(value, multiple))
    return max(multiple, int(round(value / multiple) * multiple))


def _blank_image(width=512, height=512):
    arr = np.zeros((height, width, 3), dtype=np.float32)
    return torch.from_numpy(arr)[None,]


def _is_blank_image(image):
    if not isinstance(image, torch.Tensor) or image.numel() == 0:
        return True
    return bool(torch.max(torch.abs(image.detach())).item() <= 1e-6)


def _fit_size_to_pixels(source_width, source_height, target_pixels):
    source_width = max(1, int(source_width))
    source_height = max(1, int(source_height))
    ratio = source_width / source_height
    height = math.sqrt(max(1, target_pixels) / ratio)
    width = height * ratio
    return _round_to_multiple(width, 16), _round_to_multiple(height, 16)


def _resize_crop_image(image, target_width, target_height):
    image = image[:1, :, :, :3]
    target_width = _round_to_multiple(target_width, 16)
    target_height = _round_to_multiple(target_height, 16)
    return comfy.utils.common_upscale(
        image.movedim(-1, 1), target_width, target_height, "lanczos", "center"
    ).movedim(1, -1)


def _load_ref_image_batch(refs, width, height):
    images = []
    for ref in refs:
        image = _load_input_image(ref.get("image") or "")
        images.append(_resize_crop_image(image, width, height))
    if not images:
        return _resize_crop_image(_blank_image(), width, height)
    return torch.cat(images, dim=0)


def _load_input_image(filename):
    if not filename:
        return _blank_image()

    input_dir = folder_paths.get_input_directory()
    safe_name = os.path.normpath(filename).replace("\\", os.sep).replace("/", os.sep)
    path = os.path.abspath(os.path.join(input_dir, safe_name))
    input_root = os.path.abspath(input_dir)
    if not path.startswith(input_root) or not os.path.isfile(path):
        return _blank_image()

    image = Image.open(path)
    image = ImageOps.exif_transpose(image).convert("RGB")
    arr = np.asarray(image).astype(np.float32) / 255.0
    return torch.from_numpy(arr)[None,]


def _load_timeline_ref_image(ref_interval, data, reference_images=None):
    width = data["width"]
    height = data["height"]
    ref_image_name = ref_interval.get("image") if ref_interval else ""
    ref_image = _resize_crop_image(_load_input_image(ref_image_name), width, height)
    if not _is_blank_image(ref_image):
        return ref_image

    if isinstance(reference_images, torch.Tensor) and ref_interval is not None and reference_images.ndim >= 4:
        ref_index = _safe_int(ref_interval.get("sourceRefIndex", 0), 0)
        ref_index = max(0, min(reference_images.shape[0] - 1, ref_index))
        ref_image = reference_images[ref_index:ref_index + 1]
        if ref_image.shape[1] != _round_to_multiple(height, 16) or ref_image.shape[2] != _round_to_multiple(width, 16):
            ref_image = _resize_crop_image(ref_image, width, height)
        return ref_image

    return _resize_crop_image(_blank_image(), width, height)


def _load_input_image_with_size(filename):
    if not filename:
        return _blank_image(), 512, 512

    input_dir = folder_paths.get_input_directory()
    safe_name = os.path.normpath(filename).replace("\\", os.sep).replace("/", os.sep)
    path = os.path.abspath(os.path.join(input_dir, safe_name))
    input_root = os.path.abspath(input_dir)
    if not path.startswith(input_root) or not os.path.isfile(path):
        return _blank_image(), 512, 512

    image = Image.open(path)
    image = ImageOps.exif_transpose(image).convert("RGB")
    width, height = image.size
    arr = np.asarray(image).astype(np.float32) / 255.0
    return torch.from_numpy(arr)[None,], int(width), int(height)


def _default_timeline(total_frames, frame_rate, max_segment_frames):
    total_frames = max(1, _safe_int(total_frames, 81))
    frame_rate = max(1.0, _safe_float(frame_rate, 16.0))
    max_segment_frames = max(MIN_REF_FRAMES, min(MAX_REF_FRAMES, _safe_int(max_segment_frames, 81)))
    return {
        "version": 1,
        "kind": "LH_WAN_SVI_DIRECTOR_TIMELINE",
        "frameRate": frame_rate,
        "totalFrames": total_frames,
        "timelineFrames": total_frames,
        "durationSeconds": total_frames / frame_rate,
        "timelineSeconds": total_frames / frame_rate,
        "maxSegmentFrames": max_segment_frames,
        "width": 832,
        "height": 480,
        "matchFirstImageAspect": False,
        "aspectPixelPreset": "720p",
        "enablePromptRelay": True,
        "globalPrompt": "",
        "refs": [
            {
                "id": "ref_1",
                "label": "Reference 1",
                "image": "",
                "endImage": "",
                "extraImages": [],
                "startFrame": 0,
                "endFrame": min(total_frames, max_segment_frames),
                "strength": 1.0,
                "startStrength": 1.0,
                "endStrength": 1.0,
                "prompt": "",
            }
        ],
    }


def _normalize_timeline(timeline_data, total_frames, frame_rate, max_segment_frames, min_ref_frames=MIN_REF_FRAMES, max_ref_frames=MAX_REF_FRAMES, kind="LH_WAN_SVI_DIRECTOR_TIMELINE"):
    if timeline_data and str(timeline_data).strip():
        try:
            data = json.loads(timeline_data)
        except Exception:
            data = _default_timeline(total_frames, frame_rate, max_segment_frames)
    else:
        data = _default_timeline(total_frames, frame_rate, max_segment_frames)

    frame_rate = max(1.0, _safe_float(data.get("frameRate", frame_rate), frame_rate))
    total_frames = max(1, _safe_int(data.get("totalFrames", total_frames), total_frames))
    min_ref_frames = max(1, _safe_int(min_ref_frames, MIN_REF_FRAMES))
    max_ref_frames = max(min_ref_frames, _safe_int(max_ref_frames, MAX_REF_FRAMES))
    max_segment_frames = max(min_ref_frames, min(max_ref_frames, _safe_int(data.get("maxSegmentFrames", max_segment_frames), max_segment_frames)))
    data["version"] = int(data.get("version") or 1)
    data["kind"] = kind
    data["frameRate"] = frame_rate
    data["totalFrames"] = total_frames
    data["durationSeconds"] = total_frames / frame_rate
    data["maxSegmentFrames"] = max_segment_frames
    data["width"] = _round_to_multiple(data.get("width", 832), 16)
    data["height"] = _round_to_multiple(data.get("height", 480), 16)
    data["matchFirstImageAspect"] = _safe_bool(data.get("matchFirstImageAspect"), False)
    preset = str(data.get("aspectPixelPreset") or "720p")
    data["aspectPixelPreset"] = preset if preset in {"720p", "480p"} else "720p"
    data["enablePromptRelay"] = _safe_bool(data.get("enablePromptRelay", data.get("enable_prompt_relay", True)), True)
    data["globalPrompt"] = str(data.get("globalPrompt") or data.get("global_prompt") or "")

    refs = data.get("refs")
    if not isinstance(refs, list):
        refs = []
    raw_ref_end = max([_safe_int(ref.get("endFrame", 0), 0) for ref in refs if isinstance(ref, dict)] + [0])
    # Rebuild the SVI display range from the configured duration and actual
    # segments so stale cached timelineFrames cannot create a phantom tail.
    timeline_frames = max(total_frames, raw_ref_end)
    data["timelineFrames"] = timeline_frames
    data["timelineSeconds"] = timeline_frames / frame_rate
    clean_refs = []
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            continue
        start = max(0, min(timeline_frames - 1, _safe_int(ref.get("startFrame", 0), 0)))
        end = max(start + min_ref_frames, min(timeline_frames, _safe_int(ref.get("endFrame", start + max_segment_frames), start + max_segment_frames)))
        end = min(end, start + max_ref_frames)
        clean_refs.append(
            {
                "id": str(ref.get("id") or f"ref_{index + 1}"),
                "label": str(ref.get("label") or f"Reference {index + 1}"),
                "image": str(ref.get("image") or ""),
                "endImage": str(ref.get("endImage") or ref.get("end_image") or ""),
                "extraImages": [str(item or "") for item in (ref.get("extraImages") or [])][:3],
                "startFrame": start,
                "endFrame": end,
                "strength": _safe_float(ref.get("strength", 1.0), 1.0),
                "startStrength": max(0.0, min(1.0, _safe_float(ref.get("startStrength", ref.get("strength", 1.0)), 1.0))),
                "endStrength": max(0.0, min(1.0, _safe_float(ref.get("endStrength", ref.get("strength", 1.0)), 1.0))),
                "epsilon": max(0.000001, min(1.0, _safe_float(ref.get("epsilon", 0.001), 0.001))),
                "continuePrevious": bool(ref.get("continuePrevious", False)) and index > 0,
                "prompt": str(ref.get("prompt") or ""),
                "sourceIndex": _safe_int(ref.get("sourceIndex", index), index),
            }
        )
    if not clean_refs:
        clean_refs = _default_timeline(total_frames, frame_rate, max_segment_frames)["refs"]
    clean_refs.sort(key=lambda item: (item["startFrame"], item["endFrame"]))
    for index, ref in enumerate(clean_refs):
        ref["continuePrevious"] = bool(ref.get("continuePrevious", False)) and index > 0
    if kind != "LH_WAN_CW_DIRECTOR_TIMELINE":
        data.pop("contextWindowFrames", None)
        data.pop("contextOverlapFrames", None)
    packed_refs = []
    cursor = 0
    for ref in clean_refs:
        length = max(min_ref_frames, min(max_ref_frames, ref["endFrame"] - ref["startFrame"]))
        start = max(cursor, min(ref["startFrame"], max(0, timeline_frames - length)))
        end = min(timeline_frames, start + length)
        if end - start < MIN_REF_FRAMES:
            continue
        ref["startFrame"] = start
        ref["endFrame"] = end
        packed_refs.append(ref)
        cursor = end
    data["refs"] = packed_refs or _default_timeline(total_frames, frame_rate, max_segment_frames)["refs"]
    data["timelineFrames"] = max(timeline_frames, total_frames, *[ref["endFrame"] for ref in data["refs"]])
    data["timelineSeconds"] = data["timelineFrames"] / frame_rate
    return data


def _normalize_cw_timeline(timeline_data, duration_seconds, frame_rate, context_window_frames, context_overlap_frames):
    frame_rate = max(1.0, min(240.0, _safe_float(frame_rate, 16.0)))
    duration_seconds = max(0.25, min(MAX_CW_SECONDS, _safe_float(duration_seconds, 5.0)))
    total_frames = max(MIN_CW_REF_FRAMES, int(round(duration_seconds * frame_rate)))
    context_window_frames = max(MIN_CW_REF_FRAMES, min(MAX_REF_FRAMES, _safe_int(context_window_frames, 81)))
    context_window_frames = ((context_window_frames - 1) // 4) * 4 + 1
    context_window_frames = max(MIN_CW_REF_FRAMES + 1, min(MAX_REF_FRAMES, context_window_frames))
    context_overlap_frames = max(0, min(context_window_frames - 1, _safe_int(context_overlap_frames, 16)))
    if timeline_data and str(timeline_data).strip():
        try:
            raw_data = json.loads(timeline_data)
        except Exception:
            raw_data = {}
    else:
        raw_data = {}
    raw_data["frameRate"] = frame_rate
    raw_data["durationSeconds"] = duration_seconds
    raw_data["totalFrames"] = total_frames
    raw_data["timelineFrames"] = total_frames
    raw_data["maxSegmentFrames"] = context_window_frames
    raw_data["contextWindowFrames"] = context_window_frames
    raw_data["contextOverlapFrames"] = context_overlap_frames
    data = _normalize_timeline(
        json.dumps(raw_data, ensure_ascii=False),
        total_frames,
        frame_rate,
        context_window_frames,
        min_ref_frames=MIN_CW_REF_FRAMES,
        max_ref_frames=total_frames,
        kind="LH_WAN_CW_DIRECTOR_TIMELINE",
    )
    data["durationSeconds"] = duration_seconds
    data["totalFrames"] = total_frames
    data["timelineFrames"] = total_frames
    data["timelineSeconds"] = duration_seconds
    data["maxSegmentFrames"] = context_window_frames
    data["contextWindowFrames"] = context_window_frames
    data["contextOverlapFrames"] = context_overlap_frames
    return data


def _summary(data, segment_count):
    refs = data.get("refs") or []
    lines = [
        f"LH WAN SVI Director demo",
        f"total: {int(data['totalFrames'])} frames / {data['durationSeconds']:.2f}s @ {data['frameRate']:.2f} fps",
        f"svi: {segment_count} segment(s), max {int(data['maxSegmentFrames'])} frames each",
        f"refs: {len(refs)}",
        f"global prompt: {data.get('globalPrompt') or '(empty)'}",
    ]
    warnings = []
    if segment_count > 12:
        warnings.append(f"segment count {segment_count} exceeds the 12-segment workflow limit")
    if int(data["maxSegmentFrames"]) % 4 != 1:
        warnings.append("max segment frames is not 4n+1; Wan temporal alignment may be inconsistent")
    if any(ref.get("extraImages") for ref in refs):
        warnings.append("reserved reference slots are stored but are not used by SVI sampling")
    if warnings:
        lines.extend(f"warning: {warning}" for warning in warnings)
    for i, ref in enumerate(refs[:8], 1):
        lines.append(
            f"{i}. {ref.get('label', 'Reference')} [{int(ref['startFrame'])}-{int(ref['endFrame'])}) "
            f"start={ref.get('image') or '(none)'} end={ref.get('endImage') or '(none)'}"
        )
    if len(refs) > 8:
        lines.append(f"... {len(refs) - 8} more")
    return "\n".join(lines)


def _latent_frame_count(real_frames):
    real_frames = max(1, _safe_int(real_frames, 1))
    return ((real_frames - 1) // 4) + 1


def _allocate_latent_lengths(intervals, real_frames):
    total_latents = _latent_frame_count(real_frames)
    if not intervals:
        return []

    raw = []
    for interval in intervals:
        frames = max(0, int(interval["endFrame"]) - int(interval["startFrame"]))
        raw.append((frames / max(1, real_frames)) * total_latents)

    lengths = [max(1, int(math.floor(value))) if raw[i] > 0 else 0 for i, value in enumerate(raw)]
    diff = total_latents - sum(lengths)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - math.floor(raw[i]), reverse=True)

    while diff > 0 and order:
        for i in order:
            lengths[i] += 1
            diff -= 1
            if diff == 0:
                break

    while diff < 0:
        candidates = sorted(range(len(lengths)), key=lambda i: lengths[i], reverse=True)
        changed = False
        for i in candidates:
            if lengths[i] > 1:
                lengths[i] -= 1
                diff += 1
                changed = True
                if diff == 0:
                    break
        if not changed:
            break

    return lengths


def _build_prompt_relay_plan(data, chunk_index, chunk_frames, max_chunk_frames=MAX_REF_FRAMES):
    total_frames = int(data["totalFrames"])
    max_segment_frames = int(data["maxSegmentFrames"])
    max_chunk_frames = max(MIN_REF_FRAMES, _safe_int(max_chunk_frames, MAX_REF_FRAMES))
    chunk_frames = max(MIN_REF_FRAMES, min(max_chunk_frames, _safe_int(chunk_frames, max_segment_frames)))
    chunk_index = max(1, _safe_int(chunk_index, 1))
    chunk_start = min(total_frames - 1, (chunk_index - 1) * chunk_frames)
    chunk_end = min(total_frames, chunk_start + chunk_frames)
    if chunk_end <= chunk_start:
        chunk_end = min(total_frames, chunk_start + 1)

    global_prompt = str(data.get("globalPrompt") or "")
    refs = sorted(data.get("refs") or [], key=lambda item: (item["startFrame"], item["endFrame"]))
    intervals = []
    cursor = chunk_start

    for ref in refs:
        ref_start = int(ref.get("startFrame", 0))
        ref_end = int(ref.get("endFrame", 0))
        start = max(chunk_start, ref_start)
        end = min(chunk_end, ref_end)
        if end <= start:
            continue
        if start > cursor:
            intervals.append(
                {
                    "kind": "gap",
                    "label": "Global",
                    "prompt": global_prompt,
                    "startFrame": cursor,
                    "endFrame": start,
                    "sourceRefId": "",
                    "image": "",
                    "endImage": "",
                    "startStrength": 0.0,
                    "endStrength": 0.0,
                    "epsilon": 0.001,
                }
            )
        intervals.append(
            {
                "kind": "ref",
                "label": str(ref.get("label") or "Reference"),
                "prompt": str(ref.get("prompt") or global_prompt or "video"),
                "startFrame": start,
                "endFrame": end,
                "sourceRefId": str(ref.get("id") or ""),
                "sourceRefIndex": _safe_int(ref.get("sourceIndex", 0), 0),
                "image": str(ref.get("image") or ""),
                "endImage": str(ref.get("endImage") or ""),
                "startStrength": max(0.0, min(1.0, _safe_float(ref.get("startStrength", 1.0), 1.0))),
                "endStrength": max(0.0, min(1.0, _safe_float(ref.get("endStrength", 1.0), 1.0))),
                "epsilon": max(0.000001, min(1.0, _safe_float(ref.get("epsilon", 0.001), 0.001))),
                "continuePrevious": bool(ref.get("continuePrevious", False)),
            }
        )
        cursor = max(cursor, end)

    if cursor < chunk_end:
        intervals.append(
            {
                "kind": "gap",
                "label": "Global",
                "prompt": global_prompt,
                "startFrame": cursor,
                "endFrame": chunk_end,
                "sourceRefId": "",
                "image": "",
                "endImage": "",
                "startStrength": 0.0,
                "endStrength": 0.0,
                "epsilon": 0.001,
            }
        )

    if not intervals:
        intervals = [
            {
                "kind": "gap",
                "label": "Global",
                "prompt": global_prompt or "video",
                "startFrame": chunk_start,
                "endFrame": chunk_end,
                "sourceRefId": "",
                "image": "",
                "endImage": "",
                "startStrength": 0.0,
                "endStrength": 0.0,
                "epsilon": 0.001,
            }
        ]

    local_real_frames = chunk_end - chunk_start
    latent_lengths = _allocate_latent_lengths(intervals, local_real_frames)
    for interval, latent_len in zip(intervals, latent_lengths):
        interval["localStartFrame"] = int(interval["startFrame"] - chunk_start)
        interval["localEndFrame"] = int(interval["endFrame"] - chunk_start)
        interval["realFrameLength"] = int(interval["endFrame"] - interval["startFrame"])
        interval["latentLength"] = int(latent_len)

    local_prompts = [str(item.get("prompt") or global_prompt or "video") for item in intervals]
    segment_lengths = [int(item["latentLength"]) for item in intervals]
    image_intervals = [
        item for item in intervals
        if item.get("kind") == "ref" and (item.get("image") or item.get("endImage"))
    ]
    warnings = []
    if len(image_intervals) > 1:
        warnings.append(
            "multiple image-reference segments overlap this SVI chunk; only the primary segment can condition I2V"
        )
    return {
        "version": 1,
        "kind": "LH_WAN_SVI_PROMPT_RELAY_PLAN",
        "chunkIndex": chunk_index,
        "chunkStartFrame": int(chunk_start),
        "chunkEndFrame": int(chunk_end),
        "chunkRealFrames": int(local_real_frames),
        "chunkLatentFrames": int(_latent_frame_count(local_real_frames)),
        "frameRate": float(data["frameRate"]),
        "global_prompt": global_prompt,
        "local_prompts": local_prompts,
        "segment_lengths": segment_lengths,
        "intervals": intervals,
        "warnings": warnings,
    }


def _select_primary_interval(plan):
    intervals = list(plan.get("intervals") or [])
    ref_intervals = [item for item in intervals if item.get("kind") == "ref"]
    candidates = ref_intervals or intervals
    if not candidates:
        return None
    chunk_start = int(plan.get("chunkStartFrame", 0))
    return max(
        candidates,
        key=lambda item: (
            int(item.get("endFrame", 0)) - int(item.get("startFrame", 0)),
            int(item.get("startFrame", 0)) == chunk_start,
            -int(item.get("startFrame", 0)),
        ),
    )


def _prompt_plan_summary(plan):
    lines = [
        "LH WAN SVI Prompt Relay Plan",
        f"chunk {plan['chunkIndex']}: frames {plan['chunkStartFrame']}-{plan['chunkEndFrame']} "
        f"({plan['chunkRealFrames']} real / {plan['chunkLatentFrames']} latent)",
        f"segments: {len(plan['intervals'])}, lengths: {','.join(str(x) for x in plan['segment_lengths'])}",
    ]
    for i, item in enumerate(plan["intervals"][:8], 1):
        prompt = (item.get("prompt") or "").replace("\n", " ").strip()
        if len(prompt) > 72:
            prompt = prompt[:69] + "..."
        lines.append(
            f"{i}. {item['kind']} [{item['startFrame']}-{item['endFrame']}) "
            f"latent={item['latentLength']} epsilon={_safe_float(item.get('epsilon'), 0.001):.6g} "
            f"prompt={prompt or '(empty)'}"
        )
    if len(plan["intervals"]) > 8:
        lines.append(f"... {len(plan['intervals']) - 8} more")
    lines.extend(f"warning: {warning}" for warning in plan.get("warnings", []))
    return "\n".join(lines)


def _get_raw_tokenizer(clip):
    tokenizer_wrapper = clip.tokenizer
    for attr_name in dir(tokenizer_wrapper):
        if attr_name.startswith("_"):
            continue
        inner = getattr(tokenizer_wrapper, attr_name, None)
        if inner is not None and hasattr(inner, "tokenizer"):
            return inner.tokenizer
    raise RuntimeError("Could not find the raw tokenizer on this CLIP object.")


def _map_token_indices(raw_tokenizer, global_prompt, local_prompts):
    prefixed_locals = [" " + prompt for prompt in local_prompts]
    full_prompt = (global_prompt or "") + "".join(prefixed_locals)
    has_eos = getattr(raw_tokenizer, "add_eos", False)
    eos_adj = 1 if has_eos else 0
    prev_len = max(0, len(raw_tokenizer(global_prompt or "")["input_ids"]) - eos_adj)
    ranges = []
    built = global_prompt or ""
    for prompt in prefixed_locals:
        built += prompt
        cur_len = max(prev_len + 1, len(raw_tokenizer(built)["input_ids"]) - eos_adj)
        ranges.append((prev_len, cur_len))
        prev_len = cur_len
    return full_prompt, ranges


def _parse_prompt_list(local_prompts):
    prompts = [item.strip() for item in str(local_prompts or "").split("|")]
    return [item for item in prompts if item]


def _parse_segment_lengths(segment_lengths):
    out = []
    for item in str(segment_lengths or "").split(","):
        item = item.strip()
        if item:
            out.append(max(1, _safe_int(item, 1)))
    return out


def _parse_epsilon_list(epsilon, count):
    if isinstance(epsilon, str):
        values = []
        for item in epsilon.replace("|", ",").split(","):
            item = item.strip()
            if item:
                values.append(max(0.000001, min(1.0, _safe_float(item, 0.001))))
    elif isinstance(epsilon, (list, tuple)):
        values = [max(0.000001, min(1.0, _safe_float(item, 0.001))) for item in epsilon]
    else:
        values = [max(0.000001, min(1.0, _safe_float(epsilon, 0.001)))]
    if not values:
        values = [0.001]
    while len(values) < count:
        values.append(values[-1])
    return values[:count]


def _build_relay_segments(token_ranges, segment_lengths, epsilon=1e-3):
    segments = []
    cursor = 0
    if isinstance(epsilon, (list, tuple)):
        epsilons = list(epsilon) or [1e-3]
    else:
        epsilons = [epsilon] * len(segment_lengths)
    for index, ((token_start, token_end), length) in enumerate(zip(token_ranges, segment_lengths)):
        length = max(1, int(length))
        midpoint = cursor + (length - 1) / 2.0
        window = max(0.0, length / 2.0 - 2.0)
        segment_epsilon = epsilons[index] if index < len(epsilons) else epsilons[-1]
        segment_epsilon = max(0.000001, min(1.0, _safe_float(segment_epsilon, 0.001)))
        sigma_epsilon = min(0.999999, segment_epsilon)
        sigma = 1.0 / math.log(1.0 / max(1e-9, float(sigma_epsilon)))
        segments.append(
            {
                "token_start": int(token_start),
                "token_end": int(token_end),
                "length": int(length),
                "midpoint": float(midpoint),
                "window": float(window),
                "sigma": float(sigma),
                "epsilon": float(segment_epsilon),
            }
        )
        cursor += length
    return segments


def _temporal_cost(relay_segments, latent_frames, tokens_per_frame, key_count, device, dtype, query_count=None, query_frames=None):
    latent_frames = max(1, int(latent_frames))
    tokens_per_frame = max(1, int(tokens_per_frame))
    query_count = max(1, int(query_count or (latent_frames * tokens_per_frame)))
    if query_frames is not None:
        query_frames = query_frames.to(device=device, dtype=torch.float32)
        if query_frames.numel() != query_count:
            query_frames = None
    if query_frames is None and query_count == latent_frames * tokens_per_frame:
        query_frames = (torch.arange(query_count, device=device, dtype=torch.long) // tokens_per_frame).float()
    elif query_frames is None:
        scale = float(latent_frames) / float(query_count)
        query_frames = (torch.arange(query_count, device=device, dtype=torch.float32) + 0.5) * scale - 0.5
        query_frames = query_frames.clamp(0.0, float(latent_frames - 1))
    cost = torch.zeros(query_count, key_count, device=device, dtype=dtype)
    for seg in relay_segments:
        start = max(0, min(key_count, int(seg["token_start"])))
        end = max(start + 1, min(key_count, int(seg["token_end"])))
        d = (query_frames[:, None] - float(seg["midpoint"])).abs()
        penalty = (torch.relu(d - float(seg["window"])) ** 2) / (2 * float(seg["sigma"]) ** 2)
        cost[:, start:end] = penalty.to(dtype)
    return cost


def _relay_attention(q, k, v, heads, relay_segments, latent_frames, transformer_options, chunk_size=64):
    batch, query_count, inner_dim = q.shape
    head_dim = inner_dim // heads
    qh = q.reshape(batch, query_count, heads, head_dim).transpose(1, 2)
    kh = k.reshape(batch, k.shape[1], heads, head_dim).transpose(1, 2)
    vh = v.reshape(batch, v.shape[1], heads, head_dim).transpose(1, 2)
    scale = 1.0 / math.sqrt(head_dim)
    context_window = transformer_options.get("context_window") if isinstance(transformer_options, dict) else None
    window_indices = getattr(context_window, "index_list", None)
    query_frames = None
    if window_indices:
        window_indices = [int(index) for index in window_indices]
        local_tokens_per_frame = max(1, query_count // len(window_indices))
        if len(window_indices) * local_tokens_per_frame == query_count:
            query_frames = torch.repeat_interleave(
                torch.tensor(window_indices, device=q.device, dtype=torch.float32),
                local_tokens_per_frame,
            )
    tokens_per_frame = max(1, query_count // max(1, int(latent_frames)))
    cost = _temporal_cost(
        relay_segments,
        latent_frames,
        tokens_per_frame,
        k.shape[1],
        q.device,
        torch.float32,
        query_count,
        query_frames,
    )
    cond_or_uncond = transformer_options.get("cond_or_uncond", [0] * batch)
    out = torch.empty_like(qh)
    for b in range(batch):
        row_relay = b >= len(cond_or_uncond) or int(cond_or_uncond[b]) == 0
        if not row_relay:
            regular = comfy.ldm.modules.attention.optimized_attention(
                q[b:b + 1], k[b:b + 1], v[b:b + 1], heads=heads, transformer_options=transformer_options
            )
            out[b:b + 1] = regular.reshape(1, query_count, heads, head_dim).transpose(1, 2)
            continue
        for start in range(0, query_count, chunk_size):
            end = min(query_count, start + chunk_size)
            logits = torch.matmul(qh[b:b + 1, :, start:end], kh[b:b + 1].transpose(-2, -1)) * scale
            logits = logits.float() - cost[start:end].unsqueeze(0).unsqueeze(0)
            attn = torch.softmax(logits, dim=-1).to(vh.dtype)
            out[b:b + 1, :, start:end] = torch.matmul(attn, vh[b:b + 1])
    return out.transpose(1, 2).reshape(batch, query_count, inner_dim)


class _WanPromptRelayCrossAttentionPatch:
    def __init__(self, relay_segments, latent_frames, i2v=False):
        self.relay_segments = relay_segments
        self.latent_frames = int(latent_frames)
        self.i2v = bool(i2v)

    def __get__(self, obj, objtype=None):
        def wrapped_attention(module, x, context, context_img_len=257, transformer_options={}, **kwargs):
            q = module.norm_q(module.q(x))
            if self.i2v and hasattr(module, "k_img"):
                context_img = context[:, :context_img_len]
                context_text = context[:, context_img_len:]
                k_img = module.norm_k_img(module.k_img(context_img))
                v_img = module.v_img(context_img)
                img_x = comfy.ldm.modules.attention.optimized_attention(
                    q, k_img, v_img, heads=module.num_heads, transformer_options=transformer_options
                )
            else:
                context_text = context
                img_x = 0
            k = module.norm_k(module.k(context_text))
            v = module.v(context_text)
            x_out = _relay_attention(
                q, k, v, module.num_heads, self.relay_segments, self.latent_frames, transformer_options
            )
            return module.o(x_out + img_x)
        return types.MethodType(wrapped_attention, obj)


class LHWANSVIDirectorTimeline:
    CATEGORY = "LH/WAN SVI"
    RETURN_TYPES = ("INT", "INT", "INT", "FLOAT", "FLOAT", "IMAGE", "STRING", "STRING", "INT", "INT", "IMAGE")
    RETURN_NAMES = (
        "segment_count",
        "total_frames",
        "max_segment_frames",
        "total_seconds",
        "frame_rate",
        "first_ref_image",
        "timeline_json",
        "summary",
        "width",
        "height",
        "ref_images",
    )
    FUNCTION = "build"

    @classmethod
    def INPUT_TYPES(cls):
        default = json.dumps(_default_timeline(243, 16.0, 81), ensure_ascii=False)
        return {
            "required": {
                "total_frames": ("INT", {"default": 243, "min": 1, "max": 20000, "step": 1}),
                "frame_rate": ("FLOAT", {"default": 16.0, "min": 1.0, "max": 240.0, "step": 0.01}),
                "max_segment_frames": ("INT", {"default": 81, "min": MIN_REF_FRAMES, "max": MAX_REF_FRAMES, "step": 1}),
                "timeline_data": ("STRING", {"default": default, "multiline": True}),
            }
        }

    def build(self, total_frames, frame_rate, max_segment_frames, timeline_data):
        data = _normalize_timeline(timeline_data, total_frames, frame_rate, max_segment_frames)
        segment_count = max(1, math.ceil(int(data["totalFrames"]) / int(data["maxSegmentFrames"])))
        first_ref = next((ref for ref in data.get("refs", []) if ref.get("image")), None)
        image, source_width, source_height = _load_input_image_with_size(first_ref.get("image") if first_ref else "")
        if data.get("matchFirstImageAspect"):
            target_pixels = 1280 * 720 if data.get("aspectPixelPreset") == "720p" else 854 * 480
            width, height = _fit_size_to_pixels(source_width, source_height, target_pixels)
            data["width"] = width
            data["height"] = height
        image = _resize_crop_image(image, data["width"], data["height"])
        ref_images = _load_ref_image_batch(data.get("refs") or [], data["width"], data["height"])
        timeline_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return (
            int(segment_count),
            int(data["totalFrames"]),
            int(data["maxSegmentFrames"]),
            float(data["durationSeconds"]),
            float(data["frameRate"]),
            image,
            timeline_json,
            _summary(data, segment_count),
            int(data["width"]),
            int(data["height"]),
            ref_images,
        )


class LHWANCWDirectorTimeline(LHWANSVIDirectorTimeline):
    CATEGORY = "LH/WAN CW"
    RETURN_TYPES = LHWANSVIDirectorTimeline.RETURN_TYPES + ("INT", "INT")
    RETURN_NAMES = LHWANSVIDirectorTimeline.RETURN_NAMES + ("context_window_frames", "context_overlap_frames")

    @classmethod
    def INPUT_TYPES(cls):
        default = json.dumps(_default_timeline(80, 16.0, 81), ensure_ascii=False)
        return {
            "required": {
                "duration_seconds": ("FLOAT", {"default": 5.0, "min": 0.25, "max": MAX_CW_SECONDS, "step": 0.01}),
                "frame_rate": ("FLOAT", {"default": 16.0, "min": 1.0, "max": 240.0, "step": 0.01}),
                "context_window_frames": ("INT", {"default": 81, "min": MIN_CW_REF_FRAMES + 1, "max": MAX_REF_FRAMES, "step": 4}),
                "context_overlap_frames": ("INT", {"default": 16, "min": 0, "max": MAX_REF_FRAMES - 1, "step": 1}),
                "timeline_data": ("STRING", {"default": default, "multiline": True}),
            }
        }

    def build(self, duration_seconds, frame_rate, context_window_frames, context_overlap_frames, timeline_data):
        data = _normalize_cw_timeline(timeline_data, duration_seconds, frame_rate, context_window_frames, context_overlap_frames)
        first_ref = next((ref for ref in data.get("refs", []) if ref.get("image")), None)
        image, source_width, source_height = _load_input_image_with_size(first_ref.get("image") if first_ref else "")
        if data.get("matchFirstImageAspect"):
            target_pixels = 1280 * 720 if data.get("aspectPixelPreset") == "720p" else 854 * 480
            width, height = _fit_size_to_pixels(source_width, source_height, target_pixels)
            data["width"] = width
            data["height"] = height
        image = _resize_crop_image(image, data["width"], data["height"])
        ref_images = _load_ref_image_batch(data.get("refs") or [], data["width"], data["height"])
        timeline_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        summary = _summary(data, 1).replace("LH WAN SVI Director demo", "LH WAN CW Director demo").replace("svi: 1 segment(s), max", "cw: full timeline, window")
        return (
            1,
            int(data["totalFrames"]),
            int(data["contextWindowFrames"]),
            float(data["durationSeconds"]),
            float(data["frameRate"]),
            image,
            timeline_json,
            summary,
            int(data["width"]),
            int(data["height"]),
            ref_images,
            int(data["contextWindowFrames"]),
            int(data["contextOverlapFrames"]),
        )


class LHWANSVIPromptRelayPlan:
    CATEGORY = "LH/WAN SVI"
    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING", "INT", "INT", "INT", "IMAGE")
    RETURN_NAMES = (
        "global_prompt",
        "local_prompts",
        "segment_lengths",
        "prompt_relay_json",
        "summary",
        "chunk_start_frame",
        "chunk_end_frame",
        "chunk_latent_frames",
        "chunk_ref_image",
    )
    FUNCTION = "build"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "timeline_json": ("STRING", {"forceInput": True}),
                "chunk_index": ("INT", {"default": 1, "min": 1, "max": 10000, "step": 1}),
                "chunk_frames": ("INT", {"default": 81, "min": MIN_REF_FRAMES, "max": MAX_REF_FRAMES, "step": 1}),
            },
            "optional": {
                "reference_images": ("IMAGE",),
            }
        }

    def build(self, timeline_json=None, chunk_index=1, chunk_frames=81, reference_images=None):
        if isinstance(timeline_json, torch.Tensor):
            shifted_reference_images = timeline_json
            shifted_timeline_json = chunk_index
            shifted_chunk_index = chunk_frames
            shifted_chunk_frames = reference_images
            reference_images = shifted_reference_images
            timeline_json = shifted_timeline_json
            chunk_index = shifted_chunk_index
            chunk_frames = shifted_chunk_frames

        data = _normalize_timeline(timeline_json, 81, 16.0, chunk_frames)
        plan = _build_prompt_relay_plan(data, chunk_index, chunk_frames)
        prompt_relay_json = json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
        ref_interval = _select_primary_interval(plan)
        if ref_interval and ref_interval.get("kind") != "ref":
            ref_interval = None
        ref_image = _load_timeline_ref_image(ref_interval, data, reference_images)
        ref_name = ref_interval.get("image") if ref_interval else ""
        ref_mean = float(ref_image.mean().item()) if isinstance(ref_image, torch.Tensor) and ref_image.numel() else 0.0
        print(f"[LH WAN SVI] chunk {chunk_index} ref_image='{ref_name}' mean={ref_mean:.6f}")
        return (
            plan["global_prompt"],
            "|".join(plan["local_prompts"]),
            ",".join(str(value) for value in plan["segment_lengths"]),
            prompt_relay_json,
            _prompt_plan_summary(plan),
            int(plan["chunkStartFrame"]),
            int(plan["chunkEndFrame"]),
            int(plan["chunkLatentFrames"]),
            ref_image,
        )


class LHWANPromptRelayEncode:
    CATEGORY = "LH/WAN SVI"
    RETURN_TYPES = ("MODEL", "CONDITIONING", "STRING")
    RETURN_NAMES = ("model", "positive", "summary")
    FUNCTION = "encode"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "clip": ("CLIP",),
                "global_prompt": ("STRING", {"default": "", "multiline": True}),
                "local_prompts": ("STRING", {"default": "", "multiline": True}),
                "segment_lengths": ("STRING", {"default": ""}),
                "latent_frames": ("INT", {"default": 21, "min": 1, "max": 4096, "step": 1}),
                "epsilon": ("FLOAT", {"default": 0.001, "min": 0.000001, "max": 1.0, "step": 0.0001}),
                "enabled": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "fallback_global_prompt": ("STRING", {"default": "", "multiline": True}),
            }
        }

    def encode(self, model, clip, global_prompt, local_prompts, segment_lengths, latent_frames, epsilon, enabled, fallback_global_prompt=""):
        return _encode_prompt_relay(
            model, clip, global_prompt, local_prompts, segment_lengths, latent_frames, epsilon, enabled, fallback_global_prompt
        )


def _encode_prompt_relay(model, clip, global_prompt, local_prompts, segment_lengths, latent_frames, epsilon, enabled, fallback_global_prompt=""):
        if not str(global_prompt or "").strip() and str(fallback_global_prompt or "").strip():
            global_prompt = fallback_global_prompt
        local_list = _parse_prompt_list(local_prompts)
        fallback = str(global_prompt or "").strip() or "video"
        if not local_list:
            local_list = [fallback]
        local_list = [prompt or fallback for prompt in local_list]
        lengths = _parse_segment_lengths(segment_lengths)
        if not lengths:
            lengths = [max(1, int(latent_frames) // len(local_list))] * len(local_list)
            lengths[-1] += max(0, int(latent_frames) - sum(lengths))
        if len(lengths) != len(local_list):
            raise ValueError("Prompt Relay: segment_lengths count must match local_prompts count.")
        lengths[-1] += max(0, int(latent_frames) - sum(lengths))
        if sum(lengths) > int(latent_frames):
            overflow = sum(lengths) - int(latent_frames)
            lengths[-1] = max(1, lengths[-1] - overflow)

        raw_tokenizer = _get_raw_tokenizer(clip)
        full_prompt, token_ranges = _map_token_indices(raw_tokenizer, str(global_prompt or ""), local_list)
        conditioning = clip.encode_from_tokens_scheduled(clip.tokenize(full_prompt))

        if not enabled:
            return (model, conditioning, "Prompt Relay disabled; conditioning only.")

        if len(local_list) == 1:
            summary = (
                "LH WAN Prompt Relay Encode\n"
                "single-prompt chunk: relay attention bypassed\n"
                f"latent lengths: {','.join(str(x) for x in lengths)}\n"
                f"full prompt tokens: {token_ranges[0][0] if token_ranges else 0}+local"
            )
            return (model, conditioning, summary)

        epsilons = _parse_epsilon_list(epsilon, len(lengths))
        relay_segments = _build_relay_segments(token_ranges, lengths, epsilons)
        model_clone = model.clone()
        diffusion_model = model_clone.get_model_object("diffusion_model")
        for idx, block in enumerate(diffusion_model.blocks):
            i2v = hasattr(block.cross_attn, "k_img")
            patched = _WanPromptRelayCrossAttentionPatch(relay_segments, latent_frames, i2v=i2v).__get__(
                block.cross_attn, block.cross_attn.__class__
            )
            model_clone.add_object_patch(f"diffusion_model.blocks.{idx}.cross_attn.forward", patched)

        summary = (
            "LH WAN Prompt Relay Encode\n"
            f"segments: {len(local_list)}\n"
            f"latent lengths: {','.join(str(x) for x in lengths)}\n"
            f"epsilons: {','.join(f'{x:.6g}' for x in epsilons)}\n"
            f"full prompt tokens: {token_ranges[0][0] if token_ranges else 0}+local"
        )
        return (model_clone, conditioning, summary)


class LHWANSVIDirectorRelayChunk:
    CATEGORY = "LH/WAN SVI"
    MAX_CHUNK_FRAMES = MAX_REF_FRAMES
    RETURN_TYPES = (
        "MODEL", "MODEL", "CONDITIONING", "CONDITIONING", "IMAGE", "INT", "STRING", "BOOLEAN",
        "IMAGE", "BOOLEAN", "FLOAT", "FLOAT", "BOOLEAN",
    )
    RETURN_NAMES = (
        "high_model",
        "low_model",
        "positive_high",
        "positive_low",
        "chunk_ref_image",
        "chunk_latent_frames",
        "summary",
        "anchor_enabled",
        "chunk_end_image",
        "end_anchor_enabled",
        "start_frame_strength",
        "end_frame_strength",
        "continue_previous",
    )
    FUNCTION = "build"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "high_model": ("MODEL",),
                "low_model": ("MODEL",),
                "clip": ("CLIP",),
                "timeline_json": ("STRING", {"forceInput": True}),
                "chunk_index": ("INT", {"default": 1, "min": 1, "max": 12, "step": 1}),
                "chunk_frames": ("INT", {"default": 81, "min": MIN_REF_FRAMES, "max": cls.MAX_CHUNK_FRAMES, "step": 1}),
                "epsilon": ("FLOAT", {"default": 0.001, "min": 0.000001, "max": 1.0, "step": 0.0001}),
                "enabled": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "reference_images": ("IMAGE",),
                "fallback_global_prompt": ("STRING", {"default": "", "multiline": True}),
            }
        }

    def build(
        self,
        high_model,
        low_model,
        clip,
        timeline_json,
        chunk_index,
        chunk_frames,
        epsilon,
        enabled,
        reference_images=None,
        fallback_global_prompt="",
    ):
        if isinstance(chunk_index, torch.Tensor):
            shifted_reference_images = chunk_index
            shifted_chunk_index = chunk_frames
            shifted_chunk_frames = epsilon
            shifted_epsilon = enabled
            shifted_enabled = reference_images if isinstance(reference_images, bool) else True
            reference_images = shifted_reference_images
            chunk_index = shifted_chunk_index
            chunk_frames = shifted_chunk_frames
            epsilon = shifted_epsilon
            enabled = shifted_enabled

        timeline_kind = (
            "LH_WAN_CW_DIRECTOR_TIMELINE"
            if self.MAX_CHUNK_FRAMES > MAX_REF_FRAMES
            else "LH_WAN_SVI_DIRECTOR_TIMELINE"
        )
        data = _normalize_timeline(timeline_json, 81, 16.0, chunk_frames, kind=timeline_kind)
        chunk_index = max(1, min(12, _safe_int(chunk_index, 1)))
        plan = _build_prompt_relay_plan(data, chunk_index, chunk_frames, self.MAX_CHUNK_FRAMES)
        latent_frames = int(plan["chunkLatentFrames"])
        timeline_enabled = _safe_bool(data.get("enablePromptRelay", True), True)
        effective_enabled = _safe_bool(enabled, True) and timeline_enabled
        primary_interval = _select_primary_interval(plan)
        if effective_enabled:
            encode_intervals = list(plan.get("intervals") or [])
        else:
            encode_intervals = [primary_interval] if primary_interval else []
        local_prompts = "|".join(
            str(item.get("prompt") or plan["global_prompt"] or "video") for item in encode_intervals
        )
        segment_lengths = ",".join(str(item.get("latentLength", latent_frames)) for item in encode_intervals)
        segment_epsilons = [
            max(0.000001, min(1.0, _safe_float(item.get("epsilon", epsilon), epsilon)))
            for item in encode_intervals
        ]
        high_model_out, positive_high, high_summary = _encode_prompt_relay(
            high_model,
            clip,
            plan["global_prompt"],
            local_prompts,
            segment_lengths,
            latent_frames,
            segment_epsilons,
            effective_enabled,
            fallback_global_prompt,
        )
        low_model_out, positive_low, low_summary = _encode_prompt_relay(
            low_model,
            clip,
            plan["global_prompt"],
            local_prompts,
            segment_lengths,
            latent_frames,
            segment_epsilons,
            effective_enabled,
            fallback_global_prompt,
        )
        ref_interval = primary_interval if primary_interval and primary_interval.get("kind") == "ref" else None
        ref_image = _load_timeline_ref_image(ref_interval, data, reference_images)
        ref_name = ref_interval.get("image") if ref_interval else ""
        end_ref_name = ref_interval.get("endImage") if ref_interval else ""
        end_ref_image = _resize_crop_image(_load_input_image(end_ref_name or ""), data["width"], data["height"])
        ref_mean = float(ref_image.mean().item()) if isinstance(ref_image, torch.Tensor) and ref_image.numel() else 0.0
        end_ref_mean = float(end_ref_image.mean().item()) if isinstance(end_ref_image, torch.Tensor) and end_ref_image.numel() else 0.0
        anchor_enabled = bool(str(ref_name or "").strip()) and not _is_blank_image(ref_image)
        end_anchor_enabled = bool(str(end_ref_name or "").strip()) and not _is_blank_image(end_ref_image)
        start_strength = max(0.0, min(1.0, _safe_float(ref_interval.get("startStrength", 1.0), 1.0))) if ref_interval else 0.0
        end_strength = max(0.0, min(1.0, _safe_float(ref_interval.get("endStrength", 1.0), 1.0))) if ref_interval else 0.0
        continue_previous = bool(ref_interval.get("continuePrevious", False)) if ref_interval else False
        summary = (
            "LH WAN SVI Director Relay Chunk\n"
            f"chunk {chunk_index}: frames {plan['chunkStartFrame']}-{plan['chunkEndFrame']} "
            f"({plan['chunkRealFrames']} real / {latent_frames} latent)\n"
            f"node epsilon fallback: {_safe_float(epsilon, 0.001):.6g}; effective epsilons: "
            f"{','.join(f'{value:.6g}' for value in segment_epsilons) or '(none)'}\n"
            f"prompt relay: {effective_enabled}\n"
            f"ref_image: {ref_name or '(prompt-only)'} mean={ref_mean:.6f} anchor={anchor_enabled}\n"
            f"end_image: {end_ref_name or '(none)'} mean={end_ref_mean:.6f} anchor={end_anchor_enabled}\n"
            f"frame strengths: start={start_strength:.3f} end={end_strength:.3f}\n"
            f"continue previous: {continue_previous}\n"
            f"{_prompt_plan_summary(plan)}\n\nHigh:\n{high_summary}\n\nLow:\n{low_summary}"
        )
        print(
            f"[LH WAN SVI] director relay chunk {chunk_index} ref_image='{ref_name}' "
            f"mean={ref_mean:.6f} anchor={anchor_enabled} end_image='{end_ref_name}' "
            f"end_mean={end_ref_mean:.6f} end_anchor={end_anchor_enabled}"
        )
        return (
            high_model_out,
            low_model_out,
            positive_high,
            positive_low,
            ref_image,
            latent_frames,
            summary,
            anchor_enabled,
            end_ref_image,
            end_anchor_enabled,
            start_strength,
            end_strength,
            continue_previous,
        )


class LHWANCWDirectorRelayChunk(LHWANSVIDirectorRelayChunk):
    CATEGORY = "LH/WAN CW"
    MAX_CHUNK_FRAMES = 20000


class LHContextWindowI2V:
    CATEGORY = "LH/WAN CW"
    RETURN_TYPES = ("CONDITIONING", "CONDITIONING", "CONDITIONING", "LATENT")
    RETURN_NAMES = ("positive_high", "positive_low", "negative", "latent")
    FUNCTION = "build"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "positive": ("CONDITIONING",),
                "negative": ("CONDITIONING",),
                "vae": ("VAE",),
                "width": ("INT", {"default": 832, "min": 16, "max": 8192, "step": 16}),
                "height": ("INT", {"default": 480, "min": 16, "max": 8192, "step": 16}),
                "length": ("INT", {"default": 81, "min": MIN_CW_REF_FRAMES, "max": 20000, "step": 1}),
                "batch_size": ("INT", {"default": 1, "min": 1, "max": 4096, "step": 1}),
                "motion_influence": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.05}),
                "motion_boost": ("FLOAT", {"default": 1.0, "min": 0.5, "max": 3.0, "step": 0.1}),
                "detail_boost": ("FLOAT", {"default": 1.0, "min": 0.5, "max": 4.0, "step": 0.1}),
                "high_noise_start_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05}),
                "low_noise_start_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05}),
            },
            "optional": {
                "start_image": ("IMAGE",),
                "timeline_json": ("STRING", {"forceInput": True}),
                "reference_images": ("IMAGE",),
            },
        }

    @staticmethod
    def _resize_image(image, width, height):
        if image is None:
            return None
        return comfy.utils.common_upscale(
            image[:1, :, :, :3].movedim(-1, 1), width, height, "bilinear", "center"
        ).movedim(1, -1)

    @staticmethod
    def _apply_motion_boost(anchor_latent, total_latents, motion_boost, detail_boost, motion_influence):
        if anchor_latent is None or total_latents <= 1:
            return None, 0
        trail = max(1, min(total_latents - 1, int(round(detail_boost))))
        motion = anchor_latent.repeat(1, 1, trail, 1, 1)
        motion = motion * max(0.0, _safe_float(motion_influence, 1.0))
        if motion_boost != 1.0:
            motion = motion * max(0.0, _safe_float(motion_boost, 1.0))
        return motion, trail

    def build(
        self,
        positive,
        negative,
        vae,
        width,
        height,
        length,
        batch_size,
        motion_influence,
        motion_boost,
        detail_boost,
        high_noise_start_strength,
        low_noise_start_strength,
        start_image=None,
        timeline_json=None,
        reference_images=None,
    ):
        width = _round_to_multiple(width, 16)
        height = _round_to_multiple(height, 16)
        length = max(MIN_CW_REF_FRAMES, _safe_int(length, 81))
        batch_size = max(1, _safe_int(batch_size, 1))
        spatial_scale = vae.spacial_compression_encode()
        latent_channels = vae.latent_channels
        total_latents = ((length - 1) // 4) + 1
        latent_h = height // spatial_scale
        latent_w = width // spatial_scale
        device = comfy.model_management.intermediate_device()
        latent = torch.zeros([batch_size, latent_channels, total_latents, latent_h, latent_w], device=device)

        start_image = self._resize_image(start_image, width, height)
        if start_image is not None and not _is_blank_image(start_image):
            anchor_latent = vae.encode(start_image[:1, :, :, :3])
        else:
            anchor_latent = torch.zeros([1, latent_channels, 1, latent_h, latent_w], device=device, dtype=latent.dtype)

        image_cond_latent = torch.zeros(
            1, latent_channels, total_latents, latent_h, latent_w,
            dtype=anchor_latent.dtype, device=anchor_latent.device,
        )
        image_cond_latent = comfy.latent_formats.Wan21().process_out(image_cond_latent)
        image_cond_latent[:, :, :1] = anchor_latent

        motion_latent, trail = self._apply_motion_boost(anchor_latent, total_latents, motion_boost, detail_boost, motion_influence)
        if motion_latent is not None and trail > 0:
            image_cond_latent[:, :, 1:1 + trail] = motion_latent[:, :, :trail]

        mask_high = torch.ones((1, 4, total_latents, latent_h, latent_w), device=device, dtype=anchor_latent.dtype)
        mask_low = torch.ones((1, 4, total_latents, latent_h, latent_w), device=device, dtype=anchor_latent.dtype)
        mask_high[:, :, :1] = max(0.0, 1.0 - _safe_float(high_noise_start_strength, 1.0))
        mask_low[:, :, :1] = max(0.0, 1.0 - _safe_float(low_noise_start_strength, 1.0))
        for i in range(1, min(total_latents, 1 + trail)):
            decay = 0.75 ** i
            mask_high[:, :, i:i + 1] = max(0.05, 1.0 - _safe_float(high_noise_start_strength, 1.0) * decay)
            mask_low[:, :, i:i + 1] = max(0.1, 1.0 - _safe_float(low_noise_start_strength, 1.0) * decay * 0.7)

        if timeline_json:
            try:
                data = json.loads(timeline_json)
            except Exception:
                data = {}
            refs = sorted(data.get("refs") or [], key=lambda item: (item.get("startFrame", 0), item.get("endFrame", 0)))
            image_ref_index = 0
            for ref in refs:
                if not ref.get("image"):
                    continue
                source_index = _safe_int(ref.get("sourceIndex", image_ref_index), image_ref_index)
                if isinstance(reference_images, torch.Tensor) and reference_images.ndim >= 4 and reference_images.shape[0] > 0:
                    source_index = max(0, min(reference_images.shape[0] - 1, source_index))
                    ref_image = reference_images[source_index:source_index + 1]
                else:
                    ref_image = _load_input_image(ref.get("image") or "")
                image_ref_index += 1
                if _is_blank_image(ref_image):
                    continue
                ref_image = self._resize_image(ref_image, width, height)
                ref_latent = vae.encode(ref_image[:1, :, :, :3]).to(dtype=image_cond_latent.dtype, device=image_cond_latent.device)
                start_frame = max(0, min(length - 1, _safe_int(ref.get("startFrame", 0), 0)))
                end_frame = max(start_frame + MIN_CW_REF_FRAMES, min(length, _safe_int(ref.get("endFrame", start_frame + MIN_CW_REF_FRAMES), start_frame + MIN_CW_REF_FRAMES)))
                latent_start = max(0, min(total_latents - 1, start_frame // 4))
                latent_end = max(latent_start + 1, min(total_latents, ((end_frame - 1) // 4) + 1))
                start_strength = max(0.0, min(1.0, _safe_float(ref.get("startStrength", high_noise_start_strength), high_noise_start_strength)))
                low_strength = max(0.0, min(1.0, _safe_float(ref.get("endStrength", low_noise_start_strength), low_noise_start_strength)))
                # Keep the anchor tensor and its mask aligned. Previously the
                # mask was relaxed over the whole segment while the concat
                # latent stayed zero after the first frame, introducing
                # partially-conditioned black latent frames at every cut.
                anchor_span = max(2, min(6, int(round(max(1.0, detail_boost))) + 1))
                anchor_stop = min(latent_end, latent_start + anchor_span)
                for pos in range(latent_start, anchor_stop):
                    distance = pos - latent_start
                    decay = 0.72 ** distance
                    image_cond_latent[:, :, pos:pos + 1] = ref_latent
                    mask_high[:, :, pos:pos + 1] = min(mask_high[:, :, pos:pos + 1].min().item(), max(0.08, 1.0 - start_strength * decay))
                    mask_low[:, :, pos:pos + 1] = min(mask_low[:, :, pos:pos + 1].min().item(), max(0.12, 1.0 - low_strength * decay * 0.65))

        positive_high = node_helpers.conditioning_set_values(positive, {
            "concat_latent_image": image_cond_latent,
            "concat_mask": mask_high,
        })
        positive_low = node_helpers.conditioning_set_values(positive, {
            "concat_latent_image": image_cond_latent,
            "concat_mask": mask_low,
        })
        negative_out = node_helpers.conditioning_set_values(negative, {
            "concat_latent_image": image_cond_latent,
            "concat_mask": mask_high,
        })
        return (positive_high, positive_low, negative_out, {"samples": latent})


class LHWANSVIContinuationAnchor:
    CATEGORY = "LH/WAN SVI"
    RETURN_TYPES = ("IMAGE", "IMAGE", "IMAGE", "BOOLEAN", "BOOLEAN", "BOOLEAN", "STRING", "INT")
    RETURN_NAMES = (
        "start_image", "middle_image", "end_image", "enable_start", "enable_middle", "enable_end",
        "summary", "continuation_frames",
    )
    FUNCTION = "build"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "ref_image": ("IMAGE",),
                "anchor_enabled": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "previous_images": ("IMAGE",),
                "continue_previous": ("BOOLEAN", {"default": False}),
            },
        }

    def build(self, ref_image, anchor_enabled, previous_images=None, continue_previous=False):
        if isinstance(anchor_enabled, torch.Tensor):
            shifted_previous_images = anchor_enabled
            shifted_anchor_enabled = previous_images if isinstance(previous_images, bool) else True
            previous_images = shifted_previous_images
            anchor_enabled = shifted_anchor_enabled
        anchor_enabled = _safe_bool(anchor_enabled, True)
        continue_previous = _safe_bool(continue_previous, False)
        has_previous = isinstance(previous_images, torch.Tensor) and previous_images.ndim >= 4 and previous_images.shape[0] > 0
        has_ref = isinstance(ref_image, torch.Tensor) and ref_image.ndim >= 4 and not _is_blank_image(ref_image)
        if has_ref:
            start_image = ref_image[:1]
        elif has_previous:
            start_image = previous_images[-1:]
        else:
            start_image = _blank_image()
        middle_image = start_image
        end_image = start_image
        enable_start = bool(has_previous or (has_ref and anchor_enabled))
        enable_middle = False
        enable_end = False
        # A compact 0/4 switch: off for a new shot, on for a short
        # continuation context in the downstream Wan Advanced I2V node.
        continuation_frames = 4 if has_previous and continue_previous else 0
        summary = (
            "LH WAN SVI Continuation Anchor\n"
            f"previous_last_frame: {has_previous}\n"
            f"start_ref_anchor: {has_ref and bool(anchor_enabled)}\n"
            f"start_uses_current_ref: {has_ref}\n"
            f"start_uses_previous_last_frame: {has_previous and not has_ref}\n"
            f"continue_previous: {continue_previous}\n"
            f"enable start/middle/end: {enable_start}/{enable_middle}/{enable_end}\n"
            f"continuation frames: {continuation_frames}"
        )
        return (
            start_image, middle_image, end_image, enable_start, enable_middle, enable_end,
            summary, continuation_frames,
        )


NODE_CLASS_MAPPINGS = {
    "LHWANSVIDirectorTimeline": LHWANSVIDirectorTimeline,
    "LHWANCWDirectorTimeline": LHWANCWDirectorTimeline,
    "LHWANSVIPromptRelayPlan": LHWANSVIPromptRelayPlan,
    "LHWANPromptRelayEncode": LHWANPromptRelayEncode,
    "LHWANSVIDirectorRelayChunk": LHWANSVIDirectorRelayChunk,
    "LHWANCWDirectorRelayChunk": LHWANCWDirectorRelayChunk,
    "LHContextWindowI2V": LHContextWindowI2V,
    "LHWANSVIContinuationAnchor": LHWANSVIContinuationAnchor,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LHWANSVIDirectorTimeline": "LH WAN SVI Director Timeline",
    "LHWANCWDirectorTimeline": "LH WAN CW Director Timeline",
    "LHWANSVIPromptRelayPlan": "LH WAN SVI Prompt Relay Plan",
    "LHWANPromptRelayEncode": "LH WAN Prompt Relay Encode",
    "LHWANSVIDirectorRelayChunk": "LH WAN SVI Director Relay Chunk",
    "LHWANCWDirectorRelayChunk": "LH WAN CW Director Relay Chunk",
    "LHContextWindowI2V": "LH Context Window I2V",
    "LHWANSVIContinuationAnchor": "LH WAN SVI Continuation Anchor",
}
