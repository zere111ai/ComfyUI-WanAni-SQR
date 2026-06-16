import logging

import torch

import comfy.model_management
import comfy.utils
import node_helpers


log = logging.getLogger(__name__)
TRANSITION_FRAMES = 17
TRANSITION_ADDED_FRAMES = TRANSITION_FRAMES - 1
TRANSITION_LATENT_FRAMES = ((TRANSITION_FRAMES - 1) // 4) + 1
TRANSITION_LOCKED_LATENT_FRAMES = TRANSITION_ADDED_FRAMES // 4


def _shape(value):
    return tuple(value.shape) if value is not None and hasattr(value, "shape") else None


def _normalize_frames(video, frame_count):
    if video is None or video.shape[0] == 0:
        return None
    if video.shape[0] >= frame_count:
        return video[-frame_count:]
    return torch.cat([video[:1].repeat(frame_count - video.shape[0], 1, 1, 1), video], dim=0)


def _prepend_first_frame(video, frame_count):
    if video is None or video.shape[0] == 0 or frame_count <= 0:
        return video
    return torch.cat([video[:1].repeat(frame_count, 1, 1, 1), video], dim=0)


def _extract_mask_to_28ch(rgb_video):
    """Convert a colored SCAIL-2 identity mask to its 28-channel latent mask."""
    frame_count, height, width, _ = rgb_video.shape
    threshold = 225.0 / 255.0
    mask = rgb_video.movedim(-1, 1).float()
    red = (mask[:, 0:1] > threshold).float()
    green = (mask[:, 1:2] > threshold).float()
    blue = (mask[:, 2:3] > threshold).float()
    not_red, not_green, not_blue = 1 - red, 1 - green, 1 - blue
    binary = torch.cat([
        red * green * blue,
        red * not_green * not_blue,
        not_red * green * not_blue,
        not_red * not_green * blue,
        red * green * not_blue,
        red * not_green * blue,
        not_red * green * blue,
    ], dim=1)
    latent_height, latent_width = height, width
    for _ in range(3):
        latent_height = (latent_height + 1) // 2
        latent_width = (latent_width + 1) // 2
    binary = torch.nn.functional.interpolate(binary, size=(latent_height, latent_width), mode="area")
    latent_frames = (frame_count - 1) // 4 + 1
    padded = torch.cat([binary[:1].repeat(4, 1, 1, 1), binary[1:]], dim=0)
    return padded.view(latent_frames, 28, latent_height, latent_width).unsqueeze(0)


class SQRSCAIL2TransitionToVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "positive": ("CONDITIONING",),
                "negative": ("CONDITIONING",),
                "vae": ("VAE",),
                "width": ("INT", {"default": 512, "min": 32, "max": 16384, "step": 32}),
                "height": ("INT", {"default": 896, "min": 32, "max": 16384, "step": 32}),
                "length": ("INT", {"default": 81, "min": 1, "max": 16384, "step": 4}),
                "batch_size": ("INT", {"default": 1, "min": 1, "max": 4096}),
                "pose_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "pose_start": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "pose_end": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "video_frame_offset": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 1}),
                "previous_frame_count": ("INT", {"default": 5, "min": 1, "max": 16384, "step": 4}),
                "replacement_mode": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "pose_video": ("IMAGE",),
                "pose_video_mask": ("IMAGE",),
                "reference_image": ("IMAGE",),
                "reference_image_mask": ("IMAGE",),
                "clip_vision_output": ("CLIP_VISION_OUTPUT",),
                "previous_frames": ("IMAGE",),
                "transition_video": ("IMAGE",),
                "transition_latent": ("LATENT",),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "CONDITIONING", "LATENT", "INT")
    RETURN_NAMES = ("positive", "negative", "latent", "video_frame_offset")
    FUNCTION = "execute"
    CATEGORY = "model/conditioning/video_models"
    EXPERIMENTAL = True

    def execute(self, positive, negative, vae, width, height, length, batch_size,
                pose_strength, pose_start, pose_end, video_frame_offset,
                previous_frame_count, replacement_mode=False, pose_video=None,
                pose_video_mask=None, reference_image=None, reference_image_mask=None,
                clip_vision_output=None, previous_frames=None, transition_video=None,
                transition_latent=None):
        original_length = length
        transition_latent_samples = None
        if transition_latent is not None:
            transition_latent_samples = transition_latent.get("samples")
            if transition_latent_samples is not None and transition_latent_samples.shape[2] == 0:
                transition_latent_samples = None
        transition_enabled = (
            transition_latent_samples is not None
            or (transition_video is not None and transition_video.shape[0] > 0)
        )
        transition_from_latent = transition_latent_samples is not None
        transition_count = TRANSITION_FRAMES if transition_enabled else 0
        log.info(
            "[SQR-SCAIL-TRANS] start transition=%s source=%s strategy=%s size=%sx%s length=%s offset=%s pose=%s mask=%s previous=%s transition_video=%s transition_latent=%s",
            transition_enabled,
            "latent" if transition_from_latent else "video",
            "replacement" if replacement_mode else "animation",
            width, height, original_length, video_frame_offset,
            _shape(pose_video), _shape(pose_video_mask), _shape(previous_frames), _shape(transition_video),
            _shape(transition_latent_samples),
        )

        prev_trimmed = None
        prev_latent_trimmed = None
        if transition_from_latent:
            prev_latent_trimmed = transition_latent_samples[:, :, -TRANSITION_LATENT_FRAMES:]
            length += TRANSITION_ADDED_FRAMES
            log.info(
                "[SQR-SCAIL-TRANS] latent transition: source_latents=%s carry_latents=%s locked_latents=%s locked_output_frames=%s",
                transition_latent_samples.shape[2],
                prev_latent_trimmed.shape[2],
                TRANSITION_LOCKED_LATENT_FRAMES,
                TRANSITION_ADDED_FRAMES,
            )
        elif transition_enabled:
            if replacement_mode:
                # SCAIL-2 replacement was trained with a short previous-frame
                # anchor (normally five frames). Keeping the full old RGB carry
                # conflicts with the white-background replacement mask and can
                # preserve the old person or destabilize the background.
                replacement_anchor_count = min(
                    max(1, previous_frame_count), transition_video.shape[0]
                )
                prev_trimmed = transition_video[-replacement_anchor_count:]
                log.info(
                    "[SQR-SCAIL-TRANS] replacement strategy: transition_section=%s "
                    "old_rgb_anchor_frames=%s generated_replacement_frames=%s "
                    "pre_release=False",
                    transition_count,
                    replacement_anchor_count,
                    max(0, transition_count - replacement_anchor_count),
                )
            else:
                prev_trimmed = _normalize_frames(transition_video, transition_count)
                log.info(
                    "[SQR-SCAIL-TRANS] animation strategy: dynamic_rgb_carry_frames=%s "
                    "locked_output_frames=%s pre_release=False",
                    transition_count,
                    TRANSITION_ADDED_FRAMES,
                )
            # Wan VAE uses a causal 4N+1 timeline. A 17-frame anchor has five
            # latent frames and shares its causal boundary frame with the current
            # segment, so it adds 16 output frames rather than 17.
            length += TRANSITION_ADDED_FRAMES
        elif previous_frames is not None and previous_frames.shape[0] > 0:
            prev_trimmed = previous_frames[-previous_frame_count:]
            video_frame_offset = max(0, video_frame_offset - prev_trimmed.shape[0])

        if pose_video is not None:
            pose_video = None if pose_video.shape[0] <= video_frame_offset else pose_video[video_frame_offset:]
        if pose_video_mask is not None:
            pose_video_mask = None if pose_video_mask.shape[0] <= video_frame_offset else pose_video_mask[video_frame_offset:]
        if transition_enabled:
            # Match SQR WanAnimate's proven timeline: the RGB carry remains the
            # moving previous segment, while current segment geometry conditions
            # are present throughout the carry. This removes the pose/mask switch
            # at the release boundary without replacing the carried RGB with a still frame.
            pose_video = _prepend_first_frame(pose_video, TRANSITION_ADDED_FRAMES)
            pose_video_mask = _prepend_first_frame(pose_video_mask, TRANSITION_ADDED_FRAMES)
            log.info(
                "[SQR-SCAIL-TRANS] timeline uses %s-frame causal RGB anchor "
                "with %s effective prefix frames; frame=%s starts the original "
                "current-segment pose timeline; pose=%s mask=%s",
                transition_count,
                TRANSITION_ADDED_FRAMES,
                TRANSITION_ADDED_FRAMES + 1,
                _shape(pose_video),
                _shape(pose_video_mask),
            )
            if pose_video_mask is not None:
                mask_rgb = pose_video_mask[:1, ..., :3]
                white_ratio = float((mask_rgb.min(dim=-1).values > (225.0 / 255.0)).float().mean().item())
                if replacement_mode:
                    log.info(
                        "[SQR-SCAIL-TRANS] replacement driving-mask white_ratio=%.4f "
                        "(expected substantial white background)",
                        white_ratio,
                    )
                    if white_ratio < 0.25:
                        log.warning(
                            "[SQR-SCAIL-TRANS] replacement_mode=True but driving mask "
                            "does not look white-background; check SCAIL2ColoredMask "
                            "replacement_mode is also ON"
                        )
                elif white_ratio > 0.75:
                    log.warning(
                        "[SQR-SCAIL-TRANS] animation_mode but driving mask looks "
                        "white-background; check SCAIL2ColoredMask replacement_mode is OFF"
                    )

        latent = torch.zeros(
            [batch_size, 16, ((length - 1) // 4) + 1, height // 8, width // 8],
            device=comfy.model_management.intermediate_device(),
        )
        noise_mask = None
        ref_mask_flag = not replacement_mode
        positive = node_helpers.conditioning_set_values(positive, {"ref_mask_flag": ref_mask_flag})
        negative = node_helpers.conditioning_set_values(negative, {"ref_mask_flag": ref_mask_flag})

        ref_latent = None
        if reference_image is not None:
            reference_image = comfy.utils.common_upscale(reference_image[:1].movedim(-1, 1), width, height, "bicubic", "center").movedim(1, -1)
            if replacement_mode and reference_image_mask is not None:
                ref_mask = comfy.utils.common_upscale(reference_image_mask[:1].movedim(-1, 1), width, height, "nearest-exact", "center").movedim(1, -1)
                is_character = (ref_mask[..., :3].max(dim=-1, keepdim=True).values > 0.1).to(reference_image.dtype)
                reference_image = reference_image * is_character
            ref_latent = vae.encode(reference_image[:, :, :, :3])

        if ref_latent is not None:
            positive = node_helpers.conditioning_set_values(positive, {"reference_latents": [ref_latent]}, append=True)
            negative = node_helpers.conditioning_set_values(negative, {"reference_latents": [ref_latent]}, append=True)
        if clip_vision_output is not None:
            positive = node_helpers.conditioning_set_values(positive, {"clip_vision_output": clip_vision_output})
            negative = node_helpers.conditioning_set_values(negative, {"clip_vision_output": clip_vision_output})

        frame_counts = [video.shape[0] for video in (pose_video, pose_video_mask) if video is not None]
        if frame_counts:
            kept = ((min(min(frame_counts), length) - 1) // 4) * 4 + 1
            if pose_video is not None:
                pose_video = pose_video[:kept]
            if pose_video_mask is not None:
                pose_video_mask = pose_video_mask[:kept]

        if pose_video is not None:
            pose_video = comfy.utils.common_upscale(pose_video[:length].movedim(-1, 1), width // 2, height // 2, "area", "center").movedim(1, -1)
            pose_latent = vae.encode(pose_video[:, :, :, :3]) * pose_strength
            positive = node_helpers.conditioning_set_values_with_timestep_range(positive, {"pose_video_latent": pose_latent}, pose_start, pose_end)
            negative = node_helpers.conditioning_set_values_with_timestep_range(negative, {"pose_video_latent": pose_latent}, pose_start, pose_end)

        if pose_video_mask is not None:
            mask_video = comfy.utils.common_upscale(pose_video_mask[:length].movedim(-1, 1), width // 2, height // 2, "area", "center").movedim(1, -1)
            driving_mask = _extract_mask_to_28ch(mask_video)
            positive = node_helpers.conditioning_set_values(positive, {"driving_mask_28ch": driving_mask})
            negative = node_helpers.conditioning_set_values(negative, {"driving_mask_28ch": driving_mask})

        if reference_image_mask is not None:
            ref_mask = comfy.utils.common_upscale(reference_image_mask[:1].movedim(-1, 1), width, height, "bicubic", "center").movedim(1, -1)
            ref_mask_one = _extract_mask_to_28ch(ref_mask)
            zeros = torch.zeros((1, latent.shape[2], 28, ref_mask_one.shape[-2], ref_mask_one.shape[-1]), device=ref_mask_one.device, dtype=ref_mask_one.dtype)
            ref_mask_all = torch.cat([ref_mask_one, zeros], dim=1)
            positive = node_helpers.conditioning_set_values(positive, {"ref_mask_28ch": ref_mask_all})
            negative = node_helpers.conditioning_set_values(negative, {"ref_mask_28ch": ref_mask_all})

        locked_latent_frames = 0
        if prev_trimmed is not None or prev_latent_trimmed is not None:
            if prev_latent_trimmed is not None:
                previous_latent = prev_latent_trimmed.to(latent.device)
            else:
                previous = comfy.utils.common_upscale(prev_trimmed.movedim(-1, 1), width, height, "bicubic", "center").movedim(1, -1)
                previous_latent = vae.encode(previous[:, :, :, :3])
            copied_latent_frames = min(previous_latent.shape[2], latent.shape[2])
            latent[:, :, :copied_latent_frames] = previous_latent[:, :, :copied_latent_frames].to(latent.dtype)
            noise_mask = torch.ones((1, 1, latent.shape[2], latent.shape[-2], latent.shape[-1]), device=latent.device, dtype=latent.dtype)
            if transition_enabled and not replacement_mode:
                # Match SQR WanAnimate at the release boundary: 17 source frames
                # provide a complete causal VAE encode, but only the first
                # 16 output frames (4 latent groups) are held. The fifth latent,
                # beginning at frame 17, is fully denoised with the current pose.
                locked_latent_frames = min(
                    TRANSITION_LOCKED_LATENT_FRAMES,
                    copied_latent_frames,
                )
                noise_mask[:, :, :locked_latent_frames] = 0.0
                if copied_latent_frames > locked_latent_frames:
                    release_index = locked_latent_frames
                    noise_mask[:, :, release_index:release_index + 1] = 0.45
                log.info(
                    "[SQR-SCAIL-TRANS] WanAnimate-aligned boundary: "
                    "copied_latents=%s locked_latents=%s locked_output_frames=%s; "
                    "latent_index=%s (frame 17 onward) noise_mask=0.45 then 1.0",
                    copied_latent_frames,
                    locked_latent_frames,
                    locked_latent_frames * 4,
                    locked_latent_frames,
                )
            elif transition_enabled:
                locked_latent_frames = copied_latent_frames
                noise_mask[:, :, :locked_latent_frames] = 0.0
                log.info(
                    "[SQR-SCAIL-TRANS] replacement anchor locked_latent_frames=%s; "
                    "remaining transition and segment latents are fully released",
                    locked_latent_frames,
                )
            else:
                locked_latent_frames = copied_latent_frames
                noise_mask[:, :, :locked_latent_frames] = 0.0

        output_latent = {"samples": latent}
        if noise_mask is not None:
            output_latent["noise_mask"] = noise_mask
        next_offset = video_frame_offset + original_length
        log.info(
            "[SQR-SCAIL-TRANS] output transition=%s expanded_length=%s pose=%s mask=%s locked_latent_frames=%s latent=%s next_offset=%s",
            transition_enabled, length, _shape(pose_video), _shape(pose_video_mask),
            locked_latent_frames, _shape(latent), next_offset,
        )
        return positive, negative, output_latent, next_offset
