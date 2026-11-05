#!/bin/sh
# Run on the Umbrel host (not inside the container).
# With no arguments, select a trusted update source interactively.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$#" -eq 0 ] && [ -t 0 ]; then
  printf '\nNETOPS Command Center — manual update\n'
  printf '  1) GitHub repository and exact tag/branch\n'
  printf '  2) Local directory or ZIP archive\n'
  printf '  q) Cancel\n'
  printf 'Choose source [1/2/q]: '
  IFS= read -r choice
  case "$choice" in
    1)
      printf 'GitHub repository (https://github.com/OWNER/REPO): '
      IFS= read -r repo
      printf 'Release tag or branch (e.g. v3.2.0): '
      IFS= read -r ref
      exec python3 "$HERE/update-netops.py" --github "$repo" --ref "$ref" ;;
    2)
      printf 'Local release directory or ZIP absolute path: '
      IFS= read -r local_path
      exec python3 "$HERE/update-netops.py" --local "$local_path" ;;
    q|Q) printf 'Canceled.\n'; exit 0 ;;
    *) printf 'Unrecognized choice.\n' >&2; exit 2 ;;
  esac
fi
exec python3 "$HERE/update-netops.py" "$@"
