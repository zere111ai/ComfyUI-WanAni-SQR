import math

import torch
import torch.nn.functional as F

import comfy.model_management
from comfy.ldm.sam3.tracker import unpack_masks


DEFAULT_PALETTE = [
    (0.0, 0.0, 1.0),  # Blue
    (1.0, 0.0, 0.0),  # Red
    (0.0, 1.0, 0.0),  # Green
    (1.0, 0.0, 1.0),  # Magenta
    (0.0, 1.0, 1.0),  # Cyan
    (1.0, 1.0, 0.0),  # Yellow
]


def _unpack(track_data):
    packed = track_data["packed_masks"]
    if packed is None or packed.shape[1] == 0:
        return None
    return unpack_masks(packed)


def _first_frame_cx_area(masks_bool):
    first = masks_bool[0].float()
    height, width = first.shape[-2], first.shape[-1]
    n_pixels = height * width
    grid_x = torch.arange(width, device=first.device, dtype=first.dtype).view(1, width)
    area = first.sum(dim=(-1, -2)).clamp_(min=1)
    cx = (first * grid_x).sum(dim=(-1, -2)) / area
    return (cx / width).tolist(), (area / n_pixels).tolist()


def _subset_track_data(track_data, obj_indices):
    out = dict(track_data)
    packed = track_data["packed_masks"]
    if packed is None or not obj_indices:
        out["packed_masks"] = None
        if "scores" in out:
            out["scores"] = []
        return out
    out["packed_masks"] = packed[:, obj_indices].contiguous()
    scores = track_data.get("scores")
    if scores is not None:
        out["scores"] = [scores[i] for i in obj_indices if i < len(scores)]
    return out


def _prep_track_data(track_data, sort_by, object_indices):
    masks_bool = _unpack(track_data)
    if sort_by != "none" and masks_bool is not None:
        cx, area = _first_frame_cx_area(masks_bool)
        if sort_by == "left_to_right":
            order = sorted(range(len(cx)), key=lambda i: cx[i])
        else:
            order = sorted(range(len(area)), key=lambda i: -area[i])
        track_data = _subset_track_data(track_data, order)
    if object_indices.strip():
        indices = [int(i.strip()) for i in object_indices.split(",") if i.strip().isdigit()]
        packed = track_data.get("packed_masks")
        n_obj = packed.shape[1] if packed is not None else 0
        indices = [i for i in indices if 0 <= i < n_obj]
        track_data = _subset_track_data(track_data, indices)
    return track_data


def _parse_groups(text, n_obj):
    groups = []
    used = set()
    for raw_group in str(text or "").split("|"):
        group = []
        for raw_index in raw_group.split(","):
            raw_index = raw_index.strip()
            if raw_index.isdigit():
                index = int(raw_index)
                if 0 <= index < n_obj:
                    group.append(index)
                    used.add(index)
        if group:
            groups.append(group)
    for index in range(n_obj):
        if index not in used:
            groups.append([index])
    return groups


def _render_grouped_colored_masks(track_data, background="black", groups_text="", force_single_identity=False):
    packed = track_data["packed_masks"]
    height, width = track_data["orig_size"]
    device = comfy.model_management.intermediate_device()
    dtype = comfy.model_management.intermediate_dtype()
    bg_rgb = (1.0, 1.0, 1.0) if background.startswith("white") else (0.0, 0.0, 0.0)
    if packed is None or packed.shape[1] == 0:
        frames = track_data.get("n_frames", 1) if packed is None else packed.shape[0]
        out = torch.empty(frames, height, width, 3, device=device, dtype=dtype)
        out[..., 0], out[..., 1], out[..., 2] = bg_rgb[0], bg_rgb[1], bg_rgb[2]
        return out

    frames, n_obj = packed.shape[0], packed.shape[1]
    masks_full = unpack_masks(packed.to(device)).float()
    mask_h, mask_w = masks_full.shape[-2], masks_full.shape[-1]
    masks_full = F.interpolate(
        masks_full.view(frames * n_obj, 1, mask_h, mask_w),
        size=(height, width),
        mode="nearest",
    ).view(frames, n_obj, height, width) > 0.5

    groups = [list(range(n_obj))] if force_single_identity else _parse_groups(groups_text, n_obj)
    out = torch.empty(frames, height, width, 3, device=device, dtype=dtype)
    out[..., 0], out[..., 1], out[..., 2] = bg_rgb[0], bg_rgb[1], bg_rgb[2]
    for color_index, group in enumerate(groups):
        group_mask = masks_full[:, group].any(dim=1)
        color = torch.tensor(DEFAULT_PALETTE[color_index % len(DEFAULT_PALETTE)], device=device, dtype=dtype)
        out = torch.where(group_mask.unsqueeze(-1), color.view(1, 1, 1, 3), out)
    return out


def _resize_image(image, height, width):
    return F.interpolate(
        image.movedim(-1, 1),
        size=(height, width),
        mode="bilinear",
        align_corners=False,
    ).movedim(1, -1)


def _center_crop_to_aspect(image, target_width, target_height):
    _, height, width, _ = image.shape
    target_width = max(1, int(target_width))
    target_height = max(1, int(target_height))
    target_ratio = target_width / target_height
    image_ratio = width / height
    if abs(image_ratio - target_ratio) < 1e-6:
        return image
    if image_ratio > target_ratio:
        crop_w = max(1, int(round(height * target_ratio)))
        x0 = max(0, (width - crop_w) // 2)
        return image[:, :, x0:x0 + crop_w, :]
    crop_h = max(1, int(round(width / target_ratio)))
    y0 = max(0, (height - crop_h) // 2)
    return image[:, y0:y0 + crop_h, :, :]


def _center_crop_to_size(image, crop_height, crop_width):
    _, height, width, _ = image.shape
    crop_height = min(max(1, int(crop_height)), height)
    crop_width = min(max(1, int(crop_width)), width)
    y0 = max(0, (height - crop_height) // 2)
    x0 = max(0, (width - crop_width) // 2)
    return image[:, y0:y0 + crop_height, x0:x0 + crop_width, :]


class LHResolutionSetting:
    RESOLUTIONS = {
        "1:1 480": (480, 480),
        "1:1 720": (720, 720),
        "1:1 1024": (1024, 1024),
        "1:1 1440": (1440, 1440),
        "1:1 2160": (2160, 2160),
        "4:3 480p": (640, 480),
        "4:3 768p": (1024, 768),
        "4:3 1080p": (1440, 1080),
        "4:3 1536p": (2048, 1536),
        "4:3 4K": (2880, 2160),
        "16:9 480p": (854, 480),
        "16:9 720p": (1280, 720),
        "16:9 1080p": (1920, 1080),
        "16:9 1440p": (2560, 1440),
        "16:9 4K": (3840, 2160),
        "21:9 480p": (1120, 480),
        "21:9 720p": (1680, 720),
        "21:9 1080p": (2560, 1080),
        "21:9 1440p": (3440, 1440),
        "21:9 4K": (5120, 2160),
    }

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "orientation": (["landscape", "portrait"], {"default": "portrait"}),
                "resolution": (list(cls.RESOLUTIONS.keys()), {"default": "1:1 1024"}),
                "manual_override": ("BOOLEAN", {"default": False}),
                "manual_width": ("INT", {"default": 1024, "min": 64, "max": 8192, "step": 8}),
                "manual_height": ("INT", {"default": 1024, "min": 64, "max": 8192, "step": 8}),
            }
        }

    RETURN_TYPES = ("INT", "INT")
    RETURN_NAMES = ("width", "height")
    FUNCTION = "execute"
    CATEGORY = "SQR/Resolution"

    def execute(self, orientation, resolution, manual_override, manual_width, manual_height):
        if manual_override:
            return (int(manual_width), int(manual_height))
        width, height = self.RESOLUTIONS.get(resolution, (1024, 1024))
        if orientation == "portrait" and width != height:
            width, height = height, width
        return (int(width), int(height))


class SQRScail2MultiReferenceCanvas:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image_1": ("IMAGE",),
                "layout": (["row", "grid"], {"default": "row"}),
                "cell_width": ("INT", {"default": 512, "min": 64, "max": 4096, "step": 8}),
                "cell_height": ("INT", {"default": 896, "min": 64, "max": 4096, "step": 8}),
                "gap": ("INT", {"default": 0, "min": 0, "max": 256, "step": 1}),
                "background": (["black", "white", "gray"], {"default": "black"}),
            },
            "optional": {
                "image_2": ("IMAGE",),
                "image_3": ("IMAGE",),
                "image_4": ("IMAGE",),
                "image_5": ("IMAGE",),
                "image_6": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("reference_canvas",)
    FUNCTION = "execute"
    CATEGORY = "SQR/SCAIL2"

    def execute(self, image_1, layout, cell_width, cell_height, gap, background,
                image_2=None, image_3=None, image_4=None, image_5=None, image_6=None):
        images = [img for img in (image_1, image_2, image_3, image_4, image_5, image_6) if img is not None and img.shape[0] > 0]
        tiles = [_resize_image(img[:1, :, :, :3], cell_height, cell_width) for img in images]
        count = len(tiles)
        if layout == "grid":
            cols = math.ceil(math.sqrt(count))
        else:
            cols = count
        rows = math.ceil(count / cols)
        bg = {"black": 0.0, "white": 1.0, "gray": 0.5}[background]
        canvas_h = rows * cell_height + max(0, rows - 1) * gap
        canvas_w = cols * cell_width + max(0, cols - 1) * gap
        canvas = torch.full((1, canvas_h, canvas_w, 3), bg, device=tiles[0].device, dtype=tiles[0].dtype)
        for index, tile in enumerate(tiles):
            row, col = divmod(index, cols)
            y = row * (cell_height + gap)
            x = col * (cell_width + gap)
            canvas[:, y:y + cell_height, x:x + cell_width, :] = tile
        return (canvas,)


class SQRScail2ReferenceBatchStack:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image_1": ("IMAGE",),
                "width": ("INT", {"default": 1024, "min": 64, "max": 8192, "step": 8, "forceInput": True}),
                "height": ("INT", {"default": 1024, "min": 64, "max": 8192, "step": 8, "forceInput": True}),
                "crop_mode": (["keep", "crop_to_video_aspect"], {"default": "keep"}),
            },
            "optional": {
                "image_2": ("IMAGE",),
                "image_3": ("IMAGE",),
                "image_4": ("IMAGE",),
                "image_5": ("IMAGE",),
                "image_6": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("reference_images",)
    FUNCTION = "execute"
    CATEGORY = "SQR/SCAIL2"

    def execute(self, image_1, width, height, crop_mode="keep", image_2=None, image_3=None,
                image_4=None, image_5=None, image_6=None):
        images = [img for img in (image_1, image_2, image_3, image_4, image_5, image_6) if img is not None and img.shape[0] > 0]
        refs = [img[:1, :, :, :3] for img in images]
        if crop_mode == "crop_to_video_aspect":
            refs = [_center_crop_to_aspect(img, width, height) for img in refs]
            target_ratio = max(1, int(width)) / max(1, int(height))
            max_common_w = min(img.shape[2] for img in refs)
            max_common_h = min(img.shape[1] for img in refs)
            common_w = max_common_w
            common_h = max(1, int(round(common_w / target_ratio)))
            if common_h > max_common_h:
                common_h = max_common_h
                common_w = max(1, int(round(common_h * target_ratio)))
            refs = [_center_crop_to_size(img, common_h, common_w) for img in refs]
        else:
            shapes = {(img.shape[1], img.shape[2]) for img in refs}
            if len(shapes) > 1:
                raise ValueError("Wan SQR Multi Reference keep mode requires all reference images to have the same size. Use crop_to_video_aspect for mixed sizes.")
        return (torch.cat(refs, dim=0),)


class SQRScail2ColoredMaskAdvanced:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "driving_track_data": ("SAM3_TRACK_DATA",),
                "identity_mode": (["multi_person", "single_person_multi_reference"], {"default": "multi_person"}),
                "object_indices": ("STRING", {"default": ""}),
                "sort_by": (["none", "left_to_right", "area"], {"default": "left_to_right"}),
                "replacement_mode": ("BOOLEAN", {"default": False}),
                "ref_identity_groups": ("STRING", {
                    "default": "",
                    "tooltip": "multi_person only: group reference objects with '|', e.g. 0,1|2,3. single_person_multi_reference forces all selected reference objects to color 1.",
                }),
            },
            "optional": {
                "ref_track_data": ("SAM3_TRACK_DATA",),
            },
        }

    RETURN_TYPES = ("IMAGE", "IMAGE")
    RETURN_NAMES = ("pose_video_mask", "reference_image_mask")
    FUNCTION = "execute"
    CATEGORY = "SQR/SCAIL2"

    def execute(self, driving_track_data, identity_mode, object_indices, sort_by,
                replacement_mode, ref_identity_groups="", ref_track_data=None):
        drv = _prep_track_data(driving_track_data, sort_by, object_indices)
        drv_bg = "white" if replacement_mode else "black"
        ref_bg = "black" if replacement_mode else "white"

        single_ref = identity_mode == "single_person_multi_reference"
        pose_video_mask = _render_grouped_colored_masks(
            drv,
            drv_bg,
            groups_text="0" if single_ref else "",
            force_single_identity=single_ref,
        )

        if ref_track_data is not None:
            ref = _prep_track_data(ref_track_data, sort_by, object_indices)
            reference_image_mask = _render_grouped_colored_masks(
                ref,
                ref_bg,
                groups_text=ref_identity_groups,
                force_single_identity=single_ref,
            )
        else:
            height, width = drv["orig_size"]
            fill_value = 1.0 if ref_bg == "white" else 0.0
            reference_image_mask = torch.full(
                (1, height, width, 3),
                fill_value,
                device=comfy.model_management.intermediate_device(),
                dtype=comfy.model_management.intermediate_dtype(),
            )

        return (pose_video_mask, reference_image_mask)


class SQRScail2ReferenceBatchSplit:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "reference_images": ("IMAGE",),
                "reference_masks": ("IMAGE",),
                "main_index": ("INT", {"default": 0, "min": 0, "max": 63, "step": 1}),
            }
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "IMAGE", "IMAGE")
    RETURN_NAMES = ("main_reference_image", "additional_reference_images", "main_reference_mask", "additional_reference_masks")
    FUNCTION = "execute"
    CATEGORY = "SQR/SCAIL2"

    def execute(self, reference_images, reference_masks, main_index):
        count = reference_images.shape[0]
        main_index = min(max(0, int(main_index)), count - 1)
        main_image = reference_images[main_index:main_index + 1]
        image_parts = [reference_images[i:i + 1] for i in range(count) if i != main_index]
        if image_parts:
            additional_images = torch.cat(image_parts, dim=0)
        else:
            additional_images = main_image[:0]

        mask_count = reference_masks.shape[0]
        mask_index = min(main_index, max(0, mask_count - 1))
        main_mask = reference_masks[mask_index:mask_index + 1]
        mask_parts = [reference_masks[i:i + 1] for i in range(mask_count) if i != mask_index]
        if mask_parts:
            additional_masks = torch.cat(mask_parts, dim=0)
        else:
            additional_masks = main_mask[:0]
        return (main_image, additional_images, main_mask, additional_masks)


NODE_CLASS_MAPPINGS = {
    "LHResolutionSetting": LHResolutionSetting,
    "SQRScail2MultiReferenceCanvas": SQRScail2MultiReferenceCanvas,
    "SQRScail2ReferenceBatchStack": SQRScail2ReferenceBatchStack,
    "WanSQRMultiReference": SQRScail2ReferenceBatchStack,
    "SQRScail2ColoredMaskAdvanced": SQRScail2ColoredMaskAdvanced,
    "SQRScail2ReferenceBatchSplit": SQRScail2ReferenceBatchSplit,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LHResolutionSetting": "LH Resolution Setting",
    "SQRScail2MultiReferenceCanvas": "SQR SCAIL2 Multi Reference Canvas (Trial)",
    "SQRScail2ReferenceBatchStack": "Wan SQR Multi Reference",
    "WanSQRMultiReference": "Wan SQR Multi Reference",
    "SQRScail2ColoredMaskAdvanced": "SQR SCAIL2 Colored Mask Advanced (Trial)",
    "SQRScail2ReferenceBatchSplit": "SQR SCAIL2 Reference Batch Split (Trial)",
}
