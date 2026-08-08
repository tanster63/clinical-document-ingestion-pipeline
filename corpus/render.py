"""Render a chart spec to a PDF: one WeasyPrint document per encounter, merged
with PyMuPDF. Per-encounter documents are what reset the page counter, matching
the provided chart's behaviour."""

import argparse
from pathlib import Path

import fitz
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from corpus.spec_model import ChartSpec, EncounterSpec, load_spec

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )


def html_to_pdf(html: str) -> bytes:
    return HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf()


def render_encounter(spec: ChartSpec, enc: EncounterSpec) -> bytes:
    template = _env().get_template("chart.html.j2")
    css = (TEMPLATE_DIR / "chart.css").read_text()
    return html_to_pdf(template.render(chart=spec, enc=enc, css=css))


def merge_pdfs(parts: list[bytes]) -> bytes:
    out = fitz.open()
    for part in parts:
        with fitz.open(stream=part, filetype="pdf") as doc:
            out.insert_pdf(doc)
    data = out.tobytes()
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
