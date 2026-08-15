"""
Generates the pet owner dashboard's downloadable "Medical Record" PDF (see
GET /api/v1/triage/medical-record in app/routers/triage.py) — every triage
case on file for one owner_id, in one printable document.

fpdf2's core fonts (Helvetica etc.) only support Latin-1, and any of this
content can be free-text a user typed (pet_owner_description, vet_response,
owner/pet names) — _safe_text guards against a request 500ing if that text
happens to contain characters outside Latin-1, by replacing them rather
than embedding a full Unicode font just for this. English-only by design:
proper Hebrew rendering needs RTL shaping/bidi support fpdf2 doesn't
provide out of the box, which is out of scope here — see the "Medical
Record / תיק רפואי" button label in web/dashboard.html for where Hebrew
does appear (UI label only, not the generated document content).
"""
from datetime import datetime, timezone

from fpdf import FPDF

from app.models.triage import CaseSummary


def _safe_text(text: str | None) -> str:
    if not text:
        return ""
    return text.encode("latin-1", errors="replace").decode("latin-1")


def build_medical_record_pdf(
    cases: list[CaseSummary], owner_name: str | None, pet_name: str | None
) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "Pet Medical Record", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font("Helvetica", "", 11)
    pdf.cell(
        0, 7, f"Owner: {_safe_text(owner_name) or '-'}    Pet: {_safe_text(pet_name) or '-'}",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(110, 110, 110)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pdf.cell(
        0, 6, f"Generated {generated_at}  |  {len(cases)} case(s) on file",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(6)

    if not cases:
        pdf.set_font("Helvetica", "I", 11)
        pdf.cell(0, 8, "No triage cases on file yet.", new_x="LMARGIN", new_y="NEXT")

    for case in cases:
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_fill_color(245, 245, 248)
        header = f"Case {case.triage_id[:8]}  -  {_safe_text(case.updated_at) or 'date unknown'}"
        pdf.cell(0, 9, header, fill=True, new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(
            0, 6, f"Status: {_safe_text(case.status)}    Priority: {_safe_text(case.priority.value)}",
            new_x="LMARGIN", new_y="NEXT",
        )
        pdf.ln(1)

        pdf.set_font("Helvetica", "", 10)
        if case.pet_owner_description:
            pdf.multi_cell(0, 6, f"Reported symptoms: {_safe_text(case.pet_owner_description)}", new_x="LMARGIN", new_y="NEXT")
        if case.summary:
            pdf.multi_cell(0, 6, f"AI assessment: {_safe_text(case.summary)}", new_x="LMARGIN", new_y="NEXT")
        if case.risk_factors:
            pdf.multi_cell(0, 6, "Risk factors: " + _safe_text("; ".join(case.risk_factors)), new_x="LMARGIN", new_y="NEXT")
        if case.next_steps:
            pdf.multi_cell(0, 6, "Recommended next steps: " + _safe_text("; ".join(case.next_steps)), new_x="LMARGIN", new_y="NEXT")
        if case.vet_response:
            pdf.set_font("Helvetica", "B", 10)
            pdf.multi_cell(0, 6, f"Veterinary response ({_safe_text(case.status)}):", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 6, _safe_text(case.vet_response), new_x="LMARGIN", new_y="NEXT")

        pdf.ln(2)
        pdf.set_draw_color(220, 220, 224)
        y = pdf.get_y()
        pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.ln(6)

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(140, 140, 140)
    pdf.multi_cell(
        0,
        5,
        "AI-generated triage suggestions in this record must be confirmed by licensed veterinary "
        "staff before any clinical action. This document is generated for record-keeping purposes only.",
        new_x="LMARGIN",
        new_y="NEXT",
    )

    return bytes(pdf.output())
