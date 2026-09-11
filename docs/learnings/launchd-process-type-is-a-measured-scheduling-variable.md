# Launchd process type is a measured scheduling variable

- **Problem**: The desktop monitor completed its isolated scanner pass much more slowly than its own per-cell CPU time suggested, while the LaunchAgent had no explicit `ProcessType`.
- **Dead end**: Treating low process niceness as proof that launchd was not applying background treatment conflates two controls. It does not establish the agent's CPU/IO scheduling class, and changing scanner recurrence or signal gates would not test that boundary.
- **Effective path**: Make `ProcessType=Interactive` explicit in this monitor's generated plist, retain every program argument and policy setting, and compare a later full pass's wall and thread-CPU timing with the saved pre-change snapshot.
- **General rule**: When a macOS service is unexpectedly slow, record the current plist hash and workload timing first; change one launchd scheduling field at a time and treat the after-run as evidence, not as a presumed root cause.
- **Involved**: `yoyo/monitor/manage.py`, `tests/monitor/test_manage.py`, `~/Library/LaunchAgents/com.fable.impulse-monitor.plist`; deployment requires a single deliberate service reload and must preserve the monitor's notification cutover.
