# A `until ! pgrep -f PATTERN` waiter never exits when PATTERN appears in its own command line

- **Problem**: Two background waiters (`until ! pgrep -f "layered-refit-20261027-v1/config.json|strategies-causal-...config.json"; do sleep 60; done; <summaries>`) kept sleeping hours after every run had finished, so the chained summaries never started.
- **Dead ends**: The harness runs the loop as `zsh -c '... pgrep -f "<pattern>" ...'`. pgrep excludes only itself, not the parent shell, and the shell's argv contains the pattern text, so the waiter matched itself forever. An earlier waiter with a different pattern happened to exit and hid the problem.
- **What worked**: List `pgrep -f "until ! pgrep"`, check each command line for the experiment name, kill exactly those PIDs, then run the summaries directly.
- **General rule**: Wait on recorded PIDs (`while kill -0 $PID`), or write the pattern so that it cannot match its own text (e.g. `[l]ayered-refit`). Check a waiter once by running its pgrep from another shell before relying on it.
- **Related**: the background waiters for `exp-monday-limit-layered-refit-20261027-v1` and `exp-monday-limit-strategies-causal-20261027-v1` (2026-10-27/28).
