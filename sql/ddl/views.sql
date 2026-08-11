-- Curated read layer. The agent queries these two and nothing else.
--
-- The joins a clinical question needs live here rather than in generated SQL:
-- the model picks columns, not join keys, which is the difference between a
-- wrong answer and a wrong column name. It also leaves the physical tables free
-- to change without retraining anything.

CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.v_encounter_summary`
OPTIONS(description="One row per encounter, pre-joined to the patient and to that encounter's primary diagnosis, with counts and flags for population questions. Grain is guaranteed one row per encounter: the primary diagnosis is picked with ARRAY_AGG(...LIMIT 1), so a chart that flags two diagnoses primary cannot fan the row out and inflate every aggregate built on it.")
AS
WITH primary_diagnosis AS (
  -- Grain safety, not style. A LEFT JOIN on is_primary duplicates the encounter
  -- once per primary diagnosis, and every COUNT over this view then silently
  -- over-reports.
  SELECT
    encounter_id,
    ARRAY_AGG(
      STRUCT(icd10_code, icd10_description, diagnosis_text, body_region, laterality)
      ORDER BY is_primary DESC, diagnosis_id
      LIMIT 1
    )[SAFE_OFFSET(0)] AS dx
  FROM `${PROJECT}.${DATASET}.diagnoses`
  GROUP BY encounter_id
)
SELECT
  e.encounter_id,
  e.patient_id,
  p.mrn,
  p.legal_name,
  p.preferred_name,
  p.given_name,
  p.family_name,
  p.date_of_birth,
  p.sex,
  e.encounter_date,
  e.encounter_seq,
  e.provider_name,
  e.provider_role,
  e.location_name,
  e.chief_complaint_raw,
  e.follow_up_interval_days,
  e.follow_up_raw,

  -- Body region, three ways, each honest about where it came from.
  -- `primary_body_region` is deterministic: it is decoded from the primary
  -- diagnosis's ICD-10 code. `body_region` is the model's reading of the prose
  -- and is NULL whenever the classifier was not run. `body_region_effective`
  -- prefers the code and falls back to the model, and is what population
  -- questions should group by.
  d.dx.body_region AS primary_body_region,
  d.dx.laterality  AS primary_laterality,
  e.body_region,
  e.laterality,
  e.visit_type,
  e.llm_confidence,
  e.hpi_summary,
  COALESCE(d.dx.body_region, e.body_region) AS body_region_effective,

  d.dx.icd10_code      AS primary_icd10_code,
  d.dx.diagnosis_text  AS primary_diagnosis,

  (SELECT COUNT(*) FROM `${PROJECT}.${DATASET}.diagnoses` dx
     WHERE dx.encounter_id = e.encounter_id) AS diagnosis_count,
  (SELECT COUNT(*) FROM `${PROJECT}.${DATASET}.prescriptions` rx
     WHERE rx.encounter_id = e.encounter_id) AS prescription_count,
  (SELECT COUNT(*) FROM `${PROJECT}.${DATASET}.imaging_studies` im
     WHERE im.encounter_id = e.encounter_id) AS imaging_count,
  (SELECT COUNT(*) FROM `${PROJECT}.${DATASET}.procedures` pr
     WHERE pr.encounter_id = e.encounter_id) AS procedure_count,
  (SELECT COUNT(*) FROM `${PROJECT}.${DATASET}.medication_snapshots` ms
     WHERE ms.encounter_id = e.encounter_id) AS medications_on_arrival_count,

  -- "On an anti-inflammatory" has two honest readings and the warehouse can
  -- answer both: already taking one on arrival, or prescribed one at the visit.
  -- Both resolve the class from the seeded ref_drug_class table, so nothing has
  -- to know from memory that meloxicam is an NSAID.
  EXISTS(SELECT 1
         FROM `${PROJECT}.${DATASET}.medication_snapshots` ms
         JOIN `${PROJECT}.${DATASET}.ref_drug_class` rdc
           ON LOWER(rdc.drug_name) = LOWER(ms.medication_name)
         WHERE ms.encounter_id = e.encounter_id
           AND rdc.is_anti_inflammatory) AS anti_inflammatory_on_arrival,
  EXISTS(SELECT 1
         FROM `${PROJECT}.${DATASET}.prescriptions` rx
         JOIN `${PROJECT}.${DATASET}.ref_drug_class` rdc
           ON LOWER(rdc.drug_name) = LOWER(rx.drug_name)
         WHERE rx.encounter_id = e.encounter_id
           AND rdc.is_anti_inflammatory) AS anti_inflammatory_prescribed,
  (EXISTS(SELECT 1
          FROM `${PROJECT}.${DATASET}.medication_snapshots` ms
          JOIN `${PROJECT}.${DATASET}.ref_drug_class` rdc
            ON LOWER(rdc.drug_name) = LOWER(ms.medication_name)
          WHERE ms.encounter_id = e.encounter_id AND rdc.is_anti_inflammatory)
   OR EXISTS(SELECT 1
             FROM `${PROJECT}.${DATASET}.prescriptions` rx
             JOIN `${PROJECT}.${DATASET}.ref_drug_class` rdc
               ON LOWER(rdc.drug_name) = LOWER(rx.drug_name)
             WHERE rx.encounter_id = e.encounter_id AND rdc.is_anti_inflammatory)
  ) AS anti_inflammatory_active,

  EXISTS(SELECT 1 FROM `${PROJECT}.${DATASET}.imaging_studies` im
         WHERE im.encounter_id = e.encounter_id
           AND im.performed_date = e.encounter_date) AS imaging_same_day,

  e.source_document_id,
  e.source_page_start,
  e.source_page_end
FROM `${PROJECT}.${DATASET}.encounters` e
JOIN `${PROJECT}.${DATASET}.patients` p USING (patient_id)
LEFT JOIN primary_diagnosis d USING (encounter_id);


CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.v_patient_timeline`
OPTIONS(description="One row per encounter, oldest first, with everything recorded at that visit attached as nested arrays. Two medication arrays are deliberately kept apart: medications_on_arrival is what the patient was ALREADY taking as of encounter_date, prescriptions_written is what was prescribed AT that visit. patient_history is patient-level and repeats on every row for that patient.")
AS
-- Every nested array is built here, grouped, and joined on -- NOT written as a
-- correlated ARRAY(SELECT ... WHERE x.encounter_id = e.encounter_id) subquery.
--
-- That is not a style preference. BigQuery rejects a correlated subquery that
-- references another table unless it can de-correlate it, and it could not
-- de-correlate these: every query that selected one of these columns failed
-- with "Correlated subqueries that reference other tables are not supported",
-- including plain `SELECT * FROM v_patient_timeline`. The view parsed, created
-- and described cleanly, so nothing caught it until the agent tried to read a
-- patient's history and got a 400 back. Aggregate first, join second.
WITH history AS (
  SELECT
    patient_id,
    ARRAY_AGG(STRUCT(history_type, item_text)
              ORDER BY history_type, item_text) AS patient_history
  FROM `${PROJECT}.${DATASET}.patient_history`
  GROUP BY patient_id
),
encounter_counts AS (
  SELECT patient_id, COUNT(*) AS encounter_count
  FROM `${PROJECT}.${DATASET}.encounters`
  GROUP BY patient_id
),
dx AS (
  SELECT
    encounter_id,
    ARRAY_AGG(STRUCT(icd10_code, icd10_description, diagnosis_text,
                     is_primary, body_region, laterality)) AS diagnoses
  FROM `${PROJECT}.${DATASET}.diagnoses`
  GROUP BY encounter_id
),
rx AS (
  SELECT
    rx.encounter_id,
    ARRAY_AGG(STRUCT(rx.drug_name, rx.strength, rx.strength_unit, rx.dose_form,
                     rx.route, rx.sig_text, rx.quantity, rx.quantity_unit,
                     rx.refills, rx.duration_days, rx.is_prn, rx.action,
                     rx.drug_class,
                     COALESCE(rdc.is_anti_inflammatory, FALSE) AS is_anti_inflammatory
              )) AS prescriptions_written
  FROM `${PROJECT}.${DATASET}.prescriptions` rx
  LEFT JOIN `${PROJECT}.${DATASET}.ref_drug_class` rdc
    ON LOWER(rdc.drug_name) = LOWER(rx.drug_name)
  GROUP BY rx.encounter_id
),
meds AS (
  SELECT
    ms.encounter_id,
    ARRAY_AGG(STRUCT(ms.medication_name, ms.strength, ms.strength_unit,
                     ms.dose_form, ms.route,
                     COALESCE(rdc.is_anti_inflammatory, FALSE) AS is_anti_inflammatory
              )) AS medications_on_arrival
  FROM `${PROJECT}.${DATASET}.medication_snapshots` ms
  LEFT JOIN `${PROJECT}.${DATASET}.ref_drug_class` rdc
    ON LOWER(rdc.drug_name) = LOWER(ms.medication_name)
  GROUP BY ms.encounter_id
),
img AS (
  SELECT
    encounter_id,
    ARRAY_AGG(STRUCT(modality, body_part, laterality, performed_date,
                     interpretation_text, impression)) AS imaging
  FROM `${PROJECT}.${DATASET}.imaging_studies`
  GROUP BY encounter_id
),
proc AS (
  SELECT
    encounter_id,
    ARRAY_AGG(STRUCT(procedure_name, body_part, laterality, performed_date,
                     surgeon_name, note_text)) AS procedures
  FROM `${PROJECT}.${DATASET}.procedures`
  GROUP BY encounter_id
),
exam AS (
  SELECT
    encounter_id,
    ARRAY_AGG(STRUCT(body_part, laterality, finding_type, measure_name,
                     value_numeric, unit, value_text)
              ORDER BY finding_type, laterality, measure_name) AS exam_findings
  FROM `${PROJECT}.${DATASET}.exam_findings`
  GROUP BY encounter_id
),
vit AS (
  -- One row per encounter at most, so this is a plain join rather than an
  -- aggregate; it stays a STRUCT because a visit has one set of vitals.
  SELECT
    encounter_id,
    STRUCT(taken_by, taken_date, bp_systolic, bp_diastolic, pulse,
           respirations, o2_sat, temperature_f, height_in,
           weight_lbs, bmi, bsa, is_patient_reported) AS vitals
  FROM `${PROJECT}.${DATASET}.vitals`
)
SELECT
  e.patient_id,
  p.mrn,
  p.legal_name,
  p.preferred_name,
  p.given_name,
  p.family_name,
  p.date_of_birth,
  p.sex,
  p.phone_home,
  p.first_seen_date,
  p.last_seen_date,
  ec.encounter_count,
  e.encounter_id,
  e.encounter_date,
  e.encounter_seq,
  e.provider_name,
  e.provider_role,
  e.location_name,
  e.body_region,
  e.laterality,
  e.visit_type,
  e.llm_confidence,
  e.chief_complaint_raw,
  e.hpi_summary,
  e.hpi_text,
  e.note_text,
  e.follow_up_interval_days,
  e.follow_up_raw,
  e.signed_by,
  e.signed_at,

  -- IFNULL, not the raw join result: a correlated ARRAY() subquery returns an
  -- empty array when nothing matches, a LEFT JOIN returns NULL. UNNEST(NULL)
  -- yields no rows either way, but IS NULL vs ARRAY_LENGTH = 0 does not, and
  -- callers were written against the empty-array behaviour.
  IFNULL(h.patient_history, []) AS patient_history,
  IFNULL(dx.diagnoses, []) AS diagnoses,
  IFNULL(rx.prescriptions_written, []) AS prescriptions_written,
  IFNULL(meds.medications_on_arrival, []) AS medications_on_arrival,
  IFNULL(img.imaging, []) AS imaging,
  IFNULL(proc.procedures, []) AS procedures,
  IFNULL(exam.exam_findings, []) AS exam_findings,
  vit.vitals,

  e.source_document_id,
  e.source_page_start,
  e.source_page_end
FROM `${PROJECT}.${DATASET}.encounters` e
JOIN `${PROJECT}.${DATASET}.patients` p USING (patient_id)
LEFT JOIN history h ON h.patient_id = e.patient_id
LEFT JOIN encounter_counts ec ON ec.patient_id = e.patient_id
LEFT JOIN dx ON dx.encounter_id = e.encounter_id
LEFT JOIN rx ON rx.encounter_id = e.encounter_id
LEFT JOIN meds ON meds.encounter_id = e.encounter_id
LEFT JOIN img ON img.encounter_id = e.encounter_id
LEFT JOIN proc ON proc.encounter_id = e.encounter_id
LEFT JOIN exam ON exam.encounter_id = e.encounter_id
LEFT JOIN vit ON vit.encounter_id = e.encounter_id;
