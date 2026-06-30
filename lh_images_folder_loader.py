import os
import re
import hashlib
import uuid

import numpy as np
import torch
import server
from PIL import Image, ImageOps

from .lh_batch_progress import make_job_id, read_progress, write_progress


SORT_METHODS = (
    "None",
    "Alphabetical (ASC)",
    "Alphabetical (DESC)",
    "Numerical (ASC)",
    "Numerical (DESC)",
    "Datetime (ASC)",
    "Datetime (DESC)",
)

VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".jxl"}


def _first_number(filename):
    match = re.search(r"\d+", os.path.splitext(filename)[0])
    return int(match.group()) if match else float("inf")


def _sort_files(files, directory, method):
    if method == "Alphabetical (ASC)":
        return sorted(files)
    if method == "Alphabetical (DESC)":
        return sorted(files, reverse=True)
    if method == "Numerical (ASC)":
        return sorted(files, key=_first_number)
    if method == "Numerical (DESC)":
        return sorted(files, key=_first_number, reverse=True)
    if method == "Datetime (ASC)":
        return sorted(files, key=lambda name: os.path.getmtime(os.path.join(directory, name)))
    if method == "Datetime (DESC)":
        return sorted(
            files,
            key=lambda name: os.path.getmtime(os.path.join(directory, name)),
            reverse=True,
        )
    return files


class LHImagesFolderLoader:
    """Load folder images as lists without resizing them to a common resolution."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "directory": ("STRING", {"default": ""}),
            },
            "optional": {
                "image_load_cap": ("INT", {"default": 0, "min": 0, "step": 1}),
                "start_index": (
                    "INT",
                    {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF, "step": 1},
                ),
                "load_always": (
                    "BOOLEAN",
                    {"default": False, "label_on": "enabled", "label_off": "disabled"},
                ),
                "sort_method": (SORT_METHODS,),
                "processing_mode": (["resumable sequential", "image list"],),
                "reset_progress": ("BOOLEAN", {"default": False}),
                "queue_index": ("INT", {"default": -1, "min": -1, "step": 1}),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK", "STRING", "ITEM_LIST", "INT", "INT", "STRING")
    RETURN_NAMES = (
        "IMAGE", "MASK", "FILE PATH", "IMAGE ITEM LIST",
        "CURRENT INDEX", "TOTAL IMAGES", "JOB ID",
    )
    OUTPUT_IS_LIST = (True, True, True, False, False, False, False)
    FUNCTION = "load_images"
    CATEGORY = "SQR/Image"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Folder contents and loop state may change without another widget changing.
        # Always rescan once for each newly queued prompt.
        return float("NaN")

    def load_images(
        self,
        directory,
        image_load_cap=0,
        start_index=0,
        load_always=False,
        sort_method="None",
        processing_mode="resumable sequential",
        reset_progress=False,
        queue_index=-1,
        unique_id=None,
    ):
        directory = os.path.abspath(os.path.expandvars(os.path.expanduser(directory.strip())))
        if not os.path.isdir(directory):
            raise FileNotFoundError(f"Directory '{directory}' cannot be found.")

        files = [
            name
            for name in os.listdir(directory)
            if os.path.isfile(os.path.join(directory, name))
            and os.path.splitext(name)[1].lower() in VALID_EXTENSIONS
        ]
        if not files:
            raise FileNotFoundError(f"No supported image files in directory '{directory}'.")

        files = _sort_files(files, directory, sort_method)[start_index:]
        if image_load_cap > 0:
            files = files[:image_load_cap]
        if not files:
            raise FileNotFoundError(
                f"No images remain after start_index={start_index} in directory '{directory}'."
            )

        job_variant = f"start={start_index}|cap={image_load_cap}|sort={sort_method}"
        job_id = make_job_id(directory, job_variant)
        total_images = len(files)
        manifest_hash = hashlib.sha256("\n".join(files).encode("utf-8")).hexdigest()
        current_index = 0

        if processing_mode == "resumable sequential":
            progress = {} if reset_progress else read_progress(job_id)
            run_id = progress.get("run_id") or uuid.uuid4().hex
            saved_next_index = int(progress.get("next_index", 0))
            if progress and progress.get("manifest_hash") != manifest_hash:
                last_saved_file = progress.get("last_saved_file")
                if last_saved_file in files:
                    saved_next_index = files.index(last_saved_file) + 1
                else:
                    saved_next_index = 0
            if reset_progress:
                current_index = 0
            elif int(queue_index) >= 0:
                # Never allow an already queued stale prompt to move progress backwards.
                current_index = max(int(queue_index), saved_next_index)
            else:
                current_index = saved_next_index
            if current_index >= total_images:
                raise RuntimeError(
                    f"Folder batch is already complete ({total_images}/{total_images}). "
                    "Enable reset_progress once to start it again."
                )
            files = [files[current_index]]
            progress.update({
                "directory": directory,
                "total_images": total_images,
                "next_index": current_index,
                "completed": False,
                "current_file": os.path.basename(files[0]),
                "manifest_hash": manifest_hash,
                "job_variant": job_variant,
                "run_id": run_id,
            })
            write_progress(job_id, progress)
            client_id = getattr(server.PromptServer.instance, "client_id", None)
            if client_id and unique_id is not None:
                percent = ((current_index + 1) / total_images) * 100
                server.PromptServer.instance.send_sync(
                    "lh/batch_progress",
                    {
                        "node": str(unique_id),
                        "value": current_index,
                        "total": total_images,
                        "progress": current_index / total_images,
                        "text": (
                            f"Processing {current_index + 1}/{total_images} | "
                            f"{percent:.1f}% | {os.path.basename(files[0])}"
                        ),
                    },
                    client_id,
                )

        images = []
        masks = []
        file_paths = []

        for filename in files:
            image_path = os.path.join(directory, filename)
            with Image.open(image_path) as source:
                source = ImageOps.exif_transpose(source)
                rgb = np.array(source.convert("RGB"), dtype=np.float32) / 255.0
                images.append(torch.from_numpy(rgb).unsqueeze(0))

                if "A" in source.getbands():
                    alpha = np.array(source.getchannel("A"), dtype=np.float32) / 255.0
                    masks.append(1.0 - torch.from_numpy(alpha))
                else:
                    masks.append(torch.zeros((64, 64), dtype=torch.float32))

            file_paths.append(image_path)

        # The fourth output is the same image collection packaged as one ITEM_LIST.
        # It can connect directly to Foreach List without WorklistToItemList caching.
        return images, masks, file_paths, images, current_index, total_images, job_id


NODE_CLASS_MAPPINGS = {
    "LHImagesFolderLoader": LHImagesFolderLoader,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LHImagesFolderLoader": "LH Images Folder Loader",
}
