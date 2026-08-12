#!/usr/bin/env python3
"""Print the remote training environment as one parseable line.

Shipped to the 3060 and run detached, because a child process started inside an
ssh session on that box never lets the exec channel close -- an inline
``python -c`` hangs forever instead of answering. Launching it through WMI and
reading the file it writes is the only shape that returns.

Output: ``<torch>|<ultralytics>|<numpy>|<cuda_available>|<device_name>``
"""

from __future__ import annotations


def main() -> int:
    import numpy
    import torch
    import ultralytics

    torch_version = torch.__version__.split("+")[0]
    available = torch.cuda.is_available()
    device = torch.cuda.get_device_name(0) if available else "none"
    print(
        f"{torch_version}|{ultralytics.__version__}|{numpy.__version__}|{available}|{device}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
