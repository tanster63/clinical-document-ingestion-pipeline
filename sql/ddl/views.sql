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
  (SELECT COUNT(*) FROM `${PROJECT}.${DATASET}.encounters` pe
     WHERE pe.patient_id = e.patient_id) AS encounter_count,
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

  -- Longitudinal context from the chart's left rail. True of the patient, not
  -- of the visit, so it repeats on every one of that patient's rows.
  ARRAY(SELECT AS STRUCT h.history_type, h.item_text
        FROM `${PROJECT}.${DATASET}.patient_history` h
        WHERE h.patient_id = e.patient_id
        ORDER BY h.history_type, h.item_text) AS patient_history,

  ARRAY(SELECT AS STRUCT d.icd10_code, d.icd10_description, d.diagnosis_text,
                         d.is_primary, d.body_region, d.laterality
        FROM `${PROJECT}.${DATASET}.diagnoses` d
        WHERE d.encounter_id = e.encounter_id) AS diagnoses,

  -- Prescribed AT this visit.
  ARRAY(SELECT AS STRUCT rx.drug_name, rx.strength, rx.strength_unit, rx.dose_form,
                         rx.route, rx.sig_text, rx.quantity, rx.quantity_unit,
                         rx.refills, rx.duration_days, rx.is_prn, rx.action,
                         rx.drug_class,
                         COALESCE(rdc.is_anti_inflammatory, FALSE) AS is_anti_inflammatory
        FROM `${PROJECT}.${DATASET}.prescriptions` rx
        LEFT JOIN `${PROJECT}.${DATASET}.ref_drug_class` rdc
          ON LOWER(rdc.drug_name) = LOWER(rx.drug_name)
        WHERE rx.encounter_id = e.encounter_id) AS prescriptions_written,

  -- Already being taken when the patient arrived, and only true as of
  -- encounter_date. A different fact from the array above.
  ARRAY(SELECT AS STRUCT ms.medication_name, ms.strength, ms.strength_unit,
                         ms.dose_form, ms.route,
                         COALESCE(rdc.is_anti_inflammatory, FALSE) AS is_anti_inflammatory
        FROM `${PROJECT}.${DATASET}.medication_snapshots` ms
        LEFT JOIN `${PROJECT}.${DATASET}.ref_drug_class` rdc
          ON LOWER(rdc.drug_name) = LOWER(ms.medication_name)
        WHERE ms.encounter_id = e.encounter_id) AS medications_on_arrival,

  ARRAY(SELECT AS STRUCT im.modality, im.body_part, im.laterality,
                         im.performed_date, im.interpretation_text, im.impression
        FROM `${PROJECT}.${DATASET}.imaging_studies` im
        WHERE im.encounter_id = e.encounter_id) AS imaging,

  ARRAY(SELECT AS STRUCT pr.procedure_name, pr.body_part, pr.laterality,
                         pr.performed_date, pr.surgeon_name, pr.note_text
        FROM `${PROJECT}.${DATASET}.procedures` pr
        WHERE pr.encounter_id = e.encounter_id) AS procedures,

  ARRAY(SELECT AS STRUCT f.body_part, f.laterality, f.finding_type, f.measure_name,
                         f.value_numeric, f.unit, f.value_text
        FROM `${PROJECT}.${DATASET}.exam_findings` f
        WHERE f.encounter_id = e.encounter_id
        ORDER BY f.finding_type, f.laterality, f.measure_name) AS exam_findings,

  (SELECT AS STRUCT v.taken_by, v.taken_date, v.bp_systolic, v.bp_diastolic, v.pulse,
                    v.respirations, v.o2_sat, v.temperature_f, v.height_in,
                    v.weight_lbs, v.bmi, v.bsa, v.is_patient_reported
   FROM `${PROJECT}.${DATASET}.vitals` v
   WHERE v.encounter_id = e.encounter_id) AS vitals,

  e.source_document_id,
  e.source_page_start,
  e.source_page_end
FROM `${PROJECT}.${DATASET}.encounters` e
JOIN `${PROJECT}.${DATASET}.patients` p USING (patient_id);
