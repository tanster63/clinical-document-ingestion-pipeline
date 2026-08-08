from pathlib import Path

import pytest

from corpus.spec_model import load_spec

SPEC_DIR = Path("corpus/specs")
SPECS = sorted(SPEC_DIR.glob("chart_*.json"))


def specs():
    return [load_spec(p) for p in SPECS]


def test_seven_authored_charts_exist():
    assert len(SPECS) == 7


@pytest.mark.parametrize("path", SPECS, ids=lambda p: p.name)
def test_spec_parses(path):
    load_spec(path)


def test_no_shared_mrns_and_none_collide_with_the_sample():
    mrns = [s.patient.mrn for s in specs()]
    assert len(set(mrns)) == len(mrns)
    assert "4820917" not in mrns  # the provided chart's MRN


def test_seven_distinct_body_regions_none_of_them_shoulder():
    regions = {e.body_region for s in specs() for e in s.encounters}
    assert len(regions) == 7
    assert "shoulder" not in regions  # the provided chart covers shoulder


def test_visit_count_distribution():
    counts = sorted(len(s.encounters) for s in specs())
    assert sum(c >= 3 for c in counts) >= 2
    assert sum(c == 1 for c in counts) >= 3
    assert sum(counts) == 13  # 13 authored + 2 in the provided chart = 15


def test_one_chart_carries_an_operative_note():
    assert sum(any(e.operative_note for e in s.encounters) for s in specs()) >= 1


def test_at_least_two_providers_across_the_corpus():
    providers = {e.provider_name for s in specs() for e in s.encounters}
    assert len(providers) >= 3


def test_two_deliberate_imperfections_are_present():
    missing_vitals = [s.chart_id for s in specs()
                      if any(e.vitals is None for e in s.encounters)]
    missing_imaging = [s.chart_id for s in specs()
                       if all(not e.imaging for e in s.encounters)]
    missing_phone = [s.chart_id for s in specs() if not s.patient.phone_home]
    assert missing_vitals and missing_imaging and missing_phone


def test_every_prescribed_drug_is_in_the_seeded_drug_class_table():
    seeded = Path("sql/ddl/seed_ref_drug_class.sql").read_text().lower()
    for spec in specs():
        for enc in spec.encounters:
            for rx in enc.prescriptions:
                assert f"'{rx.drug_name.lower()}'" in seeded, rx.drug_name


def test_anti_inflammatory_and_same_day_imaging_case_exists():
    """The brief's multi-table question must have a non-empty answer."""
    nsaids = {"meloxicam", "ibuprofen", "naproxen", "diclofenac", "celecoxib"}
    hits = [
        (s.chart_id, e.encounter_date)
        for s in specs() for e in s.encounters
        if any(rx.drug_name.lower() in nsaids for rx in e.prescriptions)
        and any(im.performed_date == e.encounter_date for im in e.imaging)
    ]
    assert len(hits) >= 2
