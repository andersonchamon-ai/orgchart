#!/usr/bin/env python3
"""Import PhizChat headcount from CSV into orgchart.db, preserving existing leadership."""
import sqlite3, csv, sys

DB = 'orgchart.db'
CSV_FILE = 'phiz_headcount.csv'
COMPANY = 'phiz'

# Map spreadsheet names -> existing DB ids
EXISTING_MAP = {
    'Raphael Rodrigues': 102,  # Raphael -> CEO
    'Louis Wong': 21,          # Louis -> CTO
    'Luiz Parussolo': 22,      # Luizinho -> CPO
    'Armando Areias': 104,     # Armando -> Growth
    'Davi Pozzi': 105,         # Davi Pozzi -> CBO
    'Alex': 23,                # Alex -> HR (China)
    'Mays': 103,               # May -> General Manager
    'Fillip Bayer': 108,       # Bayer -> Design Manager
    'Eduardo Mauro': 106,      # Eduardo -> Phiz Rio / Biz Dev
}

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# Load CSV
with open(CSV_FILE, 'r') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

print(f"Loaded {len(rows)} rows from CSV")

# Build name -> row lookup
name_to_row = {}
for r in rows:
    name_to_row[r['Name'].strip()] = r

# Track created ids: name -> db_id
name_to_id = {}

# Pre-populate with existing
for name, db_id in EXISTING_MAP.items():
    name_to_id[name] = db_id

# Update existing entries' roles from spreadsheet
for name, db_id in EXISTING_MAP.items():
    if name in name_to_row:
        row = name_to_row[name]
        role = row['Position'].strip()
        if role:
            conn.execute('UPDATE people SET role=?, updated_at=datetime("now") WHERE id=?', (role, db_id))
            print(f"  Updated role for {name} (id={db_id}): {role}")

# Now create all new people, resolving managers
# We need to process in order: managers before their reports
# Build dependency graph
def get_or_create(name):
    """Get DB id for a person, creating if needed."""
    if name in name_to_id:
        return name_to_id[name]
    
    if name not in name_to_row:
        print(f"  WARNING: {name} not in CSV, skipping")
        return None
    
    row = name_to_row[name]
    manager_name = row['DirectManager'].strip()
    
    # Resolve manager first
    reports_to = None
    if manager_name:
        reports_to = get_or_create(manager_name)
    
    # Determine role
    role_parts = []
    pos = row['Position'].strip()
    if pos:
        role_parts.append(pos)
    
    # Build a descriptive role including dept info
    dept2 = row['Dept2'].strip()
    dept3 = row['Dept3'].strip()
    role = pos
    if dept3:
        role = f"{pos} — {dept3}"
    elif dept2:
        role = f"{pos} — {dept2}"
    
    region = row['Region'].strip()
    
    cur = conn.execute(
        'INSERT INTO people (name, role, company_id, level, reports_to, hc_filled, hc_open, sort_order) VALUES (?, ?, ?, ?, ?, 0, 0, 0)',
        (name, role, COMPANY, 2, reports_to)
    )
    new_id = cur.lastrowid
    name_to_id[name] = new_id
    print(f"  Created: {name} (id={new_id}) -> reports_to={reports_to} | {role}")
    return new_id

# Process all rows
created = 0
skipped = 0
for row in rows:
    name = row['Name'].strip()
    if name in EXISTING_MAP:
        skipped += 1
        continue
    get_or_create(name)
    created += 1

conn.commit()
print(f"\nDone! Created {created} new people, updated {len(EXISTING_MAP)} existing, skipped warnings above.")
print(f"Total PhizChat people now: {conn.execute('SELECT COUNT(*) FROM people WHERE company_id=?', (COMPANY,)).fetchone()[0]}")
conn.close()
