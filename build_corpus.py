"""Regenerate the COI corpus PDFs from code. Run once after cloning:
    python build_corpus.py
Binary files don't always survive file-by-file copying; this rebuilds them
deterministically so data/ is never a sync liability.
"""
import sys

# --- py3.8 compat: reportlab calls hashlib.md5(usedforsecurity=False), a kwarg
# that only exists on 3.9+. We must patch BOTH hashlib.md5 AND the reference
# reportlab.pdfbase.pdfdoc already bound via `from hashlib import md5`. ---
if sys.version_info < (3, 9):
    import hashlib
    _real_md5 = hashlib.md5
    def _md5_compat(*a, **k):
        k.pop("usedforsecurity", None)
        return _real_md5(*a, **k)
    hashlib.md5 = _md5_compat
    import reportlab.pdfbase.pdfdoc as _pdfdoc   # import so we can rebind its md5
    _pdfdoc.md5 = _md5_compat

from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch

DATA = Path(__file__).parent / "data"
DATA.mkdir(exist_ok=True)

def dec_page(path, term):
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 10.3*inch
    def L(t, size=10, dy=0.26):
        nonlocal y
        c.setFont("Helvetica", size); c.drawString(1*inch, y, t); y -= dy*inch
    c.setFont("Helvetica-Bold", 13); c.drawString(1*inch, y, "THE HARTFORD — BUSINESSOWNERS POLICY"); y-=0.35*inch
    c.setFont("Helvetica-Bold", 10); c.drawString(1*inch, y, "Declarations"); y-=0.3*inch
    L("Named Insured: Paws and Provisions LLC")
    L("Mailing Address: 6469 Applegate Drive, San Jose, CA 95119")
    L("Policy Number: BOP-HTF-1010")
    L("Carrier: The Hartford (Hartford Fire Insurance Company)")
    L(f"Policy Period: {term}")
    L("")
    c.setFont("Helvetica-Bold",10); c.drawString(1*inch,y,"Commercial General Liability — Limits of Liability"); y-=0.28*inch
    L("Each Occurrence: $1,000,000")
    L("General Aggregate: $2,000,000")
    L("Products/Completed Operations Aggregate: $2,000,000")
    L("Personal & Advertising Injury: $1,000,000")
    L("Damage to Rented Premises: $100,000")
    L("Medical Expense (any one person): $5,000")
    L("")
    c.setFont("Helvetica-Bold",10); c.drawString(1*inch,y,"Coverage Notes"); y-=0.28*inch
    L("Property and General Liability coverage only. No cyber coverage on this policy.")
    L("No additional-insured endorsement on file. No waiver of subrogation on file.")
    c.setFont("Helvetica",7); c.drawString(1*inch, 0.7*inch,
        "Synthetic sample created for software testing. Not a real policy.")
    c.save()
    print("wrote", path.name)

dec_page(DATA/"coi_policy_inforce.pdf", "01/22/2026 to 01/22/2027")
dec_page(DATA/"coi_policy_expired.pdf", "01/22/2025 to 01/22/2026")

req = DATA/"coi_request.txt"
if not req.exists():
    req.write_text(
"""CERTIFICATE REQUEST — received by agency
Requested by: Acme General Contractors
Certificate holder: Acme General Contractors, 500 Biscayne Blvd, Miami, FL 33131
Requirement stated by holder: "We need proof of general liability coverage. List Acme as
additional insured and include a waiver of subrogation per our subcontract."
""")
    print("wrote coi_request.txt")
print("corpus ready.")
