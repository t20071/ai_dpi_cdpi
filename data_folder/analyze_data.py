"""Analyze CSV datasets to find good scenario test citizens."""
import csv
import os

DATA_DIR = r"c:\research data\work\AI governance\CDPI_project\Project_ai_block\data_folder"

# Load identity
identity = {}
with open(os.path.join(DATA_DIR, "mosip_identity_registry.csv"), "r", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        identity[row["national_id"]] = row

# Load consent
consents = {}
with open(os.path.join(DATA_DIR, "depa_consent_artefacts.csv"), "r", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        nid = row["national_id"]
        if nid not in consents:
            consents[nid] = []
        consents[nid].append(row)

# Load agriculture
agri = {}
with open(os.path.join(DATA_DIR, "agriculture_land_registry.csv"), "r", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        agri[row["national_id"]] = row

# Load credit
credit = {}
with open(os.path.join(DATA_DIR, "mifos_credit_registry.csv"), "r", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        credit[row["national_id"]] = row

print("=== SCENARIO A: Subsidy Success ===")
print("Need: ACTIVE identity + ACTIVE agri_land consent (valid_until > 2026-08-15) + land record")
count = 0
for nid, id_rec in identity.items():
    if id_rec["identity_status"] != "ACTIVE":
        continue
    if nid not in consents:
        continue
    if nid not in agri:
        continue
    for c in consents[nid]:
        if "agri_land:read" in c["granular_permissions"] and c["consent_status"] == "ACTIVE" and c["valid_until"] > "2026-08-15":
            land = agri[nid]
            print(f"  NID={nid}, Name={id_rec['full_name']}, Lang={id_rec['preferred_language']}")
            print(f"    Consent: {c['consent_id']}, Purpose={c['consent_purpose']}, Status={c['consent_status']}, ValidUntil={c['valid_until']}")
            print(f"    Perms: {c['granular_permissions']}")
            print(f"    Land: {land['land_record_id']}, Area={land['land_area_acres']} acres, Crop={land['current_crop']}, District={land['district']}")
            count += 1
            if count >= 3:
                break
    if count >= 3:
        break

print("\n=== SCENARIO B: DEPA Consent Failure ===")
print("Need: ACTIVE identity + consent that does NOT allow mifos_credit (only agri_land) + credit record exists")
count = 0
for nid, id_rec in identity.items():
    if id_rec["identity_status"] != "ACTIVE":
        continue
    if nid not in consents:
        continue
    if nid not in credit:
        continue
    # Check: has consent but NO mifos_credit permission
    has_agri_only = False
    for c in consents[nid]:
        if c["consent_status"] == "ACTIVE" and "agri_land:read" in c["granular_permissions"] and "mifos_credit:read" not in c["granular_permissions"]:
            has_agri_only = True
            consent_rec = c
            break
    if has_agri_only:
        print(f"  NID={nid}, Name={id_rec['full_name']}")
        print(f"    Consent: {consent_rec['consent_id']}, Perms={consent_rec['granular_permissions']}, Status={consent_rec['consent_status']}")
        print(f"    Credit exists: score={credit[nid]['credit_score']}")
        count += 1
        if count >= 3:
            break

print("\n=== SCENARIO C: Human Escalation ===")
print("Need: SUSPENDED identity OR very low confidence scenario")
count = 0
for nid, id_rec in identity.items():
    if id_rec["identity_status"] == "SUSPENDED":
        print(f"  NID={nid}, Name={id_rec['full_name']}, Status={id_rec['identity_status']}")
        count += 1
        if count >= 3:
            break

print("\n=== ALSO: Expired consent records ===")
count = 0
for nid, id_rec in identity.items():
    if id_rec["identity_status"] != "ACTIVE":
        continue
    if nid not in consents:
        continue
    for c in consents[nid]:
        if c["consent_status"] == "EXPIRED":
            print(f"  NID={nid}, Name={id_rec['full_name']}, Consent={c['consent_id']}, Status={c['consent_status']}, ValidUntil={c['valid_until']}")
            count += 1
            if count >= 3:
                break
    if count >= 3:
        break
