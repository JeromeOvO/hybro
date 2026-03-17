#!/usr/bin/env bash
set -euo pipefail

MONGO_URI="${MONGO_URI:-mongodb://127.0.0.1:27017/?replicaSet=rs0}"
DB_NAME="${DB_NAME:-hybro}"

mongosh "$MONGO_URI" --eval "
  db = db.getSiblingDB(\"$DB_NAME\");
  db.file_uploads.createIndex({ \"room_id\": 1 });
  db.file_uploads.createIndex({ \"file_id\": 1 }, { unique: true });
  db.file_uploads.createIndex({ \"referenced\": 1, \"uploaded_at\": 1 });
  print(\"Done — indexes created on file_uploads collection\");
"
