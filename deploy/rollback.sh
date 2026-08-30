#!/bin/bash
# Uwitz Cards — Rollback Script
# Run this ON THE SERVER if something goes wrong.
set -euo pipefail

CARD_API="/home/snyco/CardAPI"
BACKUP="${1:?Usage: rollback.sh <backup-directory>}"

echo "=== Uwitz Cards Rollback ==="
echo "Restoring from: $BACKUP"

if [ ! -d "$BACKUP/old-code" ]; then
  echo "ERROR: $BACKUP/old-code not found"
  exit 1
fi

# 1. Stop service
echo "[1/3] Stopping service..."
sudo systemctl stop cards.service 2>/dev/null || true

# 2. Restore code
echo "[2/3] Restoring old code..."
rm -rf "$CARD_API"
cp -r "$BACKUP/old-code" "$CARD_API"

# 3. Restore MongoDB if dump exists
if [ -d "$BACKUP/mongodump/cards" ]; then
  echo "[3/3] Restoring MongoDB..."
  mongorestore --gzip --dir="$BACKUP/mongodump/cards" --db=cards --drop 2>/dev/null || echo "  (mongorestore failed — restore manually)"
else
  echo "[3/3] No MongoDB dump found, skipping."
fi

# 4. Restart
sudo systemctl start cards.service
sleep 2

if sudo systemctl is-active --quiet cards.service; then
  echo ""
  echo "=== Rollback complete ==="
  echo "Service is running with old code."
else
  echo ""
  echo "=== ROLLBACK FAILED ==="
  echo "Check: sudo journalctl -u cards.service -n 50"
fi
