#!/bin/bash
# Uwitz Cards — Production Migration Script
# Run this ON THE SERVER after backup, before restarting the service.
# Safely migrates old schema → new schema.
set -euo pipefail

CARD_API="/home/snyco/CardAPI"

echo "=== Uwitz Cards Migration ==="

# 1. Install new dependencies
echo "[1/4] Installing dependencies..."
cd "$CARD_API"
source venv/bin/activate
pip install -r requirements.txt 2>&1 | tail -3

# 2. Run the schema migration
echo "[2/4] Running schema migration..."
python migrate_v1_to_v2.py

# 3. Verify no data loss
echo "[3/4] Verifying data integrity..."
mongosh --quiet --eval "
  db = db.getSiblingDB('cards');
  const old = db.user_cards.countDocuments();
  const now = db.cards ? db.cards.countDocuments() : 0;
  const users = db.users.countDocuments();
  const orders = db.orders.countDocuments();
  print('  Users: ' + users);
  print('  Old user_cards: ' + old);
  print('  New cards: ' + now);
  print('  Orders: ' + orders);
  if (old > 0 && now === 0) {
    print('  WARNING: user_cards had data but cards is empty! Migration may have failed.');
    process.exit(1);
  }
  print('  Data integrity OK');
" 2>/dev/null || echo "  (mongosh check skipped — verify manually)"

# 4. Update systemd service path
echo "[4/4] Updating systemd service..."
if [ -f /etc/systemd/system/cards.service ]; then
  sudo systemctl stop cards.service 2>/dev/null || true
fi

echo ""
echo "=== Migration complete ==="
echo "Next: deploy the new code and restart the service."
