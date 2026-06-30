from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont


FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
)


def _load_font(size):
    for path in FONT_CANDIDATES:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _fit_font(draw, text, requested_size, max_width, max_height):
    size = max(8, requested_size)
    while size > 8:
        font = _load_font(size)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width and box[3] - box[1] <= max_height:
            return font, box
        size -= 1
    font = _load_font(8)
    return font, draw.textbbox((0, 0), text, font=font)


class LHImageFooterBar:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "enabled": ("BOOLEAN", {"default": False}),
                "style": (["white bar / black text", "black bar / white text"],),
                "text": (
                    "STRING",
                    {
                        "default": "@your_account  |  https://example.com",
                        "multiline": True,
                    },
                ),
                "bar_height": ("INT", {"default": 80, "min": 24, "max": 512, "step": 4}),
                "font_size": ("INT", {"default": 32, "min": 8, "max": 256, "step": 1}),
                "horizontal_padding": (
                    "INT",
                    {"default": 24, "min": 0, "max": 512, "step": 2},
                ),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "add_footer"
    CATEGORY = "SQR/Image"

    def add_footer(
        self,
        images,
        enabled=False,
        style="white bar / black text",
        text="",
        bar_height=80,
        font_size=32,
        horizontal_padding=24,
    ):
        if not enabled:
            return (images,)

        bar_color, text_color = (
            ((255, 255, 255), (0, 0, 0))
            if style == "white bar / black text"
            else ((0, 0, 0), (255, 255, 255))
        )
        output = []
        for tensor_image in images:
            array = (
                tensor_image.detach().cpu().numpy().clip(0.0, 1.0) * 255.0
            ).round().astype(np.uint8)
            source = Image.fromarray(array, mode="RGB")
            canvas = Image.new("RGB", (source.width, source.height + bar_height), bar_color)
            canvas.paste(source, (0, 0))

            if text.strip():
                draw = ImageDraw.Draw(canvas)
                max_width = max(1, source.width - horizontal_padding * 2)
                max_height = max(1, bar_height - 8)
                font, box = _fit_font(draw, text.strip(), font_size, max_width, max_height)
                text_width = box[2] - box[0]
                text_height = box[3] - box[1]
                x = max(horizontal_padding, (source.width - text_width) / 2)
                y = source.height + (bar_height - text_height) / 2 - box[1]
                draw.text((x, y), text.strip(), fill=text_color, font=font)

            result = np.asarray(canvas, dtype=np.float32) / 255.0
            output.append(torch.from_numpy(result).unsqueeze(0))

        return (torch.cat(output, dim=0),)


NODE_CLASS_MAPPINGS = {
    "LHImageFooterBar": LHImageFooterBar,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LHImageFooterBar": "LH Image Footer Bar",
}
