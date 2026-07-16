import os

import numpy as np
import torch
from PIL import Image, ImageOps

import folder_paths


class LHImageEditor:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image_path": ("STRING", {"default": ""}),
                "editor_data": ("STRING", {"default": "{}", "multiline": True}),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK", "STRING")
    RETURN_NAMES = ("image", "mask", "image_path")
    FUNCTION = "load_composite"
    CATEGORY = "SQR/Image"

    @classmethod
    def IS_CHANGED(cls, image_path="", editor_data="{}"):
        path = cls._resolve_path(image_path)
        try:
            return f"{path}:{os.path.getmtime(path)}:{os.path.getsize(path)}"
        except OSError:
            return str(image_path)

    @staticmethod
    def _resolve_path(image_path):
        value = str(image_path or "").strip()
        if not value:
            return ""
        if os.path.isabs(value):
            return value
        return os.path.join(folder_paths.get_input_directory(), value.replace("/", os.sep))

    def load_composite(self, image_path="", editor_data="{}"):
        path = self._resolve_path(image_path)
        if not path or not os.path.isfile(path):
            image = torch.zeros((1, 512, 512, 3), dtype=torch.float32)
            mask = torch.zeros((1, 512, 512), dtype=torch.float32)
            return image, mask, str(image_path or "")

        with Image.open(path) as opened:
            rgba = ImageOps.exif_transpose(opened).convert("RGBA")
        array = np.asarray(rgba, dtype=np.float32) / 255.0
        image = torch.from_numpy(array[:, :, :3].copy()).unsqueeze(0)
        mask = torch.from_numpy(array[:, :, 3].copy()).unsqueeze(0)
        return image, mask, str(image_path)


NODE_CLASS_MAPPINGS = {
    "LHImageEditor": LHImageEditor,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LHImageEditor": "LH Image Editor",
}
