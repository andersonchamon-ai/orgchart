#!/bin/bash
# Backup offsite: export full DB as JSON and push to GitHub (backups branch)
# SAFETY: uses a temporary clone to avoid touching the main working directory
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ORGCHART_DIR="$(dirname "$SCRIPT_DIR")"
DB_PATH="$ORGCHART_DIR/orgchart.db"
REPO_URL="https://github.com/andersonchamon-ai/orgchart.git"
BACKUP_BRANCH="backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
TMP_DIR=$(mktemp -d)

cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

cd "$ORGCHART_DIR"

# 1. Export full DB to JSON
python3 -c "
import sys, json
sys.path.insert(0, '.')
import db
conn = db.get_db()
data = db.export_all(conn)
conn.close()
print(json.dumps(data, ensure_ascii=False, indent=2))
" > "$TMP_DIR/backup_latest.json"

# 2. Physical SQLite backup
sqlite3 "$DB_PATH" ".backup '${TMP_DIR}/backup_latest.db'"

# 3. Clone ONLY the backups branch into temp dir (shallow, fast)
cd "$TMP_DIR"
if git ls-remote --heads "$REPO_URL" "$BACKUP_BRANCH" | grep -q "$BACKUP_BRANCH"; then
    git clone --single-branch --branch "$BACKUP_BRANCH" --depth 1 "$REPO_URL" repo --quiet
    cd repo
else
    git clone --depth 1 "$REPO_URL" repo --quiet
    cd repo
    git checkout --orphan "$BACKUP_BRANCH" --quiet
    git rm -rf . --quiet 2>/dev/null || true
fi

# 4. Copy backup files
cp "$TMP_DIR/backup_latest.json" ./backup_latest.json
cp "$TMP_DIR/backup_latest.db" ./backup_latest.db

# Keep dated copies (last 30)
cp "$TMP_DIR/backup_latest.json" "./backup_${TIMESTAMP}.json"
DATED_JSONS=($(ls -1 backup_2*.json 2>/dev/null | sort))
while [ ${#DATED_JSONS[@]} -gt 30 ]; do
    rm "${DATED_JSONS[0]}"
    DATED_JSONS=("${DATED_JSONS[@]:1}")
done

# 5. Commit and push
git add -A
git commit -m "backup ${TIMESTAMP}" --quiet 2>/dev/null || true
git push origin "$BACKUP_BRANCH" --quiet --force 2>/dev/null

echo "✅ Offsite backup pushed to GitHub (branch: $BACKUP_BRANCH) at $TIMESTAMP"
