import re
import copy
import json
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import nodes
import server

from .lh_batch_progress import read_progress, write_progress


DATE_TOKEN = re.compile(r"%date:([^%]+)%")
PROGRESS_LOCK = threading.RLock()
QUEUED_STEPS = set()


def _expand_date_tokens(value):
    now = datetime.now()

    def replace(match):
        date_format = match.group(1)
        replacements = (
            ("yyyy", "%Y"),
            ("MM", "%m"),
            ("dd", "%d"),
            ("HH", "%H"),
            ("hh", "%H"),
            ("mm", "%M"),
            ("ss", "%S"),
        )
        for source, target in replacements:
            date_format = date_format.replace(source, target)
        return now.strftime(date_format)

    return DATE_TOKEN.sub(replace, value)


def _add_source_folder(filename_prefix, source_image_path):
    if not source_image_path:
        return filename_prefix
    source_folder = Path(source_image_path).parent.name
    source_folder = re.sub(r'[<>:"/\\|?*]', "_", source_folder).strip(" .")
    if not source_folder:
        source_folder = "source"
    derived_folder = f"{source_folder}-f2kmd"
    normalized = filename_prefix.replace("\\", "/").rstrip("/")
    if "/" in normalized:
        parent, filename = normalized.rsplit("/", 1)
        return f"{parent}/{derived_folder}/{filename}"
    return f"{derived_folder}/{normalized}"


class LHSaveImagePassthrough:
    """Save images and pass them through so saving can live inside flow-control loops."""

    def __init__(self):
        self._saver = nodes.SaveImage()

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "filename_prefix": ("STRING", {"default": "f2k-reskin/f2k-reskin"}),
            },
            "optional": {
                "current_index": ("INT", {"default": 0, "min": 0}),
                "total_images": ("INT", {"default": 1, "min": 1}),
                "job_id": ("STRING", {"default": ""}),
                "auto_queue_next": ("BOOLEAN", {"default": True}),
                "source_image_path": ("STRING", {"default": ""}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "save_images"
    OUTPUT_NODE = True
    CATEGORY = "SQR/Image"

    @staticmethod
    def _queue_next(prompt, queue_key, client_id):
        time.sleep(0.5)
        payload_data = {"prompt": prompt}
        if client_id:
            payload_data["client_id"] = client_id
        payload = json.dumps(payload_data).encode("utf-8")
        request = urllib.request.Request(
            "http://127.0.0.1:8188/prompt",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15):
                pass
        except Exception as error:
            print(f"[LH batch] Unable to queue next image: {error}")
            with PROGRESS_LOCK:
                QUEUED_STEPS.discard(queue_key)

    def save_images(
        self,
        images,
        filename_prefix,
        current_index=0,
        total_images=1,
        job_id="",
        auto_queue_next=True,
        source_image_path="",
        prompt=None,
        extra_pnginfo=None,
    ):
        if job_id:
            with PROGRESS_LOCK:
                existing_progress = read_progress(job_id)
                expected_index = int(existing_progress.get("next_index", current_index))
            if int(current_index) < expected_index:
                print(
                    f"[LH batch] Skipping stale duplicate index {current_index}; "
                    f"checkpoint is already at {expected_index}."
                )
                return {"result": (images,)}

        filename_prefix = _expand_date_tokens(filename_prefix)
        filename_prefix = _add_source_folder(filename_prefix, source_image_path)
        result = self._saver.save_images(
            images,
            filename_prefix=filename_prefix,
            prompt=prompt,
            extra_pnginfo=extra_pnginfo,
        )
        result["result"] = (images,)

        next_index = int(current_index) + 1
        with PROGRESS_LOCK:
            if job_id:
                progress = read_progress(job_id)
                next_index = max(next_index, int(progress.get("next_index", 0)))
                progress.update({
                    "next_index": next_index,
                    "total_images": int(total_images),
                    "completed": next_index >= int(total_images),
                    "last_saved": datetime.now().isoformat(timespec="seconds"),
                    "last_saved_file": progress.get("current_file", ""),
                })
                write_progress(job_id, progress)

        client_id = getattr(server.PromptServer.instance, "client_id", None)
        loader_node_id = None
        if prompt:
            loader_node_id = next(
                (
                    node_id for node_id, node in prompt.items()
                    if node.get("class_type") == "LHImagesFolderLoader"
                ),
                None,
            )
        if client_id and loader_node_id is not None:
            completed = next_index >= int(total_images)
            server.PromptServer.instance.send_sync(
                "lh/batch_progress",
                {
                    "node": str(loader_node_id),
                    "value": next_index,
                    "total": int(total_images),
                    "progress": min(1.0, next_index / int(total_images)),
                    "text": (
                        f"Complete {next_index}/{total_images} | 100%"
                        if completed
                        else f"Saved {next_index}/{total_images} | "
                             f"{(next_index / int(total_images)) * 100:.1f}%"
                    ),
                },
                client_id,
            )

        if auto_queue_next and prompt and next_index < int(total_images):
            next_prompt = copy.deepcopy(prompt)
            client_id = getattr(server.PromptServer.instance, "client_id", None)
            for node in next_prompt.values():
                if node.get("class_type") == "LHImagesFolderLoader":
                    node.setdefault("inputs", {})["reset_progress"] = False
                    node["inputs"]["queue_index"] = next_index
                if node.get("class_type") == "RandomNoise":
                    seed = node.get("inputs", {}).get("noise_seed")
                    if isinstance(seed, int):
                        node["inputs"]["noise_seed"] = (seed + 1) % (2**64)
            run_id = progress.get("run_id", "legacy") if job_id else "no-job"
            queue_key = (job_id, run_id, next_index)
            with PROGRESS_LOCK:
                if queue_key not in QUEUED_STEPS:
                    QUEUED_STEPS.add(queue_key)
                    threading.Thread(
                        target=self._queue_next,
                        args=(next_prompt, queue_key, client_id),
                        daemon=True,
                    ).start()

        return result


NODE_CLASS_MAPPINGS = {
    "LHSaveImagePassthrough": LHSaveImagePassthrough,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LHSaveImagePassthrough": "LH Save Image (Passthrough)",
}
