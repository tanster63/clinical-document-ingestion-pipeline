-- Curated read layer. The agent queries these two views and nothing else (§7).
--
-- The joins a clinical question actually needs live here rather than in
-- generated SQL: the model picks columns, not join keys, which is the
-- difference between a wrong answer and a wrong column name. It also leaves
-- the physical tables free to change without retraining anything.

CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.v_encounter_summary` AS
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
  e.body_region,
  e.laterality,
  e.visit_type,
  e.llm_confidence,
  e.chief_complaint_raw,
  e.hpi_summary,
  e.follow_up_interval_days,
  e.follow_up_raw,
  d.icd10_code     AS primary_icd10_code,
  d.diagnosis_text AS primary_diagnosis,
  (SELECT COUNT(*) FROM `${PROJECT}.${DATASET}.diagnoses` dx
     WHERE dx.encounter_id = e.encounter_id) AS diagnosis_count,
  (SELECT COUNT(*) FROM `${PROJECT}.${DATASET}.prescriptions` rx
     WHERE rx.encounter_id = e.encounter_id) AS prescription_count,
  (SELECT COUNT(*) FROM `${PROJECT}.${DATASET}.imaging_studies` im
     WHERE im.encounter_id = e.encounter_id) AS imaging_count,
  (SELECT COUNT(*) FROM `${PROJECT}.${DATASET}.medication_snapshots` ms
     WHERE ms.encounter_id = e.encounter_id) AS medications_on_arrival_count,
  -- Pre-computed because the brief's own example question asks for exactly
  -- this pair, and it spans encounters, prescriptions, ref_drug_class and
  -- imaging_studies. Answering it should not depend on the model rediscovering
  -- four join keys.
  EXISTS(SELECT 1
         FROM `${PROJECT}.${DATASET}.prescriptions` rx
         LEFT JOIN `${PROJECT}.${DATASET}.ref_drug_class` rdc
           ON LOWER(rdc.drug_name) = LOWER(rx.drug_name)
         WHERE rx.encounter_id = e.encounter_id
           AND rdc.is_anti_inflammatory) AS anti_inflammatory_prescribed,
  EXISTS(SELECT 1 FROM `${PROJECT}.${DATASET}.imaging_studies` im
         WHERE im.encounter_id = e.encounter_id
           AND im.performed_date = e.encounter_date) AS imaging_same_day,
  e.source_document_id,
  e.source_page_start,
  e.source_page_end
FROM `${PROJECT}.${DATASET}.encounters` e
JOIN `${PROJECT}.${DATASET}.patients` p USING (patient_id)
LEFT JOIN `${PROJECT}.${DATASET}.diagnoses` d
  ON d.encounter_id = e.encounter_id AND d.is_primary;

CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.v_patient_timeline` AS
SELECT
  e.patient_id,
  p.mrn,
  p.legal_name,
  p.preferred_name,
  p.given_name,
  p.family_name,
  p.date_of_birth,
  p.sex,
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
  e.chief_complaint_raw,
  e.hpi_summary,
  e.note_text,
  e.follow_up_interval_days,
  e.follow_up_raw,
  ARRAY(SELECT AS STRUCT d.icd10_code, d.icd10_description, d.diagnosis_text,
                         d.is_primary, d.body_region, d.laterality
        FROM `${PROJECT}.${DATASET}.diagnoses` d
        WHERE d.encounter_id = e.encounter_id) AS diagnoses,
  -- Written at this visit.
  ARRAY(SELECT AS STRUCT rx.drug_name, rx.strength, rx.strength_unit, rx.dose_form,
                         rx.route, rx.sig_text, rx.quantity, rx.refills,
                         rx.duration_days, rx.is_prn, rx.action, rx.drug_class,
                         COALESCE(rdc.is_anti_inflammatory, FALSE) AS is_anti_inflammatory
        FROM `${PROJECT}.${DATASET}.prescriptions` rx
        LEFT JOIN `${PROJECT}.${DATASET}.ref_drug_class` rdc
          ON LOWER(rdc.drug_name) = LOWER(rx.drug_name)
        WHERE rx.encounter_id = e.encounter_id) AS prescriptions_written,
  -- What the patient was already taking when they arrived. A different fact
  -- from the list above, and only true as of this encounter_date (§4.3).
  ARRAY(SELECT AS STRUCT ms.medication_name, ms.route,
                         COALESCE(rdc.is_anti_inflammatory, FALSE) AS is_anti_inflammatory
        FROM `${PROJECT}.${DATASET}.medication_snapshots` ms
        LEFT JOIN `${PROJECT}.${DATASET}.ref_drug_class` rdc
          ON LOWER(rdc.drug_name) = LOWER(ms.medication_name)
        WHERE ms.encounter_id = e.encounter_id) AS medications_on_arrival,
  ARRAY(SELECT AS STRUCT im.modality, im.body_part, im.laterality,
                         im.performed_date, im.impression
        FROM `${PROJECT}.${DATASET}.imaging_studies` im
        WHERE im.encounter_id = e.encounter_id) AS imaging,
  (SELECT AS STRUCT v.bp_systolic, v.bp_diastolic, v.pulse, v.respirations,
                    v.o2_sat, v.temperature_f, v.height_in, v.weight_lbs,
                    v.bmi, v.bsa, v.is_patient_reported
   FROM `${PROJECT}.${DATASET}.vitals` v
   WHERE v.encounter_id = e.encounter_id) AS vitals,
  e.source_document_id,
  e.source_page_start,
  e.source_page_end
FROM `${PROJECT}.${DATASET}.encounters` e
JOIN `${PROJECT}.${DATASET}.patients` p USING (patient_id);
