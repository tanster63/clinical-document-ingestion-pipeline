"""One PDF in, one validated ExtractedDocument out.

This is the only module that knows the order of operations. Each parser stays
ignorant of the others, and every section is guarded independently, so a chart
that is missing its imaging block still lands its patient, encounters,
diagnoses and prescriptions — with the gap recorded as a queryable row rather
than a log line nobody reads (§6.4).
"""

import re
import uuid
from dataclasses import replace
from datetime import date, datetime

from pydantic import ValidationError

from ingestion.config import Config
from ingestion.extract.encounters import split_encounters
from ingestion.extract.fields.diagnoses import parse_diagnoses
from ingestion.extract.fields.exam import parse_exam_findings
from ingestion.extract.fields.followup import parse_follow_up
from ingestion.extract.fields.identifiers import parse_filename_ids, parse_identity
from ingestion.extract.fields.imaging import parse_imaging
from ingestion.extract.fields.medications import parse_medications
from ingestion.extract.fields.prescriptions import parse_prescriptions
from ingestion.extract.fields.vitals import parse_vitals
from ingestion.extract.layout import PageLayout, load_pages, text_of
from ingestion.extract.llm import EMPTY_PROSE_FACTS, classify_encounter
from ingestion.extract.sections import find_sections, section_blocks, section_text
from ingestion.issues import IssueDraft, error, warn
from ingestion.keys import (
    diagnosis_key, document_key, encounter_key, exam_finding_key, imaging_key,
    issue_key, patient_key, prescription_key,
)
from ingestion.models import (
    Diagnosis, Document, Encounter, ExamFinding, ExtractedDocument, ImagingStudy,
    IngestionIssue, MedicationSnapshot, Patient, Prescription, Vitals,
)

PROVIDER_RE = re.compile(
    r"(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z'’\-]+)+)\s*,\s*"
    r"(?P<role>MD|DO|NP|PA-C|PA|DPM|APRN|RN)\b"
)
PRIMARY_PROVIDER_RE = re.compile(r"\(\s*primary\s+provider\s*\)", re.IGNORECASE)
SIGNATURE_RE = re.compile(
    r"Electronically\s+signed\s+by\s*:?\s*(?P<who>[^,\n]+(?:,\s*[A-Za-z\-]{2,5})?)"
    r"\s*(?:on\s+|,\s*)"
    r"(?P<when>\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s*(?:AM|PM))",
    re.IGNORECASE,
)
ENUMERATOR_RE = re.compile(r"(?:(?<=^)|(?<=\s))\d+[.)](?=\s|$)")
RX_ACTION_RE = re.compile(
    r"\b(?:prescription|prescrib\w+|modify\s+regimen|medication\s+management|refill)\b",
    re.IGNORECASE,
)
FOOTER_BAND_FRACTION = 0.90


def _clean_prose(text: str) -> str | None:
    """Readable prose out of PDF fragments: enumerators dropped, whitespace
    collapsed, orphaned punctuation repaired."""
    if not text:
        return None
    cleaned = ENUMERATOR_RE.sub(" ", text)
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    cleaned = re.sub(r"\.\s*\.", ".", cleaned)
    cleaned = " ".join(cleaned.split()).strip(" .,;:-")
    return cleaned or None


def _provider_of(*scopes: str) -> tuple[str | None, str | None, bool | None]:
    """The rendering provider, from the first scope that names one."""
    for scope in scopes:
        match = PROVIDER_RE.search(scope)
        if not match:
            continue
        tail = scope[match.end(): match.end() + 40]
        is_primary = True if PRIMARY_PROVIDER_RE.search(tail) else None
        return match.group("name").strip(), match.group("role").upper(), is_primary
    return None, None, None


def _signature_of(text: str) -> tuple[str | None, datetime | None]:
    match = SIGNATURE_RE.search(text)
    if not match:
        return None, None
    who = " ".join(match.group("who").split())
    try:
        # Recorded exactly as printed. The provided export stamps a clinic-local
        # zone (CDT) that a single-practice warehouse has no reason to convert;
        # a multi-site deployment would store the zone alongside it.
        when = datetime.strptime(" ".join(match.group("when").split()), "%m/%d/%Y %I:%M %p")
    except ValueError:
        when = None
    return who, when


def _location_of(pages: list[PageLayout], sidebar_sections: dict) -> str | None:
    """The clinic site: from the sidebar where the chart has one, otherwise the
    footer band, where the provided export prints it."""
    from_sidebar = _clean_prose(section_text(sidebar_sections, "location"))
    if from_sidebar:
        return from_sidebar
    for page in pages:
        floor = FOOTER_BAND_FRACTION * page.height
        for block in page.body + page.sidebar:
            text = block.text.strip()
            if block.y0 < floor or not text:
                continue
            if any(char.isdigit() for char in text) or PROVIDER_RE.search(text):
                continue
            return text
    return None


def extract_document(
    pdf_bytes: bytes,
    file_name: str,
    cfg: Config,
    *,
    gcs_uri: str | None = None,
    llm_client=None,
    run_id: str | None = None,
) -> ExtractedDocument:
    run_id = run_id or str(uuid.uuid4())
    drafts: list[IssueDraft] = []

    pages = load_pages(pdf_bytes)
    doc_id = document_key(pdf_bytes)
    file_mrn, file_pms = parse_filename_ids(file_name)

    identity, identity_drafts = parse_identity(pages[0].header if pages else [], file_name)
    drafts.extend(identity_drafts)
    if not identity.mrn:
        drafts.append(error("unparsed_field",
                            "no MRN in the header band or the filename",
                            field_name="mrn"))
    patient_id = patient_key(identity.mrn or f"UNKNOWN-{doc_id[:12]}")

    encounters: list[Encounter] = []
    vitals: list[Vitals] = []
    diagnoses: list[Diagnosis] = []
    prescriptions: list[Prescription] = []
    medications: list[MedicationSnapshot] = []
    imaging: list[ImagingStudy] = []
    exam_findings: list[ExamFinding] = []
    encounter_ids: dict[date, str] = {}

    groups = split_encounters(pages, date_of_birth=identity.date_of_birth)
    dated = [group for group in groups if group.encounter_date is not None]
    if not dated:
        drafts.append(error("unparsed_field",
                            "no encounter date could be established for any page",
                            field_name="encounter_date"))
    location: str | None = None

    for sequence, group in enumerate(sorted(dated, key=lambda g: g.encounter_date), start=1):
        encounter_date = group.encounter_date
        body_blocks = [b for page in group.pages for b in page.body]
        sidebar_blocks = [b for page in group.pages for b in page.sidebar]
        sections = find_sections(body_blocks)
        sidebar_sections = find_sections(sidebar_blocks)
        body_text = text_of(body_blocks)
        header_text = text_of(group.pages[0].header)

        provider_name, provider_role, is_primary = _provider_of(header_text, body_text)
        if not provider_name:
            drafts.append(warn("unparsed_field", "no provider named on this encounter",
                               field_name="provider_name",
                               encounter_date=encounter_date))
        encounter_id = encounter_key(patient_id, encounter_date, provider_name)
        encounter_ids[encounter_date] = encounter_id
        location = location or _location_of(group.pages, sidebar_sections)

        follow_up_days, follow_up_raw = parse_follow_up(
            section_text(sections, "plan") or section_text(sections, "assessment") or body_text
        )
        signed_by, signed_at = _signature_of(body_text)

        for name in ("chief_complaint", "assessment", "plan", "imaging"):
            if not sections.get(name):
                drafts.append(warn("missing_section", f"no {name} section on this encounter",
                                   field_name=name, encounter_date=encounter_date))

        prose = EMPTY_PROSE_FACTS
        if llm_client is not None:
            prose, prose_drafts = classify_encounter(
                section_text(sections, "chief_complaint"),
                section_text(sections, "hpi"),
                f'{section_text(sections, "plan")} {section_text(sections, "note")}'.strip(),
                cfg,
                client=llm_client,
            )
            drafts.extend(replace(d, encounter_date=encounter_date) for d in prose_drafts)

        try:
            encounters.append(Encounter(
                encounter_id=encounter_id,
                patient_id=patient_id,
                encounter_date=encounter_date,
                encounter_seq=sequence,
                provider_name=provider_name,
                provider_role=provider_role,
                is_primary_provider=is_primary,
                location_name=location,
                chief_complaint_raw=_clean_prose(section_text(sections, "chief_complaint")),
                body_region=prose.body_region,
                laterality=prose.laterality,
                visit_type=prose.visit_type,
                hpi_summary=prose.hpi_summary,
                llm_model=prose.model,
                llm_confidence=prose.confidence,
                hpi_text=_clean_prose(section_text(sections, "hpi")),
                note_text=_clean_prose(
                    section_text(sections, "note") or section_text(sections, "plan")
                ),
                follow_up_interval_days=follow_up_days,
                follow_up_raw=follow_up_raw,
                signed_by=signed_by,
                signed_at=signed_at,
                source_document_id=doc_id,
                source_page_start=group.page_start,
                source_page_end=group.page_end,
            ))
        except ValidationError as exc:
            drafts.append(error("validation_failed", str(exc), field_name="encounter",
                                encounter_date=encounter_date))
            continue

        def keep(model_cls, label: str, target: list, payload: dict,
                 _when: date = encounter_date) -> None:
            """Validate one child row; a failure costs that row and nothing else."""
            try:
                target.append(model_cls(**payload))
            except ValidationError as exc:
                drafts.append(error("validation_failed", f"{label}: {exc}",
                                    field_name=label, encounter_date=_when))

        common = {"encounter_id": encounter_id, "patient_id": patient_id,
                  "source_document_id": doc_id}

        vital_fact = parse_vitals(sections.get("vitals", []))
        if vital_fact is None:
            drafts.append(warn("missing_section", "no vitals recorded on this encounter",
                               field_name="vitals", encounter_date=encounter_date))
        else:
            keep(Vitals, "vitals", vitals, {
                **common,
                "taken_by": vital_fact.taken_by,
                "taken_date": vital_fact.taken_date or encounter_date,
                "bp_systolic": vital_fact.bp_systolic,
                "bp_diastolic": vital_fact.bp_diastolic,
                "pulse": vital_fact.pulse, "respirations": vital_fact.respirations,
                "o2_sat": vital_fact.o2_sat, "temperature_f": vital_fact.temperature_f,
                "height_in": vital_fact.height_in, "weight_lbs": vital_fact.weight_lbs,
                "bmi": vital_fact.bmi, "bsa": vital_fact.bsa,
                "is_patient_reported": vital_fact.is_patient_reported,
                "source_page": vital_fact.source_page,
            })

        for fact in parse_diagnoses(sections.get("assessment", [])):
            keep(Diagnosis, "diagnosis", diagnoses, {
                **common,
                "diagnosis_id": diagnosis_key(encounter_id, fact.icd10_code,
                                              fact.diagnosis_text),
                "icd10_code": fact.icd10_code,
                "icd10_description": fact.icd10_description,
                "diagnosis_text": fact.diagnosis_text, "is_primary": fact.is_primary,
                "body_region": fact.body_region, "laterality": fact.laterality,
                "source": fact.source, "source_page": fact.source_page,
            })

        plan_text = section_text(sections, "plan")
        parsed_prescriptions = parse_prescriptions(
            section_blocks(sections, "prescriptions", "plan")
        )
        if not parsed_prescriptions and RX_ACTION_RE.search(plan_text):
            # The plan records a prescribing action but prints no drug, dose or
            # quantity to go with it. Inferring those from a previous visit
            # would put invented dosing in the warehouse, so the gap is
            # recorded instead and stays queryable (§6.4).
            drafts.append(warn(
                "unparsed_field",
                "the plan records a prescribing action but prints no drug, sig, "
                "quantity or refill count; no prescription row was created",
                field_name="prescriptions", encounter_date=encounter_date,
            ))
        for fact in parsed_prescriptions:
            keep(Prescription, "prescription", prescriptions, {
                **common,
                "prescription_id": prescription_key(encounter_id, fact.drug_name,
                                                    fact.strength, fact.sig_text),
                "drug_name": fact.drug_name, "strength": fact.strength,
                "strength_unit": fact.strength_unit, "dose_form": fact.dose_form,
                "route": fact.route, "sig_text": fact.sig_text,
                "quantity": fact.quantity, "quantity_unit": fact.quantity_unit,
                "refills": fact.refills, "duration_days": fact.duration_days,
                "is_prn": fact.is_prn, "action": fact.action,
                "source_page": fact.source_page,
            })

        for fact in parse_medications(sidebar_blocks):
            keep(MedicationSnapshot, "medication", medications, {
                **common,
                "medication_name": fact.medication_name, "route": fact.route,
                "source_page": fact.source_page,
            })

        for fact in parse_imaging(sections.get("imaging", []), encounter_date):
            keep(ImagingStudy, "imaging", imaging, {
                **common,
                "imaging_id": imaging_key(encounter_id, fact.modality, fact.body_part,
                                          fact.performed_date),
                "modality": fact.modality, "body_part": fact.body_part,
                "laterality": fact.laterality, "performed_date": fact.performed_date,
                "interpretation_text": fact.interpretation_text,
                "impression": fact.impression, "source_page": fact.source_page,
            })

        for ordinal, fact in enumerate(parse_exam_findings(sections.get("exam", []))):
            keep(ExamFinding, "exam_finding", exam_findings, {
                **common,
                "finding_id": exam_finding_key(encounter_id, fact.body_part,
                                               fact.finding_type, fact.measure_name,
                                               ordinal),
                "body_part": fact.body_part, "laterality": fact.laterality,
                "finding_type": fact.finding_type, "measure_name": fact.measure_name,
                "value_numeric": fact.value_numeric, "value_text": fact.value_text,
                "unit": fact.unit, "source_page": fact.source_page,
            })

    seen = sorted(e.encounter_date for e in encounters)
    patient = Patient(
        patient_id=patient_id,
        mrn=identity.mrn or f"UNKNOWN-{doc_id[:12]}",
        pms_id=identity.pms_id,
        legal_name=identity.legal_name,
        family_name=identity.family_name,
        given_name=identity.given_name,
        preferred_name=identity.preferred_name,
        date_of_birth=identity.date_of_birth,
        sex=identity.sex,
        phone_home=identity.phone_home,
        first_seen_date=seen[0] if seen else None,
        last_seen_date=seen[-1] if seen else None,
        source_document_id=doc_id,
    )

    issues = [
        IngestionIssue(
            issue_id=issue_key(doc_id, ordinal, draft.issue_type, draft.field_name),
            document_id=doc_id,
            encounter_id=encounter_ids.get(draft.encounter_date),
            severity=draft.severity, issue_type=draft.issue_type,
            field_name=draft.field_name, detail=draft.detail, ingest_run_id=run_id,
        )
        for ordinal, draft in enumerate(drafts)
    ]

    if not encounters:
        parse_status = "failed"
    elif any(issue.severity == "error" for issue in issues):
        parse_status = "partial"
    else:
        parse_status = "ok"

    document = Document(
        document_id=doc_id,
        gcs_uri=gcs_uri or f"file://{file_name}",
        file_name=file_name,
        file_bytes=len(pdf_bytes),
        page_count=len(pages),
        mrn_from_filename=file_mrn,
        pms_id_from_filename=file_pms,
        ingest_run_id=run_id,
        pipeline_version=cfg.pipeline_version,
        parse_status=parse_status,
    )

    return ExtractedDocument(
        document=document, patient=patient, encounters=encounters, vitals=vitals,
        diagnoses=diagnoses, prescriptions=prescriptions, medications=medications,
        imaging=imaging, exam_findings=exam_findings, issues=issues,
    )
