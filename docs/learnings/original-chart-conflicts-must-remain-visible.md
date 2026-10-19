# Original chart conflicts must stay visible beside the learning rule

- **Problem**: A RektProof teaching chapter stated 1.618 as a definite reversal condition before its original image was archived.
- **Dead end**: ThreadReader returned a generic page for the 2026 status IDs. Repeating that request or trusting an earlier redraw could not verify the chart's labels.
- **Effective path**: FxEmbed's documented public-status endpoint returned original X media URLs. The live X pages confirmed author, post IDs, timestamps and media IDs. Inspection found the reversal image's line marked 1.168 while its own explanation says 1.618. Preserve both and label the inconsistency; do not silently repair the source or change strategy parameters.
- **General rule**: Recover and inspect the exact original before turning a diagram into an executable number. A matching post ID establishes provenance, not internal correctness. Attachments repeated in a later post do not acquire the later post's execution status.
- **Connections**: `yoyo/monitor/rektproof_assets.py`, the local RektProof handbook chapters c4/c5/c8, and the ETH PO3 case. The ETH ending post repeats an older edited plan screenshot still saying Position Closed: No; its public ending statement remains separate evidence.
