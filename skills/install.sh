#!/usr/bin/env bash
# Install the linkedin-cli agent skill to the shared MayAI skills directory.
#
# Usage:  ./skills/install.sh
#         (or:  bash skills/install.sh   from the repo root)
#
# Target: ~/.config/mayai-cli/skills/linkedin-cli.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${SCRIPT_DIR}/linkedin-cli.md"
DEST_DIR="${HOME}/.config/mayai-cli/skills"
DEST="${DEST_DIR}/linkedin-cli.md"

if [[ ! -f "${SRC}" ]]; then
    echo "error: source skill not found at ${SRC}" >&2
    exit 1
fi

mkdir -p "${DEST_DIR}"
cp "${SRC}" "${DEST}"

echo "installed: ${DEST}"
