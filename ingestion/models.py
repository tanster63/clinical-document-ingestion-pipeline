"""Validated warehouse contracts.

Nothing reaches BigQuery without passing through these models. Field
names and types mirror `sql/ddl/schema.sql` one-for-one, so a row that
validates here loads there without a translation layer to drift out of sync.

A row that fails validation is dropped and recorded as an `ingestion_issues`
row; the rest of the document still lands. Ranges are clinical sanity
bounds, not style: a systolic pressure of 900 is a parse error wearing a
number's clothes, and it is better refused at the door than queried later.
"""

from datetime import date, datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ICD10 = Annotated[str, StringConstraints(pattern=r"^[A-Z]\d{2}(\.\d{1,4})?$")]
NonEmpty = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
Laterality = Literal["left", "right", "bilateral"]
VisitType = Literal["new", "follow_up", "post_op"]
Severity = Literal["warn", "error"]
ParseStatus = Literal["ok", "partial", "failed"]
FindingType = Literal["rom_active", "rom_passive", "strength", "special_test",
                      "inspection", "skin", "stability", "narrative"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Row(BaseModel):
    """Base for every warehouse row."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    def to_row(self) -> dict:
        """JSON-safe dict for a BigQuery load job (dates as ISO strings)."""
        return self.model_dump(mode="json")


class Document(Row):
    document_id: str                      # sha256 of the file bytes
    gcs_uri: NonEmpty
    file_name: NonEmpty
    file_bytes: int = Field(ge=0)
    page_count: int = Field(ge=0)
    mrn_from_filename: str | None = None
    pms_id_from_filename: str | None = None
    ingested_at: datetime = Field(default_factory=_now)
    ingest_run_id: str
    pipeline_version: str | None = None
    parse_status: ParseStatus = "ok"


class Patient(Row):
    patient_id: str
    mrn: NonEmpty
    pms_id: str | None = None
    legal_name: str | None = None
    family_name: str | None = None
    given_name: str | None = None
    preferred_name: str | None = None
    date_of_birth: date | None = None
    sex: Literal["M", "F", "O", "U"] | None = None
    phone_home: str | None = None
    phone_work: str | None = None
    first_seen_date: date | None = None
    last_seen_date: date | None = None
    source_document_id: str | None = None
    ingested_at: datetime = Field(default_factory=_now)


class Encounter(Row):
    encounter_id: str
    patient_id: str
    encounter_date: date
    encounter_seq: int | None = Field(default=None, ge=1)
    provider_name: str | None = None
    provider_role: str | None = None
    is_primary_provider: bool | None = None
    location_name: str | None = None
    chief_complaint_raw: str | None = None
    # --- the only LLM-derived columns in the warehouse ------------------------
    body_region: str | None = None
    laterality: Laterality | None = None
    visit_type: VisitType | None = None
    hpi_summary: str | None = Field(default=None, max_length=1000)
    llm_model: str | None = None
    llm_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    # --- everything below is parsed from the page -----------------------------
    hpi_text: str | None = None
    note_text: str | None = None
    follow_up_interval_days: int | None = Field(default=None, ge=0, le=3650)
    follow_up_raw: str | None = None
    signed_by: str | None = None
    signed_at: datetime | None = None
    source_document_id: str | None = None
    source_page_start: int | None = None
    source_page_end: int | None = None


class Vitals(Row):
    encounter_id: str
    patient_id: str
    taken_by: str | None = None
    taken_date: date | None = None
    bp_systolic: int | None = Field(default=None, ge=40, le=300)
    bp_diastolic: int | None = Field(default=None, ge=20, le=200)
    pulse: int | None = Field(default=None, ge=20, le=250)
    respirations: int | None = Field(default=None, ge=4, le=80)
    o2_sat: int | None = Field(default=None, ge=50, le=100)
    temperature_f: float | None = Field(default=None, ge=90.0, le=110.0)
    height_in: float | None = Field(default=None, ge=20.0, le=100.0)
    weight_lbs: float | None = Field(default=None, ge=20.0, le=800.0)
    bmi: float | None = Field(default=None, ge=5.0, le=100.0)
    bsa: float | None = Field(default=None, ge=0.3, le=4.0)
    is_patient_reported: bool = False
    source_document_id: str | None = None
    source_page: int | None = None


class Diagnosis(Row):
    diagnosis_id: str
    encounter_id: str
    patient_id: str
    icd10_code: ICD10 | None = None
    icd10_description: str | None = None
    diagnosis_text: NonEmpty
    is_primary: bool = False
    body_region: str | None = None
    laterality: Laterality | None = None
    source: Literal["impression", "imaging"] = "impression"
    source_document_id: str | None = None
    source_page: int | None = None


class Prescription(Row):
    prescription_id: str
    encounter_id: str
    patient_id: str
    drug_name: NonEmpty
    strength: str | None = None
    strength_unit: str | None = None
    dose_form: str | None = None
    route: str | None = None
    sig_text: str | None = None
    quantity: float | None = Field(default=None, ge=0)
    quantity_unit: str | None = None
    refills: int | None = Field(default=None, ge=0, le=99)
    duration_days: int | None = Field(default=None, ge=0, le=3650)
    is_prn: bool = False
    drug_class: str | None = None      # resolved from ref_drug_class after load
    action: Literal["new", "modify", "continue"] | None = None
    source_document_id: str | None = None
    source_page: int | None = None


class MedicationSnapshot(Row):
    """What the patient was already taking, as of one encounter.

    Not a patient attribute: the same patient's list differs between visits,
    and flattening it to patient level destroys that.
    """

    encounter_id: str
    patient_id: str
    medication_name: NonEmpty
    strength: str | None = None
    strength_unit: str | None = None
    dose_form: str | None = None
    route: str | None = None
    source_document_id: str | None = None
    source_page: int | None = None


class PatientHistory(Row):
    """A longitudinal fact about the patient, read from the left rail.

    Patient-level on purpose: unlike the medication list beside it, a
    surgical or family history does not change between two visits three weeks
    apart, and storing it per encounter would multiply one fact by the number
    of times the chart happened to print it.
    """

    history_id: str
    patient_id: str
    history_type: Literal["medical", "musculoskeletal", "family",
                          "musculoskeletal_surgery", "surgical", "social", "allergy"]
    item_text: NonEmpty
    source_document_id: str | None = None
    source_page: int | None = None


class Procedure(Row):
    """Something that was done to the patient, at an encounter.

    `performed_date` is separate from the encounter date on purpose: an
    operation is usually reported at the visit *after* it, and collapsing the
    two would date every procedure to its follow-up appointment.
    """

    procedure_id: str
    encounter_id: str
    patient_id: str
    procedure_name: NonEmpty
    body_part: str | None = None
    laterality: Laterality | None = None
    performed_date: date | None = None
    surgeon_name: str | None = None
    note_text: str | None = None
    source_document_id: str | None = None
    source_page: int | None = None


class ImagingStudy(Row):
    imaging_id: str
    encounter_id: str
    patient_id: str
    modality: NonEmpty
    body_part: str | None = None
    laterality: Laterality | None = None
    performed_date: date | None = None
    interpretation_text: str | None = None
    impression: str | None = None
    source_document_id: str | None = None
    source_page: int | None = None


class ExamFinding(Row):
    finding_id: str
    encounter_id: str
    patient_id: str
    body_part: str | None = None
    laterality: Laterality | None = None
    finding_type: FindingType
    measure_name: str | None = None
    value_numeric: float | None = None
    value_text: str | None = None
    unit: str | None = None
    source_document_id: str | None = None
    source_page: int | None = None


class IngestionIssue(Row):
    issue_id: str
    document_id: str
    encounter_id: str | None = None
    severity: Severity
    issue_type: str
    field_name: str | None = None
    detail: str | None = None
    created_at: datetime = Field(default_factory=_now)
    ingest_run_id: str | None = None


class IngestRun(Row):
    run_id: str
    document_id: str | None = None
    gcs_uri: str | None = None
    trigger_source: Literal["eventarc", "manual", "backfill"]
    status: Literal["succeeded", "partial", "failed"]
    started_at: datetime
    finished_at: datetime | None = None
    encounters_written: int = 0
    issues_warn: int = 0
    issues_error: int = 0
    pipeline_version: str | None = None
    error_detail: str | None = None


class ExtractedDocument(BaseModel):
    """Everything one PDF produced, validated and ready for the warehouse."""

    model_config = ConfigDict(extra="forbid")

    document: Document
    # None when the file never opened, or opened but carried no identity at all.
    # A fabricated placeholder patient would be a row nobody can trace to a
    # person, sitting in the table clinical questions are counted from.
    patient: Patient | None = None
    encounters: list[Encounter] = Field(default_factory=list)
    vitals: list[Vitals] = Field(default_factory=list)
    diagnoses: list[Diagnosis] = Field(default_factory=list)
    prescriptions: list[Prescription] = Field(default_factory=list)
    medications: list[MedicationSnapshot] = Field(default_factory=list)
    history: list[PatientHistory] = Field(default_factory=list)
    procedures: list[Procedure] = Field(default_factory=list)
    imaging: list[ImagingStudy] = Field(default_factory=list)
    exam_findings: list[ExamFinding] = Field(default_factory=list)
    issues: list[IngestionIssue] = Field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)
