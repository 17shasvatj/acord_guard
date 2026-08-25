"""ACORD Guard pipeline — run the three demo scenarios.

  python pipeline.py fabrication   # verifier rejecting invented fields
  python pipeline.py expired       # validation blocking a loss outside the policy term
  python pipeline.py clean         # full pass: validated notice + audit manifest

In production, extraction proposals come from an LLM constrained to the
{field, value, source, span} contract (see extract_live.py). For deterministic
demos, proposals are canned in data/proposals_*.json — including, in the
fabrication scenario, the exact classes of invention observed in GailGPT's
output (invented phone, producer code, transplanted zip, unstated time).
"""
import json, sys
from datetime import date
from pathlib import Path
from engine import load_sources, verify_proposals, validate, decide, SCHEMA, FieldResult
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

HERE = Path(__file__).parent
OUT = HERE / "out"; OUT.mkdir(exist_ok=True)
NOTICE_DATE = date(2026, 8, 24)

SCEN = {
    "fabrication": dict(policy="policy_delgado_2026.pdf", proposals="proposals_fabrication.json"),
    "expired":     dict(policy="policy_delgado_expired.pdf", proposals="proposals_expired.json"),
    "clean":       dict(policy="policy_delgado_2026.pdf", proposals="proposals_clean.json"),
}

def render_form(results, rules, status, path):
    styles = getSampleStyleSheet()
    NAVY = colors.HexColor("#1F3B5B")
    h = ParagraphStyle("h", parent=styles["Title"], fontName="Helvetica-Bold",
                       fontSize=13, textColor=NAVY, spaceAfter=2)
    small = ParagraphStyle("s", parent=styles["Normal"], fontSize=8, leading=10)
    doc = SimpleDocTemplate(str(path), pagesize=letter, topMargin=0.6*inch, bottomMargin=0.6*inch)
    st = [Paragraph("PROPERTY LOSS NOTICE — ACORD 1 layout (prototype reproduction)", h),
          Paragraph(f"Status: <b>{status}</b> &nbsp;|&nbsp; Notice date {NOTICE_DATE} &nbsp;|&nbsp; "
                    "Every field below is traceable — see audit manifest", small),
          HRFlowable(width="100%", color=NAVY), Spacer(1, 6)]
    rows = [["Field", "Value", "Provenance"]]
    for r in sorted(results, key=lambda x: list(SCHEMA).index(x.name)):
        if r.value is not None:
            prov = {"VERIFIED": f"verified quote in {r.source}",
                    "VERIFIED_DERIVED": f"derived from verified quote in {r.source}",
                    "CONFIG": "agency configuration",
                    "REQUEST": "user-supplied (recorded)"}[r.status]
            rows.append([r.name, str(r.value), prov])
        elif SCHEMA[r.name]["required"]:
            rows.append([r.name, "** REQUIRED — NOT CAPTURED **", "submission held"])
        else:
            rows.append([r.name, "NOT CAPTURED", "left blank — never guessed"])
    t = Table(rows, colWidths=[1.6*inch, 3.4*inch, 2.0*inch])
    t.setStyle(TableStyle([
        ("FONT", (0,0), (-1,-1), "Helvetica", 8),
        ("FONT", (0,0), (-1,0), "Helvetica-Bold", 8),
        ("BACKGROUND", (0,0), (-1,0), NAVY), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#EEF1F5")]),
        ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#CCCCCC")),
        ("VALIGN", (0,0), (-1,-1), "TOP")]))
    st.append(t); st.append(Spacer(1, 8))
    st.append(Paragraph("Validation results", h))
    for ru in rules:
        mark = "PASS" if ru.passed else f"{ru.severity} FAILED"
        st.append(Paragraph(f"<b>[{mark}]</b> {ru.name} — {ru.detail}", small))
    doc.build(st)

def main(scenario):
    cfg = SCEN[scenario]
    sources = load_sources(cfg["policy"])
    proposals = json.loads((HERE / "data" / cfg["proposals"]).read_text())
    results = verify_proposals(proposals, sources)
    rules = validate(results, NOTICE_DATE, sources["policy"])
    status, missing, rejected, blocks = decide(results, rules)

    print(f"\n=== SCENARIO: {scenario} | policy: {cfg['policy']} ===")
    print(f"DECISION: {status}")
    if rejected:
        print(f"\nProposals REJECTED by span-verifier ({len(rejected)}):")
        for r in rejected:
            print(f"  - {r.name}: {r.reason}")
    if missing:
        print(f"\nRequired fields not captured (gap report -> ask the caller): {missing}")
    if blocks:
        print("\nValidation BLOCKS:")
        for b in blocks: print(f"  - {b.name}: {b.detail}")

    manifest = {
        "scenario": scenario, "notice_date": str(NOTICE_DATE), "decision": status,
        "fields": [vars(r) for r in results], "validations": [vars(ru) for ru in rules]}
    mpath = OUT / f"manifest_{scenario}.json"
    mpath.write_text(json.dumps(manifest, indent=2))
    fpath = OUT / f"loss_notice_{scenario}.pdf"
    render_form(results, rules, status, fpath)
    print(f"\nWrote: {fpath.name}, {mpath.name}")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "clean")
