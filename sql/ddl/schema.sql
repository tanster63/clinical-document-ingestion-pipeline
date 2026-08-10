-- Cumberland Orthopedics clinical warehouse. All data synthetic.
-- Grain is stated on every table; see docs/schema.md for column-level notes.

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.documents` (
  document_id STRING NOT NULL OPTIONS(description="sha256 of the file bytes"),
  gcs_uri STRING NOT NULL,
  file_name STRING NOT NULL,
  file_bytes INT64,
  page_count INT64,
  mrn_from_filename STRING OPTIONS(description="MRN parsed from the filename, for cross-check"),
  pms_id_from_filename STRING,
  ingested_at TIMESTAMP NOT NULL,
  ingest_run_id STRING NOT NULL,
  pipeline_version STRING,
  parse_status STRING OPTIONS(description="ok | partial | failed")
) OPTIONS(description="One row per ingested PDF. Provenance and audit anchor.");

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.patients` (
  patient_id STRING NOT NULL OPTIONS(description="= MRN; single-practice key"),
  mrn STRING NOT NULL,
  pms_id STRING,
  legal_name STRING OPTIONS(description="as printed, e.g. 'BARLOW, TREMAINE (Trey Barlow)'"),
  family_name STRING,
  given_name STRING,
  preferred_name STRING OPTIONS(description="parenthetical name; required to answer 'Trey Barlow'"),
  date_of_birth DATE,
  sex STRING,
  phone_home STRING,
  phone_work STRING,
  first_seen_date DATE,
  last_seen_date DATE,
  source_document_id STRING,
  ingested_at TIMESTAMP
) OPTIONS(description="One row per patient, natural key MRN.");

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.encounters` (
  encounter_id STRING NOT NULL OPTIONS(description="sha256(patient_id, encounter_date, provider_name)"),
  patient_id STRING NOT NULL,
  encounter_date DATE NOT NULL,
  encounter_seq INT64 OPTIONS(description="1-based visit ordinal for this patient"),
  provider_name STRING,
  provider_role STRING,
  is_primary_provider BOOL,
  location_name STRING,
  chief_complaint_raw STRING,
  body_region STRING OPTIONS(description="LLM-derived"),
  laterality STRING OPTIONS(description="LLM-derived: left | right | bilateral | none"),
  visit_type STRING OPTIONS(description="LLM-derived: new | follow_up | post_op"),
  hpi_text STRING,
  hpi_summary STRING OPTIONS(description="LLM-derived one-sentence summary"),
  note_text STRING,
  follow_up_interval_days INT64,
  follow_up_raw STRING,
  signed_by STRING,
  signed_at TIMESTAMP,
  source_document_id STRING,
  source_page_start INT64,
  source_page_end INT64,
  llm_model STRING OPTIONS(description="model behind body_region, laterality, visit_type, hpi_summary"),
  llm_confidence FLOAT64
)
PARTITION BY encounter_date
CLUSTER BY patient_id
OPTIONS(description="One row per visit. The spine of the model.");

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.diagnoses` (
  diagnosis_id STRING NOT NULL,
  encounter_id STRING NOT NULL,
  patient_id STRING NOT NULL,
  icd10_code STRING,
  icd10_description STRING,
  diagnosis_text STRING,
  is_primary BOOL,
  body_region STRING OPTIONS(description="deterministic: ICD-10 lookup, else inherited from encounter"),
  laterality STRING OPTIONS(description="deterministic: from the ICD-10 code where encoded"),
  source STRING OPTIONS(description="impression | imaging"),
  source_document_id STRING,
  source_page INT64
) OPTIONS(description="One row per diagnosis within an encounter.");

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.prescriptions` (
  prescription_id STRING NOT NULL,
  encounter_id STRING NOT NULL,
  patient_id STRING NOT NULL,
  drug_name STRING,
  strength STRING,
  strength_unit STRING,
  dose_form STRING,
  route STRING,
  sig_text STRING,
  quantity FLOAT64,
  quantity_unit STRING,
  refills INT64,
  duration_days INT64,
  is_prn BOOL,
  drug_class STRING OPTIONS(description="joined from ref_drug_class, never LLM-derived"),
  action STRING OPTIONS(description="new | modify | continue"),
  source_document_id STRING,
  source_page INT64
) OPTIONS(description="One row per prescription WRITTEN at an encounter.");

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.medication_snapshots` (
  encounter_id STRING NOT NULL,
  patient_id STRING NOT NULL,
  medication_name STRING NOT NULL,
  strength STRING,
  strength_unit STRING,
  dose_form STRING,
  route STRING,
  source_document_id STRING,
  source_page INT64
) OPTIONS(description="One row per (encounter x medication the patient was ALREADY on). Sidebar list.");

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.imaging_studies` (
  imaging_id STRING NOT NULL,
  encounter_id STRING NOT NULL,
  patient_id STRING NOT NULL,
  modality STRING,
  body_part STRING,
  laterality STRING,
  performed_date DATE,
  interpretation_text STRING,
  impression STRING,
  source_document_id STRING,
  source_page INT64
) OPTIONS(description="One row per imaging study within an encounter.");

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.procedures` (
  procedure_id STRING NOT NULL,
  encounter_id STRING NOT NULL,
  patient_id STRING NOT NULL,
  procedure_name STRING NOT NULL,
  body_part STRING,
  laterality STRING,
  performed_date DATE OPTIONS(description="the date of the operation, which is not always the encounter date"),
  surgeon_name STRING,
  note_text STRING,
  source_document_id STRING,
  source_page INT64
) OPTIONS(description="One row per surgical procedure recorded at an encounter. Distinct from a diagnosis and from a prescription: it is a thing that was done.");

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.vitals` (
  encounter_id STRING NOT NULL,
  patient_id STRING NOT NULL,
  taken_by STRING,
  taken_date DATE,
  bp_systolic INT64,
  bp_diastolic INT64,
  pulse INT64,
  respirations INT64,
  o2_sat INT64,
  temperature_f FLOAT64,
  height_in FLOAT64,
  weight_lbs FLOAT64,
  bmi FLOAT64,
  bsa FLOAT64,
  is_patient_reported BOOL,
  source_document_id STRING,
  source_page INT64
) OPTIONS(description="One row per encounter, wide. NULL columns record the gap.");

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.exam_findings` (
  finding_id STRING NOT NULL,
  encounter_id STRING NOT NULL,
  patient_id STRING NOT NULL,
  body_part STRING,
  laterality STRING,
  finding_type STRING OPTIONS(description="rom_active|rom_passive|strength|special_test|inspection|skin|stability|narrative"),
  measure_name STRING,
  value_numeric FLOAT64,
  value_text STRING,
  unit STRING,
  source_document_id STRING,
  source_page INT64
) OPTIONS(description="One row per measurement (encounter x body part x test). Populated in Task 19.");

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.ingestion_issues` (
  issue_id STRING NOT NULL,
  document_id STRING,
  encounter_id STRING,
  severity STRING OPTIONS(description="warn | error"),
  issue_type STRING OPTIONS(description="missing_section|unparsed_field|low_confidence|validation_failed|identifier_mismatch"),
  field_name STRING,
  detail STRING,
  created_at TIMESTAMP,
  ingest_run_id STRING
) OPTIONS(description="Queryable record of every gap. A missing section lands here, not in a log.");

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.patient_history` (
  history_id STRING NOT NULL,
  patient_id STRING NOT NULL,
  history_type STRING OPTIONS(description="medical|musculoskeletal|family|musculoskeletal_surgery|surgical|social|allergy"),
  item_text STRING NOT NULL,
  source_document_id STRING,
  source_page INT64
) OPTIONS(description="One row per recorded history item for a patient. Patient-level, not visit-level: the left rail carries context true of the patient rather than of the encounter.");

-- Run-level audit. Added during implementation: a download that fails never
-- produces a `documents` row, so without this table the most interesting
-- failure in the pipeline would be invisible to SQL. Every invocation of the
-- service lands here, successful or not.
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.ingest_runs` (
  run_id STRING NOT NULL,
  document_id STRING OPTIONS(description="null when the file never parsed"),
  gcs_uri STRING,
  trigger_source STRING OPTIONS(description="eventarc | manual | backfill"),
  status STRING OPTIONS(description="succeeded | partial | failed"),
  started_at TIMESTAMP NOT NULL,
  finished_at TIMESTAMP,
  encounters_written INT64,
  issues_warn INT64,
  issues_error INT64,
  pipeline_version STRING,
  error_detail STRING
) OPTIONS(description="One row per ingest attempt. The audit trail for both trigger paths.");

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.ref_drug_class` (
  drug_name STRING NOT NULL,
  drug_class STRING,
  is_anti_inflammatory BOOL
) OPTIONS(description="Seed lookup. Deterministic drug classification; the LLM never assigns a class.");
