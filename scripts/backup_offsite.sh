#!/bin/bash
# Backup offsite: export full DB as JSON and push to GitHub (backups branch)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ORGCHART_DIR="$(dirname "$SCRIPT_DIR")"
DB_PATH="$ORGCHART_DIR/orgchart.db"
BACKUP_BRANCH="backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

cd "$ORGCHART_DIR"

# 1. Export full DB to JSON (includes people, todos, threads, etc.)
python3 -c "
import sys, json
sys.path.insert(0, '.')
import db
conn = db.get_db()
data = db.export_all(conn)
conn.close()
print(json.dumps(data, ensure_ascii=False, indent=2))
" > /tmp/orgchart_backup.json

# 2. Also create a physical SQLite backup
SQLITE_BACKUP="/tmp/orgchart_backup_${TIMESTAMP}.db"
sqlite3 "$DB_PATH" ".backup '${SQLITE_BACKUP}'"

# 3. Switch to backups branch (orphan if doesn't exist), commit, push
CURRENT_BRANCH=$(git branch --show-current)

# Check if backups branch exists
if git show-ref --verify --quiet "refs/heads/$BACKUP_BRANCH"; then
    git checkout "$BACKUP_BRANCH" --quiet
else
    git checkout --orphan "$BACKUP_BRANCH" --quiet
    git rm -rf . --quiet 2>/dev/null || true
fi

# Copy backup files
cp /tmp/orgchart_backup.json ./backup_latest.json
cp "$SQLITE_BACKUP" ./backup_latest.db

# Keep a dated copy too (last 30)
cp /tmp/orgchart_backup.json "./backup_${TIMESTAMP}.json"
DATED_JSONS=($(ls -1 backup_2*.json 2>/dev/null | sort))
while [ ${#DATED_JSONS[@]} -gt 30 ]; do
    rm "${DATED_JSONS[0]}"
    DATED_JSONS=("${DATED_JSONS[@]:1}")
done

git add -A
git commit -m "backup ${TIMESTAMP}" --quiet 2>/dev/null || true
git push origin "$BACKUP_BRANCH" --quiet --force 2>/dev/null

# Switch back
git checkout "$CURRENT_BRANCH" --quiet

# Cleanup
rm -f /tmp/orgchart_backup.json "$SQLITE_BACKUP"

echo "✅ Offsite backup pushed to GitHub (branch: $BACKUP_BRANCH) at $TIMESTAMP"
