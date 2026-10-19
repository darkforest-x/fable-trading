#!/bin/zsh
set -euo pipefail
module_dir="${0:A:h:h}"
tool_cache="$HOME/Library/Caches/SpikeDiscipline"
export JAVA_HOME="$tool_cache/jdk/jdk-21.0.12.1.jdk/Contents/Home"
export ANDROID_HOME="$tool_cache/android-sdk"
export GRADLE_USER_HOME="$tool_cache/gradle"
cd "$module_dir/mobile"
npm ci
npm run build
npx cap sync
cd android
./gradlew --no-daemon assembleDebug
mkdir -p "$module_dir/releases"
cp app/build/outputs/apk/debug/app-debug.apk "$module_dir/releases/spike-discipline-0.8.14.apk"
"$ANDROID_HOME/build-tools/36.0.0/apksigner" verify --verbose "$module_dir/releases/spike-discipline-0.8.14.apk"
