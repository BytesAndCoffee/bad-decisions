#!/usr/bin/env bash
set -euo pipefail

output=${1:-build/demo.cast}
command -v asciinema >/dev/null || { echo "asciinema is required" >&2; exit 1; }
command -v regret >/dev/null || { echo "regret is required" >&2; exit 1; }
mkdir -p "$(dirname "$output")"

demo='set -eu
printf "\\033[1;38;2;184;86;24m$ regret --version\\033[0m\\n"
regret --version
sleep 1
printf "\\n\\033[1;38;2;184;86;24m$ regret health\\033[0m\\n"
regret health
sleep 1
printf "\\n\\033[1;38;2;184;86;24m$ regret deal --packs coffee\\033[0m\\n"
regret deal --packs coffee
sleep 3'

asciinema rec --overwrite --idle-time-limit 2 --command "bash -c '$demo'" "$output"
