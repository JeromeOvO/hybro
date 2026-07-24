#!/usr/bin/env bash
set -euo pipefail

MONGO_URI="${MONGO_URI:-mongodb://127.0.0.1:27017/?replicaSet=rs0}"
DB_NAME="${DB_NAME:-hybro}"

mongosh "$MONGO_URI" --eval "
  db = db.getSiblingDB(\"$DB_NAME\");
  db.room_files.createIndex({ \"file_id\": 1 }, { unique: true, name: \"room_file_id_unique\" });
  db.room_files.createIndex({ \"room_id\": 1, \"created_at\": -1 }, { name: \"room_file_room_created\" });
  db.room_files.createIndex({ \"source_message_id\": 1 }, { sparse: true, name: \"room_file_source_message\" });
  db.room_files.createIndex(
    { \"origin_key\": 1 },
    {
      unique: true,
      name: \"room_file_origin_unique\",
      partialFilterExpression: { \"origin_key\": { \$type: \"string\" } }
    }
  );
  db.room_files.createIndex({ \"status\": 1, \"updated_at\": 1 }, { name: \"room_file_status_updated\" });
  db.room_files.createIndex(
    { \"source\": 1, \"status\": 1, \"last_referenced_at\": 1, \"created_at\": 1 },
    { name: \"room_file_retention\" }
  );
  db.room_files.createIndex(
    { \"reference_claims.message_id\": 1 },
    { name: \"room_file_reference_message\" }
  );
  print(\"Done — indexes created on room_files collection\");
"
