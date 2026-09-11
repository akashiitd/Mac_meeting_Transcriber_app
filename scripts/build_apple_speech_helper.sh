#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_FILE="$ROOT_DIR/src/mac_native_speech_transcriber.swift"
INFO_PLIST="$ROOT_DIR/src/mac_native_speech_transcriber_info.plist"
OUTPUT_DIR="$ROOT_DIR/app/native"
OUTPUT_FILE="$OUTPUT_DIR/mac_native_speech_transcriber"

if [[ -x "$OUTPUT_FILE" && "$OUTPUT_FILE" -nt "$SOURCE_FILE" && "$OUTPUT_FILE" -nt "$INFO_PLIST" && "$OUTPUT_FILE" -nt "${BASH_SOURCE[0]}" ]]; then
  echo "Apple Speech helper is up to date: $OUTPUT_FILE"
  exit 0
fi

mkdir -p "$OUTPUT_DIR"

xcrun swiftc \
  -parse-as-library \
  "$SOURCE_FILE" \
  -Xlinker -sectcreate \
  -Xlinker __TEXT \
  -Xlinker __info_plist \
  -Xlinker "$INFO_PLIST" \
  -o "$OUTPUT_FILE"

chmod +x "$OUTPUT_FILE"
echo "Built Apple Speech helper: $OUTPUT_FILE"
