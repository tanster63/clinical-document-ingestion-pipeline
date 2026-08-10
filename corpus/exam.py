"""Templated physical-exam content, in the shape the source EMR emits.

The provided chart's exam is boilerplate with a handful of abnormal findings
dropped into it: every joint gets the same list of movements, every normal side
gets the same sentence, and only what the clinician actually found differs.
Reproducing that is more faithful than authoring thirteen bespoke exams, and it
is what makes the authored charts exercise the same two-column parsing path the
sample does.

A chart spec supplies only the abnormalities; everything else is filled in from
the region's own template.
"""

from dataclasses import dataclass, field

# Movements a joint is examined through, in the order the EMR prints them.
# Axial regions have no side, which is why some charts render a single column.
REGION_MOVEMENTS: dict[str, dict[str, str]] = {
    "Shoulder": {
        "Forward Flexion": "180 degrees", "Extension": "50 degrees",
        "Abduction": "180 degrees", "Adduction": "40 degrees",
        "External Rotation": "45 degrees",
        "External Rotation in Abduction": "120 degrees",
        "Internal Rotation": "T4 - T8",
        "Internal Rotation in Abduction": "60 degrees",
    },
    "Knee": {
        "Flexion": "135 degrees", "Extension": "0 degrees",
        "Internal Rotation": "10 degrees", "External Rotation": "10 degrees",
    },
    "Hip": {
        "Flexion": "120 degrees", "Extension": "20 degrees",
        "Abduction": "45 degrees", "Adduction": "30 degrees",
        "Internal Rotation": "35 degrees", "External Rotation": "45 degrees",
    },
    "Elbow": {
        "Flexion": "145 degrees", "Extension": "0 degrees",
        "Pronation": "80 degrees", "Supination": "80 degrees",
    },
    "Wrist": {
        "Flexion": "70 degrees", "Extension": "70 degrees",
        "Radial Deviation": "20 degrees", "Ulnar Deviation": "30 degrees",
    },
    "Ankle": {
        "Dorsiflexion": "20 degrees", "Plantarflexion": "45 degrees",
        "Inversion": "35 degrees", "Eversion": "15 degrees",
    },
    "Lumbar Spine": {
        "Flexion": "60 degrees", "Extension": "25 degrees",
        "Right Lateral Bending": "25 degrees", "Left Lateral Bending": "25 degrees",
    },
    "Cervical Spine": {
        "Flexion": "50 degrees", "Extension": "60 degrees",
        "Right Rotation": "80 degrees", "Left Rotation": "80 degrees",
    },
}

# Movements graded for strength, a subset of the above.
STRENGTH_MOVEMENTS: dict[str, tuple[str, ...]] = {
    "Shoulder": ("Forward Flexion", "External Rotation", "Internal Rotation"),
    "Knee": ("Flexion", "Extension"),
    "Hip": ("Flexion", "Abduction"),
    "Elbow": ("Flexion", "Extension"),
    "Wrist": ("Extension", "Flexion"),
    "Ankle": ("Dorsiflexion", "Plantarflexion"),
    "Lumbar Spine": ("Flexion", "Extension"),
    "Cervical Spine": ("Flexion", "Extension"),
}

AXIAL_REGIONS = ("Lumbar Spine", "Cervical Spine", "Thoracic Spine")

NORMAL_SKIN = "skin intact, no rashes or lesions."
NORMAL_INSPECTION = (
    "Normal alignment, no deformity, no tenderness, no warmth, no masses, "
    "no muscle atrophy, no crepitus"
)
NORMAL_STABILITY = "Stable"
NORMAL_SPECIAL = "no provocative findings"
NORMAL_STRENGTH = "Strength: 5/5, normal muscle tone."

GENERAL_UPPER = [
    "Comprehensive, Upper Extremity Neurovascular",
    "Appearance: well nourished",
    "Orientation: Alert and oriented to person, place, time.",
    "Mood: mood and affect well-adjusted, pleasant and cooperative,",
]
GENERAL_LOWER = [
    "Comprehensive, Lower Extremity Neurovascular",
    "Appearance: well nourished",
    "Orientation: Alert and oriented to person, place, time.",
    "Mood: mood and affect well-adjusted, pleasant and cooperative,",
]
UPPER_REGIONS = ("Shoulder", "Elbow", "Wrist", "Hand", "Cervical Spine")


@dataclass
class ExamBlock:
    """One labelled block of the exam, rendered as one or two columns."""

    label: str
    columns: list[tuple[str, list[str]]] = field(default_factory=list)


@dataclass
class RenderedExam:
    general: list[str]
    neurovascular: list[tuple[str, str]]
    region: str
    blocks: list[ExamBlock]


def _sides(region: str, laterality: str | None) -> list[str]:
    if region in AXIAL_REGIONS:
        return [""]
    if laterality in ("right", "left"):
        return ["Right", "Left"]
    return ["Right", "Left"]


def _prefix(side: str, region: str) -> str:
    return f"{side} {region}".strip()


def build_exam(region: str, laterality: str | None, findings: dict | None = None) -> RenderedExam:
    """Fill the region's exam template, overriding only what the chart found.

    `findings` is keyed by block then by side, e.g.
    ``{"rom_active": {"Right": {"Flexion": "115 degrees"}},
       "inspection": {"Right": "medial joint line tenderness"}}``
    """
    findings = findings or {}
    movements = REGION_MOVEMENTS.get(region, REGION_MOVEMENTS["Knee"])
    sides = _sides(region, laterality)
    upper = region in UPPER_REGIONS
    limb = "UE" if upper else "LE"

    neurovascular = []
    for side in ("Right", "Left"):
        neurovascular.append((
            f"{side} {limb} Pulses:",
            "normal radial pulse and good capillary refill" if upper
            else "normal dorsalis pedis pulse and good capillary refill",
        ))
    for side in ("R", "L"):
        neurovascular.append((
            f"{side}{limb} Peripheral Sensation:",
            "intact to light touch throughout peripheral nerve distributions",
        ))

    def override(block: str, side: str, default):
        return (findings.get(block) or {}).get(side or "Right", default)

    blocks: list[ExamBlock] = []

    for block_name, label in (("rom_active", "Active ROM"), ("rom_passive", "Passive ROM")):
        columns = []
        for side in sides:
            values = dict(movements)
            values.update(override(block_name, side, {}) or {})
            columns.append((
                f"{_prefix(side, region)} {label}:",
                [f"{name}: {value}." for name, value in values.items()],
            ))
        blocks.append(ExamBlock(label=label, columns=columns))

    skin = ExamBlock(label="Skin:", columns=[
        (None, [f"{_prefix(side, region)}: {override('skin', side, NORMAL_SKIN)}"])
        for side in sides
    ])
    blocks.append(skin)

    inspection = ExamBlock(label="Inspection:", columns=[
        (None, [f"{_prefix(side, region)}: {override('inspection', side, NORMAL_INSPECTION)}"])
        for side in sides
    ])
    blocks.append(inspection)

    strength_columns = []
    for side in sides:
        lines = []
        for movement in STRENGTH_MOVEMENTS.get(region, ("Flexion", "Extension")):
            graded = override("strength", side, {}) or {}
            lines.append(
                f"{_prefix(side, region)} {movement}: {graded.get(movement, NORMAL_STRENGTH)}"
            )
        strength_columns.append((None, lines))
    blocks.append(ExamBlock(label="Strength:", columns=strength_columns))

    # Stability and special tests are printed for the affected side only, which
    # is what the provided chart does.
    affected = (laterality or "right").capitalize() if region not in AXIAL_REGIONS else ""
    stability = override("stability", affected, NORMAL_STABILITY)
    blocks.append(ExamBlock(label="Stability:", columns=[
        (None, [f"{_prefix(affected, region)}: {stability}"])
    ]))
    special = override("special", affected, NORMAL_SPECIAL)
    blocks.append(ExamBlock(label="Special:", columns=[
        (None, [f"{_prefix(affected, region)}: {special}"])
    ]))

    return RenderedExam(
        general=GENERAL_UPPER if upper else GENERAL_LOWER,
        neurovascular=neurovascular,
        region=region,
        blocks=blocks,
    )
