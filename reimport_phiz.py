#!/usr/bin/env python3
"""Clean reimport: delete all PhizChat people and reimport from CSV."""
import sqlite3, csv

DB = 'orgchart.db'
CSV_FILE = 'phiz_headcount.csv'
COMPANY = 'phiz'

conn = sqlite3.connect(DB)

# Backup: keep Anderson Chamon (id=20) who is in LionX holding but is the PhizChat anchor
# Delete all PhizChat people and their responsibilities
phiz_ids = [r[0] for r in conn.execute('SELECT id FROM people WHERE company_id=?', (COMPANY,)).fetchall()]
print(f"Deleting {len(phiz_ids)} existing PhizChat people...")
for pid in phiz_ids:
    conn.execute('DELETE FROM responsibilities WHERE person_id=?', (pid,))
conn.execute('DELETE FROM people WHERE company_id=?', (COMPANY,))
conn.commit()

# Load CSV
with open(CSV_FILE, 'r') as f:
    rows = list(csv.DictReader(f))
print(f"Loaded {len(rows)} rows from CSV")

# First pass: create all people without reports_to
name_to_id = {}
for row in rows:
    name = row['Name'].strip()
    region = row['Region'].strip()
    pos = row['Position'].strip()
    dept2 = row['Dept2'].strip()
    dept3 = row['Dept3'].strip()
    
    role = pos
    if dept3:
        role = f"{pos} — {dept3}" if pos else dept3
    elif dept2:
        role = f"{pos} — {dept2}" if pos else dept2
    
    # CEO level for top people (Raphael Rodrigues reports to nobody in PhizChat)
    manager = row['DirectManager'].strip()
    level = 1 if not manager else 2
    
    cur = conn.execute(
        'INSERT INTO people (name, role, company_id, level, reports_to, hc_filled, hc_open, sort_order, region) VALUES (?,?,?,?,NULL,0,0,0,?)',
        (name, role, COMPANY, level, region)
    )
    name_to_id[name] = cur.lastrowid

print(f"Created {len(name_to_id)} people")

# Second pass: set reports_to
# Anderson Chamon (id=20) is in LionX, Raphael reports to him
anderson_id = 20
for row in rows:
    name = row['Name'].strip()
    manager = row['DirectManager'].strip()
    if not manager:
        # Top person (Raphael Rodrigues) reports to Anderson in LionX
        conn.execute('UPDATE people SET reports_to=?, level=1 WHERE id=?', (anderson_id, name_to_id[name]))
        print(f"  {name} -> Anderson Chamon (id={anderson_id})")
    elif manager in name_to_id:
        conn.execute('UPDATE people SET reports_to=? WHERE id=?', (name_to_id[manager], name_to_id[name]))
    else:
        print(f"  WARNING: manager '{manager}' not found for {name}")

conn.commit()

# Verify
total = conn.execute('SELECT COUNT(*) FROM people WHERE company_id=?', (COMPANY,)).fetchone()[0]
orphans = conn.execute('SELECT COUNT(*) FROM people WHERE company_id=? AND reports_to IS NULL', (COMPANY,)).fetchone()[0]
print(f"\nDone! Total PhizChat: {total}, Orphans: {orphans}")
conn.close()
