"""Launch the monitor under its authorized Python identity on macOS.

Task 52 (2026-10-16): TCC attributed caffeinate-wrapped Python disk reads to
caffeinate, rather than the explicitly authorized interpreter. Keep Python as
the LaunchAgent entry and run the same power assertion as its child instead.
"""
import os
import runpy
import subprocess
import sys


def main():
    keeper = None
    if sys.platform == "darwin":
        keeper = subprocess.Popen(
            ["/usr/bin/caffeinate", "-is", "-w", str(os.getpid())],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    try:
        runpy.run_module("yoyo.monitor.server", run_name="__main__", alter_sys=True)
    finally:
        if keeper is not None and keeper.poll() is None:
            keeper.terminate()
            try:
                keeper.wait(timeout=5)
            except subprocess.TimeoutExpired:
                keeper.kill()
                keeper.wait()


if __name__ == "__main__":
    main()
