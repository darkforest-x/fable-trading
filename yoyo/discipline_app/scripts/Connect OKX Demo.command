#!/bin/zsh
set -eu
task_script_dir="${0:A:h}"
exec "$HOME/Library/Caches/SpikeDiscipline/service-venv/bin/python" "$task_script_dir/setup_okx_demo.py"
