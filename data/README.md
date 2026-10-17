# Data

All data is **synthetic**. No real citizen information is included.

| Folder | What it holds | In git? |
|--------|---------------|---------|
| `raw/` | The five source registries as CSV. Read-only: the app imports them into SQLite and never edits them. | Yes |
| `runtime/` | Everything the app writes: the SQLite database, audit logs, the +1 legacy output CSV and eval reports. Created on first run. Delete it to start fresh. | No |

## `raw/`: source registries

Each file has 10,000 rows, one per citizen, joined on `national_id` (format `123-456-789`).

### `mosip_identity_registry.csv`: identity (MOSIP-style)

| Column | Meaning |
|--------|---------|
| `national_id` | Citizen ID (primary key) |
| `full_name` | Name |
| `phone_number` | Phone. **Corrupted:** saved in spreadsheet scientific notation (e.g. `9.18171E+11`), so the last digits are lost. The app fully masks it. |
| `preferred_language` | Hindi, Kannada, Marathi, Tamil, … (7 values) |
| `identity_status` | `ACTIVE` (8,513) or `SUSPENDED` (1,487). Only `ACTIVE` identities can use a service. |

### `agriculture_land_registry.csv`: land records

| Column | Meaning |
|--------|---------|
| `land_record_id` | `LAND-IN-00001` … (primary key) |
| `national_id` | Owner |
| `survey_number` | Land survey number |
| `district` | One of 16 districts |
| `land_area_acres` | Area. The subsidy needs at least `MINIMUM_LAND_AREA_ACRES` (2.0). |
| `current_crop` | One of 10 crops |

### `mifos_credit_registry.csv`: credit profiles (Mifos-style)

| Column | Meaning |
|--------|---------|
| `national_id` | Citizen (primary key) |
| `credit_score` | Score; compared with `MINIMUM_CREDIT_SCORE` (500) |
| `avg_monthly_turnover_inr` | Average monthly turnover in rupees |
| `repayment_history_status` | Excellent, Good, Average, Poor, … |
| `active_gst_billing_status` | `ACTIVE`, `INACTIVE` or `NOT_REGISTERED` |

### `depa_consent_artefacts.csv`: consent records (DEPA-style)

| Column | Meaning |
|--------|---------|
| `consent_id` | `CON-100001` … (primary key) |
| `national_id` | Citizen who gave consent |
| `consent_purpose` | Subsidy Screening, Micro-Loan Processing, Agricultural Support or Credit Assessment |
| `granular_permissions` | `agri_land:read`, `mifos_credit:read`, or both (comma separated) |
| `consent_status` | `ACTIVE` (7,502), `REVOKED` (1,498) or `EXPIRED` (1,000) |
| `valid_until` | Expiry, ISO 8601 (2026-02-16 … 2027-08-15, read as UTC) |
| `cryptographic_signature` | SHA-256 of `consent_id + national_id + consent_purpose + granular_permissions + consent_status + valid_until` concatenated with no separator. All 10,000 verify; editing any field breaks it. |

### `legacy_municipal_log.csv`: historical municipal transactions

| Column | Meaning |
|--------|---------|
| `transaction_id` | `TXN-10001` … (primary key) |
| `national_id` | Citizen |
| `service_requested` | One of 6 services (DBT registration, crop insurance, …) |
| `timestamp` | When it happened |
| `consent_token_used` | Token recorded with the transaction |
| `slm_confidence_score` | Model confidence recorded at the time |
| `execution_status` | `COMPLETED` or `ESCALATED_TO_HUMAN` |

## `runtime/`: generated files

| Path | Written by |
|------|-----------|
| `simulated_dpi.db` | First run (or `dpi-agent init-db`): the five CSVs imported into SQLite with indexes |
| `audit/consent_audit_log.jsonl` | Every consent check |
| `audit/workflow_audit_log.jsonl` | Every request, with its interpreter, intent and final status |
| `audit/escalations_log.jsonl` | Every case sent to human review |
| `legacy_municipal_log_output.csv` | The +1 legacy adapter: new transactions, kept apart from the original CSV |
| `evals/*.json` | `dpi-agent eval` reports |

Audit logs are hash-chained: each record stores the hash of the one before it. Run `dpi-agent audit verify` to check that none were edited or deleted.

## Demo citizens

| Scenario | National ID | Citizen | Why |
|----------|-------------|---------|-----|
| A, subsidy success | `765-741-434` | Suresh Kaur | ACTIVE identity, ACTIVE consent with `agri_land:read`, 12.8 acres |
| B, consent denied | `213-163-577` | Anjali Iyer | Consent covers `agri_land:read` only, so the credit check is refused |
| C, human escalation | `715-454-455` | Suresh Mishra | SUSPENDED identity |
| (revoked consent) | `201-206-655` | Sanjay Kumar | Only consent is REVOKED |

`scripts/analyze_data.py` is the script that found these.
