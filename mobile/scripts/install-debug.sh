#!/usr/bin/env bash
set -euo pipefail
task_mobile_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
task_adb="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/.cache/xiaowo-android/sdk}}/platform-tools/adb"
if [[ ! -x "$task_adb" ]]; then task_adb="$(command -v adb)"; fi
task_args=()
if [[ -n "${ANDROID_SERIAL:-}" ]]; then task_args=(-s "$ANDROID_SERIAL"); fi
"$task_adb" "${task_args[@]}" get-state
"$task_adb" "${task_args[@]}" reverse tcp:8000 tcp:8000
"$task_adb" "${task_args[@]}" install -r "$task_mobile_dir/artifacts/xiaowo-debug.apk"
"$task_adb" "${task_args[@]}" shell am start -n cn.xiaowo.app/.MainActivity
