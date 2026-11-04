#!/usr/bin/env bash
# Streams the verified source file into HDFS under a checksum-versioned path, then proves it arrived.
set -euo pipefail
cd "$(dirname "$0")/.."
SOURCE="data/raw/accepted_2007_to_2018Q4.csv.gz"
[ -f "$SOURCE" ] || { echo "Missing $SOURCE. See data/README.md." >&2; exit 2; }
./scripts/local_hadoop.sh status | grep -q NameNode || ./scripts/local_hadoop.sh start
eval "$(./scripts/local_hadoop.sh env)"
SHA="$(shasum -a 256 "$SOURCE" | cut -d' ' -f1)"
EXPECTED="$(grep expected_sha256 configs/pipeline.yaml | awk '{print $2}')"
[ "$SHA" = "$EXPECTED" ] || { echo "Checksum mismatch: $SHA" >&2; exit 3; }
TARGET="/credit_risk/bronze/${SHA:0:12}"
hdfs dfs -mkdir -p "$TARGET" 2>/dev/null
if hdfs dfs -test -e "$TARGET/accepted.csv" 2>/dev/null; then
  echo "already present: $TARGET/accepted.csv"
else
  # Stored uncompressed, so Spark can split the file into blocks and read them in parallel.
  gunzip -c "$SOURCE" | hdfs dfs -put - "$TARGET/accepted.csv" 2>/dev/null
fi
echo "--- hdfs dfs -ls"; hdfs dfs -ls "$TARGET" 2>/dev/null
echo "--- hdfs dfs -du"; hdfs dfs -du -s -h "$TARGET" 2>/dev/null
echo "--- hdfs dfs -cat (header, first 120 chars)"; hdfs dfs -cat "$TARGET/accepted.csv" 2>/dev/null | head -1 | cut -c1-120 || true
echo "--- hdfs fsck"; hdfs fsck "$TARGET/accepted.csv" -files -blocks 2>/dev/null | grep -E "Total size|Total blocks|Status|Default replication" 
