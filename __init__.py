from .segment_queue_node import (
    NODE_CLASS_MAPPINGS as QUEUE_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as QUEUE_NODE_DISPLAY_NAME_MAPPINGS,
)
from .wan_transition_node import SQRWanAnimateTransitionToVideo
from .scail_transition_node import SQRSCAIL2TransitionToVideo

NODE_CLASS_MAPPINGS = {
    **QUEUE_NODE_CLASS_MAPPINGS,
    "SQRWanAnimateTransitionToVideo": SQRWanAnimateTransitionToVideo,
    "SQRSCAIL2TransitionToVideo": SQRSCAIL2TransitionToVideo,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    **QUEUE_NODE_DISPLAY_NAME_MAPPINGS,
    "SQRWanAnimateTransitionToVideo": "SQR WanAnimate Transition To Video",
    "SQRSCAIL2TransitionToVideo": "SQR SCAIL2 Transition To Video",
}

WEB_DIRECTORY = "./js"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
