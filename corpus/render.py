"""Render a chart spec to a PDF: one WeasyPrint document per encounter, merged
with PyMuPDF. Per-encounter documents are what reset the page counter, matching
the provided chart's behaviour.

The template reproduces the source EMR's export — its section labels, their
order, the left rail of longitudinal context, the bordered vitals table with
its merged BMI/BSA cell, and the two-column exam — so an authored chart puts
the same problem in front of the parser that the provided chart does.
"""

import argparse
from pathlib import Path

import fitz
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from corpus.exam import build_exam
from corpus.spec_model import ChartSpec, EncounterSpec, load_spec

TEMPLATE_DIR = Path(__file__).parent / "templates"

# How a body region is spelled as an exam heading in the chart.
EXAM_REGION = {
    "shoulder": "Shoulder", "knee": "Knee", "hip": "Hip", "elbow": "Elbow",
    "wrist": "Wrist", "hand": "Wrist", "hand/wrist": "Wrist",
    "ankle": "Ankle", "foot": "Ankle", "foot/ankle": "Ankle",
    "lumbar spine": "Lumbar Spine", "cervical spine": "Cervical Spine",
}
SEX_WORD = {"m": "male", "male": "male", "f": "female", "female": "female"}


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )


def html_to_pdf(html: str) -> bytes:
    return HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf()


def _age_on(born, when) -> int:
    return when.year - born.year - ((when.month, when.day) < (born.month, born.day))


def _hpi_lead(spec: ChartSpec, enc: EncounterSpec) -> str:
    """The EMR's stock HPI opener, e.g. "This is a 33 year old male who:"."""
    age = _age_on(spec.patient.date_of_birth, enc.encounter_date)
    sex = SEX_WORD.get(spec.patient.sex.strip().lower(), "patient")
    return f"This is a {age} year old {sex} who:"


def _address_lines(spec: ChartSpec) -> tuple[str, str]:
    parts = [part.strip() for part in spec.location_address.split(",", 1)]
    return (parts[0], parts[1] if len(parts) > 1 else "")


def render_encounter(spec: ChartSpec, enc: EncounterSpec) -> bytes:
    template = _env().get_template("chart.html.j2")
    css = (TEMPLATE_DIR / "chart.css").read_text()
    region = enc.exam_region or EXAM_REGION.get((enc.body_region or "").lower())
    exam = build_exam(region, enc.laterality, enc.exam_findings) if region else None
    line1, line2 = _address_lines(spec)
    return html_to_pdf(template.render(
        chart=spec, enc=enc, exam=exam, css=css,
        hpi_lead=_hpi_lead(spec, enc),
        location_address_line1=line1, location_address_line2=line2,
    ))


def merge_pdfs(parts: list[bytes]) -> bytes:
    out = fitz.open()
    for part in parts:
        with fitz.open(stream=part, filetype="pdf") as doc:
            out.insert_pdf(doc)
    # no_new_id: PyMuPDF otherwise stamps a fresh random trailer /ID on every
    # save, so re-rendering an unchanged spec produces a spurious binary diff.
    data = out.tobytes(no_new_id=True)
    out.close()
    return data


def render_chart(spec: ChartSpec, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = merge_pdfs([render_encounter(spec, enc) for enc in spec.encounters])
    target = out_dir / spec.file_name
    target.write_bytes(pdf)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Render chart specs to PDFs.")
    parser.add_argument("specs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=Path("charts/generated"))
    args = parser.parse_args()
    for spec_path in args.specs:
        target = render_chart(load_spec(spec_path), args.out)
        print(f"{spec_path.name} -> {target}")


if __name__ == "__main__":
    main()
