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
    from engine import SCHEMA
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=letter)
    top = H - 0.6*inch
    c.setFont("Helvetica-Bold", 14)
    c.drawString(0.6*inch, top, "CERTIFICATE OF LIABILITY INSURANCE")
    c.setFont("Helvetica", 6.5); c.setFillColor(GREY)
    c.drawString(0.6*inch, top-11, "ACORD 25 layout reproduction — prototype — synthetic test data")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 8); c.drawString(W-2.3*inch, top, "Certificate date: 08/25/2026")

    y = top - 0.5*inch
    labels = {
      "producer_name":"Producer (agency)","producer_address":"Producer address","producer_phone":"Producer phone",
      "insured_name":"Named insured","insured_address":"Insured address","carrier":"Insurer (carrier)",
      "policy_number":"Policy number","policy_term":"Policy period","cert_holder":"Certificate holder",
      "each_occurrence":"Each occurrence","general_aggregate":"General aggregate",
      "products_aggregate":"Products/completed ops","personal_adv_injury":"Personal & adv injury",
      "damage_rented":"Damage to rented premises","med_expense":"Medical expense",
      "additional_insured":"Additional insured","waiver_subrogation":"Waiver of subrogation",
      "producer_code":"Producer code"}
    for name in SCHEMA:
        r = _value(results, name)
        lab = labels.get(name, name)
        c.setStrokeColor(colors.HexColor("#bbbbbb")); c.setLineWidth(0.5)
        c.rect(0.6*inch, y-16, W-1.2*inch, 16)
        c.setFont("Helvetica", 6); c.setFillColor(GREY)
        c.drawString(0.66*inch, y-6, lab.upper())
        c.setFillColor(colors.black); c.setFont("Helvetica", 9)
        if r:
            mark = MARKER.get(r.status, "")
            c.drawString(2.6*inch, y-11, f"{r.value} {mark}")
        elif SCHEMA[name]["required"]:
            c.setFillColor(RED); c.drawString(2.6*inch, y-11, "** REQUIRED — NOT CAPTURED **"); c.setFillColor(colors.black)
        else:
            c.setFillColor(GREY); c.drawString(2.6*inch, y-11, "not captured — left blank"); c.setFillColor(colors.black)
        y -= 17
        if y < 1.6*inch: break

    color,label = {"READY_TO_SUBMIT":(GREEN,"VALIDATED — SAFE TO ISSUE"),
                   "HOLD_FOR_INFO":(AMBER,"HELD — REQUIRED INFORMATION MISSING"),
                   "BLOCKED":(RED,"BLOCKED — DO NOT ISSUE")}[status]
    c.setFillColor(color); c.rect(0.6*inch, y-24, W-1.2*inch, 22, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 11)
    c.drawString(0.7*inch, y-18, label)
    fails=[r for r in rules if not r.passed and r.severity=="BLOCK"]
    if fails:
        c.setFont("Helvetica",7.5); c.drawString(3.6*inch, y-17, fails[0].detail[:80])
    c.setFillColor(GREY); c.setFont("Helvetica",5.8)
    c.drawString(0.6*inch, 0.5*inch,
        "† verified quote  ‡ derived  § agency config — full provenance on page 2. "
        "Nothing is certified that isn't in the policy.")
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
