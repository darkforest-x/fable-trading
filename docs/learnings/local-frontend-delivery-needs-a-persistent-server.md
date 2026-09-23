# Local frontend delivery needs a persistent server

- **Problem**: The SPIKE Vision Lab opened during initial QA, but the owner's next turn found no listener on port 8771 and both page/API requests were refused.
- **Dead end**: A successful browser check inside a temporary tool execution session proved only momentary availability. It did not establish that the server would survive the session ending. The exact process termination event was not recorded.
- **Effective path**: Manage this workbench under its own macOS user LaunchAgent with RunAtLoad/KeepAlive and retained logs. Verify launchd ownership plus HTTP and browser availability after the start command has exited. Never copy inherited credentials into the plist or print launchctl's environment dump.
- **General rule**: When delivering a local web URL, validate both rendering and process lifetime. Keep start/status/stop instructions beside the project entry point.
- **Related files**: `yoyo/vision_research/manage.py`, `experiments/active/exp-spike-gemini-vision-20260923-v1/README.md`; loopback port 8771. The existing SPIKE monitor on 8766 is a different service.
