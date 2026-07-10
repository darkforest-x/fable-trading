# Unattended CLI runners need a real startup smoke test

- **问题**：A long-running Grok scheduler passed shell syntax checks but its first model call exited immediately, and the first shutdown attempt left the sleeping scheduler alive.
- **死胡同**：Checking that each CLI flag appeared in `--help` missed a pairwise incompatibility between `--check` and `--no-subagents`. Reusing one trap handler for `EXIT`, `INT`, and `TERM` cleaned the lock on a signal but did not terminate the shell.
- **有效路径**：Start one real bounded invocation before leaving the runner unattended, inspect the process tree and log, then verify stop and restart behavior with the exact scheduler PID. Keep cleanup on `EXIT`; make `INT` and `TERM` exit explicitly so the `EXIT` cleanup runs once.
- **通用规则**：For any unattended agent loop, acceptance requires one successful agent turn plus a stop/restart drill. Syntax checks and independent flag checks are not sufficient.
- **牵连**：`.omo/runtime/run_grok_two_day.sh`, Grok CLI option compatibility, atomic lock ownership, five-hour quota backoff.
