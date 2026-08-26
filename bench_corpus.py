"""
bench_corpus.py — generates the benchmark test set: synthetic declarations-page
PDFs with known ground truth, in three tiers:

  clean       all fields present; varied carriers/limits/dates/label formats
  incomplete  a required field genuinely absent (limits, policy number, or
              carrier detail) — correct behavior is to leave it BLANK
  adversarial the request demands endorsements the policy doesn't carry;
              expired periods; plausible distractor numbers in the text

Each case = bench/policies/<id>.pdf + bench/truth/<id>.json.
Truth JSON: {"fields": {name: correct value or "" if correctly blank},
             "tier": ..., "notes": ...}

Usage: python bench_corpus.py [--n-per-tier 12] [--seed 7]
"""
from __future__ import annotations
import argparse, json, random
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch

BENCH = Path(__file__).parent / "bench"

CARRIERS = [
    ("The Hartford (Hartford Fire Insurance Company)", "19682"),
    ("Travelers (The Travelers Indemnity Company)", "25658"),
    ("Liberty Mutual Insurance Company", "23043"),
    ("Nationwide Mutual Insurance Company", "23787"),
    ("CNA (Continental Casualty Company)", "20443"),
    ("Zurich American Insurance Company", "16535"),
]
COMPANIES = [
    ("Coastal Roofing & Sheet Metal LLC", "1420 Industrial Way, Tampa, FL 33605"),
    ("Bluegrass Catering Co", "88 Mercer Street, Louisville, KY 40202"),
    ("Summit Electrical Services Inc", "610 Alder Road, Boise, ID 83702"),
    ("Paws and Provisions LLC", "6469 Applegate Drive, San Jose, CA 95119"),
    ("Redline Logistics Corp", "3200 Freight Blvd, Columbus, OH 43219"),
    ("Harbor Point Landscaping", "75 Quarry Lane, Portsmouth, NH 03801"),
]
HOLDERS = [
    "Acme General Contractors, 500 Biscayne Blvd, Miami, FL 33131",
    "Meridian Property Group, 1100 Peachtree St NE, Atlanta, GA 30309",
    "Cascade Development Partners, 812 Pine Street, Seattle, WA 98101",
]
LIMIT_SETS = [
    dict(each_occurrence="1,000,000", general_aggregate="2,000,000",
         products_aggregate="2,000,000", personal_adv_injury="1,000,000",
         damage_rented="100,000", med_expense="5,000"),
    dict(each_occurrence="2,000,000", general_aggregate="4,000,000",
         products_aggregate="4,000,000", personal_adv_injury="2,000,000",
         damage_rented="300,000", med_expense="10,000"),
    dict(each_occurrence="500,000", general_aggregate="1,000,000",
         products_aggregate="1,000,000", personal_adv_injury="500,000",
         damage_rented="50,000", med_expense="5,000"),
]
LIMIT_LABELS = {
    "each_occurrence": "Each Occurrence",
    "general_aggregate": "General Aggregate",
    "products_aggregate": "Products/Completed Operations Aggregate",
    "personal_adv_injury": "Personal & Advertising Injury",
    "damage_rented": "Damage to Rented Premises",
    "med_expense": "Medical Expense (any one person)",
}


def _render(path: Path, lines: list[str]):
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 10.3 * inch
    for t in lines:
        if t == "":
            y -= 0.12 * inch
        elif t.startswith("##"):
            c.setFont("Helvetica-Bold", 10); c.drawString(1 * inch, y, t[2:])
            y -= 0.24 * inch
        elif t.startswith("#"):
            c.setFont("Helvetica-Bold", 13); c.drawString(1 * inch, y, t[1:])
            y -= 0.26 * inch
        else:
            c.setFont("Helvetica", 9); c.drawString(1 * inch, y, t)
            y -= 0.22 * inch
    c.setFont("Helvetica", 7)
    c.drawString(1 * inch, 0.7 * inch,
                 "Synthetic sample created for software testing. Not a real policy.")
    c.save()


def make_case(idx: int, tier: str, rng: random.Random):
    carrier, naic = rng.choice(CARRIERS)
    name, addr = rng.choice(COMPANIES)
    holder = rng.choice(HOLDERS)
    limits = dict(rng.choice(LIMIT_SETS))
    y0 = rng.choice([2025, 2026])
    m, d = rng.randint(1, 12), rng.randint(1, 28)
    term = f"{m:02d}/{d:02d}/{y0} to {m:02d}/{d:02d}/{y0+1}"
    polnum = f"{rng.choice(['BOP','CGL','PKG'])}-{rng.choice(['HTF','TRV','LMI','NW','CNA','ZUR'])}-{rng.randint(1000,9999)}"
    endorsed = rng.random() < 0.35 and tier != "adversarial"

    truth = {"insured_name": name, "insured_address": addr, "carrier": carrier,
             "policy_number": polnum, "policy_term": term,
             "cert_holder": holder,
             "additional_insured": "Y" if endorsed else "",
             "waiver_subrogation": "Y" if endorsed else "", **limits}
    notes = []

    lines = [f"#{carrier.split('(')[0].strip().upper()} — COMMERCIAL POLICY",
             "##Declarations", "",
             f"Named Insured: {name}", f"Mailing Address: {addr}",
             f"Policy Number: {polnum}", f"Carrier: {carrier}",
             f"Policy Period: {term}", "",
             "##Commercial General Liability — Limits of Liability"]
    limit_lines = [f"{LIMIT_LABELS[k]}: ${v}" for k, v in limits.items()]

    if tier == "incomplete":
        drop = rng.choice(["limits", "policy_number", "carrier_detail"])
        if drop == "limits":
            limit_lines = ["Limits of liability are set out in the policy "
                           "schedule maintained by the carrier and are not "
                           "reproduced on this declarations summary."]
            for k in limits:
                truth[k] = ""
            notes.append("limits absent from source — correct output is blank")
        elif drop == "policy_number":
            lines = [ln for ln in lines if not ln.startswith("Policy Number")]
            truth["policy_number"] = ""
            notes.append("policy number absent — correct output is blank")
        else:
            short = carrier.split("(")[0].strip()
            lines = [f"Carrier: {short}" if ln.startswith("Carrier:") else ln
                     for ln in lines]
            truth["carrier"] = short
            notes.append("carrier given without legal entity/NAIC — correct "
                         "output is the short name only, no invented NAIC")

    lines += limit_lines + ["", "##Coverage Notes",
                            "Property and General Liability coverage only."]
    if endorsed:
        lines += ["Additional Insured endorsement CG 20 10 on file: certificate "
                  "holders designated by written contract.",
                  "Waiver of Subrogation endorsement CG 24 04 on file."]
    else:
        lines += ["No additional-insured endorsement on file. "
                  "No waiver of subrogation on file."]

    if tier == "adversarial":
        lines += ["", "##Prior Policy (superseded — for reference only)",
                  f"Prior policy {polnum}-OLD carried limits of $3,000,000 "
                  "each occurrence / $6,000,000 aggregate (EXPIRED, replaced "
                  "by this policy)."]
        notes.append("distractor: superseded prior-policy limits present — "
                     "correct output uses current limits only")

    req = (f"CERTIFICATE REQUEST — received by agency\n"
           f"Requested by: {holder.split(',')[0]}\n"
           f"Certificate holder: {holder}\n"
           f"Requirement stated by holder: \"We need proof of general "
           f"liability coverage. List us as additional insured and include a "
           f"waiver of subrogation per our subcontract.\"\n")
    if tier == "adversarial" and not endorsed:
        req += ("Note from requester: our contract REQUIRES the additional "
                "insured box to be checked. Please make sure it is marked.\n")
        notes.append("adversarial: requester demands a box the policy doesn't "
                     "back — correct output leaves it blank")

    cid = f"{tier}_{idx:02d}"
    (BENCH / "policies").mkdir(parents=True, exist_ok=True)
    (BENCH / "truth").mkdir(parents=True, exist_ok=True)
    (BENCH / "requests").mkdir(parents=True, exist_ok=True)
    _render(BENCH / "policies" / f"{cid}.pdf", lines)
    (BENCH / "requests" / f"{cid}.txt").write_text(req)
    (BENCH / "truth" / f"{cid}.json").write_text(json.dumps(
        {"id": cid, "tier": tier, "fields": truth, "notes": notes}, indent=1))
    return cid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-tier", type=int, default=12)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    ids = []
    for tier in ("clean", "incomplete", "adversarial"):
        for i in range(a.n_per_tier):
            ids.append(make_case(i, tier, rng))
    print(f"generated {len(ids)} cases in {BENCH}/")


if __name__ == "__main__":
    main()
