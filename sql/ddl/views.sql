CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.v_encounter_summary` AS
SELECT
  e.encounter_id,
  e.patient_id,
  p.legal_name,
  p.preferred_name,
  p.given_name,
  p.family_name,
  p.mrn,
  p.date_of_birth,
  p.sex,
  e.encounter_date,
  e.encounter_seq,
  e.provider_name,
  e.location_name,
  e.body_region,
  e.laterality,
  e.visit_type,
  e.chief_complaint_raw,
  e.hpi_summary,
  e.follow_up_interval_days,
  d.icd10_code    AS primary_icd10_code,
  d.diagnosis_text AS primary_diagnosis,
  (SELECT COUNT(*) FROM `${PROJECT}.${DATASET}.prescriptions` rx
     WHERE rx.encounter_id = e.encounter_id) AS prescription_count,
  (SELECT COUNT(*) FROM `${PROJECT}.${DATASET}.imaging_studies` im
     WHERE im.encounter_id = e.encounter_id) AS imaging_count
FROM `${PROJECT}.${DATASET}.encounters` e
JOIN `${PROJECT}.${DATASET}.patients` p USING (patient_id)
LEFT JOIN `${PROJECT}.${DATASET}.diagnoses` d
  ON d.encounter_id = e.encounter_id AND d.is_primary;

CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.v_patient_timeline` AS
SELECT
  e.patient_id,
  p.preferred_name,
  p.legal_name,
  e.encounter_date,
  e.encounter_seq,
  e.provider_name,
  e.body_region,
  e.visit_type,
  e.chief_complaint_raw,
  ARRAY(SELECT AS STRUCT d.icd10_code, d.diagnosis_text, d.is_primary
        FROM `${PROJECT}.${DATASET}.diagnoses` d
        WHERE d.encounter_id = e.encounter_id) AS diagnoses,
  ARRAY(SELECT AS STRUCT rx.drug_name, rx.strength, rx.strength_unit, rx.sig_text,
                         rx.quantity, rx.refills, rx.drug_class, rx.action
        FROM `${PROJECT}.${DATASET}.prescriptions` rx
        WHERE rx.encounter_id = e.encounter_id) AS prescriptions,
  ARRAY(SELECT AS STRUCT im.modality, im.body_part, im.laterality, im.impression
        FROM `${PROJECT}.${DATASET}.imaging_studies` im
        WHERE im.encounter_id = e.encounter_id) AS imaging,
  e.follow_up_interval_days
FROM `${PROJECT}.${DATASET}.encounters` e
JOIN `${PROJECT}.${DATASET}.patients` p USING (patient_id)
ORDER BY e.patient_id, e.encounter_date;
