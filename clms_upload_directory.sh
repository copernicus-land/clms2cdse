#!/usr/bin/env bash
set -u

# ---- configurable paths ----
CLMS_UPLOAD_SH="/mnt/m/delivery_data/cfg25_logs/clms_upload.sh"
TAR_FOLDER="/mnt/m/delivery_data/cfg25_tar"
LOGFILE="/mnt/m/delivery_data/cfg25_logs/tar_upload_log.txt"
# ----------------------------

count=1

# Basic checks
[[ -x "$CLMS_UPLOAD_SH" ]] || { echo "ERROR: clms_upload.sh not found/executable: $CLMS_UPLOAD_SH" >&2; exit 1; }
[[ -d "$TAR_FOLDER" ]]     || { echo "ERROR: tar folder not found: $TAR_FOLDER" >&2; exit 1; }
mkdir -p "$(dirname "$LOGFILE")"

shopt -s nullglob

for f in "$TAR_FOLDER"/*.tar; do
  TS=$(date '+%d:%m:%Y %H:%M:%S')
  NAME=$(basename "$f")

  if "$CLMS_UPLOAD_SH" -b CLMS-EEA-DEV -l "$f"; then
    echo "$TS|$count|SUCCESS|$NAME" | tee -a "$LOGFILE"
  else
    echo "$TS|$count|ERROR|$NAME" | tee -a "$LOGFILE"
  fi

  ((count++))
done