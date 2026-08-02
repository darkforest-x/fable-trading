"""Pin the native import order, or the suite segfaults halfway through.

LightGBM and PyTorch each ship their own OpenMP runtime. On macOS, loading
lightgbm's first and torch's second kills the process; the other order is fine.
Measured directly, same interpreter, nothing else in the script:

    import lightgbm, then torchvision + one Resize   -> exit 139 (SIGSEGV)
    import torchvision, then lightgbm                -> exit 0

Nothing in the test files is wrong. `full_frame_transforms` imports torchvision
lazily on purpose, so that tests which never touch training do not pay for torch.
But by the time it runs inside a full-suite session, the judgment tests have
already pulled in lightgbm, and the lazy import lands on the losing side of that
ordering. Alone the file passes in 1.1s; with the rest of the suite it hangs, and
with the files that sort after it, it segfaults -- same cause, two faces.

Importing torchvision here costs about 1.7s once per session and makes the
ordering an enforced property of the suite rather than an accident of which tests
happen to run first. The alternative -- KMP_DUPLICATE_LIB_OK, the usual
workaround -- suppresses the runtime's own duplicate-library check and is
documented as liable to crash or produce wrong answers, which is not a trade
worth making in a repo whose numbers decide whether money moves.

If torch is not installed, skip quietly: most of this suite has nothing to do
with it.
"""
from __future__ import annotations


def pytest_configure(config) -> None:  # noqa: ARG001
    try:
        import torchvision  # noqa: F401
    except Exception:  # noqa: BLE001
        # torch absent or broken -- the tests that need it will say so themselves
        pass
