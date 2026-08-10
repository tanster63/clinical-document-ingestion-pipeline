from ingestion.extract.fields.prescriptions import parse_duration_days, parse_prescriptions
from ingestion.extract.layout import Block

SAMPLE_SIG = ("Take 1 po qd x 2 weeks then PRN- take with food - "
              "don't take with ibuprofen or naproxen")


def blk(text, y0):
    return Block(text=text, x0=260, y0=y0, x1=560, y1=y0 + 10, page=3)


def test_parse_duration_days_normalizes_units():
    assert parse_duration_days("Take 1 po qd x 2 weeks then PRN") == 14
    assert parse_duration_days("Take 1 po tid x 10 days") == 10
    assert parse_duration_days("Take 1 po bid for 3 weeks with food") == 21
    assert parse_duration_days("Take 1 po qd x 1 month") == 30
    assert parse_duration_days("Take 1 po q6h PRN pain") is None


def test_parse_the_provided_charts_prescription():
    found = parse_prescriptions([
        blk("meloxicam 15 mg tablet PO", 100),
        blk(f"Sig: {SAMPLE_SIG}", 112),
        blk("Quantity: 30 Tablet  Refills: 2", 124),
    ])
    assert len(found) == 1
    rx = found[0]
    assert rx.drug_name == "meloxicam"
    assert rx.strength == "15"
    assert rx.strength_unit == "mg"
    assert rx.dose_form == "tablet"
    assert rx.route == "PO"
    assert rx.sig_text == SAMPLE_SIG
    assert rx.quantity == 30.0
    assert rx.quantity_unit == "Tablet"
    assert rx.refills == 2
    assert rx.duration_days == 14
    assert rx.is_prn is True
    assert rx.action == "new"          # the chart prints no action; "new" is the default


def test_a_prescription_preceded_by_plan_prose_still_parses():
    """In the provided chart the drug line follows "Plan: Prescription." on the
    same flowed text, with no heading of its own."""
    found = parse_prescriptions([
        blk("Prescription. meloxicam 15 mg tablet PO", 100),
        blk(f"Sig: {SAMPLE_SIG}", 112),
        blk("Quantity: 30 Tablet Refills: 2", 124),
    ])
    assert [rx.drug_name for rx in found] == ["meloxicam"]


def test_two_prescriptions_in_one_section_do_not_bleed_together():
    found = parse_prescriptions([
        blk("cyclobenzaprine 10 mg tablet PO", 100),
        blk("Sig: Take 1 po qhs x 2 weeks", 112),
        blk("Quantity: 14 Tablet  Refills: 0  Action: new", 124),
        blk("prednisone 10 mg tablet PO", 140),
        blk("Sig: Take 4 tabs day 1, taper by 1 tab daily", 152),
        blk("Quantity: 21 Tablet  Refills: 0  Action: new", 164),
    ])
    assert [rx.drug_name for rx in found] == ["cyclobenzaprine", "prednisone"]
    assert [rx.quantity for rx in found] == [14.0, 21.0]
    assert found[0].duration_days == 14
    assert found[1].duration_days is None


def test_the_second_prescriptions_sig_does_not_swallow_its_own_tail():
    """Regression: the tail match indexes the whole section, the sig slice
    indexes one segment of it. Mixing the two only goes wrong from the second
    prescription onwards."""
    found = parse_prescriptions([
        blk("ibuprofen 600 mg tablet PO", 100),
        blk("Sig: Take 1 po tid with food", 112),
        blk("Quantity: 30 Tablet Refills: 1 Action: new", 124),
        blk("prednisone 10 mg tablet PO", 140),
        blk("Sig: Take 4 tabs day 1, taper by 1 tab daily", 152),
        blk("Quantity: 21 Tablet Refills: 0 Action: new", 164),
    ])
    assert found[1].sig_text == "Take 4 tabs day 1, taper by 1 tab daily"
    assert "Quantity" not in found[1].sig_text


def test_hyphenated_multiword_drug_name_survives():
    found = parse_prescriptions([
        blk("hydrocodone-acetaminophen 5 mg tablet PO", 100),
        blk("Sig: Take 1 po q6h PRN severe pain", 112),
        blk("Quantity: 12 Tablet  Refills: 0  Action: new", 124),
    ])
    assert found[0].drug_name == "hydrocodone-acetaminophen"
    assert found[0].is_prn is True


def test_modify_action_is_captured():
    found = parse_prescriptions([
        blk("ibuprofen 600 mg tablet PO", 100),
        blk("Sig: Take 1 po tid PRN with food", 112),
        blk("Quantity: 30 Tablet  Refills: 1  Action: modify", 124),
    ])
    assert found[0].action == "modify"


def test_a_plan_that_mentions_prescribing_but_prints_no_drug_yields_nothing():
    """The provided chart's August visit says only "Modify Regimen: Modify
    prescription medication therapy." Inventing dose and quantity from the
    previous visit is exactly what deterministic parsing exists to prevent."""
    assert parse_prescriptions([
        blk("Prescription Medication Management.", 100),
        blk("Modify Regimen: Modify prescription medication therapy.", 112),
    ]) == []


def test_empty_section_returns_empty_list():
    assert parse_prescriptions([]) == []
