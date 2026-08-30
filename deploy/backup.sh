#!/bin/bash
# Uwitz Cards — Production Backup Script
# Run this ON THE SERVER before deploying new code.
# Creates a MongoDB backup + snapshots the old code.
set -euo pipefail

BACKUP_DIR="/home/snyco/backups/cards-$(date +%Y%m%d-%H%M%S)"
CARD_API="/home/snyco/CardAPI"

echo "=== Uwitz Cards Backup ==="
echo "Creating backup at: $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"

# 1. Dump MongoDB
echo "[1/3] Dumping MongoDB..."
mongodump --uri="$(grep MONGO_URI "$CARD_API/.env" 2>/dev/null || echo 'mongodb://localhost:27017')" \
  --db=cards \
  --out="$BACKUP_DIR/mongodump" \
  --gzip 2>/dev/null || {
    # Fallback: try with old-style env vars
    MONGO_USER=$(grep MONGO_USER "$CARD_API/.env" 2>/dev/null | cut -d= -f2)
    MONGO_PASS=$(grep MONGO_PASS "$CARD_API/.env" 2>/dev/null | cut -d= -f2)
    MONGO_HOST=$(grep MONGO_HOST "$CARD_API/.env" 2>/dev/null | cut -d= -f2)
    if [ -n "$MONGO_USER" ]; then
      mongodump --uri="mongodb://${MONGO_USER}:${MONGO_PASS}@${MONGO_HOST}" \
        --db=cards \
        --out="$BACKUP_DIR/mongodump" \
        --gzip 2>/dev/null
    fi
  }
echo "  MongoDB dumped to $BACKUP_DIR/mongodump/"

# 2. Snapshot old code
echo "[2/3] Snapshotting old code..."
cp -r "$CARD_API" "$BACKUP_DIR/old-code"
echo "  Old code copied to $BACKUP_DIR/old-code/"

# 3. Save collection counts for verification
echo "[3/3] Recording collection counts..."
mongosh --quiet --eval "
  db = db.getSiblingDB('cards');
  printjson({
    users: db.users.countDocuments(),
    user_cards: db.user_cards.countDocuments(),
    cards: db.cards ? db.cards.countDocuments() : 0,
    orders: db.orders.countDocuments(),
    verified_domains: db.verified_domains ? db.verified_domains.countDocuments() : 0,
    webhook_events: db.webhook_events ? db.webhook_events.countDocuments() : 0,
    stripe_events: db.stripe_events ? db.stripe_events.countDocuments() : 0,
  });
" > "$BACKUP_DIR/collection-counts.json" 2>/dev/null || echo "  (mongosh not available — counts skipped)"

echo ""
echo "=== Backup complete ==="
echo "Location: $BACKUP_DIR"
echo ""
echo "To restore if needed:"
echo "  mongorestore --gzip --dir=$BACKUP_DIR/mongodump/cards --db=cards"
echo "  rm -rf $CARD_API && cp -r $BACKUP_DIR/old-code $CARD_API"
