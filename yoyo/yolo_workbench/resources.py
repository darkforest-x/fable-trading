"""Low-priority cooperative work and explicit memory-pressure backoff on macOS."""
import re
import subprocess
import sys
import time


class Governor:
    def __init__(self):
        self.checked=0;self.free_percent=None

    def pause_reason(self):
        if sys.platform!='darwin':return None
        if time.monotonic()-self.checked>20:
            self.checked=time.monotonic()
            try:
                result=subprocess.run(['memory_pressure','-Q'],capture_output=True,text=True,timeout=3)
                match=re.search(r'free percentage:\s*(\d+)%',result.stdout)
                self.free_percent=int(match[1]) if match else None
            except (OSError,subprocess.TimeoutExpired):self.free_percent=None
        if self.free_percent is not None and self.free_percent<12:
            return f'内存压力较高（可用指标 {self.free_percent}%），暂缓检测，进度已保存'
        return None
