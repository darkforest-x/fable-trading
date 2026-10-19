#!/bin/zsh
set -euo pipefail
module_dir="${0:A:h:h}"
adb_path="$HOME/Library/Caches/SpikeDiscipline/android-sdk/platform-tools/adb"
device_id="${1:?Pass the authorized USB device ID from adb devices}"
"$adb_path" -s "$device_id" install -r "$module_dir/releases/spike-discipline-0.6.0.apk"
"$adb_path" -s "$device_id" reverse tcp:8897 tcp:8897
"$adb_path" -s "$device_id" shell am start -n com.spike.discipline/.MainActivity
print 'In the app, connect to http://127.0.0.1:8897 using your local service access token.'
