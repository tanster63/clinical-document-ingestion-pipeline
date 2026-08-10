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
    # The source EMR prints the films taken before the findings, under its own
    # "X-Ray Data:" sub-heading. Reproduced so the corpus exercises the same
    # multi-paragraph study the provided chart does.
    films: str | None = None
    coded_diagnosis_line: str | None = None

    @property
    def modality_label(self) -> str:
        """How the EMR spells the modality in a study heading."""
        return {"XR": "X-Ray", "X-RAY": "X-Ray"}.get(self.modality.upper(), self.modality)


class EncounterSpec(BaseModel):
    encounter_date: date
    provider_name: str
    provider_role: str = "MD"
    is_primary_provider: bool = True
    chief_complaint: str
    hpi_text: str
    # The numbered problem heading the EMR prints above the coded diagnosis.
    # It is the clinician's shorthand ("Shoulder Pain, Right"), not the code's
    # description ("Pain in right shoulder") — the provided chart prints both.
    problem_title: str | None = None
    # Abnormalities only; everything else comes from the region's exam template
    # in corpus/exam.py, which is how the source EMR fills an exam too.
    exam_region: str | None = None
    exam_findings: dict = Field(default_factory=dict)
    exam_text: str | None = None
    note_text: str | None = None
    # Non-prescription plan entries, printed as the EMR's own "Plan: ..." lines.
    plan_lines: list[str] = Field(default_factory=list)
    operative_note: str | None = None
    procedure_name: str | None = None
    procedure_date: date | None = None
    surgeon: str | None = None
    follow_up_raw: str | None = None
    # Ground truth only — never rendered. The chart prints prose; this is the
    # interval the author meant by it, so eval/accuracy.py scores the
    # normalizer against a declared answer rather than against its own output.
    # Left None where the phrasing is deliberately vague, and then unscored.
    follow_up_days: int | None = None
    signed_by: str | None = None
    signed_at: datetime | None = None
    vitals: VitalsSpec | None = None
    current_medications: list[MedicationSpec] = Field(default_factory=list)
    diagnoses: list[DiagnosisSpec] = Field(default_factory=list)
    prescriptions: list[PrescriptionSpec] = Field(default_factory=list)
    imaging: list[ImagingSpec] = Field(default_factory=list)
    # Ground truth for the four LLM-derived columns:
    body_region: str
    laterality: str
    visit_type: str


class HistorySpec(BaseModel):
    """The left sidebar's longitudinal context.

    The brief calls this out directly: the sidebar carries what is true of the
    *patient* rather than of the visit. It is therefore modelled on the patient,
    not the encounter — unlike the medication list beside it, which changes
    between visits and is captured per encounter.
    """

    medical: list[str] = Field(default_factory=list)
    musculoskeletal: list[str] = Field(default_factory=list)
    musculoskeletal_family: list[str] = Field(default_factory=list)
    musculoskeletal_surgery: list[str] = Field(default_factory=list)
    surgical: list[str] = Field(default_factory=list)
    social: list[str] = Field(default_factory=list)


class PatientSpec(BaseModel):
    mrn: str
    pms_id: str
    family_name: str
    given_name: str
    preferred_name: str | None = None
    date_of_birth: date
    sex: str
    phone_home: str | None = None
    history: HistorySpec = Field(default_factory=HistorySpec)

    @property
    def legal_name(self) -> str:
        base = f"{self.family_name}, {self.given_name}"
        return f"{base} ({self.preferred_name})" if self.preferred_name else base


class ChartSpec(BaseModel):
    chart_id: str
    file_name: str
    location_name: str
    location_address: str
    practice_phone: str = "(615) 555-0100"
    practice_fax: str = "(615) 555-0198"
    patient: PatientSpec
    encounters: list[EncounterSpec]


def load_spec(path: Path) -> ChartSpec:
    return ChartSpec.model_validate(json.loads(Path(path).read_text()))
