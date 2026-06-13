import torch
import logging

import comfy.model_management
import comfy.utils
import node_helpers

log = logging.getLogger("SQRWanTransition")


class SQRWanAnimateTransitionToVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "positive": ("CONDITIONING",),
                "negative": ("CONDITIONING",),
                "vae": ("VAE",),
                "width": ("INT", {"default": 832, "min": 16, "max": 16384, "step": 16}),
                "height": ("INT", {"default": 480, "min": 16, "max": 16384, "step": 16}),
                "length": ("INT", {"default": 77, "min": 1, "max": 16384, "step": 4}),
                "batch_size": ("INT", {"default": 1, "min": 1, "max": 4096}),
                "continue_motion_max_frames": ("INT", {"default": 5, "min": 1, "max": 16384, "step": 4}),
                "video_frame_offset": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 1}),
            },
            "optional": {
                "clip_vision_output": ("CLIP_VISION_OUTPUT",),
                "reference_image": ("IMAGE",),
                "face_video": ("IMAGE",),
                "pose_video": ("IMAGE",),
                "background_video": ("IMAGE",),
                "character_mask": ("MASK",),
                "continue_motion": ("IMAGE",),
                "transition_video": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "CONDITIONING", "LATENT", "INT", "INT", "INT")
    RETURN_NAMES = ("positive", "negative", "latent", "trim_latent", "trim_image", "video_frame_offset")
    FUNCTION = "execute"
    CATEGORY = "conditioning/video_models"

    def execute(
        self,
        positive,
        negative,
        vae,
        width,
        height,
        length,
        batch_size,
        continue_motion_max_frames,
        video_frame_offset,
        reference_image=None,
        clip_vision_output=None,
        face_video=None,
        pose_video=None,
        continue_motion=None,
        background_video=None,
        character_mask=None,
        transition_video=None,
    ):
        trim_to_pose_video = False
        transition_frame_count = 32 if transition_video is not None else 0
        transition_blend_count = 0
        original_length = length
        log.info(
            "[SQR-TRANS] start transition=%s width=%s height=%s length=%s batch=%s "
            "video_frame_offset=%s continue_motion_max=%s ref=%s pose=%s face=%s bg=%s mask=%s",
            transition_video is not None,
            width,
            height,
            length,
            batch_size,
            video_frame_offset,
            continue_motion_max_frames,
            _shape(reference_image),
            _shape(pose_video),
            _shape(face_video),
            _shape(background_video),
            _shape(character_mask),
        )
        if transition_frame_count:
            length = length + transition_frame_count
            pose_video = _pad_frames(pose_video, transition_frame_count)
            face_video = _pad_frames(face_video, transition_frame_count)
            background_video = _pad_frames(background_video, transition_frame_count)
            character_mask = _pad_frames(character_mask, transition_frame_count)
            log.info(
                "[SQR-TRANS] transition active: hold=%s blend=%s expanded_length=%s "
                "padded pose=%s face=%s bg=%s mask=%s",
                transition_frame_count,
                transition_blend_count,
                length,
                _shape(pose_video),
                _shape(face_video),
                _shape(background_video),
                _shape(character_mask),
            )

        latent_length = ((length - 1) // 4) + 1
        latent_width = width // 8
        latent_height = height // 8
        trim_latent = 0

        if reference_image is None:
            reference_image = torch.zeros((1, height, width, 3))

        image = comfy.utils.common_upscale(reference_image[:length].movedim(-1, 1), width, height, "area", "center").movedim(1, -1)
        concat_latent_image = vae.encode(image[:, :, :, :3])
        mask = torch.zeros((1, 4, concat_latent_image.shape[-3], concat_latent_image.shape[-2], concat_latent_image.shape[-1]), device=concat_latent_image.device, dtype=concat_latent_image.dtype)
        trim_latent += concat_latent_image.shape[2]
        ref_motion_latent_length = 0

        if transition_video is not None:
            log.info("[SQR-TRANS] transition_video input shape before normalize=%s", _shape(transition_video))
            transition_video = _normalize_transition_frames(transition_video, transition_frame_count)
            log.info("[SQR-TRANS] transition_video shape after normalize=%s", _shape(transition_video))
            continue_motion = transition_video
            continue_motion_max_frames = transition_frame_count

        if continue_motion is None:
            image = torch.ones((length, height, width, 3)) * 0.5
            log.info("[SQR-TRANS] no continue/transition motion; using neutral image length=%s", length)
        else:
            continue_motion = continue_motion[-continue_motion_max_frames:]
            if transition_frame_count:
                video_frame_offset = max(0, video_frame_offset)
            else:
                video_frame_offset -= continue_motion.shape[0]
                video_frame_offset = max(0, video_frame_offset)
            continue_motion = comfy.utils.common_upscale(continue_motion[-length:].movedim(-1, 1), width, height, "area", "center").movedim(1, -1)
            image = torch.ones((length, height, width, continue_motion.shape[-1]), device=continue_motion.device, dtype=continue_motion.dtype) * 0.5
            image[:continue_motion.shape[0]] = continue_motion
            if transition_frame_count:
                log.info(
                    "[SQR-TRANS] canvas prepared: continue_frames=%s image_shape=%s; "
                    "frames after transition are neutral and fully released",
                    continue_motion.shape[0],
                    _shape(image),
                )
            ref_motion_latent_length += ((continue_motion.shape[0] - 1) // 4) + 1
            log.info(
                "[SQR-TRANS] ref_motion_latent_length=%s trim_image_pixels=%s video_frame_offset_after=%s",
                ref_motion_latent_length,
                max(0, ref_motion_latent_length * 4 - 3),
                video_frame_offset,
            )

        if clip_vision_output is not None:
            positive = node_helpers.conditioning_set_values(positive, {"clip_vision_output": clip_vision_output})
            negative = node_helpers.conditioning_set_values(negative, {"clip_vision_output": clip_vision_output})

        if pose_video is not None:
            if pose_video.shape[0] <= video_frame_offset:
                pose_video = None
            else:
                pose_video = pose_video[video_frame_offset:]

        if pose_video is not None:
            pose_video = comfy.utils.common_upscale(pose_video[:length].movedim(-1, 1), width, height, "area", "center").movedim(1, -1)
            if not trim_to_pose_video and pose_video.shape[0] < length:
                pose_video = torch.cat((pose_video,) + (pose_video[-1:],) * (length - pose_video.shape[0]), dim=0)

            pose_video_latent = vae.encode(pose_video[:, :, :, :3])
            positive = node_helpers.conditioning_set_values(positive, {"pose_video_latent": pose_video_latent})
            negative = node_helpers.conditioning_set_values(negative, {"pose_video_latent": pose_video_latent})

            if trim_to_pose_video:
                latent_length = pose_video_latent.shape[2]
                length = latent_length * 4 - 3
                image = image[:length]

        if face_video is not None:
            if face_video.shape[0] <= video_frame_offset:
                face_video = None
            else:
                face_video = face_video[video_frame_offset:]

        if face_video is not None:
            face_video = comfy.utils.common_upscale(face_video[:length].movedim(-1, 1), 512, 512, "area", "center") * 2.0 - 1.0
            face_video = face_video.movedim(0, 1).unsqueeze(0)
            positive = node_helpers.conditioning_set_values(positive, {"face_video_pixels": face_video})
            negative = node_helpers.conditioning_set_values(negative, {"face_video_pixels": face_video * 0.0 - 1.0})

        ref_images_num = max(0, ref_motion_latent_length * 4 - 3)
        if background_video is not None:
            if background_video.shape[0] > video_frame_offset:
                background_video = background_video[video_frame_offset:]
                background_video = comfy.utils.common_upscale(background_video[:length].movedim(-1, 1), width, height, "area", "center").movedim(1, -1)
                if background_video.shape[0] > ref_images_num:
                    image[ref_images_num:background_video.shape[0]] = background_video[ref_images_num:]

        mask_refmotion = torch.ones((1, 1, latent_length * 4, concat_latent_image.shape[-2], concat_latent_image.shape[-1]), device=mask.device, dtype=mask.dtype)
        if continue_motion is not None:
            if transition_frame_count:
                hold_frames = min(transition_frame_count, mask_refmotion.shape[2])
                if hold_frames > 0:
                    mask_refmotion[:, :, :hold_frames] = 0.0
                blend_frames = min(transition_blend_count, max(0, mask_refmotion.shape[2] - hold_frames))
                if blend_frames > 0:
                    ramp = torch.linspace(0.0, 1.0, blend_frames, device=mask_refmotion.device, dtype=mask_refmotion.dtype)
                    ramp = ramp * ramp * (3.0 - 2.0 * ramp)
                    mask_refmotion[:, :, hold_frames:hold_frames + blend_frames] = ramp.view(1, 1, -1, 1, 1)
                debug_vals = mask_refmotion[0, 0, :min(mask_refmotion.shape[2], hold_frames + blend_frames + 4), 0, 0].detach().float().cpu().tolist()
                log.info(
                    "[SQR-TRANS] mask timeline: hold_frames=%s blend_frames=%s total_mask_frames=%s "
                    "first_values=%s",
                    hold_frames,
                    blend_frames,
                    mask_refmotion.shape[2],
                    [round(v, 4) for v in debug_vals[:80]],
                )
            else:
                transition_mask_frames = min(ref_motion_latent_length * 4, mask_refmotion.shape[2])
                mask_refmotion[:, :, :transition_mask_frames] = 0.0
                log.info("[SQR-TRANS] continue_motion mask hard frames=%s total_mask_frames=%s", transition_mask_frames, mask_refmotion.shape[2])

        if character_mask is not None:
            if character_mask.shape[0] > video_frame_offset or character_mask.shape[0] == 1:
                if character_mask.shape[0] == 1:
                    character_mask = character_mask.repeat((length,) + (1,) * (character_mask.ndim - 1))
                else:
                    character_mask = character_mask[video_frame_offset:]
                if character_mask.ndim == 3:
                    character_mask = character_mask.unsqueeze(1)
                    character_mask = character_mask.movedim(0, 1)
                if character_mask.ndim == 4:
                    character_mask = character_mask.unsqueeze(1)
                character_mask = comfy.utils.common_upscale(character_mask[:, :, :length], concat_latent_image.shape[-1], concat_latent_image.shape[-2], "nearest-exact", "center")
                if character_mask.shape[2] > ref_images_num:
                    mask_refmotion[:, :, ref_images_num:character_mask.shape[2]] = character_mask[:, :, ref_images_num:]

        concat_latent_image = torch.cat((concat_latent_image, vae.encode(image[:, :, :, :3])), dim=2)
        log.info("[SQR-TRANS] concat_latent_image shape=%s trim_latent=%s latent_length=%s", _shape(concat_latent_image), trim_latent, latent_length)

        mask_refmotion = mask_refmotion.view(1, mask_refmotion.shape[2] // 4, 4, mask_refmotion.shape[3], mask_refmotion.shape[4]).transpose(1, 2)
        mask = torch.cat((mask, mask_refmotion), dim=2)
        log.info("[SQR-TRANS] final concat_mask shape=%s mask_refmotion shape=%s", _shape(mask), _shape(mask_refmotion))
        positive = node_helpers.conditioning_set_values(positive, {"concat_latent_image": concat_latent_image, "concat_mask": mask})
        negative = node_helpers.conditioning_set_values(negative, {"concat_latent_image": concat_latent_image, "concat_mask": mask})

        latent = torch.zeros([batch_size, 16, latent_length + trim_latent, latent_height, latent_width], device=comfy.model_management.intermediate_device())
        out_latent = {"samples": latent}
        next_offset = video_frame_offset + original_length
        log.info(
            "[SQR-TRANS] output latent shape=%s trim_latent=%s trim_image=%s next_offset=%s",
            _shape(latent),
            trim_latent,
            max(0, ref_motion_latent_length * 4 - 3),
            next_offset,
        )
        return (positive, negative, out_latent, trim_latent, max(0, ref_motion_latent_length * 4 - 3), next_offset)


def _pad_frames(frames, count):
    if frames is None or count <= 0:
        return frames
    return torch.cat([frames[0:1].repeat((count,) + (1,) * (frames.ndim - 1)), frames], dim=0)


def _normalize_transition_frames(frames, count):
    if frames.shape[0] == count:
        return frames
    if frames.shape[0] > count:
        indices = torch.linspace(0, frames.shape[0] - 1, count).long()
        return frames[indices]
    repeat_factor = (count + frames.shape[0] - 1) // frames.shape[0]
    return frames.repeat((repeat_factor,) + (1,) * (frames.ndim - 1))[:count]


def _shape(value):
    if value is None:
        return None
    shape = getattr(value, "shape", None)
    if shape is None:
        return type(value).__name__
    return tuple(shape)
