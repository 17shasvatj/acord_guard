"""ACORD Guard — form-shaped exhibit renderer.

Page 1: the ACORD 1 Property Loss Notice *layout* (boxed grid, standard field
positions) so exhibits sit visually 1:1 beside GailGPT's output. Clearly
labeled a reproduction. Page 2: the provenance addendum — the audit trail the
other side doesn't have (field -> source -> quote -> status, plus validations).

Official-form path: if data/acord1_official.pdf exists (a fillable AcroForm
ACORD 1, which requires an ACORD license to obtain) and data/acord1_field_map.json
maps our field names to its AcroForm names, page 1 is the OFFICIAL form filled
via the same pypdf mechanism Gail's own COI skill uses (get_fields ->
update_page_form_field_values -> NeedAppearances). Run
`python form_render.py --inspect <pdf>` to dump a form's field names when
building the map. Until then, the reproduction path keeps everything honest
and self-contained.

Decision treatment on the form itself:
  READY_TO_SUBMIT -> green VALIDATED strip; signature line left blank
                     (a system must never assert a signature — see exhibits).
  HOLD_FOR_INFO   -> amber strip listing what to ask the caller.
  BLOCKED         -> red DO-NOT-SUBMIT banner with the failed rule(s).
"""
from __future__ import annotations
import io, json, sys, textwrap
from pathlib import Path

# --- py3.8 compat: newer reportlab calls hashlib.md5(usedforsecurity=False),
# a keyword that exists only on 3.9+. Strip it on older interpreters.
if sys.version_info < (3, 9):
    import hashlib
    _md5 = hashlib.md5
    def _md5_compat(*a, **kw):
        kw.pop("usedforsecurity", None)
        return _md5(*a, **kw)
    hashlib.md5 = _md5_compat
    # If reportlab was imported before this shim (import-order drift), its
    # pdfdoc module already bound the real md5 into its namespace — rebind it.
    _pd = sys.modules.get("reportlab.pdfbase.pdfdoc")
    if _pd is not None:
        _pd.md5 = _md5_compat
# ---------------------------------------------------------------------------

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

if sys.version_info < (3, 9):     # post-import rebind: pdfdoc holds its own md5 ref
    import hashlib as _hl
    import reportlab.pdfbase.pdfdoc as _pdfdoc
    _pdfdoc.md5 = _hl.md5

DATA = Path(__file__).parent / "data"
W, H = letter
NAVY = colors.HexColor("#1F3B5B")
RED = colors.HexColor("#B3261E")
AMBER = colors.HexColor("#8A6D00")
GREEN = colors.HexColor("#1E6B3A")
GREY = colors.HexColor("#666666")

MARKER = {"VERIFIED": "\u2020", "VERIFIED_DERIVED": "\u2021", "CONFIG": "\u00a7"}


def _value(results, name):
    for r in results:
        if r.name == name and r.value is not None:
            return r
    return None


def _text(results, name, default="NOT CAPTURED"):
    r = _value(results, name)
    return (str(r.value) + " " + MARKER.get(r.status, "")) if r else default


# ------------------------- Page 1: layout reproduction -----------------------

def _box(c, x, y, w, h, label=None):
    c.setStrokeColor(colors.black); c.setLineWidth(0.7)
    c.rect(x, y, w, h)
    if label:
        c.setFont("Helvetica", 5.2); c.setFillColor(GREY)
        c.drawString(x + 2, y + h - 7, label.upper())
        c.setFillColor(colors.black)


def _val(c, x, y, text, size=8, max_chars=None):
    c.setFont("Helvetica", size); c.setFillColor(colors.black)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars - 1] + "\u2026"
    c.drawString(x + 3, y, text)


def _wrap(c, x, y, w_chars, text, size=7.5, leading=9):
    c.setFont("Helvetica", size)
    for i, line in enumerate(textwrap.wrap(text, w_chars)[:8]):
        c.drawString(x, y - i * leading, line)


def render_reproduction_page(results, rules, status) -> bytes:
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=letter)
    top = H - 0.55 * inch

    # Header
    c.setFont("Helvetica-Bold", 13)
    c.drawString(0.55 * inch, top, "PROPERTY LOSS NOTICE")
    c.setFont("Helvetica", 6.5); c.setFillColor(GREY)
    c.drawString(0.55 * inch, top - 9, "ACORD 1 layout reproduction \u2014 prototype \u2014 synthetic test data")
    c.setFillColor(colors.black)
    _box(c, W - 2.0 * inch, top - 14, 1.45 * inch, 24, "date (mm/dd/yyyy)")
    _val(c, W - 2.0 * inch, top - 10, "08/24/2026")

    y = top - 0.45 * inch
    # Producer | Insured row
    _box(c, 0.55 * inch, y - 58, 3.55 * inch, 58, "producer (agency)")
    _val(c, 0.55 * inch, y - 20, _text(results, "agency_name"), 8)
    _val(c, 0.55 * inch, y - 32, _text(results, "agency_address"), 7)
    _val(c, 0.55 * inch, y - 44, _text(results, "agency_phone"), 7)
    _box(c, 4.15 * inch, y - 58, W - 4.7 * inch, 58, "insured name and address")
    _val(c, 4.15 * inch, y - 20, _text(results, "insured_name"), 8)
    _val(c, 4.15 * inch, y - 32, _text(results, "insured_address"), 7)
    _val(c, 4.15 * inch, y - 44, "Phone: " + _text(results, "insured_phone"), 7)

    y -= 58 + 6
    # Carrier / policy row
    _box(c, 0.55 * inch, y - 28, 2.9 * inch, 28, "company (carrier)")
    _val(c, 0.55 * inch, y - 20, _text(results, "carrier"), 7, max_chars=44)
    _box(c, 3.5 * inch, y - 28, 1.5 * inch, 28, "policy number")
    _val(c, 3.5 * inch, y - 20, _text(results, "policy_number"), 8)
    _box(c, 5.05 * inch, y - 28, 1.9 * inch, 28, "policy period (eff - exp)")
    _val(c, 5.05 * inch, y - 20, _text(results, "policy_term"), 7.5)
    _box(c, 7.0 * inch, y - 28, W - 7.55 * inch, 28, "type")
    c.rect(7.06 * inch, y - 22, 7, 7)  # HOMEOWNERS checkbox
    c.setFont("Helvetica-Bold", 8); c.drawString(7.08 * inch, y - 20.5, "X")
    c.setFont("Helvetica", 6); c.drawString(7.22 * inch, y - 20, "HOMEOWNERS (HO-3)")

    y -= 28 + 6
    # Loss row
    _box(c, 0.55 * inch, y - 28, 1.7 * inch, 28, "date of loss")
    _val(c, 0.55 * inch, y - 20, _text(results, "date_of_loss"), 8)
    _box(c, 2.3 * inch, y - 28, 1.4 * inch, 28, "time of loss")
    _val(c, 2.3 * inch, y - 20, _text(results, "time_of_loss"), 7.5)
    _box(c, 3.75 * inch, y - 28, 1.6 * inch, 28, "type of loss / peril")
    _val(c, 3.75 * inch, y - 20, _text(results, "peril"), 8)
    _box(c, 5.4 * inch, y - 28, W - 5.95 * inch, 28, "deductible (applicable)")
    _val(c, 5.4 * inch, y - 20, _text(results, "deductible"), 7.5)

    y -= 28 + 6
    _box(c, 0.55 * inch, y - 24, W - 1.1 * inch, 24, "location of loss")
    _val(c, 0.55 * inch, y - 16, _text(results, "insured_address"), 8)

    y -= 24 + 6
    _box(c, 0.55 * inch, y - 86, W - 1.1 * inch, 86, "description of loss & damage")
    _wrap(c, 0.62 * inch, y - 20, 118, _text(results, "loss_description", "NOT CAPTURED"))

    y -= 86 + 6
    _box(c, 0.55 * inch, y - 40, W - 1.1 * inch, 40, "remarks / prior losses / coverage a")
    _val(c, 0.55 * inch, y - 18, "Coverage A: " + _text(results, "coverage_a"), 7.5)
    _val(c, 0.55 * inch, y - 30, "Prior claims: " + _text(results, "prior_claims", "none on file"), 7)

    y -= 40 + 10
    # Decision strip + signature block (never asserted)
    color, label = {"READY_TO_SUBMIT": (GREEN, "VALIDATED \u2014 READY TO SUBMIT"),
                    "HOLD_FOR_INFO": (AMBER, "HELD \u2014 REQUIRED INFORMATION MISSING"),
                    "BLOCKED": (RED, "BLOCKED \u2014 DO NOT SUBMIT")}[status]
    c.setFillColor(color); c.rect(0.55 * inch, y - 22, W - 1.1 * inch, 22, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 10)
    c.drawString(0.65 * inch, y - 16, label)
    fails = [r for r in rules if not r.passed and r.severity == "BLOCK"]
    if fails:
        c.setFont("Helvetica", 7.5)
        c.drawString(3.4 * inch, y - 15.5, fails[0].detail[:88])
    c.setFillColor(colors.black)
    y -= 34
    _box(c, 0.55 * inch, y - 26, 4.4 * inch, 26, "reported by (signature)")
    _val(c, 0.55 * inch, y - 18, "", 8)  # deliberately blank — never asserted
    _box(c, 5.0 * inch, y - 26, W - 5.55 * inch, 26, "reported to carrier (date)")
    _val(c, 5.0 * inch, y - 18, "\u2014 pending" if status == "READY_TO_SUBMIT" else "\u2014 withheld", 7.5)

    c.setFont("Helvetica", 5.8); c.setFillColor(GREY)
    c.drawString(0.55 * inch, 0.42 * inch,
                 "\u2020 verified quote  \u2021 derived from verified quote  \u00a7 agency configuration \u2014 "
                 "full provenance and validation record: Addendum (page 2). "
                 "Every field traces to a source or is marked NOT CAPTURED; nothing is guessed.")
    c.showPage(); c.save()
    return buf.getvalue()


# ------------------------- Official-form fill (skill mechanism) --------------

def fill_official_page(results, official_pdf: Path, field_map: dict) -> bytes:
    """Fill the licensed ACORD 1 AcroForm using the same pypdf mechanism as
    Gail's COI skill: clone -> update_page_form_field_values -> NeedAppearances."""
    reader = PdfReader(str(official_pdf))
    writer = PdfWriter()
    writer.append(reader)
    fields = {pdf_field: (_value(results, ours).value if _value(results, ours) else "")
              for ours, pdf_field in field_map.items()}
    for page in writer.pages:
        writer.update_page_form_field_values(page, fields, auto_regenerate=False)
    try:  # NeedAppearances so viewers render the values (same as the skill)
        writer.set_need_appearances_writer(True)
    except AttributeError:
        pass
    buf = io.BytesIO(); writer.write(buf)
    return buf.getvalue()


# ------------------------- Page 2: provenance addendum -----------------------

def render_addendum(results, rules, status) -> bytes:
    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    small = ParagraphStyle("s", parent=styles["Normal"], fontSize=7, leading=9)
    h = ParagraphStyle("h", parent=styles["Title"], fontName="Helvetica-Bold",
                       fontSize=12, textColor=NAVY, spaceAfter=4)
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.5 * inch, bottomMargin=0.5 * inch)
    story = [Paragraph(f"ADDENDUM \u2014 FIELD PROVENANCE &amp; VALIDATION (decision: {status})", h)]
    rows = [["Field", "Value", "Source", "Status", "Supporting quote"]]
    for r in results:
        rows.append([r.name, str(r.value) if r.value is not None else "\u2014",
                     r.source or "\u2014", r.status,
                     Paragraph((r.span or r.reason or "")[:160], small)])
    t = Table(rows, colWidths=[1.05 * inch, 1.5 * inch, 0.55 * inch, 1.35 * inch, 2.8 * inch])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 6.5),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 6.5),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EEF1F5")]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [t, Spacer(1, 8), Paragraph("Validation results", h)]
    for ru in rules:
        mark = "PASS" if ru.passed else f"{ru.severity} FAILED"
        story.append(Paragraph(f"<b>[{mark}]</b> {ru.name} \u2014 {ru.detail}", small))
    doc.build(story)
    return buf.getvalue()


# ------------------------- Entry point ---------------------------------------

def render(results, rules, status, path):
    official = DATA / "acord1_official.pdf"
    fmap = DATA / "acord1_field_map.json"
    if official.exists() and fmap.exists():
        page1 = fill_official_page(results, official, json.loads(fmap.read_text()))
    else:
        page1 = render_reproduction_page(results, rules, status)
    out = PdfWriter()
    for blob in (page1, render_addendum(results, rules, status)):
        out.append(PdfReader(io.BytesIO(blob)))
    with open(path, "wb") as f:
        out.write(f)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--inspect":
        flds = PdfReader(sys.argv[2]).get_fields() or {}
        print(json.dumps({k: str(v.get("/FT")) for k, v in flds.items()}, indent=1)
              if flds else "No AcroForm fields (flat PDF \u2014 not fillable).")
    else:
        print(__doc__)