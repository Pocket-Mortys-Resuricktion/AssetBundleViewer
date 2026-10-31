#!/usr/bin/env bash
# ===================================================================
#  Pocket Mortys Bundle Catalog - run me
#
#  Installs what is needed the first time, builds the catalog if it is
#  missing, then opens the browser.
# ===================================================================
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3 was not found. Install it and try again." >&2
  exit 1
fi

echo "Checking requirements..."
"$PY" -m pip install -q -r requirements.txt

if [ ! -d AssetBundles ]; then
  cat >&2 <<'MSG'

No AssetBundles folder here.
Put your bundle files in a folder called AssetBundles next to this script, or:
    python3 catalog_server.py /path/to/bundles
MSG
  exit 1
fi

if [ ! -f catalog.json ]; then
  echo
  echo "Building the catalog. This takes a couple of minutes the first time."
  "$PY" catalog_builder.py AssetBundles -o catalog.json
fi

echo
exec "$PY" catalog_server.py AssetBundles
