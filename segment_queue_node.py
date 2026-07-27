"""
ComfyUI 分段自动队列节点 - 最终版
"""

import math, copy, json, time, os, threading, urllib.request, urllib.error, hashlib, socket
import server, folder_paths
from aiohttp import web
from comfy.cli_args import args

# ── 日志缓冲（前端弹窗读取）──────────────────────────────────────
_sqr_log_buf: dict = {}

def _sqr_log(uid, msg):
    text = "" if msg is None else str(msg)
    print(text)
    if not uid:
        return
    k = str(uid)
    buf = _sqr_log_buf.setdefault(k, [])
    lines = text.splitlines()
    if not lines:
        lines = [""]
    buf.extend(lines)
    if text.endswith("\n"):
        buf.append("")
    if len(buf) > 3000:
        _sqr_log_buf[k] = buf[-3000:]

def _sqr_log_clear(uid):
    _sqr_log_buf.pop(str(uid), None)


def _sqr_format_exc(e: Exception) -> str:
    return f"{type(e).__name__}: {e}"


def _sqr_log_cv2_issue(uid, scene: str, e: Exception):
    detail = _sqr_format_exc(e)
    if isinstance(e, ModuleNotFoundError) and getattr(e, "name", "") == "cv2":
        _sqr_log(uid, f"[SQR] ✗ {scene}: {detail}")
        _sqr_log(uid, "[SQR] ✗ 未安装 cv2 / opencv-python，请安装插件 requirements.txt 中的依赖后重启 ComfyUI。")
    else:
        _sqr_log(uid, f"[SQR] ✗ {scene}: {detail}")


def calc_segments(total_frames: int, segments: int) -> list:
    total_frames = max(0, int(total_frames))
    segments = max(1, int(segments))
    if total_frames <= 0:
        return []
    per_seg = ((math.ceil(total_frames / segments) + 3) // 4) * 4 + 1
    result = []
    for i in range(segments):
        skip = i * per_seg
        if skip >= total_frames:
            break
        if i < segments - 1:
            limit = per_seg
        else:
            remaining = total_frames - skip
            limit = ((remaining + 3) // 4) * 4 + 1
        result.append((skip, limit))
    return result


def parse_director_plan(director_data, total_frames: int) -> list:
    """Return validated, enabled Director segments as (skip, length, config).

    Director ranges use an end-exclusive frame convention.  We intentionally do
    not silently snap hand-picked edit points here: the downstream Wan/SCAIL
    nodes already own their temporal padding, while SQR trims the visible result
    back to the requested source range.
    """
    if not director_data or str(director_data).strip() in ("", "{}"):
        return []
    try:
        data = json.loads(director_data) if isinstance(director_data, str) else director_data
    except Exception as exc:
        raise ValueError(f"Director JSON 无法解析: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise ValueError("Director 数据缺少 segments 数组")

    result = []
    previous_end = 0
    for index, raw in enumerate(data["segments"]):
        if not isinstance(raw, dict) or not _sqr_to_bool(raw.get("enabled", True), True):
            continue
        start = max(0, _sqr_to_int(raw.get("start"), previous_end))
        end = min(max(0, int(total_frames)), _sqr_to_int(raw.get("end"), start + 1))
        if end <= start:
            raise ValueError(f"Director 第 {index + 1} 段范围无效: {start}-{end}")
        if result and start < previous_end:
            raise ValueError(f"Director 第 {index + 1} 段与上一段重叠")
        visible_length = end - start
        # Wan video latents use 4n+1 frame windows.  Read/generate the smallest
        # compatible window, then crop back to the exact hand-authored range.
        model_length = int(math.ceil(max(0, visible_length - 1) / 4.0) * 4) + 1
        config = copy.deepcopy(raw)
        config["start"] = start
        config["end"] = end
        config["id"] = str(config.get("id") or f"seg_{index + 1}")
        config["visible_length"] = visible_length
        config["model_length"] = model_length
        refs = config.get("references", [])
        config["references"] = refs if isinstance(refs, list) else []
        result.append((start, model_length, config))
        previous_end = end
    if not result:
        raise ValueError("Director 没有可执行的有效分段")
    return result


def resolve_director_prompts(director_data) -> list[str]:
    """Resolve per-segment prompts using forward-fill inheritance."""
    try:
        data = json.loads(director_data) if isinstance(director_data, str) else director_data
    except Exception:
        return []
    segments = data.get("segments", []) if isinstance(data, dict) else []
    resolved, previous = [], ""
    for segment in segments:
        if not isinstance(segment, dict) or not _sqr_to_bool(segment.get("enabled", True), True):
            continue
        current = str(segment.get("positive", "") or "").strip()
        if current:
            previous = current
        resolved.append(previous)
    return resolved


def _sqr_director_character_lock_text(segment: dict) -> str:
    if not isinstance(segment, dict):
        return ""
    lock_data = segment.get("character_lock", {})
    if not isinstance(lock_data, dict):
        lock_data = {}
    enabled = _sqr_to_bool(lock_data.get("enabled", True), True)
    descriptions = lock_data.get("descriptions", {})
    if not isinstance(descriptions, dict):
        descriptions = {}
    people = sorted({
        _sqr_ref_entry_person(ref)
        for ref in segment.get("references", [])
        if isinstance(ref, dict) and _sqr_ref_entry_person(ref) > 0 and not _sqr_ref_entry_is_bg(ref)
    })
    lines = []
    for person in people:
        text = str(
            descriptions.get(str(person))
            or descriptions.get(person)
            or ""
        ).strip()
        if text:
            lines.append(f"P{person}: {text}")
    if not enabled or not lines:
        return ""
    result = [
        "Character lock:",
        *lines,
        "Preserve each P character's face, hairstyle, outfit, color palette, and identity from that character's own reference images.",
    ]
    if _sqr_to_bool(lock_data.get("no_swap", True), True) and len(lines) > 1:
        result.append("Do not swap clothes, hair, face, colors, or identity between P characters.")
    return " ".join(result)


def compose_director_positive(base_prompt: str, segment: dict) -> str:
    base = str(base_prompt or "").strip()
    lock_text = _sqr_director_character_lock_text(segment)
    if lock_text:
        return f"{base}\n\n{lock_text}" if base else lock_text
    return base


def resolve_director_composed_prompts(director_data) -> list[str]:
    try:
        data = json.loads(director_data) if isinstance(director_data, str) else director_data
    except Exception:
        return []
    segments = data.get("segments", []) if isinstance(data, dict) else []
    resolved, previous = [], ""
    for segment in segments:
        if not isinstance(segment, dict) or not _sqr_to_bool(segment.get("enabled", True), True):
            continue
        current = str(segment.get("positive", "") or "").strip()
        if current:
            previous = current
        resolved.append(compose_director_positive(previous, segment))
    return resolved


def first_director_guide_path(director_data) -> str:
    """Return the first existing extracted Director scale-guide path."""
    try:
        data = json.loads(director_data) if isinstance(director_data, str) else director_data
    except Exception:
        data = {}
    segments = data.get("segments", []) if isinstance(data, dict) else []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        guide = segment.get("guide_frame")
        guide_path = guide.get("path", "") if isinstance(guide, dict) else str(guide or "")
        path = _sqr_resolve_media_path(guide_path) if guide_path else None
        if path and os.path.isfile(path):
            return guide_path
    return ""


def first_director_color_match_config(director_data) -> dict:
    """Resolve the first guide segment and its first reference/color settings."""
    try:
        data = json.loads(director_data) if isinstance(director_data, str) else director_data
    except Exception:
        data = {}
    segments = data.get("segments", []) if isinstance(data, dict) else []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        guide = segment.get("guide_frame")
        guide_path = guide.get("path", "") if isinstance(guide, dict) else str(guide or "")
        if not guide_path or not os.path.isfile(_sqr_resolve_media_path(guide_path) or ""):
            continue
        references = segment.get("references", [])
        first_ref = references[0] if isinstance(references, list) and references else ""
        ref_path = _sqr_ref_entry_path(first_ref)
        if ref_path and not os.path.isfile(_sqr_resolve_media_path(ref_path) or ""):
            ref_path = ""
        ref_strength = first_ref.get("color_match_strength") if isinstance(first_ref, dict) else None
        strength_value = ref_strength if ref_strength is not None else segment.get("color_match_strength", 1.0)
        return {
            "guide_path": guide_path,
            "reference_path": ref_path,
            "enabled": _sqr_to_bool(segment.get("color_match", False)),
            "strength": max(0.0, min(10.0, float(strength_value if strength_value is not None else 1.0))),
        }
    return {"guide_path": "", "reference_path": "", "enabled": False, "strength": 1.0}


def _sqr_color_match_tensor(image_target, image_ref, strength=1.0):
    """Apply ColorMatchV2's default MKL method and strength formula."""
    import torch
    from color_matcher import ColorMatcher

    target_np = image_target[0].cpu().numpy()
    ref_np = image_ref[0].cpu().numpy()
    result = ColorMatcher().transfer(src=target_np, ref=ref_np, method="mkl")
    strength = max(0.0, min(10.0, float(strength)))
    if strength != 1.0:
        result = target_np + strength * (result - target_np)
    return torch.from_numpy(result).to(torch.float32).clamp_(0, 1).unsqueeze(0)


def _sqr_load_image_tensor(path):
    import numpy as np
    import torch
    from PIL import Image, ImageOps

    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        array = np.asarray(image, dtype=np.float32) / 255.0
    return torch.from_numpy(array).unsqueeze(0)


def load_first_director_guide_frame(director_data):
    """Return the first extracted Director scale-guide frame as an IMAGE tensor."""
    import torch

    config = first_director_color_match_config(director_data)
    guide_path = _sqr_resolve_media_path(config["guide_path"]) if config["guide_path"] else None
    if guide_path and os.path.isfile(guide_path):
        guide = _sqr_load_image_tensor(guide_path)
        ref_path = _sqr_resolve_media_path(config["reference_path"]) if config["reference_path"] else None
        if config["enabled"] and ref_path and os.path.isfile(ref_path):
            return _sqr_color_match_tensor(_sqr_load_image_tensor(ref_path), guide, config["strength"])
        return guide
    # Keep the IMAGE socket valid before any guide frame has been extracted.
    return torch.zeros((1, 1, 1, 3), dtype=torch.float32)


def replace_director_positive_links(workflow: dict, director_node_id, value: str) -> int:
    """Replace links from WanAniDirector.positive with the current segment text."""
    changed = 0
    source_id = str(director_node_id)
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        for key, input_value in list(inputs.items()):
            if (isinstance(input_value, list) and len(input_value) == 2
                    and str(input_value[0]) == source_id
                    and _sqr_to_int(input_value[1], -1) == 0):
                inputs[key] = value
                changed += 1
    return changed


def _sqr_to_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on", "enabled"):
            return True
        if text in ("0", "false", "no", "off", "disabled", ""):
            return False
    return default


def _sqr_to_int(value, default=0):
    try:
        if value is None:
            return default
        if isinstance(value, str) and not value.strip():
            return default
        return int(value)
    except Exception:
        return default


# ── 速度记录（预计时长）──
_SPEED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sqr_speed.json')

def load_speed_record():
    try:
        if os.path.exists(_SPEED_FILE):
            with open(_SPEED_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return None

# ── checkpoint 断点保护 ──────────────────────────────────────────
def get_checkpoint_path(unique_id):
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(plugin_dir, f"sqr_checkpoint_{unique_id}.json")

def write_checkpoint(unique_id, data):
    try:
        with open(get_checkpoint_path(unique_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[SQR] checkpoint 写入失败: {e}")

def read_checkpoint(unique_id):
    try:
        p = get_checkpoint_path(unique_id)
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def clear_checkpoint(unique_id):
    try:
        p = get_checkpoint_path(unique_id)
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


def _sqr_is_managed_ref_path(path: str | None, unique_id=None) -> bool:
    base = os.path.basename(str(path or ""))
    if not base:
        return False
    prefixes = ["sqr_refkeep_", "sqr_refsnap_"]
    if unique_id:
        prefixes = [f"sqr_refkeep_{unique_id}_", f"sqr_refsnap_{unique_id}_"]
    return any(base.startswith(pref) for pref in prefixes)


def _sqr_cleanup_ref_images(paths, unique_id=None, keep_paths=None):
    def _entry_path(v):
        if isinstance(v, dict):
            return str(v.get("path") or v.get("image") or v.get("file") or "").strip()
        return str(v or "").strip()
    keep = {os.path.realpath(_entry_path(p)) for p in (keep_paths or []) if _entry_path(p)}
    input_dir = os.path.realpath(folder_paths.get_input_directory())
    for raw in paths or []:
        p = _entry_path(raw)
        if not p or not _sqr_is_managed_ref_path(p, unique_id=unique_id):
            continue
        real = os.path.realpath(p)
        if real in keep:
            continue
        try:
            if os.path.commonpath([real, input_dir]) != input_dir:
                continue
        except Exception:
            continue
        try:
            if os.path.exists(real):
                os.remove(real)
                print(f"[SQR] 已清理 checkpoint 参考图: {os.path.basename(real)}")
        except Exception:
            pass


def _sqr_prepare_checkpoint_ref_images(ref_images_list, unique_id=None):
    if not ref_images_list:
        return []
    input_dir = folder_paths.get_input_directory()
    os.makedirs(input_dir, exist_ok=True)
    keep_list = []
    stamp = _sqr_now_stamp()
    import shutil as _snap_shutil
    for idx, raw in enumerate(ref_images_list, start=1):
        src = _sqr_resolve_media_path(raw) or str(raw or "").strip()
        if not src:
            continue
        src_real = os.path.realpath(src)
        if _sqr_is_managed_ref_path(src_real, unique_id=unique_id) and os.path.isfile(src_real):
            keep_list.append(src_real)
            continue
        if os.path.isfile(src_real):
            keep_name = f"sqr_refkeep_{unique_id}_{stamp}_{idx:02d}_{os.path.basename(src_real)}" if unique_id else f"sqr_refkeep_{stamp}_{idx:02d}_{os.path.basename(src_real)}"
            keep_dst = os.path.join(input_dir, keep_name)
            try:
                _snap_shutil.copy2(src_real, keep_dst)
                keep_list.append(keep_dst)
            except Exception as e:
                print(f"[SQR] ⚠ 参考图持久化失败({os.path.basename(src_real)}): {e}")
                keep_list.append(src_real)
        else:
            keep_list.append(str(raw))
    return keep_list


def _sqr_ref_entry_path(entry):
    if isinstance(entry, dict):
        return str(entry.get("path") or entry.get("image") or entry.get("file") or "").strip()
    return str(entry or "").strip()


def _sqr_ref_entry_is_bg(entry):
    return bool(isinstance(entry, dict) and (entry.get("bg") or entry.get("background") or entry.get("is_bg")))


def _sqr_ref_entry_person(entry):
    if not isinstance(entry, dict):
        return 0
    for key in ("person", "person_id", "identity", "group"):
        value = entry.get(key)
        try:
            value = int(value)
        except Exception:
            continue
        if value > 0:
            return value
    return 0


def _sqr_ref_identity_groups(ref_entries, max_refs=6):
    grouped = {}
    for index, entry in enumerate((ref_entries or [])[:max_refs]):
        if _sqr_ref_entry_is_bg(entry):
            continue
        person = _sqr_ref_entry_person(entry)
        if person > 0:
            grouped.setdefault(person, []).append(index)
    groups = [indexes for _, indexes in sorted(grouped.items()) if indexes]
    if len(groups) < 2:
        return "", 0
    return "|".join(",".join(str(index) for index in group) for group in groups), len(groups)


def _sqr_make_ref_entry(path, is_bg=False, template=None):
    path = str(path or "").strip()
    if isinstance(template, dict):
        result = copy.deepcopy(template)
        result["path"] = path
        result.pop("image", None)
        result.pop("file", None)
        result["background"] = bool(is_bg)
        result.pop("bg", None)
        result.pop("is_bg", None)
        return result
    return {"path": path, "bg": True} if is_bg else path


def _sqr_prepare_checkpoint_ref_entries(ref_entries, unique_id=None):
    if not ref_entries:
        return []
    prepared = []
    for entry in ref_entries:
        path = _sqr_ref_entry_path(entry)
        if not path:
            continue
        kept = _sqr_prepare_checkpoint_ref_images([path], unique_id=unique_id)
        if kept:
            prepared.append(_sqr_make_ref_entry(kept[0], _sqr_ref_entry_is_bg(entry), entry))
    return prepared

_SQR_COMFY_HOST_CACHE = None


def _sqr_now_stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S") + f"_{int((time.time() % 1) * 1000):03d}"


def _sqr_transition_seg_from_name(fname: str):
    import re
    patterns = [
        r"^sqr_trans_[0-9_]+_seg(\d+)\.mp4$",
        r"^sqr_trans_[a-f0-9]+_seg(\d+)\.mp4$",
        r"^segment_transition_seg(\d+)\.mp4$",
    ]
    for pat in patterns:
        m = re.match(pat, fname, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def _sqr_unique_filepath(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    while True:
        cand = f"{base}_{_sqr_now_stamp()}{ext}"
        if not os.path.exists(cand):
            return cand
        time.sleep(0.002)


def _sqr_collect_comfy_hosts() -> list[str]:
    candidates = []
    seen = set()

    def add(host, port):
        if port in (None, ""):
            return
        try:
            port = int(port)
        except Exception:
            return
        host = str(host or "").strip()
        if host in ("", "0.0.0.0", "::", "[::]"):
            host = "127.0.0.1"
        if host.startswith("http://") or host.startswith("https://"):
            host = host.split("://", 1)[1]
        host = host.strip("/ ")
        key = f"{host}:{port}"
        if key not in seen:
            seen.add(key)
            candidates.append(key)

    inst = getattr(getattr(server, "PromptServer", None), "instance", None)
    if inst is not None:
        add(getattr(inst, "address", None), getattr(inst, "port", None))
        add(getattr(inst, "host", None), getattr(inst, "port", None))
        srv = getattr(inst, "server", None)
        if srv is not None:
            add(getattr(srv, "address", None), getattr(srv, "port", None))
            add(getattr(srv, "host", None), getattr(srv, "port", None))

    add(getattr(args, "listen", None), getattr(args, "port", None))
    add("127.0.0.1", getattr(args, "port", None))
    add("localhost", getattr(args, "port", None))

    add(os.environ.get("COMFYUI_HOST"), os.environ.get("COMFYUI_PORT"))
    add(os.environ.get("SERVER_HOST"), os.environ.get("SERVER_PORT"))

    for port in (8188, 8000, 9000, 8080):
        add("127.0.0.1", port)
        add("localhost", port)
    return candidates


def _sqr_probe_comfy_host(host: str) -> bool:
    for ep in ("/system_stats", "/queue", "/object_info", "/features"):
        try:
            with urllib.request.urlopen(f"http://{host}{ep}", timeout=1.2) as resp:
                code = getattr(resp, "status", 200)
                if code < 500:
                    return True
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return True
        except Exception:
            continue
    return False


def _sqr_get_comfy_host(force_refresh: bool = False) -> str:
    global _SQR_COMFY_HOST_CACHE
    if _SQR_COMFY_HOST_CACHE and not force_refresh:
        return _SQR_COMFY_HOST_CACHE
    for cand in _sqr_collect_comfy_hosts():
        if _sqr_probe_comfy_host(cand):
            _SQR_COMFY_HOST_CACHE = cand
            return cand
    _SQR_COMFY_HOST_CACHE = "127.0.0.1:8188"
    return _SQR_COMFY_HOST_CACHE


def _build_safe_input_copy_name(src_path: str, unique_id=None, prefix: str = "sqr_ref") -> str:
    try:
        real = os.path.realpath(src_path)
        st = os.stat(real)
        sig_src = f"{real}|{st.st_mtime_ns}|{st.st_size}"
    except Exception:
        real = os.path.realpath(src_path)
        sig_src = real
    sig = hashlib.sha1(sig_src.encode("utf-8", errors="ignore")).hexdigest()[:12]
    base = os.path.basename(src_path)
    if unique_id:
        return f"{prefix}_{unique_id}_{sig}_{base}"
    return f"{prefix}_{sig}_{base}"




def _sqr_media_roots() -> list[str]:
    roots = []
    seen = set()
    for getter_name in ("get_input_directory", "get_output_directory", "get_temp_directory"):
        getter = getattr(folder_paths, getter_name, None)
        if not callable(getter):
            continue
        try:
            p = getter()
        except Exception:
            continue
        if not p:
            continue
        rp = os.path.realpath(str(p))
        if rp not in seen:
            seen.add(rp)
            roots.append(rp)
    return roots


def _sqr_resolve_media_path(path: str | None) -> str | None:
    raw = str(path or "").strip().strip('"').strip("'")
    if not raw:
        return None

    if os.path.isfile(raw):
        return os.path.realpath(raw)

    try:
        ann = folder_paths.get_annotated_filepath(raw)
        if ann and os.path.isfile(ann):
            return os.path.realpath(ann)
    except Exception:
        pass

    candidates = []
    seen = set()

    def add_candidate(p):
        if not p:
            return
        rp = os.path.realpath(p)
        if rp not in seen:
            seen.add(rp)
            candidates.append(rp)

    if os.path.isabs(raw):
        add_candidate(raw)
    else:
        add_candidate(raw)
        base = os.path.basename(raw)
        for root in _sqr_media_roots():
            add_candidate(os.path.join(root, raw))
            if base != raw:
                add_candidate(os.path.join(root, base))

    for cand in candidates:
        if os.path.isfile(cand):
            return cand

    base = os.path.basename(raw)
    if base == raw:
        for root in _sqr_media_roots():
            try:
                for dirpath, _, files in os.walk(root):
                    if base in files:
                        return os.path.realpath(os.path.join(dirpath, base))
            except Exception:
                continue
    return None


def _sqr_copy_into_input(src_path: str, desired_name: str | None = None,
                         unique_id=None, prefix: str = "sqr_copy") -> str:
    src_real = _sqr_resolve_media_path(src_path) or os.path.realpath(str(src_path))
    if not os.path.isfile(src_real):
        raise FileNotFoundError(src_path)

    input_dir = folder_paths.get_input_directory()
    os.makedirs(input_dir, exist_ok=True)

    if os.path.realpath(os.path.dirname(src_real)) == os.path.realpath(input_dir):
        return src_real

    name = (desired_name or "").strip() or os.path.basename(src_real)
    dst = os.path.join(input_dir, name)

    try:
        if os.path.exists(dst) and os.path.samefile(src_real, dst):
            return dst
    except Exception:
        pass

    if os.path.exists(dst):
        if desired_name:
            dst = _sqr_unique_filepath(dst)
        else:
            safe_name = _build_safe_input_copy_name(src_real, unique_id=unique_id, prefix=prefix)
            dst = os.path.join(input_dir, safe_name)

    import shutil
    shutil.copy2(src_real, dst)
    return dst
def save_speed_record(total_secs, total_frames_run):
    if total_frames_run <= 0 or total_secs <= 0:
        return
    try:
        from datetime import datetime
        with open(_SPEED_FILE, 'w') as f:
            json.dump({'spf': round(total_secs / total_frames_run, 4),
                       'date': datetime.now().strftime('%Y-%m-%d %H:%M')}, f)
    except Exception:
        pass


def build_plan_text(total_frames, segments, start_from_segment, node_id, frame_rate,
                    seg_list_override=None):
    if total_frames <= 0:
        return "✗ total_frames 必须大于 0。"
    if seg_list_override is not None:
        seg_list = seg_list_override
    else:
        seg_list = calc_segments(total_frames, segments)
    start_from_segment = max(1, min(start_from_segment, len(seg_list)))
    start_idx = start_from_segment - 1
    SEP = "═" * 45
    lines = [
        f"参考视频节点：{node_id}  总帧数：{total_frames}  模式：平均分段",
        f"共 {len(seg_list)} 段，从第 {start_from_segment} 段开始",
        "",
    ]
    for i, (skip, limit) in enumerate(seg_list):
        status = "→ 执行" if i >= start_idx else "  跳过"
        audio_s = skip / frame_rate if frame_rate > 0 else 0
        lines.append(f"  第{i+1}段 skip={skip} limit={limit} 音频={audio_s:.2f}s  {status}")
    lines.append(SEP)
    lines.append("")
    speed = load_speed_record()
    frames_to_run = sum(lmt for ii, (_, lmt) in enumerate(seg_list) if ii >= start_idx)
    segs_to_run_n = len(seg_list) - start_idx
    if speed and frames_to_run > 0:
        est = speed['spf'] * frames_to_run
        est_str = f"{est/3600:.1f}h" if est >= 3600 else f"{est/60:.0f}分钟"
        spf_str = f"{speed['spf']:.1f}s/帧"
        date_str = speed['date']
        lines.append(f"预计执行 {segs_to_run_n} 段约 {est_str}（基于 {date_str} 记录的 {spf_str}，实际因分辨率/步数等可能不同）")
    return "\n".join(lines)


def find_video_combine_node(prompt: dict, combine_node_id: str) -> str | None:
    nid = combine_node_id.strip()
    if nid and nid in prompt:
        return nid
    for nid, node in prompt.items():
        if node.get("class_type") == "VHS_VideoCombine":
            inputs = node.get("inputs", {})
            if inputs.get("save_output") is True:
                return nid
    return None


def find_audio_filename(prompt: dict, node_id: str) -> str | None:
    node = prompt.get(node_id, {})
    inputs = node.get("inputs", {})
    video = inputs.get("video", "")
    if video and isinstance(video, str):
        return video
    return None


def find_latent_source_for_images(prompt: dict, image_src_node):
    if not (isinstance(image_src_node, list) and len(image_src_node) == 2):
        return None
    nid = str(image_src_node[0])
    node = prompt.get(nid, {})
    class_type = node.get("class_type", "") if isinstance(node, dict) else ""
    inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
    if class_type in ("VAEDecode", "VAEDecodeTiled"):
        samples = inputs.get("samples")
        if isinstance(samples, list) and len(samples) == 2:
            return samples
    if class_type == "ImageFromBatch":
        return find_latent_source_for_images(prompt, inputs.get("image"))
    return None


def find_animate_embeds_node(prompt: dict) -> str | None:
    for nid, node in prompt.items():
        if node.get("class_type") in ("WanVideoAnimateEmbeds", "WanAnimateToVideo", "SQRWanAnimateTransitionToVideo", "SQRSCAIL2TransitionToVideo"):
            return nid
    for nid, node in prompt.items():
        inputs = node.get("inputs", {})
        if isinstance(inputs, dict) and "transition_video" in inputs:
            return nid
    return None


def find_multi_reference_node(prompt: dict) -> str | None:
    for nid, node in prompt.items():
        if node.get("class_type") in ("WanSQRMultiReference", "SQRScail2ReferenceBatchStack"):
            return nid
    return None


def find_driving_sam3_node(prompt: dict, video_node_id: str) -> str | None:
    """Find the SAM3 tracker whose image input comes from the driving video."""
    source_id = str(video_node_id)
    fallback = None
    for nid, node in prompt.items():
        if node.get("class_type") != "SAM3_VideoTrack":
            continue
        fallback = fallback or str(nid)
        images = node.get("inputs", {}).get("images")
        if isinstance(images, list) and len(images) == 2 and str(images[0]) == source_id:
            return str(nid)
    return fallback


def media_has_audio(path: str | None) -> bool:
    """Return True only when the media file contains at least one audio stream."""
    if not path or not os.path.isfile(path):
        return False
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    ffprobe = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe" if os.name == "nt" else "ffprobe")
    if not os.path.isfile(ffprobe):
        ffprobe = shutil.which("ffprobe")
    try:
        if ffprobe:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index", "-of", "csv=p=0", path],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20,
        )
        return "Audio:" in (result.stderr or "")
    except Exception:
        return False


def _sqr_rewire_image_output(prompt: dict, old_ref, new_ref):
    changed = 0
    for nid, node in prompt.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        for key, value in list(inputs.items()):
            if value == old_ref:
                inputs[key] = new_ref
                changed += 1
    return changed


def _sqr_add_to_input_or_linked_value(prompt: dict, node_id: str, input_name: str, delta: int):
    try:
        delta = int(delta)
    except Exception:
        return False, ""
    if delta == 0 or node_id not in prompt:
        return False, ""
    inputs = prompt.get(node_id, {}).get("inputs", {})
    if not isinstance(inputs, dict) or input_name not in inputs:
        return False, ""
    value = inputs.get(input_name)
    if isinstance(value, int):
        inputs[input_name] = value + delta
        return True, f"{node_id}.{input_name}: {value}->{inputs[input_name]}"
    if isinstance(value, float) and value.is_integer():
        inputs[input_name] = int(value) + delta
        return True, f"{node_id}.{input_name}: {int(value)}->{inputs[input_name]}"
    if isinstance(value, list) and value:
        linked_id = str(value[0])
        linked = prompt.get(linked_id, {})
        linked_inputs = linked.get("inputs", {}) if isinstance(linked, dict) else {}
        if isinstance(linked_inputs, dict):
            for key in ("value", "int", "length", "frame_count", "frames"):
                linked_value = linked_inputs.get(key)
                if isinstance(linked_value, int):
                    linked_inputs[key] = linked_value + delta
                    return True, f"{linked_id}.{key}: {linked_value}->{linked_inputs[key]}"
                if isinstance(linked_value, float) and linked_value.is_integer():
                    linked_inputs[key] = int(linked_value) + delta
                    return True, f"{linked_id}.{key}: {int(linked_value)}->{linked_inputs[key]}"
    return False, f"{node_id}.{input_name}"


def _sqr_node_supports_transition(prompt: dict, node_id: str) -> tuple[bool, str]:
    node = prompt.get(node_id, {}) if node_id else {}
    class_type = node.get("class_type", "") if isinstance(node, dict) else ""
    inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
    supported = class_type in ("WanVideoAnimateEmbeds", "SQRWanAnimateTransitionToVideo", "SQRSCAIL2TransitionToVideo")
    if not supported and isinstance(inputs, dict) and "transition_video" in inputs:
        supported = True
    return supported, class_type


def queue_prompt(workflow, host=None, client_id="") -> str:
    payload = json.dumps({"prompt": workflow, "client_id": client_id}).encode("utf-8")
    last_err = None
    for _host in [host or _sqr_get_comfy_host(), _sqr_get_comfy_host(force_refresh=True)]:
        try:
            req = urllib.request.Request(
                f"http://{_host}/prompt", data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())["prompt_id"]
        except Exception as e:
            last_err = e
    raise last_err


def wait_for_prompt(prompt_id, host=None, poll=5) -> bool:
    while True:
        time.sleep(poll)
        for _host in [host or _sqr_get_comfy_host(), _sqr_get_comfy_host(force_refresh=True)]:
            try:
                with urllib.request.urlopen(f"http://{_host}/history/{prompt_id}", timeout=10) as resp:
                    history = json.loads(resp.read())
                if prompt_id in history:
                    st = history[prompt_id].get("status", {})
                    if st.get("completed"):
                        return True
                    if st.get("status_str") == "error":
                        return False
                    break
            except Exception:
                continue


def get_output_video_info(prompt_id, combine_node_id, host=None, logger=None):
    last_err = None
    for _host in [host or _sqr_get_comfy_host(), _sqr_get_comfy_host(force_refresh=True)]:
        try:
            with urllib.request.urlopen(f"http://{_host}/history/{prompt_id}", timeout=10) as resp:
                history = json.loads(resp.read())
            node_out = history.get(prompt_id, {}).get("outputs", {}).get(str(combine_node_id), {})
            gifs = node_out.get("gifs", [])
            if not gifs:
                return None, None
            gi = gifs[0]
            base_dir = folder_paths.get_output_directory() if gi.get("type") == "output" \
                       else folder_paths.get_input_directory()
            subfolder = gi.get("subfolder", "")
            video_path = os.path.join(base_dir, subfolder, gi["filename"]) if subfolder \
                         else os.path.join(base_dir, gi["filename"])
            import cv2
            cap = cv2.VideoCapture(video_path)
            try:
                frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if cap.isOpened() else None
            finally:
                cap.release()
            return video_path, frames
        except Exception as e:
            last_err = e
    msg = f"✗ 获取视频信息失败: {_sqr_format_exc(last_err)}" if last_err else "✗ 获取视频信息失败"
    if logger:
        logger(msg)
    else:
        print(f"[SQR] {msg}")
    return None, None


def get_output_latent_info(prompt_id, latent_node_id, host=None, logger=None):
    last_err = None
    for _host in [host or _sqr_get_comfy_host(), _sqr_get_comfy_host(force_refresh=True)]:
        try:
            with urllib.request.urlopen(f"http://{_host}/history/{prompt_id}", timeout=10) as resp:
                history = json.loads(resp.read())
            node_out = history.get(prompt_id, {}).get("outputs", {}).get(str(latent_node_id), {})
            latents = node_out.get("latents", [])
            if not latents:
                return None
            li = latents[0]
            base_dir = folder_paths.get_output_directory() if li.get("type") == "output" \
                       else folder_paths.get_input_directory()
            subfolder = li.get("subfolder", "")
            return os.path.join(base_dir, subfolder, li["filename"]) if subfolder \
                   else os.path.join(base_dir, li["filename"])
        except Exception as e:
            last_err = e
    msg = f"✗ 获取 latent 信息失败: {_sqr_format_exc(last_err)}" if last_err else "✗ 获取 latent 信息失败"
    if logger:
        logger(msg)
    else:
        print(f"[SQR] {msg}")
    return None


def _sqr_copy_latent_into_input(path: str, unique_id=None, seg_num=None) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    input_dir = folder_paths.get_input_directory()
    os.makedirs(input_dir, exist_ok=True)
    tag = f"_{unique_id}" if unique_id else ""
    seg = f"_seg{seg_num}" if seg_num is not None else ""
    dst_name = f"sqr_latent{tag}{seg}_{_sqr_now_stamp()}.latent"
    dst = os.path.join(input_dir, dst_name)
    import shutil
    shutil.copy2(path, dst)
    return dst_name


def interrupt_current(host=None):
    for _host in [host or _sqr_get_comfy_host(), _sqr_get_comfy_host(force_refresh=True)]:
        try:
            urllib.request.urlopen(
                urllib.request.Request(f"http://{_host}/interrupt", data=b"", method="POST"), timeout=10)
            return
        except Exception:
            continue


TRANSITION_FRAMES = 32
SCAIL2_TRANSITION_FRAMES = 17
KEEP_FULL_TRANSITION_IN_MERGE = True
MULTI_REF_STARTUP_TRIM_FRAMES = 9


def _sqr_transition_frame_count(class_type: str) -> int:
    if class_type == "SQRSCAIL2TransitionToVideo":
        return SCAIL2_TRANSITION_FRAMES
    return TRANSITION_FRAMES


def _sqr_transition_added_frames(class_type: str) -> int:
    if class_type == "SQRSCAIL2TransitionToVideo":
        return SCAIL2_TRANSITION_FRAMES - 1
    return TRANSITION_FRAMES


class SQRReplaceBatchPrefix:
    """Replace VAE-decoded carry frames with the original decoded video frames."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "prefix_images": ("IMAGE",),
                "prefix_length": ("INT", {"default": 32, "min": 0, "max": 4096}),
                "prefix_start": ("INT", {"default": 0, "min": 0, "max": 16}),
                "color_release_frames": ("INT", {"default": 20, "min": 0, "max": 64}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "replace"
    CATEGORY = "video/utils"

    def replace(self, images, prefix_images, prefix_length, prefix_start,
                color_release_frames):
        import torch

        start = min(max(0, int(prefix_start)), prefix_images.shape[0])
        count = min(int(prefix_length), images.shape[0], prefix_images.shape[0] - start)
        if count <= 0:
            return (images,)

        prefix = prefix_images[start:start + count]
        if prefix.shape[1:3] != images.shape[1:3]:
            prefix = torch.nn.functional.interpolate(
                prefix.movedim(-1, 1),
                size=images.shape[1:3],
                mode="area",
            ).movedim(1, -1)
        prefix = prefix.to(device=images.device, dtype=images.dtype)

        if start > 0:
            boundary = prefix_images[start - 1:start]
            if boundary.shape[1:3] != images.shape[1:3]:
                boundary = torch.nn.functional.interpolate(
                    boundary.movedim(-1, 1),
                    size=images.shape[1:3],
                    mode="area",
                ).movedim(1, -1)
            boundary = boundary.to(device=images.device, dtype=images.dtype).float()
            dims = (0, 1, 2)
            boundary_mean = boundary.mean(dim=dims, keepdim=True)
            boundary_std = boundary.std(dim=dims, keepdim=True).clamp_min(1e-4)
            seam_release = min(8, prefix.shape[0])
            for frame_index in range(seam_release):
                frame = prefix[frame_index:frame_index + 1].float()
                frame_mean = frame.mean(dim=dims, keepdim=True)
                frame_std = frame.std(dim=dims, keepdim=True).clamp_min(1e-4)
                gain = (boundary_std / frame_std).clamp(0.85, 1.18)
                offset = (boundary_mean - frame_mean * gain).clamp(-0.12, 0.12)
                progress = frame_index / max(1, seam_release - 1)
                amount = 1.0 - progress * progress * (3.0 - 2.0 * progress)
                prefix[frame_index:frame_index + 1] = torch.lerp(
                    frame, frame * gain + offset, amount
                ).clamp(0.0, 1.0).to(prefix.dtype)

        suffix = images[count:].clone()

        release_count = min(int(color_release_frames), suffix.shape[0])
        if release_count > 0:
            # Match only low-frequency RGB statistics at the release boundary.
            # The correction fades out while every generated frame keeps its own
            # pose and detail, avoiding a frozen-image crossfade.
            # A short average is more stable than using only the final carry
            # frame, which may contain motion blur or a lighting outlier.
            target = prefix[-min(4, prefix.shape[0]):].float()
            dims = (0, 1, 2)
            target_mean = target.mean(dim=dims, keepdim=True)
            target_std = target.std(dim=dims, keepdim=True).clamp_min(1e-4)

            for frame_index in range(release_count):
                frame = suffix[frame_index:frame_index + 1].float()
                source_mean = frame.mean(dim=dims, keepdim=True)
                source_std = frame.std(dim=dims, keepdim=True).clamp_min(1e-4)
                gain = (target_std / source_std).clamp(0.80, 1.25)
                offset = (target_mean - source_mean * gain).clamp(-0.15, 0.15)
                corrected = frame * gain + offset

                # Smoothstep prevents a visible change in correction strength at
                # either end of the release interval.
                progress = frame_index / max(1, release_count - 1)
                amount = 1.0 - progress * progress * (3.0 - 2.0 * progress)
                suffix[frame_index:frame_index + 1] = torch.lerp(
                    frame,
                    corrected,
                    amount,
                ).clamp(0.0, 1.0).to(suffix.dtype)

        return (torch.cat((prefix, suffix), dim=0),)


class SQRImageBatchConcat:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images_a": ("IMAGE",),
                "images_b": ("IMAGE",),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "concat"
    CATEGORY = "video/utils"

    def concat(self, images_a, images_b):
        import torch
        if images_a.shape[0] == 0:
            return (images_b,)
        if images_b.shape[0] == 0:
            return (images_a,)
        if images_a.shape[1:3] != images_b.shape[1:3]:
            images_b = torch.nn.functional.interpolate(
                images_b.movedim(-1, 1),
                size=images_a.shape[1:3],
                mode="area",
            ).movedim(1, -1)
        images_b = images_b.to(device=images_a.device, dtype=images_a.dtype)
        return (torch.cat((images_a, images_b), dim=0),)


class SQRRepeatFirstFrames:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "repeat_count": ("INT", {"default": 9, "min": 0, "max": 128}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "execute"
    CATEGORY = "video/utils"

    def execute(self, images, repeat_count):
        import torch
        repeat_count = max(0, int(repeat_count))
        if repeat_count <= 0 or images.shape[0] == 0:
            return (images,)
        prefix = images[:1].repeat((repeat_count, 1, 1, 1))
        return (torch.cat((prefix, images), dim=0),)


def merge_videos(video_paths: list, output_path: str, target_fps: float = None,
                 source_audio_path: str = None, total_frames: int = None,
                 source_fps: float = None) -> bool:
    import subprocess, tempfile
    if not video_paths:
        return False

    replace_audio = bool(source_audio_path and os.path.isfile(source_audio_path)
                         and source_fps and source_fps > 0
                         and total_frames and total_frames > 0)
    concat_output = tempfile.mktemp(suffix=".mp4") if replace_audio else output_path
    list_path = None
    converted = []
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False, encoding="utf-8") as f:
            for p in video_paths:
                f.write("file " + repr(p) + "\n")
            list_path = f.name

        if target_fps and target_fps > 0:
            fps_str = f"{target_fps:.6f}".rstrip("0").rstrip(".")
            for vp in video_paths:
                tmp = tempfile.mktemp(suffix=".mp4")
                cv_cmd = ["ffmpeg", "-y", "-i", vp,
                          "-r", fps_str,
                          "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                          "-c:a", "copy", tmp]
                r2 = subprocess.run(cv_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
                converted.append(tmp if r2.returncode == 0 else vp)
            with open(list_path, "w", encoding="utf-8") as lf:
                for p in converted:
                    lf.write("file " + repr(p) + "\n")

        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
               "-i", list_path, "-c", "copy", concat_output]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            print(f"[SQR] ffmpeg concat error: {result.stderr[-300:]}")
            return False

        if not replace_audio:
            return True

        duration = total_frames / source_fps
        remux_cmd = [
            "ffmpeg", "-y", "-i", concat_output, "-i", source_audio_path,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration:.9f}", "-movflags", "+faststart", output_path,
        ]
        remux_result = subprocess.run(remux_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if remux_result.returncode == 0:
            return True
        print(f"[SQR] ffmpeg audio remux error: {remux_result.stderr[-300:]}")
        return False
    except FileNotFoundError:
        print("[SQR] ffmpeg executable was not found")
        return False
    except Exception as e:
        print(f"[SQR] merge error: {e}")
        return False
    finally:
        for temp_path in [list_path, concat_output if replace_audio else None]:
            try:
                if temp_path and os.path.exists(temp_path):
                    os.unlink(temp_path)
            except Exception:
                pass
        for temp_path in converted:
            try:
                if temp_path not in video_paths and os.path.exists(temp_path):
                    os.unlink(temp_path)
            except Exception:
                pass


class SegmentQueueRunner:
    CATEGORY = "video/utils"
    FUNCTION = "run"
    OUTPUT_NODE = True
    RETURN_TYPES = ()
    RETURN_NAMES = ()

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "帧率": ("FLOAT", {"default": 16.0, "min": 1.0, "max": 120.0, "forceInput": True,
                    "tooltip": "视频帧率，必须连接 Load Video 的帧率输出。\nFrame rate: must connect to Load Video fps output."}),
                "总帧数": ("INT", {"default": 0, "min": 0, "max": 99999, "forceInput": True,
                    "tooltip": "参考视频总帧数，必须连接 Load Video 的 frame_count 输出。\nTotal frames: must connect to Load Video frame_count output."}),
                "启用过渡效果": ("BOOLEAN", {"default": False,
                    "tooltip": "关闭=保持当前已验证的直出合并；开启=尝试使用上一段输出作为下一段过渡输入。"}),
                "分段数": ("INT", {"default": 2, "min": 1, "max": 100, "step": 1, "display": "slider",
                    "tooltip": "平均分段的段数（最大值可在设置处调整）。\nNumber of average segments (max adjustable in settings)."}),
                "从第几段开始": ("INT", {"default": 1, "min": 1, "max": 100, "step": 1, "display": "slider",
                    "tooltip": "从第几段开始生成，续跑时填写实际起始段。\nStart from which segment. Set accordingly when resuming."}),
                "执行": ("BOOLEAN", {"default": False,
                    "tooltip": "关闭=预览分段规划；开启=正式执行。\nOff=preview plan only; On=start execution."}),
                "启用续跑": ("BOOLEAN", {"default": False,
                    "tooltip": "开启后使用上方选择的视频作为首段过渡起点。\nEnable resume: use selected video as transition source for first segment."}),
                "参考视频节点ID": ("STRING", {"default": ""}),
                "输出节点ID":     ("STRING", {"default": ""}),
                "动作嵌入节点ID": ("STRING", {"default": ""}),
                "参考图节点ID":   ("STRING", {"default": ""}),
                "分段参考图":     ("STRING", {"default": ""}),
                "续跑视频路径":   ("STRING", {"default": ""}),
                "multi_ref_enabled": ("BOOLEAN", {"default": False,
                    "tooltip": "Multi Ref OFF keeps the original per-segment reference mode. Multi Ref ON loads the selected references as one single-person multi-reference batch for every segment."}),
                "replacement_enabled": ("BOOLEAN", {"default": False,
                    "tooltip": "Replacement OFF/ON syncs SCAIL-2 colored mask and transition replacement_mode."}),
                "multi_ref_startup_fix": ("BOOLEAN", {"default": False,
                    "tooltip": "OFF = legacy Multi Ref timing. ON = prepend repeated first frames for SCAIL-2 Multi Ref and trim them from the visible output to reduce reference-image flashes."}),
                "sqr_save_png":      ("STRING", {"default": "true"}),
                "sqr_frame_offset":  ("INT",    {"default": -1}),
                "sqr_pre_segments":  ("STRING", {"default": ""}),
            },
            "hidden": {
                "过渡跳过帧数": ("INT", {"default": -1}),
                "prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO", "unique_id": "UNIQUE_ID",
            },
        }

    def run(self,
            总帧数, 帧率, 启用过渡效果, 分段数, 从第几段开始,
            执行, 启用续跑,
            参考视频节点ID, 输出节点ID, 动作嵌入节点ID, 参考图节点ID,
            分段参考图, 续跑视频路径,
            multi_ref_enabled=False,
            replacement_enabled=False,
            multi_ref_startup_fix=False,
            sqr_save_png="true",
            sqr_frame_offset=-1,
            sqr_pre_segments="",
            过渡跳过帧数=-1,
            director_data="{}",
            prompt=None, extra_pnginfo=None, unique_id=None):

        total_frames       = 总帧数
        segments           = 分段数
        transition_enabled = _sqr_to_bool(启用过渡效果)
        multi_ref_enabled  = _sqr_to_bool(multi_ref_enabled)
        replacement_enabled = _sqr_to_bool(replacement_enabled)
        multi_ref_startup_fix = _sqr_to_bool(multi_ref_startup_fix)
        node_id            = 参考视频节点ID.strip()
        frame_rate         = 帧率
        combine_nid        = 输出节点ID.strip()
        ae_node_id         = 动作嵌入节点ID.strip()
        resume_video_path  = 续跑视频路径.strip()
        resume_enabled     = _sqr_to_bool(启用续跑) and bool(resume_video_path)
        skip_frames_manual = 过渡跳过帧数
        ri_node_id         = 参考图节点ID.strip()
        ref_imgs_str       = 分段参考图.strip()

        sqr_frame_offset = _sqr_to_int(sqr_frame_offset, -1)
        _frame_offset_param = sqr_frame_offset if sqr_frame_offset >= 0 else -1
        if _frame_offset_param < 0 and prompt and unique_id:
            _self_inputs = (prompt or {}).get(str(unique_id), {}).get("inputs", {})
            _fo_val = _self_inputs.get("sqr_frame_offset", -1)
            _fo_int = _sqr_to_int(_fo_val, -1)
            _frame_offset_param = _fo_int if _fo_int >= 0 else -1
        _frame_offset = _frame_offset_param if _frame_offset_param >= 0 else 0

        _plan_frames = max(1, total_frames - _frame_offset) if _frame_offset > 0 else total_frames

        director_plan = []
        director_base_prompts = []
        director_prompts = []
        director_guide_path = ""
        director_color_config = {"enabled": False, "reference_path": "", "strength": 1.0}
        if director_data and str(director_data).strip() not in ("", "{}"):
            try:
                director_plan = parse_director_plan(director_data, _plan_frames)
                director_base_prompts = resolve_director_prompts(director_data)
                director_prompts = resolve_director_composed_prompts(director_data)
                director_guide_path = first_director_guide_path(director_data)
                director_color_config = first_director_color_match_config(director_data)
            except ValueError as exc:
                _sqr_log(unique_id, f"[SQR] ✗ {exc}")
                return {}
            if director_plan and (not director_base_prompts or not director_base_prompts[0]):
                _sqr_log(unique_id, "[SQR] ✗ Director 第一段必须填写 positive 提示词。")
                return {}

        _preview_segments = len(director_plan) if director_plan else segments
        start_from_segment = max(1, min(从第几段开始, _preview_segments))
        if director_plan:
            rows = ["Director 手动分段计划:"]
            for idx, (skip, length, cfg) in enumerate(director_plan, 1):
                mode = "人物替换" if str(cfg.get("mode", "transfer")).lower() == "replacement" else "动作/表情迁移"
                visible = _sqr_to_int(cfg.get("visible_length"), length)
                rows.append(
                    f"  {idx}. {skip}-{skip + visible}帧 ({visible}帧, Wan窗口={length}) | {mode} | "
                    f"Multi Ref={'ON' if _sqr_to_bool(cfg.get('multi_ref', False)) else 'OFF'} | "
                    f"参考图={len(cfg.get('references', []))}"
                )
            plan_text = "\n".join(rows)
        else:
            plan_text = build_plan_text(
                _plan_frames, _preview_segments, start_from_segment, node_id, frame_rate)

        def _do_interrupt():
            try:
                from comfy import model_management as _mm
                _mm.interrupt_current_processing()
                print("[SQR] ✓ 中断标志已设置（内部API）。")
                return
            except Exception:
                pass
            try:
                interrupt_current()
                print("[SQR] ✓ 中断标志已设置（HTTP）。")
            except Exception as _e:
                print(f"[SQR] ⚠ 中断设置失败: {_e}")

        if not 执行:
            msg = "[预览模式]\n" + plan_text
            def _pi(): time.sleep(0.005); _do_interrupt()
            threading.Thread(target=_pi, daemon=True).start()
            _sqr_log(unique_id, msg)
            return {}

        if total_frames <= 0:
            _sqr_log(unique_id, "[SQR] ✗ 总帧数必须大于 0。")
            return {}
        if not node_id:
            _sqr_log(unique_id, "[SQR] ✗ 参考视频节点ID 不能为空。")
            return {}

        _sqr_full_prompt = (extra_pnginfo or {}).get("sqr_full_prompt")
        _effective_prompt = _sqr_full_prompt if _sqr_full_prompt else prompt
        _need_interrupt = (_sqr_full_prompt is None)
        _client_id = str((extra_pnginfo or {}).get("sqr_client_id") or "")
        _is_remote = bool((extra_pnginfo or {}).get("sqr_is_remote", False))

        if node_id not in (_effective_prompt or {}):
            _sqr_log(unique_id, f"[SQR] ✗ 找不到节点 ID「{node_id}」（完整工作流中）。")
            return {}

        print(f"[SQR] sqr_frame_offset: 参数={sqr_frame_offset}, 实际使用={_frame_offset}"
              f" | 工作流来源={'extra_pnginfo' if _sqr_full_prompt else 'prompt(回退)'}"
              f" | 分段模式={'director' if director_plan else 'average'}")
        _effective_frames = max(1, total_frames - _frame_offset) if _frame_offset > 0 else total_frames

        if director_plan:
            seg_list = [(skip, limit) for skip, limit, _ in director_plan]
            segment_configs = [cfg for _, _, cfg in director_plan]
            _sqr_log(unique_id, f"[SQR] Director 模式: 使用 {len(seg_list)} 个手动分段")
        else:
            seg_list = calc_segments(_effective_frames, segments)
            segment_configs = [{} for _ in seg_list]
        if not seg_list:
            _sqr_log(unique_id, "[SQR] ✗ 没有可执行分段，请检查总帧数和帧偏移。")
            return {}

        start_from_segment = max(1, min(start_from_segment, len(seg_list)))
        start_idx   = start_from_segment - 1
        segs_to_run = seg_list[start_idx:]
        base_prompt = copy.deepcopy(_effective_prompt)

        for _node in base_prompt.values():
            if not isinstance(_node, dict):
                continue
            if _node.get("class_type") in ("SQRScail2ColoredMaskAdvanced", "SQRSCAIL2TransitionToVideo"):
                _node.setdefault("inputs", {})["replacement_mode"] = replacement_enabled
            if _node.get("class_type") == "SQRSCAIL2TransitionToVideo":
                # Director/SCAIL-2 segmented video execution is one timeline at
                # a time. Some saved workflows may accidentally keep a sampler
                # context/window value in this slot (for example 20), which
                # would create a batched latent and make ComfyUI context-window
                # conditioning index out of range. Normalize it before each
                # queued segment is submitted; the transition node also keeps a
                # runtime guard as a last-resort safety net.
                _node.setdefault("inputs", {})["batch_size"] = 1
            if _node.get("class_type") == "SQRScail2ColoredMaskAdvanced":
                _inputs = _node.setdefault("inputs", {})
                current_identity = _inputs.get("identity_mode")
                if multi_ref_enabled and current_identity == "multi_person_multi_reference":
                    _inputs["identity_mode"] = "multi_person_multi_reference"
                else:
                    _inputs["identity_mode"] = "single_person_multi_reference" if multi_ref_enabled else "multi_person"

        ae_nid = ae_node_id or find_animate_embeds_node(base_prompt) or ""
        vc_nid = find_video_combine_node(base_prompt, combine_nid) or ""
        driving_sam3_nid = find_driving_sam3_node(base_prompt, node_id) or ""

        ref_images_list = []
        ref_image_groups = []
        inherited_ref_segments = set()
        if ref_imgs_str:
            if ref_imgs_str.lstrip().startswith("["):
                try:
                    parsed_refs = json.loads(ref_imgs_str)
                    if isinstance(parsed_refs, list):
                        if any(isinstance(x, list) for x in parsed_refs):
                            for group in parsed_refs:
                                if isinstance(group, list):
                                    cleaned = [
                                        _sqr_make_ref_entry(_sqr_ref_entry_path(x), _sqr_ref_entry_is_bg(x))
                                        for x in group
                                        if _sqr_ref_entry_path(x)
                                    ]
                                else:
                                    cleaned = [_sqr_make_ref_entry(_sqr_ref_entry_path(group), _sqr_ref_entry_is_bg(group))] if _sqr_ref_entry_path(group) else []
                                if cleaned:
                                    ref_image_groups.append(cleaned)
                            ref_images_list = [x for group in ref_image_groups for x in group]
                        else:
                            ref_images_list = [
                                _sqr_make_ref_entry(_sqr_ref_entry_path(x), _sqr_ref_entry_is_bg(x))
                                for x in parsed_refs
                                if _sqr_ref_entry_path(x)
                            ]
                except Exception as e:
                    print(f"[SQR] Reference image JSON parse failed; using legacy format: {e}")
            if not ref_images_list:
                import re as _re
                legacy_refs = _re.findall(r"(?:^|,)\s*(.*?\.(?:png|jpe?g|webp|bmp))(?=,|$)", ref_imgs_str, flags=_re.IGNORECASE)
                ref_images_list = [x.strip() for x in (legacy_refs or ref_imgs_str.split(",")) if x.strip()]
        if director_plan:
            ref_image_groups = [
                [
                    _sqr_make_ref_entry(_sqr_ref_entry_path(x), _sqr_ref_entry_is_bg(x), x)
                    for x in cfg.get("references", [])
                    if _sqr_ref_entry_path(x)
                ]
                for cfg in segment_configs
            ]
            ref_images_list = [x for group in ref_image_groups for x in group]
        if ref_image_groups:
            prepared_groups = []
            for group in ref_image_groups:
                prepared = _sqr_prepare_checkpoint_ref_entries(group, unique_id=unique_id)
                if director_plan or prepared:
                    prepared_groups.append(prepared)
            ref_image_groups = prepared_groups
            if director_plan:
                previous_group = []
                for group_index, group in enumerate(ref_image_groups):
                    if group:
                        previous_group = group
                    elif previous_group:
                        ref_image_groups[group_index] = copy.deepcopy(previous_group)
                        inherited_ref_segments.add(group_index + 1)
            ref_images_list = [x for group in ref_image_groups for x in group]
        elif ref_images_list:
            ref_images_list = _sqr_prepare_checkpoint_ref_entries(ref_images_list, unique_id=unique_id)

        # Director-specific recovery snapshot. Reference paths are replaced by
        # durable checkpoint copies while all newer Director metadata remains
        # intact (prompts, ranges, SAM3, Character Lock, Color Match and IDs).
        director_checkpoint_snapshot = None
        if director_plan:
            try:
                director_checkpoint_snapshot = json.loads(director_data) if isinstance(director_data, str) else copy.deepcopy(director_data)
                if isinstance(director_checkpoint_snapshot, dict):
                    snapshot_segments = director_checkpoint_snapshot.get("segments", [])
                    for snapshot_index, snapshot_segment in enumerate(snapshot_segments):
                        if isinstance(snapshot_segment, dict) and snapshot_index < len(ref_image_groups):
                            snapshot_segment["references"] = copy.deepcopy(ref_image_groups[snapshot_index])
                    director_checkpoint_snapshot["checkpoint_version"] = 2
            except Exception as exc:
                print(f"[SQR] Director checkpoint snapshot failed: {exc}")
                director_checkpoint_snapshot = None

        manual_video_path = manual_video_frames = None
        if resume_enabled and resume_video_path:
            p = _sqr_resolve_media_path(resume_video_path)
            if p and os.path.isfile(p):
                try:
                    src_p = p
                    p = _sqr_copy_into_input(p, unique_id=unique_id, prefix="sqr_resume")
                    if os.path.realpath(src_p) != os.path.realpath(p):
                        _sqr_log(unique_id, f"[SQR] 已复制续跑视频到 input/: {os.path.basename(p)}")
                    fname = os.path.basename(p)
                    import cv2
                    cap = cv2.VideoCapture(p)
                    try:
                        if not cap.isOpened():
                            _sqr_log(unique_id, f"[SQR] ✗ cv2 无法打开续跑视频: {fname}")
                        else:
                            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                            if frames <= 0:
                                _sqr_log(unique_id, f"[SQR] ✗ 续跑视频帧数异常: {fname} ({frames})")
                            else:
                                manual_video_frames = frames
                                manual_video_path = p
                                _sqr_log(unique_id, f"[SQR] ✓ 续跑视频: {fname} ({manual_video_frames}帧)")
                    finally:
                        cap.release()
                except Exception as e:
                    _sqr_log_cv2_issue(unique_id, "读取续跑视频失败", e)
            else:
                _sqr_log(unique_id, f"[SQR] ⚠ 续跑视频不存在或无法解析: {resume_video_path}")

        width_src = height_src = None
        target_inputs = base_prompt.get(node_id, {}).get("inputs", {})
        if "custom_width" in target_inputs and isinstance(target_inputs["custom_width"], list):
            width_src = target_inputs["custom_width"]
        if "custom_height" in target_inputs and isinstance(target_inputs["custom_height"], list):
            height_src = target_inputs["custom_height"]

        def log(msg: str):
            _sqr_log(unique_id, f"[SQR] {msg}")

        audio_filename = find_audio_filename(base_prompt, node_id)
        if audio_filename:
            _audio_source_path = _sqr_resolve_media_path(audio_filename)
            if media_has_audio(_audio_source_path):
                _sqr_log(unique_id, f"[SQR] 音频文件: {audio_filename}")
            else:
                _sqr_log(unique_id, f"[SQR] ℹ 输入视频不含音频流，将按无音频模式处理: {audio_filename}")
                audio_filename = None
        else:
            _sqr_log(unique_id, f"[SQR] ⚠ 无法获取音频文件名")

        try:
            main_ref_skip_first = max(0, int(base_prompt.get(node_id, {}).get("inputs", {}).get("skip_first_frames", 0) or 0))
        except Exception:
            main_ref_skip_first = 0
        if main_ref_skip_first > 0:
            _sqr_log(unique_id, f"[SQR] Load Video 原始 skip_first_frames={main_ref_skip_first}，分段读取会保留这个起始偏移")

        image_src_node = None
        latent_src_node = None
        if vc_nid and vc_nid in base_prompt:
            img_input = base_prompt[vc_nid]["inputs"].get("images")
            if isinstance(img_input, list) and len(img_input) == 2:
                image_src_node = img_input
                print(f"[SQR] 图像来源: {image_src_node}")
                latent_src_node = find_latent_source_for_images(base_prompt, image_src_node)
                if latent_src_node:
                    print(f"[SQR] latent来源: {latent_src_node}")

        pre_segment_paths = [p.strip() for p in sqr_pre_segments.split(",")
                             if p.strip() and os.path.isfile(p.strip())] \
                            if sqr_pre_segments.strip() else []
        if pre_segment_paths:
            print(f"[SQR] 续跑前段素材: {len(pre_segment_paths)} 个文件")

        run_stamp = _sqr_now_stamp()

        def submit_all():
            last_video_path   = manual_video_path
            last_video_frames = manual_video_frames
            last_latent_name  = None
            segment_output_paths = []
            sqr_cut_cleanup = []
            sqr_full_cleanup = []
            sqr_cut_paths   = []
            _t0 = time.time()
            _total_frames_ran = sum(limit for _, limit in segs_to_run)
            _all_done = False

            log(f"{'═'*20} 运行时间码={run_stamp} {'═'*20}")
            log(f"AnimateEmbeds节点: [{ae_nid}]")
            log(f"输出节点: [{vc_nid}]")
            if ref_images_list:
                log(f"参考图列表: {ref_images_list}")
            if _frame_offset > 0:
                log(f"=== 重新设计续跑模式（帧偏移={_frame_offset}，跳过前{_frame_offset}帧参考视频）===")
            elif resume_enabled:
                log(f"=== 自动续跑模式 ===")
            else:
                log(f"=== 全新生成 ===")
            if resume_enabled:
                if manual_video_path:
                    log(f"✓ 续跑视频: {os.path.basename(manual_video_path)} ({manual_video_frames}帧)")
                else:
                    log(f"⚠ 续跑已启用但视频无效，首段无过渡")

            previous_character_lock = None
            for i, (skip, limit) in enumerate(segs_to_run):
                seg_num        = start_idx + i + 1
                total_segs     = len(seg_list)
                wf             = copy.deepcopy(base_prompt)
                seg_config     = segment_configs[seg_num - 1] if seg_num - 1 < len(segment_configs) else {}
                seg_positive   = director_prompts[seg_num - 1] if seg_num - 1 < len(director_prompts) else ""
                visible_limit  = _sqr_to_int(seg_config.get("visible_length"), limit)
                seg_multi_ref  = _sqr_to_bool(seg_config.get("multi_ref", multi_ref_enabled), multi_ref_enabled)
                seg_replacement = _sqr_to_bool(replacement_enabled) or str(
                    seg_config.get("mode", "transfer")
                ).lower() == "replacement"
                seg_refs = seg_config.get("references", []) if isinstance(seg_config.get("references", []), list) else []
                if director_plan and seg_num - 1 < len(ref_image_groups):
                    seg_refs = ref_image_groups[seg_num - 1]
                if seg_num in inherited_ref_segments:
                    log(f"  第{seg_num}段未填写参考图，自动沿用上一有效分段的参考图组（{len(seg_refs)}张）")
                if director_plan:
                    _prompt_base = director_base_prompts[seg_num - 1] if seg_num - 1 < len(director_base_prompts) else ""
                    _prompt_config = copy.deepcopy(seg_config) if isinstance(seg_config, dict) else {}
                    _prompt_config["references"] = seg_refs
                    _char_lock = _prompt_config.get("character_lock")
                    if isinstance(_char_lock, dict) and _char_lock.get("descriptions"):
                        previous_character_lock = copy.deepcopy(_char_lock)
                    elif previous_character_lock is not None:
                        _prompt_config["character_lock"] = copy.deepcopy(previous_character_lock)
                    seg_positive = compose_director_positive(_prompt_base, _prompt_config)
                seg_ref_identity_groups, seg_ref_person_count = _sqr_ref_identity_groups(seg_refs, max_refs=6)
                for _node in wf.values():
                    if not isinstance(_node, dict):
                        continue
                    if _node.get("class_type") in ("SQRScail2ColoredMaskAdvanced", "SQRSCAIL2TransitionToVideo"):
                        _node.setdefault("inputs", {})["replacement_mode"] = seg_replacement
                    if _node.get("class_type") == "SQRSCAIL2TransitionToVideo":
                        _node.setdefault("inputs", {})["batch_size"] = 1
                    if _node.get("class_type") == "SQRScail2ColoredMaskAdvanced":
                        _mask_inputs = _node.setdefault("inputs", {})
                        _mask_inputs["identity_mode"] = (
                            "multi_person_multi_reference" if (seg_multi_ref and seg_ref_identity_groups) else (
                                "single_person_multi_reference" if seg_multi_ref else "multi_person"
                            )
                        )
                        if seg_ref_identity_groups:
                            _mask_inputs["ref_identity_groups"] = seg_ref_identity_groups
                        if not seg_multi_ref:
                            _mask_inputs["background_indices"] = ""
                if seg_ref_person_count > 1 and driving_sam3_nid and driving_sam3_nid in wf:
                    wf[driving_sam3_nid].setdefault("inputs", {})["max_objects"] = min(6, seg_ref_person_count)
                TRIM           = 16
                audio_skip_frames = skip

                _actual_skip = skip + _frame_offset
                transition_supported, _ae_class_type = _sqr_node_supports_transition(wf, ae_nid)
                transition_frames = _sqr_transition_frame_count(_ae_class_type)
                transition_added_frames = _sqr_transition_added_frames(_ae_class_type)
                force_direct_segment_merge = not transition_supported
                _ae_inputs_for_mode = wf.get(ae_nid, {}).get("inputs", {}) if ae_nid in wf else {}
                _sqr_replacement_mode = bool(_ae_inputs_for_mode.get("replacement_mode", False))
                _supports_latent_transition = _ae_class_type == "SQRSCAIL2TransitionToVideo"
                _latent_transition_name = (
                    None if (_sqr_replacement_mode or not _supports_latent_transition) else last_latent_name
                )
                # OFF means truly independent visual generations. Contiguous source
                # video frame ranges preserve driving motion; generated video/latent
                # carry is only allowed when the user explicitly enables transition.
                use_transition = bool(transition_enabled) and (last_video_path is not None or _latent_transition_name is not None) and transition_supported
                continuity_mode = "transition-on" if transition_enabled else "transition-off"
                log(f"  Director模式: replacement={'ON' if seg_replacement else 'OFF'} multi_ref={'ON' if seg_multi_ref else 'OFF'} refs={len(seg_refs)}")
                log(f"  接缝调试: mode={continuity_mode} supported={transition_supported} class={_ae_class_type} replacement={_sqr_replacement_mode} last_latent={last_latent_name or 'None'} last_video={os.path.basename(last_video_path) if last_video_path else 'None'} use_carry={use_transition}")
                startup_trim_frames = 0
                startup_trim_reason = ""
                if multi_ref_startup_fix and seg_multi_ref and (seg_refs or ref_images_list) and _ae_class_type == "SQRSCAIL2TransitionToVideo":
                    if use_transition:
                        log("  Multi Ref startup fix: skipped on transition segment to avoid duplicated seam motion")
                    elif seg_num == 1:
                        startup_trim_reason = "first segment"
                    elif ref_image_groups:
                        cur_group_index = min(i, len(ref_image_groups) - 1)
                        prev_group_index = min(max(0, i - 1), len(ref_image_groups) - 1)
                        cur_group_sig = tuple((_sqr_ref_entry_path(x), _sqr_ref_entry_is_bg(x)) for x in ref_image_groups[cur_group_index])
                        prev_group_sig = tuple((_sqr_ref_entry_path(x), _sqr_ref_entry_is_bg(x)) for x in ref_image_groups[prev_group_index])
                        if cur_group_sig != prev_group_sig:
                            startup_trim_reason = "reference group changed"
                    if startup_trim_reason:
                        startup_trim_frames = MULTI_REF_STARTUP_TRIM_FRAMES
                        log(f"  Multi Ref startup fix: repeat_prefix={startup_trim_frames} trim_after_prefix={startup_trim_frames} reason={startup_trim_reason}")
                _transition_frames_for_load = 0
                _video_skip = max(0, _actual_skip - _transition_frames_for_load)
                _video_limit = limit + (_actual_skip - _video_skip)
                if _frame_offset > 0:
                    log(f"--- 第{seg_num}/{total_segs}段  实际skip={_actual_skip}（段内{skip}+偏移{_frame_offset}）limit={limit} ---")
                else:
                    log(f"--- 第{seg_num}/{total_segs}段  skip={_actual_skip}  limit={limit} ---")
                if transition_enabled and not transition_supported and seg_num == start_idx + 1:
                    log(f"  ⚠ 当前节点[{_ae_class_type}]不支持 transition_video，过渡效果回退为直出合并")

                wf[node_id]["inputs"]["skip_first_frames"] = main_ref_skip_first + _video_skip
                wf[node_id]["inputs"]["frame_load_cap"]    = _video_limit
                if startup_trim_frames > 0:
                    length_ok, length_note = _sqr_add_to_input_or_linked_value(
                        wf, ae_nid, "length", startup_trim_frames
                    )
                    if length_ok:
                        log(f"  Multi Ref startup fix: extended generation length by {startup_trim_frames} ({length_note})")
                    else:
                        forced_length = limit + startup_trim_frames
                        if ae_nid and ae_nid in wf:
                            wf[ae_nid].setdefault("inputs", {})["length"] = forced_length
                            log(f"  Multi Ref startup fix: forced generation length={forced_length} ({length_note})")
                        else:
                            log(f"  ⚠ Multi Ref startup fix: could not extend generation length ({length_note})")
                    startup_repeat_id = f"sqr_startup_repeat_{seg_num}"
                    wf[startup_repeat_id] = {
                        "class_type": "SQRRepeatFirstFrames",
                        "inputs": {
                            "images": [node_id, 0],
                            "repeat_count": startup_trim_frames,
                        },
                    }
                    rewired = _sqr_rewire_image_output(wf, [node_id, 0], [startup_repeat_id, 0])
                    wf[startup_repeat_id]["inputs"]["images"] = [node_id, 0]
                    log(f"  Multi Ref startup fix: repeated first frame x{startup_trim_frames}; rewired_image_links={rewired}")

                sam3_marking = seg_config.get("sam3_marking", {}) if isinstance(seg_config, dict) else {}
                positive_points = sam3_marking.get("positive", []) if isinstance(sam3_marking, dict) else []
                negative_points = sam3_marking.get("negative", []) if isinstance(sam3_marking, dict) else []
                marking_frame = _sqr_to_int(sam3_marking.get("frame"), -1) if isinstance(sam3_marking, dict) else -1
                if seg_ref_person_count > 1 and positive_points:
                    log("  ℹ 多人多参模式：忽略旧的单人 SAM3 手动打标，保留视频 SAM 提示词自动识别")
                    positive_points = []
                    negative_points = []
                if marking_frame != _sqr_to_int(seg_config.get("start"), skip):
                    if positive_points:
                        log("  ⚠ SAM3 手动打标帧与当前分段首帧不一致，本段回退到原文字识别")
                    positive_points = []
                    negative_points = []
                if positive_points:
                    sam3_node = wf.get(driving_sam3_nid, {}) if driving_sam3_nid else {}
                    sam3_inputs = sam3_node.get("inputs", {}) if isinstance(sam3_node, dict) else {}
                    sam3_images = sam3_inputs.get("images")
                    sam3_model = sam3_inputs.get("model")
                    if sam3_images and sam3_model:
                        sam3_first_id = f"sqr_sam3_first_frame_{seg_num}"
                        sam3_points_id = f"sqr_sam3_points_{seg_num}"
                        sam3_detect_id = f"sqr_sam3_detect_{seg_num}"
                        wf[sam3_first_id] = {
                            "class_type": "ImageFromBatch",
                            "inputs": {"image": sam3_images, "batch_index": 0, "length": 1},
                        }
                        wf[sam3_points_id] = {
                            "class_type": "SQRSAM3NormalizedPoints",
                            "inputs": {
                                "images": [sam3_first_id, 0],
                                "points_json": json.dumps({
                                    "positive": positive_points,
                                    "negative": negative_points,
                                }, ensure_ascii=False),
                            },
                        }
                        wf[sam3_detect_id] = {
                            "class_type": "SAM3_Detect",
                            "inputs": {
                                "model": sam3_model,
                                "image": [sam3_first_id, 0],
                                "positive_coords": [sam3_points_id, 0],
                                "negative_coords": [sam3_points_id, 1],
                                "threshold": 0.5,
                                "refine_iterations": 2,
                                "individual_masks": False,
                            },
                        }
                        sam3_inputs["initial_mask"] = [sam3_detect_id, 0]
                        sam3_inputs.pop("conditioning", None)
                        sam3_inputs["max_objects"] = 1
                        log(f"  SAM3 手动打标: 第{seg_num}段 正向={len(positive_points)} 负向={len(negative_points)}，已锁定单人物跟踪")
                    else:
                        log("  ⚠ SAM3 手动打标已保存，但未找到驱动视频 SAM3_VideoTrack 或其模型连接")

                if vc_nid and vc_nid in wf and audio_filename:
                    _real_skip = main_ref_skip_first + skip + _frame_offset
                    if use_transition and transition_enabled:
                        audio_skip_frames    = _real_skip
                        main_audio_frames    = max(0, _real_skip - transition_added_frames)
                        transition_note      = f"主节点skip{_real_skip}-{transition_added_frames}={main_audio_frames}帧, cut_vc从原分段边界{audio_skip_frames}帧开始"
                    else:
                        audio_skip_frames    = _real_skip
                        main_audio_frames    = _real_skip
                        transition_note      = f"{_real_skip}帧" + ("（仅动作接力，画面硬切）" if use_transition else "")
                    audio_start_time  = main_audio_frames / frame_rate
                    audio_tmp_id      = f"sqr_audio_{seg_num}"
                    wf[audio_tmp_id] = {
                        "class_type": "VHS_LoadAudioUpload",
                        "inputs": {
                            "audio":      audio_filename,
                            "start_time": audio_start_time,
                            "duration":   0,
                        }
                    }
                    wf[vc_nid]["inputs"]["audio"] = [audio_tmp_id, 0]
                    log(f"  ✓ 主节点音频: start={audio_start_time:.3f}s ({transition_note})")
                elif vc_nid and vc_nid in wf:
                    wf[vc_nid]["inputs"].pop("audio", None)
                    log("  ℹ 当前输入没有可用音频，输出纯视频")

                transition_image_node = None
                if ae_nid and ae_nid in wf:
                    if use_transition:
                        wf[ae_nid]["inputs"].pop("continue_motion", None)
                        if _latent_transition_name:
                            tl_tmp_id = f"sqr_tlatent_{seg_num}"
                            wf[tl_tmp_id] = {
                                "class_type": "LoadLatent",
                                "inputs": {"latent": _latent_transition_name},
                            }
                            wf[ae_nid]["inputs"]["transition_latent"] = [tl_tmp_id, 0]
                            if last_video_path:
                                t_skip = skip_frames_manual if skip_frames_manual >= 0 \
                                         else (max(0, last_video_frames - transition_frames) if last_video_frames else 0)
                                tv_tmp_id = f"sqr_tv_{seg_num}"
                                tv_inputs = {
                                    "video":             os.path.basename(last_video_path),
                                    "force_rate":        0,
                                    "custom_width":      0,
                                    "custom_height":     0,
                                    "frame_load_cap":    transition_frames,
                                    "skip_first_frames": t_skip,
                                    "select_every_nth":  1,
                                    "format":            "AnimateDiff",
                                }
                                if width_src:
                                    tv_inputs["custom_width"]  = width_src
                                if height_src:
                                    tv_inputs["custom_height"] = height_src
                                wf[tv_tmp_id] = {"class_type": "VHS_LoadVideo", "inputs": tv_inputs}
                                transition_image_node = [tv_tmp_id, 0]
                                wf[ae_nid]["inputs"]["transition_video"] = [tv_tmp_id, 0]
                                log(f"  ✓ 可见过渡前缀: {os.path.basename(last_video_path)} skip={t_skip} limit={transition_frames}")
                            else:
                                wf[ae_nid]["inputs"].pop("transition_video", None)
                            log(f"  ✓ 过渡latent: {_latent_transition_name}（有效新增{transition_added_frames}帧）")
                            log(f"  过渡调试: 已注入节点 {tl_tmp_id} -> {ae_nid}.transition_latent")
                        elif last_video_path:
                            t_skip = skip_frames_manual if skip_frames_manual >= 0 \
                                     else (max(0, last_video_frames - transition_frames) if last_video_frames else 0)
                            tv_tmp_id = f"sqr_tv_{seg_num}"
                            tv_inputs = {
                                "video":             os.path.basename(last_video_path),
                                "force_rate":        0,
                                "custom_width":      0,
                                "custom_height":     0,
                                "frame_load_cap":    transition_frames,
                                "skip_first_frames": t_skip,
                                "select_every_nth":  1,
                                "format":            "AnimateDiff",
                            }
                            if width_src:
                                tv_inputs["custom_width"]  = width_src
                            if height_src:
                                tv_inputs["custom_height"] = height_src
                            wf[tv_tmp_id] = {"class_type": "VHS_LoadVideo", "inputs": tv_inputs}
                            transition_image_node = [tv_tmp_id, 0]
                            wf[ae_nid]["inputs"]["transition_video"] = [tv_tmp_id, 0]
                            wf[ae_nid]["inputs"].pop("transition_latent", None)
                            log(f"  ✓ 过渡视频: {os.path.basename(last_video_path)} skip={t_skip} limit={transition_frames}")
                            log(f"  过渡调试: 已注入节点 {tv_tmp_id} -> {ae_nid}.transition_video, inputs={tv_inputs}")
                    else:
                        wf[ae_nid]["inputs"].pop("transition_video", None)
                        wf[ae_nid]["inputs"].pop("transition_latent", None)
                        wf[ae_nid]["inputs"].pop("continue_motion", None)
                        if transition_enabled:
                            log("  首段无过渡")
                        else:
                            log("  过渡OFF：不注入上一段画面/latent，按连续源视频帧独立生成后硬切")

                ref_target_id = ri_node_id
                if seg_multi_ref:
                    ref_target_id = (ri_node_id if ri_node_id and wf.get(ri_node_id, {}).get("class_type") in ("WanSQRMultiReference", "SQRScail2ReferenceBatchStack") else None) or find_multi_reference_node(wf) or ri_node_id

                if seg_multi_ref and ref_target_id and ref_target_id in wf:
                    _target = wf.get(ref_target_id, {})
                    _target_inputs = _target.get("inputs", {})
                    _has_existing_ref = any(_target_inputs.get(f"image_{slot}") is not None for slot in range(1, 7))
                    _available_segment_refs = seg_refs if director_plan else ref_images_list
                    if not (_available_segment_refs or _has_existing_ref):
                        log(f"  ✗ 第{seg_num}段启用了 Multi Ref，但没有参考图；已停止后续分段。")
                        return

                _refs_to_inject = seg_refs if director_plan else ref_images_list
                if _refs_to_inject and ref_target_id and ref_target_id in wf:
                    def _sqr_ref_entry_to_input_name(img_entry):
                        img_path = _sqr_ref_entry_path(img_entry)
                        if os.path.isabs(img_path):
                            import shutil as _shutil
                            input_dir = folder_paths.get_input_directory()
                            src_real = os.path.realpath(img_path)
                            if os.path.realpath(os.path.dirname(src_real)) == os.path.realpath(input_dir):
                                return os.path.basename(src_real)
                            img_fname = _build_safe_input_copy_name(src_real, unique_id=unique_id, prefix="sqr_refrun")
                            img_dst = os.path.join(input_dir, img_fname)
                            try:
                                _shutil.copy2(src_real, img_dst)
                            except Exception as e:
                                log(f"  ! reference image copy failed: {e}")
                            return img_fname
                        return img_path

                    color_match_enabled = _sqr_to_bool(seg_config.get("color_match", False)) if director_plan else False
                    guide_entry = seg_config.get("guide_frame", {}) if director_plan else {}
                    guide_path = _sqr_ref_entry_path(guide_entry)
                    color_guide_id = None
                    if color_match_enabled and guide_path:
                        color_guide_id = f"sqr_color_guide_{seg_num}"
                        wf[color_guide_id] = {
                            "class_type": "LoadImage",
                            "inputs": {"image": _sqr_ref_entry_to_input_name(guide_path)},
                        }

                    def _sqr_color_matched_ref(img_entry, load_id, ref_slot):
                        if not color_guide_id:
                            return [load_id, 0]
                        strength_value = (
                            img_entry.get("color_match_strength")
                            if isinstance(img_entry, dict) else None
                        )
                        if strength_value is None:
                            strength_value = seg_config.get("color_match_strength", 1.0)
                        try:
                            strength_value = max(0.0, min(10.0, float(strength_value)))
                        except Exception:
                            strength_value = 1.0
                        color_id = f"sqr_color_match_{seg_num}_{ref_slot}"
                        wf[color_id] = {
                            "class_type": "ColorMatchV2",
                            "inputs": {
                                "image_target": [load_id, 0],
                                "image_ref": [color_guide_id, 0],
                                "method": "mkl",
                                "strength": strength_value,
                                "multithread": True,
                            },
                        }
                        return [color_id, 0]

                    ref_node = wf.get(ref_target_id, {})
                    ref_class = ref_node.get("class_type", "")
                    ref_inputs = ref_node.setdefault("inputs", {})
                    if seg_multi_ref and ref_class in ("WanSQRMultiReference", "SQRScail2ReferenceBatchStack"):
                        active_refs = seg_refs if director_plan else (seg_refs or ref_images_list)
                        if ref_image_groups:
                            group_index = min(seg_num - 1, len(ref_image_groups) - 1)
                            active_refs = ref_image_groups[group_index]
                        max_refs = min(len(active_refs), 6)
                        bg_indices = [idx + 1 for idx, entry in enumerate(active_refs[:max_refs]) if _sqr_ref_entry_is_bg(entry)]
                        for ref_slot in range(1, 7):
                            ref_inputs.pop(f"image_{ref_slot}", None)
                        for ref_slot, img_entry in enumerate(active_refs[:max_refs], start=1):
                            img_name = _sqr_ref_entry_to_input_name(img_entry)
                            load_id = f"sqr_mref_{seg_num}_{ref_slot}"
                            wf[load_id] = {"class_type": "LoadImage", "inputs": {"image": img_name}}
                            ref_inputs[f"image_{ref_slot}"] = _sqr_color_matched_ref(img_entry, load_id, ref_slot)
                        for _node in wf.values():
                            if isinstance(_node, dict) and _node.get("class_type") == "SQRScail2ColoredMaskAdvanced":
                                _mask_inputs = _node.setdefault("inputs", {})
                                _mask_inputs["background_indices"] = ",".join(str(x) for x in bg_indices)
                                if seg_ref_identity_groups:
                                    _mask_inputs["identity_mode"] = "multi_person_multi_reference"
                                    _mask_inputs["ref_identity_groups"] = seg_ref_identity_groups
                        group_note = f" group {min(i + 1, len(ref_image_groups))}/{len(ref_image_groups)}" if ref_image_groups else ""
                        bg_note = f", BG={bg_indices}" if bg_indices else ""
                        person_note = f", persons={seg_ref_person_count}, groups={seg_ref_identity_groups}" if seg_ref_identity_groups else ""
                        log(f"  OK Multi Ref{group_note}: loaded {max_refs} reference images into {ref_class}{bg_note}{person_note}")
                        if color_guide_id:
                            strengths = [
                                (entry.get("color_match_strength", seg_config.get("color_match_strength", 1.0))
                                 if isinstance(entry, dict) else seg_config.get("color_match_strength", 1.0))
                                for entry in active_refs[:max_refs]
                            ]
                            log(f"  Color Match 已接入实际 Multi Ref：{max_refs} 张，strength={strengths}")
                    else:
                        if seg_multi_ref:
                            log(f"  ! Multi Ref ON expects reference node ID to point to Wan SQR Multi Reference; current={ref_class or 'Unknown'}, fallback to single image mode")
                        active_single_refs = seg_refs if director_plan else (seg_refs or ref_images_list)
                        img_idx = 0 if seg_refs else min(seg_num - 1, len(active_single_refs) - 1)
                        img_entry = _sqr_make_ref_entry(_sqr_ref_entry_path(active_single_refs[img_idx]), False)
                        img_name = _sqr_ref_entry_to_input_name(img_entry)
                        if ref_class in ("WanSQRMultiReference", "SQRScail2ReferenceBatchStack"):
                            for ref_slot in range(1, 7):
                                ref_inputs.pop(f"image_{ref_slot}", None)
                            load_id = f"sqr_ref_{seg_num}_1"
                            wf[load_id] = {"class_type": "LoadImage", "inputs": {"image": img_name}}
                            ref_inputs["image_1"] = _sqr_color_matched_ref(img_entry, load_id, 1)
                        else:
                            ref_inputs["image"] = img_name
                            wv = ref_node.get("widgets_values", [])
                            if wv:
                                wv[0] = img_name
                        log(f"  OK reference image[{img_idx+1}]: {img_name}")
                        if color_guide_id:
                            log("  Color Match 已接入实际单参考图输入")

                TRIM = 16
                is_last_seg = (seg_num == total_segs)
                total_raw = limit + startup_trim_frames + (transition_added_frames if use_transition else 0)

                image_src = image_src_node
                if use_transition and transition_enabled and transition_image_node is not None:
                    prefix_start = 1 if _ae_class_type == "SQRSCAIL2TransitionToVideo" else 0
                    prefix_node = f"sqr_prefix_{seg_num}"
                    wf[prefix_node] = {
                        "class_type": "SQRReplaceBatchPrefix",
                        "inputs": {
                            "images": image_src,
                            "prefix_images": transition_image_node,
                            "prefix_length": transition_added_frames,
                            "prefix_start": prefix_start,
                            "color_release_frames": 20,
                        },
                    }
                    image_src = [prefix_node, 0]
                    log(f"  Color continuity: restored {transition_added_frames} carry frames from source offset {prefix_start}, then released per-frame color correction over 20 frames")
                if force_direct_segment_merge:
                    trim_len = max(1, min(limit, max(0, total_frames - _actual_skip)))
                    trim_start = max(0, limit - trim_len)
                    ifb_a = f"sqr_ifb_{seg_num}_a"
                    wf[ifb_a] = {"class_type": "ImageFromBatch",
                                 "inputs": {"image": image_src, "batch_index": trim_start, "length": trim_len}}
                    final_image_node = ifb_a
                    log(f"  直出裁切：裁前{trim_start}帧，输出{trim_len}帧")
                elif _ae_class_type == "WanAnimateToVideo":
                    trim_start = 0
                    trim_len = max(1, min(limit, max(0, total_frames - _actual_skip)))
                    ifb_a = f"sqr_ifb_{seg_num}_a"
                    wf[ifb_a] = {"class_type": "ImageFromBatch",
                                 "inputs": {"image": image_src, "batch_index": trim_start, "length": trim_len}}
                    final_image_node = ifb_a
                    log(f"  core裁切：不做过渡裁切，输出{trim_len}帧")
                elif KEEP_FULL_TRANSITION_IN_MERGE:
                    transition_prefix_trim = transition_added_frames if use_transition else 0
                    trim_start = startup_trim_frames + transition_prefix_trim
                    trim_len = max(1, total_raw - startup_trim_frames - transition_prefix_trim)
                    tail_trim = 0
                    ifb_a = f"sqr_ifb_{seg_num}_a"
                    wf[ifb_a] = {"class_type": "ImageFromBatch",
                                 "inputs": {"image": image_src, "batch_index": trim_start, "length": trim_len}}
                    final_image_node = ifb_a
                    if use_transition:
                        log(f"  裁切：读取{transition_frames}帧过渡（有效前缀{transition_added_frames}帧仅用于采样），合并前裁掉前缀→输出{trim_len}帧")
                    else:
                        log(f"  裁切：不裁前，裁后{tail_trim}帧→输出{trim_len}帧")
                    if startup_trim_frames > 0:
                        log(f"  Multi Ref startup fix: hidden_startup_frames={startup_trim_frames}, visible_output={trim_len}")
                elif not use_transition:
                    trim_start = 0
                    trim_len   = total_raw - TRIM
                    ifb_a = f"sqr_ifb_{seg_num}_a"
                    wf[ifb_a] = {"class_type": "ImageFromBatch",
                                 "inputs": {"image": image_src, "batch_index": trim_start, "length": trim_len}}
                    final_image_node = ifb_a
                    log(f"  裁切：不裁前，裁后{TRIM}帧→输出{trim_len}帧")
                elif is_last_seg:
                    trim_start = TRIM
                    trim_len   = total_raw - TRIM
                    ifb_a = f"sqr_ifb_{seg_num}_a"
                    wf[ifb_a] = {"class_type": "ImageFromBatch",
                                 "inputs": {"image": image_src, "batch_index": trim_start, "length": trim_len}}
                    final_image_node = ifb_a
                    log(f"  裁切：裁前{TRIM}帧，不裁后→输出{trim_len}帧")
                else:
                    trim_start  = TRIM
                    after_front = total_raw - TRIM
                    trim_len    = after_front - TRIM
                    ifb_a = f"sqr_ifb_{seg_num}_a"
                    ifb_b = f"sqr_ifb_{seg_num}_b"
                    wf[ifb_a] = {"class_type": "ImageFromBatch",
                                 "inputs": {"image": image_src, "batch_index": trim_start, "length": after_front}}
                    wf[ifb_b] = {"class_type": "ImageFromBatch",
                                 "inputs": {"image": [ifb_a, 0], "batch_index": 0, "length": trim_len}}
                    final_image_node = ifb_b
                    log(f"  裁切：裁前{TRIM}裁后{TRIM}→输出{trim_len}帧")

                if director_plan and visible_limit > 0 and trim_len != visible_limit:
                    director_crop_id = f"sqr_director_crop_{seg_num}"
                    wf[director_crop_id] = {
                        "class_type": "ImageFromBatch",
                        "inputs": {"image": [final_image_node, 0], "batch_index": 0, "length": visible_limit},
                    }
                    final_image_node = director_crop_id
                    trim_len = visible_limit
                    log(f"  Director 精确裁切：Wan窗口{limit}帧 → 时间线{visible_limit}帧")

                if vc_nid and vc_nid in wf:
                    # Keep the user's existing Video Combine node as the live
                    # per-segment preview target. The hidden full/cut clones are
                    # still used for transition handoff and reliable file lookup.
                    wf[vc_nid]["inputs"]["images"] = [final_image_node, 0]

                    full_image_node = image_src
                    if startup_trim_frames > 0:
                        full_align_id = f"sqr_ifb_{seg_num}_full_startup_aligned"
                        full_align_len = max(1, limit + (transition_added_frames if use_transition else 0))
                        wf[full_align_id] = {
                            "class_type": "ImageFromBatch",
                            "inputs": {
                                "image": image_src,
                                "batch_index": startup_trim_frames,
                                "length": full_align_len,
                            },
                        }
                        full_image_node = [full_align_id, 0]
                        log(f"  Multi Ref startup fix: transition source aligned from frame {startup_trim_frames}, length={full_align_len}")

                    full_vc_id = f"sqr_full_vc_{seg_num}"
                    full_inputs = copy.deepcopy(wf[vc_nid]["inputs"])
                    full_inputs["images"] = full_image_node
                    full_inputs["save_output"] = True
                    full_inputs["save_metadata"] = False

                    cut_vc_id = f"sqr_cut_vc_{seg_num}"
                    cut_inputs = copy.deepcopy(wf[vc_nid]["inputs"])
                    cut_inputs["images"]          = [final_image_node, 0]
                    cut_inputs["save_output"]     = True
                    cut_inputs["save_metadata"]   = False
                    _main_prefix = wf[vc_nid]["inputs"].get("filename_prefix", "")
                    _slash = max(_main_prefix.rfind("/"), _main_prefix.rfind("\\"))
                    _subfolder_prefix = _main_prefix[:_slash+1] if _slash >= 0 else ""
                    _full_file_prefix = f"sqr_full_{run_stamp}_seg{seg_num}_"
                    full_inputs["filename_prefix"] = f"{_subfolder_prefix}{_full_file_prefix}"
                    _cut_file_prefix = f"sqr_cut_{run_stamp}_seg{seg_num}_"
                    cut_inputs["filename_prefix"] = f"{_subfolder_prefix}{_cut_file_prefix}"

                    if audio_filename:
                        cut_audio_id = f"sqr_cut_audio_{seg_num}"
                        wf[cut_audio_id] = {
                            "class_type": "VHS_LoadAudioUpload",
                            "inputs": {
                                "audio":      audio_filename,
                                "start_time": audio_skip_frames / frame_rate,
                                "duration":   0,
                            }
                        }
                        cut_inputs["audio"] = [cut_audio_id, 0]
                        wf[vc_nid]["inputs"]["audio"] = [cut_audio_id, 0]
                        log(f"  ✓ cut_vc音频: start={audio_skip_frames/frame_rate:.3f}s (={audio_skip_frames}帧)")
                    else:
                        full_inputs.pop("audio", None)
                        cut_inputs.pop("audio", None)

                    wf[full_vc_id] = {"class_type": "VHS_VideoCombine", "inputs": full_inputs}
                    wf[cut_vc_id] = {"class_type": "VHS_VideoCombine", "inputs": cut_inputs}
                    _cut_search_dir = os.path.join(folder_paths.get_output_directory(),
                                                   _subfolder_prefix.rstrip("/\\")) \
                                      if _subfolder_prefix else folder_paths.get_output_directory()
                    sqr_cut_cleanup.append((_cut_search_dir, _cut_file_prefix))
                    sqr_full_cleanup.append((_cut_search_dir, _full_file_prefix))

                latent_save_id = None
                if latent_src_node and _supports_latent_transition:
                    latent_save_id = f"sqr_save_latent_{seg_num}"
                    wf[latent_save_id] = {
                        "class_type": "SaveLatent",
                        "inputs": {
                            "samples": latent_src_node,
                            "filename_prefix": f"latents/sqr_latent_{run_stamp}_seg{seg_num}",
                        },
                    }
                    log(f"  ✓ 已启用latent接力保存: {latent_save_id}")

                if unique_id:
                    positive_links = replace_director_positive_links(wf, unique_id, seg_positive)
                    if director_plan:
                        log(f"  Positive提示词: {seg_positive[:120]}{'...' if len(seg_positive) > 120 else ''} (rewired={positive_links})")
                    guide_node_id = f"sqr_director_guide_{seg_num}"
                    if director_guide_path:
                        wf[guide_node_id] = {
                            "class_type": "LoadImage",
                            "inputs": {"image": director_guide_path},
                        }
                    else:
                        wf[guide_node_id] = {
                            "class_type": "EmptyImage",
                            "inputs": {"width": 1, "height": 1, "batch_size": 1, "color": 0},
                        }
                    guide_output = [guide_node_id, 0]
                    color_ref_path = director_color_config.get("reference_path", "")
                    if director_color_config.get("enabled") and color_ref_path:
                        color_target_id = f"sqr_director_color_target_{seg_num}"
                        color_match_id = f"sqr_director_color_match_{seg_num}"
                        wf[color_target_id] = {
                            "class_type": "LoadImage",
                            "inputs": {"image": color_ref_path},
                        }
                        wf[color_match_id] = {
                            "class_type": "ColorMatchV2",
                            "inputs": {
                                "image_target": [color_target_id, 0],
                                "image_ref": [guide_node_id, 0],
                                "method": "mkl",
                                "strength": director_color_config.get("strength", 1.0),
                                "multithread": True,
                            },
                        }
                        guide_output = [color_match_id, 0]
                    guide_links = _sqr_rewire_image_output(
                        wf, [str(unique_id), 1], guide_output
                    )
                    if guide_links:
                        log(
                            f"  比例引导帧: {'已载入 ' + director_guide_path if director_guide_path else '未提取，使用空白图像'} "
                            f"(rewired={guide_links})"
                        )
                        if director_color_config.get("enabled") and color_ref_path:
                            log(
                                f"  Color Match: ON · method=mkl · "
                                f"strength={director_color_config.get('strength', 1.0):.2f} · 使用第一张参考图"
                            )
                    else:
                        del wf[guide_node_id]
                        if director_color_config.get("enabled") and color_ref_path:
                            wf.pop(f"sqr_director_color_target_{seg_num}", None)
                            wf.pop(f"sqr_director_color_match_{seg_num}", None)
                    if unique_id in wf:
                        del wf[unique_id]

                if ae_nid and ae_nid in wf:
                    if not _supports_latent_transition:
                        wf[ae_nid].get("inputs", {}).pop("transition_latent", None)
                    _ae_inputs_debug = wf[ae_nid].get("inputs", {})
                    log(f"  过渡调试: 提交前 {ae_nid} transition_video={_ae_inputs_debug.get('transition_video')} transition_latent={_ae_inputs_debug.get('transition_latent')} continue_motion={_ae_inputs_debug.get('continue_motion')} length={_ae_inputs_debug.get('length')} video_frame_offset={_ae_inputs_debug.get('video_frame_offset')}")
                log(f"  → 提交中...")
                try:
                    pid = queue_prompt(wf, client_id=_client_id)
                    log(f"  prompt_id={pid[:8]}...")
                    ok  = wait_for_prompt(pid)
                    if ok:
                        log(f"✓ 第{seg_num}段完成")
                        if is_last_seg:
                            _all_done = True
                        if unique_id and not _is_remote:
                            _lv_inputs = base_prompt.get(node_id, {}).get("inputs", {})
                            _ref_video_params = {
                                "video":             _lv_inputs.get("video", ""),
                                "force_rate":        _lv_inputs.get("force_rate", 0),
                                "frame_load_cap":    _lv_inputs.get("frame_load_cap", 0),
                                "skip_first_frames": _lv_inputs.get("skip_first_frames", 0),
                                "select_every_nth":  _lv_inputs.get("select_every_nth", 1),
                            }
                            _next_seg_idx = seg_num
                            if _next_seg_idx < len(seg_list):
                                _frame_offset_for_resume = _frame_offset + seg_list[_next_seg_idx][0]
                            else:
                                _frame_offset_for_resume = _frame_offset + (skip + limit)
                            _trans_fname = f"sqr_trans_{run_stamp}_seg{seg_num}.mp4"
                            write_checkpoint(unique_id, {
                                "unique_id":              unique_id,
                                "run_stamp":                 run_stamp,
                                "completed_seg":          seg_num,
                                "total_segs":             total_segs,
                                "next_seg":               seg_num + 1,
                                "transition_video":       _trans_fname,
                                "transition_latent":      last_latent_name or "",
                                "ref_images":             ref_images_list,
                                "ref_image_groups":      ref_image_groups,
                                "director_snapshot":      director_checkpoint_snapshot,
                                "director_settings": {
                                    "transition_enabled": transition_enabled,
                                    "multi_ref_enabled": multi_ref_enabled,
                                    "replacement_enabled": replacement_enabled,
                                    "video_node_id": node_id,
                                    "reference_node_id": ri_node_id,
                                    "motion_node_id": ae_node_id,
                                    "output_node_id": combine_nid,
                                },
                                "identity_groups":        [
                                    _sqr_ref_identity_groups(group, max_refs=6)[0]
                                    for group in ref_image_groups
                                ],
                                "segment_outputs":        list(segment_output_paths),
                                "segments":               segments,
                                "ref_video":              _ref_video_params.get("video", ""),
                                "ref_video_params":       _ref_video_params,
                                "timestamp":              time.strftime("%Y-%m-%d %H:%M:%S"),
                                "base_frame_offset":      _frame_offset,
                                "frame_offset_for_resume": _frame_offset_for_resume,
                                "total_frames_used":      total_frames,
                                "frame_rate_used":        frame_rate,
                            })
                        _elapsed = time.time() - _t0
                        _frames_done = sum(lmt for _, lmt in segs_to_run[:i+1])
                        save_speed_record(_elapsed, _frames_done)

                        cut_vc_id_done = f"sqr_cut_vc_{seg_num}"
                        if vc_nid:
                            cut_vpath, _ = get_output_video_info(pid, cut_vc_id_done, logger=log)
                            if not cut_vpath:
                                cut_vpath, _ = get_output_video_info(pid, vc_nid, logger=log)
                            if cut_vpath:
                                segment_output_paths.append(cut_vpath)
                                sqr_cut_paths.append(cut_vpath)
                                log(f"  ✓ 裁切输出: {os.path.basename(cut_vpath)}")
                            else:
                                log(f"  ⚠ 未找到裁切输出视频")

                        full_vc_id_done = f"sqr_full_vc_{seg_num}"
                        vpath, vframes = get_output_video_info(pid, full_vc_id_done, logger=log) if vc_nid else (None, None)
                        if not vpath and vc_nid:
                            vpath, vframes = get_output_video_info(pid, vc_nid, logger=log)
                        if latent_save_id:
                            lpath = get_output_latent_info(pid, latent_save_id, logger=log)
                            lname = _sqr_copy_latent_into_input(lpath, unique_id=unique_id, seg_num=seg_num) if lpath else None
                            if lname:
                                last_latent_name = lname
                                log(f"  ✓ latent接力已准备: {lname}")
                            else:
                                last_latent_name = None
                                log(f"  ⚠ latent接力保存失败，下一段将退回视频过渡")
                        if not vpath:
                            log(f"  ⚠ 完整视频获取失败，下段过渡将跳过")
                        if vpath:
                            import shutil
                            input_dir   = folder_paths.get_input_directory()
                            input_fname = f"sqr_trans_{run_stamp}_seg{seg_num}.mp4"
                            input_path  = os.path.join(input_dir, input_fname)
                            try:
                                shutil.copy2(vpath, input_path)
                                last_video_path   = input_path
                                last_video_frames = vframes
                                log(f"  ✓ 已复制: {input_fname} ({vframes}帧，完整未裁切)")
                            except Exception as e:
                                log(f"  ✗ 复制失败: {e}")
                                last_video_path = last_video_frames = None
                        else:
                            log(f"  ⚠ 未找到完整视频，下段过渡将跳过")
                            last_video_path = last_video_frames = None

                        # Update the record after discovering the real files.
                        # This makes final merging recoverable as well as the
                        # transition hand-off to the next unfinished segment.
                        if unique_id and not _is_remote:
                            _checkpoint = read_checkpoint(unique_id) or {}
                            _checkpoint["segment_outputs"] = list(segment_output_paths)
                            _checkpoint["transition_latent"] = last_latent_name or ""
                            if last_video_path:
                                _checkpoint["transition_video"] = os.path.basename(last_video_path)
                            write_checkpoint(unique_id, _checkpoint)
                    else:
                        log(f"✗ 第{seg_num}段出错，终止。")
                        break
                except Exception as e:
                    log(f"✗ 提交失败：{e}")
                    break

            if pre_segment_paths:
                log(f"续跑合并：前段 {len(pre_segment_paths)} 个 + 本次 {len(segment_output_paths)} 个")
                segment_output_paths = pre_segment_paths + segment_output_paths

            if len(segment_output_paths) >= 2:
                log(f"开始合并 {len(segment_output_paths)} 段视频...")
                output_dir   = folder_paths.get_output_directory()
                if vc_nid and base_prompt and vc_nid in base_prompt:
                    _mp = base_prompt[vc_nid]["inputs"].get("filename_prefix", "")
                    _sl = max(_mp.rfind("/"), _mp.rfind("\\"))
                    _sub = _mp[:_sl+1] if _sl >= 0 else ""
                    if _sub:
                        os.makedirs(os.path.join(output_dir, _sub.rstrip("/\\")), exist_ok=True)
                else:
                    _sub = ""
                merged_fname = f"sqr_merged_{run_stamp}.mp4"
                merged_path  = _sqr_unique_filepath(os.path.join(output_dir, _sub + merged_fname))
                merged_fname = os.path.basename(merged_path)
                source_audio_path = _sqr_resolve_media_path(audio_filename)
                if source_audio_path:
                    log(f"Final audio: full source track {os.path.basename(source_audio_path)}, target={total_frames} frames")
                else:
                    log("Final audio: source media not found; keeping segmented audio")
                if merge_videos(segment_output_paths, merged_path,
                               target_fps=frame_rate if pre_segment_paths else None,
                               source_audio_path=source_audio_path,
                               total_frames=total_frames,
                               source_fps=frame_rate):
                    log(f"✓ 合并完成: {_sub + merged_fname}")
                else:
                    log(f"✗ 合并失败，请手动拼接各段视频")
            elif len(segment_output_paths) == 1:
                log(f"只有1段，无需合并")

            for (_clean_dir, _clean_prefix) in sqr_cut_cleanup:
                try:
                    if not os.path.isdir(_clean_dir):
                        continue
                    for _f in os.listdir(_clean_dir):
                        if not _f.startswith(_clean_prefix):
                            continue
                        _fpath = os.path.join(_clean_dir, _f)
                        if _f.endswith(".mp4") and "-audio" in _f:
                            continue
                        if _f.endswith(".mp4") or _f.endswith(".png"):
                            try:
                                os.remove(_fpath)
                                print(f"[SQR] 已清理临时文件: {_f}")
                            except Exception:
                                pass
                except Exception:
                    pass

            for (_clean_dir, _clean_prefix) in sqr_full_cleanup:
                try:
                    if not os.path.isdir(_clean_dir):
                        continue
                    for _f in os.listdir(_clean_dir):
                        if not _f.startswith(_clean_prefix):
                            continue
                        _fpath = os.path.join(_clean_dir, _f)
                        if _f.endswith(".mp4") or _f.endswith(".png"):
                            try:
                                os.remove(_fpath)
                                print(f"[SQR] 已清理内部过渡源: {_f}")
                            except Exception:
                                pass
                except Exception:
                    pass

            _sqr_save_png = _sqr_to_bool(sqr_save_png, True)
            # A segment is not recoverably complete until its visible output
            # file has been found. Keep the Director checkpoint if discovery
            # or final collection was incomplete.
            if len(segment_output_paths) < len(segs_to_run) + len(pre_segment_paths):
                _all_done = False
            _should_clean_main_png = not _sqr_save_png
            print(f"[SQR] Save png 设置: {sqr_save_png} → {'保留' if _sqr_save_png else '清理'}主节点 png")

            if _should_clean_main_png and vc_nid and base_prompt and vc_nid in base_prompt:
                try:
                    _main_prefix = base_prompt[vc_nid]["inputs"].get("filename_prefix", "")
                    _output_root = folder_paths.get_output_directory()
                    _sl = max(_main_prefix.rfind("/"), _main_prefix.rfind("\\"))
                    _sub = _main_prefix[:_sl+1] if _sl >= 0 else ""
                    _fname_prefix = _main_prefix[_sl+1:] if _sl >= 0 else _main_prefix
                    _search_dir = os.path.join(_output_root, _sub.rstrip("/\\")) if _sub else _output_root
                    if os.path.isdir(_search_dir) and _fname_prefix:
                        for _f in os.listdir(_search_dir):
                            if _f.startswith(_fname_prefix) and _f.endswith(".png"):
                                try:
                                    os.remove(os.path.join(_search_dir, _f))
                                    print(f"[SQR] 已清理主节点元数据图: {_f}")
                                except Exception:
                                    pass
                except Exception:
                    pass

            if unique_id:
                if _all_done:
                    clear_checkpoint(unique_id)
                    _sqr_cleanup_ref_images(ref_images_list, unique_id=unique_id)
                    print("[SQR] checkpoint 已清除（全部完成）")
                else:
                    print("[SQR] 任务中断，checkpoint 保留供续跑检测")

            log("═══ 全部完成 ═══")

        if unique_id:
            _old_ckpt = read_checkpoint(unique_id)
            _old_refs = _old_ckpt.get("ref_images", []) if isinstance(_old_ckpt, dict) else []
            clear_checkpoint(unique_id)
            _sqr_cleanup_ref_images(_old_refs, unique_id=unique_id, keep_paths=ref_images_list)

        if _frame_offset > 0:
            _mode_header = f"=== 重新设计续跑模式（帧偏移={_frame_offset}，跳过前{_frame_offset}帧）==="
        elif resume_enabled:
            _mode_header = "=== 自动续跑模式 ==="
        else:
            _mode_header = "=== 全新生成 ==="
        exec_msg = _mode_header + "\n" + plan_text

        t = threading.Thread(target=submit_all, daemon=True)
        t.start()
        if _need_interrupt:
            def _ei(): time.sleep(0.005); _do_interrupt()
            threading.Thread(target=_ei, daemon=True).start()
        _sqr_log(unique_id, exec_msg)
        return {}


class WanAniDirector(SegmentQueueRunner):
    CATEGORY = "video/utils"
    RETURN_TYPES = ("STRING", "IMAGE")
    RETURN_NAMES = ("positive", "比例引导帧")

    @classmethod
    def INPUT_TYPES(cls):
        data = copy.deepcopy(super().INPUT_TYPES())
        data["required"]["director_data"] = ("STRING", {
            "default": "{}",
            "tooltip": "JSON state for the WAN ANI DIRECTOR UI."
        })
        return data

    def run(self, *args, director_data="{}", **kwargs):
        resolved = resolve_director_composed_prompts(director_data)
        guide_frame = load_first_director_guide_frame(director_data)
        super().run(*args, director_data=director_data, **kwargs)
        return (resolved[0] if resolved else "", guide_frame)


NODE_CLASS_MAPPINGS = {
    "WanAniSQRSegmentQueue": SegmentQueueRunner,
    "WanAniDirector": WanAniDirector,
    "SQRReplaceBatchPrefix": SQRReplaceBatchPrefix,
    "SQRImageBatchConcat": SQRImageBatchConcat,
    "SQRRepeatFirstFrames": SQRRepeatFirstFrames,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "WanAniSQRSegmentQueue": "WanAni SQR",
    "WanAniDirector": "WAN ANI DIRECTOR",
    "SQRReplaceBatchPrefix": "SQR Replace Batch Prefix",
    "SQRRepeatFirstFrames": "SQR Repeat First Frames",
}


# ── 后端 API ─────────────────────────────────────────────────────
@server.PromptServer.instance.routes.get("/sqr/logs")
async def sqr_get_logs(request):
    uid = request.rel_url.query.get("uid", "")
    return web.json_response({"logs": list(_sqr_log_buf.get(str(uid), []))})

@server.PromptServer.instance.routes.post("/sqr/logs/clear")
async def sqr_clear_logs(request):
    _sqr_log_clear(request.rel_url.query.get("uid", ""))
    return web.json_response({"ok": True})

@server.PromptServer.instance.routes.get("/sqr/checkpoint")
async def sqr_get_checkpoint(request):
    uid = request.rel_url.query.get("uid", "")
    if not uid:
        return web.json_response({"checkpoint": None})
    ckpt = read_checkpoint(uid)
    if ckpt:
        input_dir = folder_paths.get_input_directory()
        tv = ckpt.get("transition_video", "")
        tv_path = os.path.join(input_dir, tv) if tv else ""
        ckpt["transition_exists"] = os.path.isfile(tv_path)
        if ckpt["transition_exists"] and tv_path:
            tv_mtime   = os.path.getmtime(tv_path)
            ckpt_mtime = os.path.getmtime(get_checkpoint_path(uid))
            if tv_mtime > ckpt_mtime + 60:
                ckpt["transition_exists"] = False
        import urllib.parse as _up
        cur_params_str = request.rel_url.query.get("ref_params", "")
        ckpt_params    = ckpt.get("ref_video_params", {})
        if not ckpt_params and ckpt.get("ref_video"):
            ckpt_params = {"video": ckpt.get("ref_video")}
        if cur_params_str and ckpt_params:
            try:
                import json as _json
                cur_params = _json.loads(_up.unquote(cur_params_str))
                mismatches = []
                for key in ("video", "force_rate", "frame_load_cap", "skip_first_frames", "select_every_nth"):
                    cv = cur_params.get(key, None)
                    kv = ckpt_params.get(key, None)
                    if key == "video":
                        if str(cv or "") != str(kv or ""):
                            mismatches.append(key)
                    else:
                        try:
                            if float(cv or 0) != float(kv or 0):
                                mismatches.append(key)
                        except (TypeError, ValueError):
                            if str(cv) != str(kv):
                                mismatches.append(key)
                ckpt["ref_video_match"]    = len(mismatches) == 0
                ckpt["ref_video_mismatches"] = mismatches
            except Exception:
                ckpt["ref_video_match"] = True
        else:
            ckpt["ref_video_match"]    = True
            ckpt["ref_video_mismatches"] = []
    return web.json_response({"checkpoint": ckpt})


@server.PromptServer.instance.routes.get("/sqr/director_checkpoint")
async def sqr_get_director_checkpoint(request):
    """Manual-only Director recovery probe used by its Resume button."""
    uid = request.rel_url.query.get("uid", "")
    ckpt = read_checkpoint(uid) if uid else None
    if not isinstance(ckpt, dict) or not isinstance(ckpt.get("director_snapshot"), dict):
        return web.json_response({"checkpoint": None})

    snapshot = copy.deepcopy(ckpt["director_snapshot"])
    missing = []
    segments_data = snapshot.get("segments", []) if isinstance(snapshot, dict) else []
    for seg_index, segment in enumerate(segments_data, start=1):
        if not isinstance(segment, dict):
            continue
        available_refs = []
        for ref_index, entry in enumerate(segment.get("references", []) or [], start=1):
            path = _sqr_ref_entry_path(entry)
            resolved = _sqr_resolve_media_path(path)
            if resolved and os.path.isfile(resolved):
                restored = copy.deepcopy(entry)
                if isinstance(restored, dict):
                    restored["path"] = resolved
                else:
                    restored = resolved
                available_refs.append(restored)
            else:
                missing.append(f"segment {seg_index} reference {ref_index}")
        segment["references"] = available_refs

        guide = segment.get("guide_frame")
        guide_path = _sqr_ref_entry_path(guide)
        if guide_path:
            resolved = _sqr_resolve_media_path(guide_path)
            if resolved and os.path.isfile(resolved):
                if isinstance(guide, dict):
                    guide["path"] = resolved
            else:
                segment["guide_frame"] = None
                missing.append(f"segment {seg_index} guide frame")

        marking = segment.get("sam3_marking")
        if isinstance(marking, dict) and marking.get("frame_path"):
            resolved = _sqr_resolve_media_path(marking.get("frame_path"))
            if resolved and os.path.isfile(resolved):
                marking["frame_path"] = resolved
            else:
                marking["frame_path"] = ""
                missing.append(f"segment {seg_index} SAM3 frame")

    outputs = []
    for index, raw_path in enumerate(ckpt.get("segment_outputs", []) or [], start=1):
        resolved = _sqr_resolve_media_path(raw_path)
        if resolved and os.path.isfile(resolved):
            outputs.append(resolved)
        else:
            missing.append(f"completed segment output {index}")

    transition = _sqr_resolve_media_path(ckpt.get("transition_video", ""))
    if not transition or not os.path.isfile(transition):
        transition = ""
        missing.append("resume transition video")

    source_video = _sqr_resolve_media_path(ckpt.get("ref_video", ""))
    if ckpt.get("ref_video") and (not source_video or not os.path.isfile(source_video)):
        missing.append("reference video")

    result = copy.deepcopy(ckpt)
    result["director_snapshot"] = snapshot
    result["segment_outputs"] = outputs
    result["transition_video"] = transition
    result["source_video"] = source_video if source_video and os.path.isfile(source_video) else ""
    result["missing"] = missing
    return web.json_response({"checkpoint": result})


def _sqr_safe_upload_name(input_dir: str, original: str, default_ext: str) -> str:
    """生成不冲突的安全文件名（保留原扩展名，去除路径分隔符）。"""
    base = os.path.basename(str(original or "")).strip()
    if not base:
        base = f"upload{default_ext}"
    # 去掉危险字符
    base = base.replace("\\", "_").replace("/", "_")
    name, ext = os.path.splitext(base)
    if not ext:
        ext = default_ext
    # 文件名前缀，便于识别 + 避免与已有文件冲突
    safe = f"sqr_up_{name}{ext}"
    dst = os.path.join(input_dir, safe)
    if not os.path.exists(dst):
        return safe
    # 加时间戳兜底
    stamp = _sqr_now_stamp()
    safe = f"sqr_up_{name}_{stamp}{ext}"
    return safe


@server.PromptServer.instance.routes.post("/sqr/upload_images")
async def sqr_upload_images(request):
    """接收浏览器多文件上传，保存到 ComfyUI input/ 目录。"""
    saved = []
    try:
        input_dir = folder_paths.get_input_directory()
        os.makedirs(input_dir, exist_ok=True)
        reader = await request.multipart()
        async for part in reader:
            if part.name not in ("files[]", "files", "file"):
                continue
            filename = part.filename or ""
            if not filename:
                continue
            safe = _sqr_safe_upload_name(input_dir, filename, ".png")
            dst = os.path.join(input_dir, safe)
            try:
                with open(dst, "wb") as f:
                    while True:
                        chunk = await part.read_chunk(1024 * 64)
                        if not chunk:
                            break
                        f.write(chunk)
                saved.append(safe)
            except Exception as e:
                print(f"[SQR] upload_images 写入失败 {filename}: {e}")
        return web.json_response({"saved": saved})
    except Exception as e:
        print(f"[SQR] upload_images 出错: {_sqr_format_exc(e)}")
        return web.json_response({"saved": saved, "error": str(e)})


@server.PromptServer.instance.routes.post("/sqr/upload_video")
async def sqr_upload_video(request):
    """接收浏览器单文件视频上传，保存到 ComfyUI input/ 目录。"""
    try:
        input_dir = folder_paths.get_input_directory()
        os.makedirs(input_dir, exist_ok=True)
        reader = await request.multipart()
        async for part in reader:
            if part.name not in ("file", "files[]", "files"):
                continue
            filename = part.filename or ""
            if not filename:
                continue
            safe = _sqr_safe_upload_name(input_dir, filename, ".mp4")
            dst = os.path.join(input_dir, safe)
            try:
                with open(dst, "wb") as f:
                    while True:
                        chunk = await part.read_chunk(1024 * 256)
                        if not chunk:
                            break
                        f.write(chunk)
                return web.json_response({"saved": safe})
            except Exception as e:
                print(f"[SQR] upload_video 写入失败 {filename}: {e}")
                return web.json_response({"saved": "", "error": str(e)})
        return web.json_response({"saved": "", "error": "未收到文件"})
    except Exception as e:
        print(f"[SQR] upload_video 出错: {_sqr_format_exc(e)}")
        return web.json_response({"saved": "", "error": str(e)})


@server.PromptServer.instance.routes.get("/sqr/list_images")
async def sqr_list_images(request):
    import re
    img_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    def nat_key(s):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]
    try:
        files = sorted([f for f in os.listdir(folder_paths.get_input_directory())
                        if os.path.splitext(f)[1].lower() in img_exts], key=nat_key)
    except Exception:
        files = []
    return web.json_response({"images": files})


@server.PromptServer.instance.routes.get("/sqr/list_videos")
async def sqr_list_videos(request):
    import re
    vid_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    def sort_key(fname):
        m = re.match(r"sqr_trans_[0-9_]+_seg(\d+)\.mp4$", fname, re.IGNORECASE) or re.match(r"sqr_trans_[a-f0-9]+_seg(\d+)\.mp4$", fname, re.IGNORECASE)
        if m:
            return (0, int(m.group(1)), fname)
        m = re.match(r"segment_transition_seg(\d+)\.mp4$", fname, re.IGNORECASE)
        if m:
            return (0, int(m.group(1)), fname)
        parts = re.split(r"(\d+)", fname)
        return (1, 0, tuple(int(p) if p.isdigit() else p.lower() for p in parts))
    try:
        files = sorted(
            [f for f in os.listdir(folder_paths.get_input_directory())
             if os.path.splitext(f)[1].lower() in vid_exts],
            key=sort_key
        )
    except Exception:
        files = []
    return web.json_response({"videos": files})


@server.PromptServer.instance.routes.get("/sqr/video_thumb")
async def sqr_video_thumb(request):
    fpath = request.rel_url.query.get("file", "").strip()
    if not fpath:
        return web.Response(status=400)

    raw_path = fpath
    fpath = _sqr_resolve_media_path(fpath)
    if not fpath or not os.path.isfile(fpath):
        print(f"[SQR] video_thumb: 文件不存在或无法解析: {raw_path}")
        return web.Response(status=404)

    try:
        import cv2
        cap = cv2.VideoCapture(fpath)
        try:
            if not cap.isOpened():
                print(f"[SQR] video_thumb: cv2 无法打开视频: {fpath}")
                return web.Response(status=404)

            ok, frame = cap.read()
            if not ok or frame is None:
                print(f"[SQR] video_thumb: 读取首帧失败: {fpath}")
                return web.Response(status=404)
        finally:
            cap.release()

        h, w = frame.shape[:2]
        new_w = 160
        new_h = int(h * new_w / w)
        frame = cv2.resize(frame, (new_w, new_h))
        ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok2:
            print(f"[SQR] video_thumb: JPEG 编码失败: {fpath}")
            return web.Response(status=500)

        return web.Response(body=buf.tobytes(), content_type="image/jpeg")

    except ModuleNotFoundError as e:
        if getattr(e, "name", "") == "cv2":
            print("[SQR] video_thumb失败: 未安装 cv2 / opencv-python。请安装 requirements.txt 中的依赖后重启 ComfyUI。")
        else:
            print(f"[SQR] video_thumb失败: {_sqr_format_exc(e)}")
        return web.Response(status=500)
    except Exception as e:
        print(f"[SQR] video_thumb失败: {_sqr_format_exc(e)}")
        return web.Response(status=500)


@server.PromptServer.instance.routes.get("/sqr/video_info")
async def sqr_video_info(request):
    """Return exact source metadata for the Director timeline."""
    raw_path = request.rel_url.query.get("file", "")
    fpath = _sqr_resolve_media_path(raw_path)
    if not fpath or not os.path.isfile(fpath):
        return web.json_response({"ok": False, "error": "video not found"}, status=404)
    try:
        import cv2
        cap = cv2.VideoCapture(fpath)
        try:
            if not cap.isOpened():
                raise RuntimeError("cv2 could not open video")
            frames = max(0, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            return web.json_response({
                "ok": True,
                "frames": frames,
                "fps": fps,
                "duration": (frames / fps) if frames > 0 and fps > 0 else 0.0,
                "filename": os.path.basename(fpath),
            })
        finally:
            cap.release()
    except Exception as e:
        _sqr_log_cv2_issue("", "读取 Director 视频信息失败", e)
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@server.PromptServer.instance.routes.get("/sqr/browse_videos")
async def sqr_browse_videos(request):
    import re
    vid_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    def nat_key(s):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]
    def sort_key(fname):
        m = re.match(r"sqr_trans_[0-9_]+_seg(\d+)\.mp4$", fname, re.IGNORECASE) or re.match(r"sqr_trans_[a-f0-9]+_seg(\d+)\.mp4$", fname, re.IGNORECASE)
        if m:
            return (0, int(m.group(1)), fname)
        m = re.match(r"segment_transition_seg(\d+)\.mp4$", fname, re.IGNORECASE)
        if m:
            return (0, int(m.group(1)), fname)
        parts = re.split(r"(\d+)", fname)
        return (1, 0, tuple(int(p) if p.isdigit() else p.lower() for p in parts))
    req_path = request.rel_url.query.get("path", "").strip()
    import platform, string as _str
    if req_path == "__drives__":
        drives = []
        if platform.system() == "Windows":
            for d in _str.ascii_uppercase:
                dp = d + ":\\"
                if os.path.exists(dp):
                    drives.append({"label": dp, "path": dp, "is_drive": True})
        else:
            drives.append({"label": "/", "path": "/", "is_drive": True})
        return web.json_response({"type": "roots", "roots": drives})
    if not req_path:
        starts = []
        for label, p in [("ComfyUI input", folder_paths.get_input_directory()),
                         ("ComfyUI output", folder_paths.get_output_directory())]:
            if os.path.isdir(p):
                starts.append({"label": label, "path": p})
        starts.append({"label": "此电脑", "path": "__drives__", "is_virtual": True})
        home = os.path.expanduser("~")
        for sub in ["Desktop", "桌面", "Videos", "视频", "Downloads", "下载"]:
            p = os.path.join(home, sub)
            if os.path.isdir(p):
                starts.append({"label": sub, "path": p})
        return web.json_response({"type": "roots", "roots": starts})
    req_path = os.path.realpath(req_path)
    if not os.path.isdir(req_path):
        return web.json_response({"error": "路径不存在"}, status=400)
    try:
        entries = os.listdir(req_path)
    except PermissionError:
        return web.json_response({"error": "无权限访问"}, status=403)
    folders = sorted([e for e in entries
                      if os.path.isdir(os.path.join(req_path, e))
                      and not e.startswith(".")], key=nat_key)
    videos  = sorted([e for e in entries
                      if os.path.splitext(e)[1].lower() in vid_exts], key=sort_key)
    parent  = os.path.dirname(req_path) if req_path != os.path.dirname(req_path) else None
    return web.json_response({
        "type":    "dir",
        "path":    req_path,
        "parent":  parent,
        "folders": folders,
        "videos":  videos,
    })


@server.PromptServer.instance.routes.get("/sqr/image_thumb")
async def sqr_image_thumb(request):
    fname = request.rel_url.query.get("file", "")
    if not fname:
        return web.Response(status=400)
    path = _sqr_resolve_media_path(fname)
    if not path or not os.path.isfile(path):
        return web.Response(status=404)
    return web.FileResponse(path, headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    })


@server.PromptServer.instance.routes.get("/sqr/image_info")
async def sqr_image_info(request):
    fname = request.rel_url.query.get("file", "")
    path = _sqr_resolve_media_path(fname)
    if not path or not os.path.isfile(path):
        return web.json_response({"ok": False, "error": "image not found"}, status=404)
    try:
        from PIL import Image
        with Image.open(path) as image:
            width, height = image.size
        return web.json_response({"ok": True, "width": int(width), "height": int(height)})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


def _sqr_remove_solid_background(image, threshold=32, feather=18):
    """Remove only near-solid pixels connected to the image boundary."""
    import cv2
    import numpy as np
    from PIL import Image

    rgba = image.convert("RGBA")
    array = np.asarray(rgba, dtype=np.float32).copy()
    h, w = array.shape[:2]
    sample = max(1, min(h, w, 24))
    corners = np.concatenate((
        array[:sample, :sample, :3].reshape(-1, 3),
        array[:sample, -sample:, :3].reshape(-1, 3),
        array[-sample:, :sample, :3].reshape(-1, 3),
        array[-sample:, -sample:, :3].reshape(-1, 3),
    ), axis=0)
    background = np.median(corners, axis=0)
    distance = np.sqrt(np.sum((array[:, :, :3] - background) ** 2, axis=2))
    threshold = max(1.0, min(220.0, float(threshold)))
    feather = max(1.0, min(96.0, float(feather)))
    candidate = (distance < threshold + feather).astype(np.uint8)
    _, labels = cv2.connectedComponents(candidate, connectivity=4)
    edge_labels = np.unique(np.concatenate((
        labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]
    )))
    edge_labels = edge_labels[edge_labels != 0]
    connected = np.isin(labels, edge_labels) if edge_labels.size else np.zeros_like(candidate, dtype=bool)
    keyed_alpha = np.clip((distance - threshold) / feather, 0.0, 1.0) * array[:, :, 3]
    array[:, :, 3] = np.where(connected, keyed_alpha, array[:, :, 3])
    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), "RGBA")


def _sqr_adjust_layer_details(image, raw):
    import cv2
    import numpy as np
    from PIL import Image

    alpha = image.getchannel("A")
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    legacy_brightness = max(0.5, min(1.5, float(raw.get("detail_brightness", 1.0))))
    exposure = max(-3.0, min(3.0, float(raw.get("detail_exposure", math.log2(legacy_brightness)))))
    contrast = max(0.5, min(1.5, float(raw.get("detail_contrast", 1.0))))
    highlights = max(-1.0, min(1.0, float(raw.get("detail_highlights", 0.0))))
    shadows = max(-1.0, min(1.0, float(raw.get("detail_shadows", 0.0))))
    whites = max(-1.0, min(1.0, float(raw.get("detail_whites", 0.0))))
    blacks = max(-1.0, min(1.0, float(raw.get("detail_blacks", 0.0))))
    temperature = max(-100.0, min(100.0, float(raw.get("detail_temperature", 0.0))))
    tint = max(-100.0, min(100.0, float(raw.get("detail_tint", 0.0))))
    hue = max(-180.0, min(180.0, float(raw.get("detail_hue", 0.0))))
    saturation = max(0.0, min(2.0, float(raw.get("detail_saturation", 1.0))))
    vibrance = max(-1.0, min(1.0, float(raw.get("detail_vibrance", 0.0))))
    texture = max(-1.0, min(1.0, float(raw.get("detail_texture", 0.0))))
    clarity = max(-1.0, min(1.0, float(raw.get("detail_clarity", 0.0))))
    sharpness = max(0.0, min(3.0, float(raw.get("detail_sharpness", 1.0))))
    denoise = max(0.0, min(1.0, float(raw.get("detail_denoise", 0.0))))
    blur = max(0.0, min(20.0, float(raw.get("detail_blur", 0.0))))

    pixels *= 2.0 ** exposure
    luminance = np.mean(pixels, axis=2, keepdims=True)
    pixels += highlights * np.square(np.clip(luminance, 0.0, 1.0)) * 0.35
    pixels += shadows * np.square(1.0 - np.clip(luminance, 0.0, 1.0)) * 0.35
    pixels += whites * np.power(np.clip(luminance, 0.0, 1.0), 4.0) * 0.3
    pixels += blacks * np.power(1.0 - np.clip(luminance, 0.0, 1.0), 4.0) * 0.3
    pixels = (pixels - 0.5) * contrast + 0.5
    if abs(temperature) > 0.001:
        shift = temperature / 100.0
        pixels[:, :, 0] *= 1.0 + 0.25 * shift
        pixels[:, :, 2] *= 1.0 - 0.25 * shift
    if abs(tint) > 0.001:
        shift = tint / 100.0
        pixels[:, :, 0] *= 1.0 + 0.08 * shift
        pixels[:, :, 1] *= 1.0 - 0.16 * shift
        pixels[:, :, 2] *= 1.0 + 0.08 * shift
    rgb8 = np.clip(pixels * 255.0, 0, 255).astype(np.uint8)
    hsv = cv2.cvtColor(rgb8, cv2.COLOR_RGB2HSV).astype(np.float32)
    hsv[:, :, 0] = (hsv[:, :, 0] + hue / 2.0) % 180.0
    current_saturation = hsv[:, :, 1] / 255.0
    vibrance_gain = 1.0 + vibrance * (1.0 - current_saturation)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * saturation * vibrance_gain, 0, 255)
    rgb8 = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)
    for name, channels in (("y", (0, 1, 2)), ("r", (0,)), ("g", (1,)), ("b", (2,))):
        points = raw.get(f"detail_curve_{name}", [[0.0, 0.0], [1.0, 1.0]])
        if not isinstance(points, list) or len(points) < 2:
            continue
        clean = sorted((max(0.0, min(1.0, float(point[0]))), max(0.0, min(1.0, float(point[1])))) for point in points if isinstance(point, (list, tuple)) and len(point) >= 2)
        if len(clean) < 2:
            continue
        lut = np.interp(np.arange(256) / 255.0, [point[0] for point in clean], [point[1] for point in clean])
        lut = np.clip(lut * 255.0, 0, 255).astype(np.uint8)
        for channel in channels:
            rgb8[:, :, channel] = lut[rgb8[:, :, channel]]
    if abs(texture) > 0.001:
        fine = rgb8.astype(np.float32) - cv2.GaussianBlur(rgb8, (0, 0), 1.2).astype(np.float32)
        rgb8 = np.clip(rgb8.astype(np.float32) + fine * texture, 0, 255).astype(np.uint8)
    if abs(clarity) > 0.001:
        local = rgb8.astype(np.float32) - cv2.GaussianBlur(rgb8, (0, 0), 8.0).astype(np.float32)
        rgb8 = np.clip(rgb8.astype(np.float32) + local * clarity * 0.7, 0, 255).astype(np.uint8)
    if denoise > 0.001:
        strength = max(1, round(denoise * 12))
        rgb8 = cv2.fastNlMeansDenoisingColored(rgb8, None, strength, strength, 7, 21)
    if blur > 0.001:
        rgb8 = cv2.GaussianBlur(rgb8, (0, 0), max(0.1, blur))
    if abs(sharpness - 1.0) > 0.001:
        soft = cv2.GaussianBlur(rgb8, (0, 0), 1.0)
        rgb8 = np.clip(rgb8.astype(np.float32) + (rgb8.astype(np.float32) - soft.astype(np.float32)) * (sharpness - 1.0), 0, 255).astype(np.uint8)
    rgb = Image.fromarray(rgb8, "RGB")
    rgb.putalpha(alpha)
    return rgb


_sqr_sam3_segment_instance = None
_sqr_sam3_segment_lock = threading.Lock()


@server.PromptServer.instance.routes.post("/sqr/sam3_cutout")
async def sqr_sam3_cutout(request):
    """Create and cache an alpha cutout with the installed RMBG SAM3 node."""
    try:
        import nodes
        import numpy as np
        import torch
        from PIL import Image, ImageOps

        payload = await request.json()
        source_path = _sqr_resolve_media_path(str(payload.get("image") or ""))
        if not source_path or not os.path.isfile(source_path):
            raise FileNotFoundError("Source image was not found.")
        prompt_text = str(payload.get("prompt") or "person").strip() or "person"
        confidence = max(0.05, min(0.95, float(payload.get("confidence", 0.5))))
        mask_blur = max(0, min(64, _sqr_to_int(payload.get("mask_blur"), 2)))
        mask_offset = max(-64, min(64, _sqr_to_int(payload.get("mask_offset"), 0)))

        node_class = nodes.NODE_CLASS_MAPPINGS.get("SAM3Segment")
        if node_class is None:
            raise RuntimeError("SAM3 Segmentation (RMBG) is not installed or failed to load.")
        with Image.open(source_path) as opened:
            source = ImageOps.exif_transpose(opened).convert("RGB")
        tensor = torch.from_numpy(np.asarray(source, dtype=np.float32) / 255.0).unsqueeze(0)

        global _sqr_sam3_segment_instance
        with _sqr_sam3_segment_lock:
            if _sqr_sam3_segment_instance is None:
                _sqr_sam3_segment_instance = node_class()
            _, masks, _ = _sqr_sam3_segment_instance.segment(
                tensor, prompt_text, "Auto",
                confidence_threshold=confidence,
                max_segments=0,
                segment_pick=0,
                mask_blur=mask_blur,
                mask_offset=mask_offset,
                invert_output=False,
                unload_model=False,
                background="Alpha",
                background_color="#000000",
                output_mode="Merged",
            )
        if masks is None or masks.numel() == 0:
            raise RuntimeError("SAM3 did not return a mask for this prompt.")
        mask = masks[0].detach().float().clamp(0, 1).cpu().numpy()
        if float(mask.max()) <= 0.001:
            raise RuntimeError("SAM3 found no matching subject. Try a more specific prompt or lower confidence.")
        alpha = Image.fromarray((mask * 255.0).astype(np.uint8), "L")
        result = source.convert("RGBA")
        result.putalpha(alpha)

        subfolder = "sqr_sam3_cutouts"
        output_dir = os.path.join(folder_paths.get_input_directory(), subfolder)
        os.makedirs(output_dir, exist_ok=True)
        filename = f"sqr_sam3_{_sqr_now_stamp()}.png"
        result.save(os.path.join(output_dir, filename), "PNG")
        return web.json_response({
            "ok": True,
            "path": f"{subfolder}/{filename}",
            "width": result.width,
            "height": result.height,
        })
    except Exception as e:
        print(f"[SQR] sam3_cutout error: {_sqr_format_exc(e)}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@server.PromptServer.instance.routes.get("/sqr/image_original")
async def sqr_image_original(request):
    """Serve the original image bytes for full-resolution editor previews."""
    try:
        path = _sqr_resolve_media_path(request.rel_url.query.get("file", ""))
        if not path or not os.path.isfile(path):
            raise FileNotFoundError("Image was not found.")
        return web.FileResponse(path)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=404)


@server.PromptServer.instance.routes.post("/sqr/select_save_directory")
async def sqr_select_save_directory(request):
    """Open the native Windows directory picker on the ComfyUI host."""
    try:
        import asyncio

        def choose_directory():
            import base64
            import subprocess

            initial = folder_paths.get_input_directory().replace("'", "''")
            script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$owner = New-Object System.Windows.Forms.Form
$owner.Text = 'Select Save Folder'
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.Opacity = 0.01
$owner.Show()
$owner.Activate()
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Select save folder'
$dialog.SelectedPath = '{initial}'
$dialog.ShowNewFolderButton = $true
$dialog.AutoUpgradeEnabled = $true
try {{
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {{
        [Console]::Out.Write($dialog.SelectedPath)
    }}
}} finally {{
    $owner.Close()
    $owner.Dispose()
}}
"""
            encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-STA", "-EncodedCommand", encoded],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=300,
            )
            if completed.returncode != 0:
                error = completed.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(error or "Windows folder picker failed.")
            return completed.stdout.decode("utf-8", errors="replace").strip()

        selected = await asyncio.to_thread(choose_directory)
        return web.json_response({"ok": True, "path": os.path.normpath(selected) if selected else ""})
    except Exception as e:
        print(f"[SQR] directory picker error: {_sqr_format_exc(e)}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@server.PromptServer.instance.routes.post("/sqr/compose_reference")
async def sqr_compose_reference(request):
    """Compose positioned reference cutouts onto a solid or uploaded background."""
    try:
        from PIL import Image, ImageOps

        payload = await request.json()
        width = max(64, min(4096, _sqr_to_int(payload.get("width"), 1024)))
        height = max(64, min(4096, _sqr_to_int(payload.get("height"), 1024)))
        layers = payload.get("layers", [])
        if not isinstance(layers, list) or not layers:
            raise ValueError("At least one person layer is required.")

        background_mode = str(payload.get("background", "black")).lower()
        colors = {
            "black": (0, 0, 0, 255),
            "white": (255, 255, 255, 255),
            "gray": (128, 128, 128, 255),
            "transparent": (0, 0, 0, 0),
        }
        if background_mode == "custom":
            color = str(payload.get("background_color") or "#202020").lstrip("#")
            if len(color) == 6:
                try:
                    colors["custom"] = (*tuple(int(color[i:i + 2], 16) for i in (0, 2, 4)), 255)
                except ValueError:
                    colors["custom"] = (32, 32, 32, 255)
            else:
                colors["custom"] = (32, 32, 32, 255)
        background_path = _sqr_resolve_media_path(str(payload.get("background_image") or ""))
        if background_mode == "image" and background_path and os.path.isfile(background_path):
            with Image.open(background_path) as opened:
                bg = ImageOps.exif_transpose(opened).convert("RGBA")
            ratio = max(width / max(1, bg.width), height / max(1, bg.height))
            resized = bg.resize(
                (max(1, round(bg.width * ratio)), max(1, round(bg.height * ratio))),
                Image.Resampling.LANCZOS,
            )
            left = (resized.width - width) // 2
            top = (resized.height - height) // 2
            canvas = resized.crop((left, top, left + width, top + height))
        else:
            canvas = Image.new("RGBA", (width, height), colors.get(background_mode, colors["black"]))

        composed = 0
        for raw in layers[:12]:
            if not isinstance(raw, dict):
                continue
            cutout_mode = str(raw.get("cutout_mode") or "solid").lower()
            selected_path = raw.get("sam3_cutout_path") if cutout_mode == "sam3" else raw.get("path")
            path = _sqr_resolve_media_path(str(selected_path or raw.get("path") or ""))
            if not path or not os.path.isfile(path):
                continue
            with Image.open(path) as opened:
                layer = ImageOps.exif_transpose(opened).convert("RGBA")
            if cutout_mode == "solid" and _sqr_to_bool(raw.get("remove_background", True), True):
                layer = _sqr_remove_solid_background(layer, raw.get("threshold", 32), raw.get("feather", 18))
            layer = _sqr_adjust_layer_details(layer, raw)
            scale_value = max(0.05, min(5.0, float(raw.get("scale", 1.0))))
            target_w = max(1, round(width * 0.35 * scale_value))
            target_h = max(1, round(target_w * layer.height / max(1, layer.width)))
            layer = layer.resize((target_w, target_h), Image.Resampling.LANCZOS)
            if _sqr_to_bool(raw.get("flip_horizontal", False), False):
                layer = ImageOps.mirror(layer)
            if _sqr_to_bool(raw.get("flip_vertical", False), False):
                layer = ImageOps.flip(layer)
            rotation = max(-180.0, min(180.0, float(raw.get("rotation", 0.0))))
            if abs(rotation) > 0.001:
                layer = layer.rotate(-rotation, resample=Image.Resampling.BICUBIC, expand=True)
            opacity = max(0.0, min(1.0, float(raw.get("opacity", 1.0))))
            if opacity < 1.0:
                layer.putalpha(layer.getchannel("A").point(lambda value: round(value * opacity)))
            target_w, target_h = layer.size
            center_x = float(raw.get("x", 0.5)) * width
            center_y = float(raw.get("y", 0.5)) * height
            canvas.alpha_composite(layer, (round(center_x - target_w / 2), round(center_y - target_h / 2)))
            composed += 1
        if not composed:
            raise ValueError("No valid person images were found.")

        selected_directory = str(payload.get("save_directory") or "").strip()
        if selected_directory and os.path.isabs(selected_directory) and os.path.isdir(selected_directory):
            output_dir = os.path.realpath(selected_directory)
            subfolder = ""
        else:
            requested_subfolder = str(payload.get("save_subfolder") or "sqr_composites").replace("\\", "/").strip("/")
            safe_parts = []
            for part in requested_subfolder.split("/"):
                if not part or part in (".", ".."):
                    continue
                safe_part = "".join(ch for ch in part if ch.isalnum() or ch in (" ", "-", "_", ".")).strip(" .")
                if safe_part:
                    safe_parts.append(safe_part)
            subfolder = "/".join(safe_parts) or "sqr_composites"
            output_dir = os.path.join(folder_paths.get_input_directory(), subfolder)
        os.makedirs(output_dir, exist_ok=True)
        filename = f"sqr_composite_{_sqr_now_stamp()}.png"
        saved_path = os.path.join(output_dir, filename)
        (canvas if background_mode == "transparent" else canvas.convert("RGB")).save(saved_path, "PNG")
        result_path = os.path.normpath(saved_path) if selected_directory else f"{subfolder}/{filename}"
        return web.json_response({
            "ok": True,
            "path": result_path,
            "width": width,
            "height": height,
        })
    except Exception as e:
        print(f"[SQR] compose_reference error: {_sqr_format_exc(e)}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@server.PromptServer.instance.routes.post("/sqr/extract_frame")
async def sqr_extract_frame(request):
    """Extract a Director guide frame at an exact source-video time."""
    try:
        data = await request.json()
        video_path = _sqr_resolve_media_path(data.get("video", ""))
        time_seconds = max(0.0, float(data.get("time_seconds", 0.0)))
        if not video_path or not os.path.isfile(video_path):
            return web.json_response({"ok": False, "error": "video not found"}, status=404)
        import cv2
        cap = cv2.VideoCapture(video_path)
        try:
            if not cap.isOpened():
                raise RuntimeError("cv2 could not open video")
            cap.set(cv2.CAP_PROP_POS_MSEC, time_seconds * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                raise RuntimeError("could not decode requested frame")
        finally:
            cap.release()
        subfolder = "sqr_director_frames"
        output_dir = os.path.join(folder_paths.get_input_directory(), subfolder)
        os.makedirs(output_dir, exist_ok=True)
        filename = f"guide_{_sqr_now_stamp()}_{int(time_seconds * 1000):09d}.png"
        path = os.path.join(output_dir, filename)
        if not cv2.imwrite(path, frame):
            raise RuntimeError("could not save extracted frame")
        return web.json_response({"ok": True, "path": f"{subfolder}/{filename}"})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@server.PromptServer.instance.routes.post("/sqr/detect_cuts")
async def sqr_detect_cuts(request):
    """Detect hard scene changes in a source-video time range."""
    try:
        data = await request.json()
        video_path = _sqr_resolve_media_path(data.get("video", ""))
        start_time = max(0.0, float(data.get("start_time", 0.0)))
        end_time = max(start_time, float(data.get("end_time", start_time)))
        threshold = max(0.12, min(0.8, float(data.get("threshold", 0.30))))
        if not video_path or not os.path.isfile(video_path):
            return web.json_response({"ok": False, "error": "video not found"}, status=404)

        import cv2
        import numpy as np
        cap = cv2.VideoCapture(video_path)
        cuts, previous, previous_hist = [], None, None
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            if fps <= 0:
                raise RuntimeError("invalid video fps")
            cap.set(cv2.CAP_PROP_POS_MSEC, start_time * 1000.0)
            last_cut_time = start_time - 1.0
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                current_time = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0) / 1000.0
                if current_time > end_time:
                    break
                height, width = frame.shape[:2]
                scale = min(1.0, 192.0 / max(1, width))
                small = cv2.resize(frame, (max(32, int(width * scale)), max(18, int(height * scale))))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                hist = cv2.calcHist([small], [0, 1], None, [24, 24], [0, 256, 0, 256])
                cv2.normalize(hist, hist)
                if previous is not None:
                    pixel_score = float(np.mean(cv2.absdiff(gray, previous))) / 255.0
                    hist_score = float(cv2.compareHist(previous_hist, hist, cv2.HISTCMP_BHATTACHARYYA))
                    score = pixel_score * 0.55 + hist_score * 0.45
                    if score >= threshold and current_time - last_cut_time >= 0.35:
                        cuts.append({"time_seconds": current_time, "score": round(score, 4)})
                        last_cut_time = current_time
                previous, previous_hist = gray, hist
        finally:
            cap.release()
        return web.json_response({"ok": True, "cuts": cuts, "threshold": threshold})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@server.PromptServer.instance.routes.post("/sqr/color_match_preview")
async def sqr_color_match_preview(request):
    """Create a small preview using ColorMatchV2's default MKL behavior."""
    try:
        data = await request.json()
        guide_rel = str(data.get("guide", "") or "")
        target_rel = str(data.get("target", "") or "")
        guide_path = _sqr_resolve_media_path(guide_rel)
        target_path = _sqr_resolve_media_path(target_rel)
        strength = max(0.0, min(10.0, float(data.get("strength", 1.0) or 1.0)))
        if not guide_path or not os.path.isfile(guide_path):
            return web.json_response({"ok": False, "error": "guide frame not found"}, status=404)
        if not target_path or not os.path.isfile(target_path):
            return web.json_response({"ok": False, "error": "reference image not found"}, status=404)

        matched = _sqr_color_match_tensor(
            _sqr_load_image_tensor(target_path),
            _sqr_load_image_tensor(guide_path),
            strength,
        )
        from PIL import Image
        import numpy as np
        output = (matched[0].cpu().numpy() * 255.0).round().clip(0, 255).astype(np.uint8)
        subfolder = "sqr_director_color_preview"
        output_dir = os.path.join(folder_paths.get_input_directory(), subfolder)
        os.makedirs(output_dir, exist_ok=True)
        signature = hashlib.sha1(
            f"{guide_path}|{os.path.getmtime(guide_path)}|{target_path}|{os.path.getmtime(target_path)}|{strength:.4f}".encode("utf-8")
        ).hexdigest()[:16]
        filename = f"color_match_{signature}.png"
        Image.fromarray(output, mode="RGB").save(os.path.join(output_dir, filename))
        return web.json_response({
            "ok": True,
            "path": f"{subfolder}/{filename}",
            "width": int(output.shape[1]),
            "height": int(output.shape[0]),
        })
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@server.PromptServer.instance.routes.post("/sqr/adjust_reference")
async def sqr_adjust_reference(request):
    """Scale/translate a reference on its original-size gray canvas."""
    try:
        data = await request.json()
        source_path = _sqr_resolve_media_path(data.get("image", ""))
        if not source_path or not os.path.isfile(source_path):
            return web.json_response({"ok": False, "error": "reference image not found"}, status=404)
        scale = max(0.05, min(5.0, float(data.get("scale", 1.0))))
        offset_x = max(-2.0, min(2.0, float(data.get("offset_x", 0.0))))
        offset_y = max(-2.0, min(2.0, float(data.get("offset_y", 0.0))))
        from PIL import Image
        with Image.open(source_path) as opened:
            source = opened.convert("RGBA")
        width, height = source.size
        resized = source.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )
        canvas = Image.new("RGBA", (width, height), (128, 128, 128, 255))
        left = round((width - resized.width) / 2 + offset_x * width)
        top = round((height - resized.height) / 2 + offset_y * height)
        canvas.paste(resized, (left, top), resized)
        subfolder = "sqr_director_adjusted"
        output_dir = os.path.join(folder_paths.get_input_directory(), subfolder)
        os.makedirs(output_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(source_path))[0][:80]
        filename = f"{base}_adjusted_{_sqr_now_stamp()}.png"
        output_path = os.path.join(output_dir, filename)
        canvas.convert("RGB").save(output_path, "PNG")
        return web.json_response({
            "ok": True, "path": f"{subfolder}/{filename}",
            "width": width, "height": height,
        })
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
