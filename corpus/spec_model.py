"""Pydantic model of an authored chart. Field names mirror the BigQuery columns
so eval/accuracy.py can diff spec against warehouse without a mapping layer."""

import json
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, Field


class VitalsSpec(BaseModel):
    taken_by: str | None = None
    taken_date: date | None = None
    bp_systolic: int | None = None
    bp_diastolic: int | None = None
    pulse: int | None = None
    respirations: int | None = None
    o2_sat: int | None = None
    temperature_f: float | None = None
    height_in: float | None = None
    weight_lbs: float | None = None
    bmi: float | None = None
    bsa: float | None = None
    is_patient_reported: bool = False


class MedicationSpec(BaseModel):
    medication_name: str
    route: str | None = None


class DiagnosisSpec(BaseModel):
    icd10_code: str | None = None
    icd10_description: str | None = None
    diagnosis_text: str
    is_primary: bool = False
    source: str = "impression"


class PrescriptionSpec(BaseModel):
    drug_name: str
    strength: str | None = None
    strength_unit: str | None = None
    dose_form: str | None = None
    route: str | None = None
    sig_text: str
    quantity: float | None = None
    quantity_unit: str | None = None
    refills: int | None = None
    action: str = "new"  # new | modify | continue


class ImagingSpec(BaseModel):
    modality: str
    body_part: str
    laterality: str | None = None
    performed_date: date | None = None
    interpretation_text: str | None = None
    impression: str | None = None


class EncounterSpec(BaseModel):
    encounter_date: date
    provider_name: str
    provider_role: str = "MD"
    is_primary_provider: bool = True
    chief_complaint: str
    hpi_text: str
    exam_text: str | None = None
    note_text: str | None = None
    operative_note: str | None = None
    follow_up_raw: str | None = None
    signed_by: str | None = None
    signed_at: datetime | None = None
    vitals: VitalsSpec | None = None
    current_medications: list[MedicationSpec] = Field(default_factory=list)
    diagnoses: list[DiagnosisSpec] = Field(default_factory=list)
    prescriptions: list[PrescriptionSpec] = Field(default_factory=list)
    imaging: list[ImagingSpec] = Field(default_factory=list)
    # Ground truth for the four LLM-derived columns (§6.3):
    body_region: str
    laterality: str
    visit_type: str


class PatientSpec(BaseModel):
    mrn: str
    pms_id: str
    family_name: str
    given_name: str
    preferred_name: str | None = None
    date_of_birth: date
    sex: str
    phone_home: str | None = None

    @property
    def legal_name(self) -> str:
        base = f"{self.family_name}, {self.given_name}"
        return f"{base} ({self.preferred_name})" if self.preferred_name else base


class ChartSpec(BaseModel):
    chart_id: str
    file_name: str
    location_name: str
    location_address: str
    patient: PatientSpec
    encounters: list[EncounterSpec]


def load_spec(path: Path) -> ChartSpec:
    return ChartSpec.model_validate(json.loads(Path(path).read_text()))
