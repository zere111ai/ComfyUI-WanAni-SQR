"""Pure runtime helpers for segmented queue planning and cleanup."""

from __future__ import annotations

import copy
import gc
import math
import os
from collections.abc import Iterable
from typing import Any


PASSTHROUGH_MODES = {
    "blank",
    "hard_cut",
    "hard-cut",
    "original",
    "passthrough",
    "source",
}

WAN_CONTEXT_FRAMES = 81
SCAIL_TRANSITION_CARRY_FRAMES = 16


def is_passthrough_segment(config: dict[str, Any] | None) -> bool:
    """Return whether a Director segment should use the source frames directly."""

    if not isinstance(config, dict):
        return False
    if bool(config.get("skip_sampling") or config.get("passthrough")):
        return True
    return str(config.get("mode") or "").strip().lower() in PASSTHROUGH_MODES


def wan_model_length(visible_frames: int) -> int:
    """Return the smallest Wan-compatible 4n+1 window for visible frames."""

    visible_frames = max(1, int(visible_frames))
    return int(math.ceil((visible_frames - 1) / 4.0) * 4) + 1


def expand_director_plan_for_context(
    plan: Iterable[tuple[int, int, dict[str, Any]]],
    *,
    transition_enabled: bool,
    context_frames: int = WAN_CONTEXT_FRAMES,
    transition_carry_frames: int = SCAIL_TRANSITION_CARRY_FRAMES,
) -> list[tuple[int, int, dict[str, Any]]]:
    """Split oversized sampled Director ranges into balanced model chunks.

    The Director timeline remains the editorial source of truth.  Only sampled
    ranges larger than the configured context are expanded; passthrough ranges
    remain intact because they never enter the diffusion model.  With SCAIL-2
    transition carry enabled, new chunks target ``context - carry`` visible
    frames so the expanded latent remains within the 81-frame context window.
    """

    context_frames = max(5, int(context_frames))
    transition_carry_frames = max(0, int(transition_carry_frames))
    safe_visible_frames = context_frames
    if transition_enabled:
        safe_visible_frames = max(
            5,
            context_frames - transition_carry_frames,
        )

    expanded: list[tuple[int, int, dict[str, Any]]] = []
    for source_index, (start, model_length, raw_config) in enumerate(plan):
        config = copy.deepcopy(raw_config or {})
        visible_frames = max(
            1,
            int(config.get("visible_length") or model_length),
        )
        config["source_segment_index"] = source_index
        config["source_segment_id"] = str(
            config.get("id") or f"seg_{source_index + 1}"
        )

        if is_passthrough_segment(config) or model_length <= context_frames:
            config["micro_index"] = 1
            config["micro_count"] = 1
            expanded.append((int(start), int(model_length), config))
            continue

        micro_count = max(
            2,
            int(math.ceil(visible_frames / safe_visible_frames)),
        )
        base_length, remainder = divmod(visible_frames, micro_count)
        cursor = int(start)
        for micro_index in range(micro_count):
            chunk_visible = base_length + (1 if micro_index < remainder else 0)
            chunk_model_length = wan_model_length(chunk_visible)
            chunk = copy.deepcopy(config)
            chunk["id"] = (
                f"{config['source_segment_id']}__micro_"
                f"{micro_index + 1}_of_{micro_count}"
            )
            chunk["start"] = cursor
            chunk["end"] = cursor + chunk_visible
            chunk["visible_length"] = chunk_visible
            chunk["model_length"] = chunk_model_length
            chunk["micro_index"] = micro_index + 1
            chunk["micro_count"] = micro_count
            expanded.append((cursor, chunk_model_length, chunk))
            cursor += chunk_visible

    return expanded


def prune_prompt_to_outputs(
    prompt: dict[str, Any],
    output_node_ids: Iterable[str | int],
) -> dict[str, Any]:
    """Keep only selected outputs and their transitive node dependencies."""

    normalized = {str(node_id): node for node_id, node in prompt.items()}
    keep: set[str] = set()
    pending = [str(node_id) for node_id in output_node_ids]

    while pending:
        node_id = pending.pop()
        if node_id in keep:
            continue
        node = normalized.get(node_id)
        if not isinstance(node, dict):
            continue
        keep.add(node_id)
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        for value in inputs.values():
            if (
                isinstance(value, list)
                and len(value) == 2
                and str(value[0]) in normalized
            ):
                pending.append(str(value[0]))

    return {
        node_id: node
        for node_id, node in normalized.items()
        if node_id in keep
    }


def release_segment_memory() -> tuple[bool, str]:
    """Release Python references and unused accelerator allocator blocks."""

    gc.collect()
    try:
        import torch

        if not torch.cuda.is_available():
            return True, "gc"
        torch.cuda.empty_cache()
        try:
            torch.cuda.ipc_collect()
        except Exception:
            pass
        return True, "gc + cuda cache"
    except Exception as exc:
        return True, f"gc ({type(exc).__name__}: {exc})"


def remove_managed_path(
    path: str | None,
    roots: Iterable[str],
    prefixes: Iterable[str],
    *,
    keep_paths: Iterable[str] = (),
) -> bool:
    """Delete only an explicitly managed file below an allowed root."""

    if not path:
        return False
    resolved = os.path.realpath(path)
    if resolved in {os.path.realpath(item) for item in keep_paths if item}:
        return False
    if not any(
        os.path.basename(resolved).startswith(prefix)
        for prefix in prefixes
    ):
        return False
    allowed = False
    for root in roots:
        try:
            root = os.path.realpath(root)
            if os.path.commonpath([root, resolved]) == root:
                allowed = True
                break
        except ValueError:
            continue
    if not allowed or not os.path.isfile(resolved):
        return False
    os.unlink(resolved)
    return True
