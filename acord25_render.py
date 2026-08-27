"""
acord25_render.py — renders a faithful ACORD-25-layout Certificate of Liability
Insurance. Layout reproduces the standard form structure (2016/03 revision):
header + date box, disclaimer blocks, producer/contact, insured + insurer table
with NAIC column, the coverages grid (CGL row filled; Auto / Umbrella / Workers
Comp rendered blank), description of operations, certificate holder +
cancellation, authorized representative.

No ACORD logo or registered marks are reproduced; the footer identifies the
document as an ACORD-25-compatible layout sample. Page 2 is a provenance
addendum: one receipt per value (source + exact supporting sentence).
"""
from __future__ import annotations
from datetime import date

# --- Python 3.8 compatibility shim for reportlab's md5(usedforsecurity=...) ---
import hashlib as _hashlib
_orig_md5 = _hashlib.md5
def _md5_compat(*a, **k):
    k.pop("usedforsecurity", None)
    return _orig_md5(*a, **k)
_hashlib.md5 = _md5_compat

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

W, H = letter
M = 0.35 * inch          # outer margin
IW = W - 2 * M           # inner width


def _clip(c, t, size, maxw):
    while t and c.stringWidth(t, "Helvetica", size) > maxw:
        t = t[:-1]
    return t


def _val(fields: dict, key: str) -> str:
    v = fields.get(key)
    return "" if v is None else str(v)


def _yes(fields: dict, key: str) -> bool:
    return str(fields.get(key, "")).strip().casefold() in ("y", "yes", "true")


def render_acord25(path: str, fields: dict, receipts: list[dict],
                   warnings: list[str], issue_date: date | None = None,
                   cert_number: str | None = None) -> None:
    """fields: flat dict of verified values (unverified fields absent/None ->
    rendered blank). receipts: [{field,label,source,quote}]. warnings: rule
    warnings for the addendum."""
    issue_date = issue_date or date.today()
    c = canvas.Canvas(path, pagesize=letter)

    def hline(y, x0=M, x1=W - M, w=0.7):
        c.setLineWidth(w); c.line(x0, y, x1, y)

    def vline(x, y0, y1, w=0.7):
        c.setLineWidth(w); c.line(x, y0, x, y1)

    def text(x, y, t, size=7, bold=False, maxw=None):
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        if maxw:
            t = _clip(c, t, size, maxw)
        c.drawString(x, y, t)

    def tiny(x, y, t, maxw=None):
        text(x, y, t, size=5.4, maxw=maxw)

    # ---------- header ----------
    top = H - M
    box_w = 1.55 * inch
    hline(top); hline(top - 0.42 * inch)
    vline(M, top, top - 0.42 * inch); vline(W - M, top, top - 0.42 * inch)
    vline(W - M - box_w, top, top - 0.42 * inch)
    text(M + 4, top - 0.17 * inch, "CERTIFICATE OF LIABILITY INSURANCE",
         size=13, bold=True)
    tiny(W - M - box_w + 4, top - 0.11 * inch, "DATE (MM/DD/YYYY)")
    text(W - M - box_w + 4, top - 0.26 * inch,
         issue_date.strftime("%m/%d/%Y"), size=8)

    # ---------- disclaimer blocks ----------
    y = top - 0.42 * inch
    disc1 = ("THIS CERTIFICATE IS ISSUED AS A MATTER OF INFORMATION ONLY AND "
             "CONFERS NO RIGHTS UPON THE CERTIFICATE HOLDER. THIS CERTIFICATE "
             "DOES NOT AFFIRMATIVELY OR NEGATIVELY AMEND, EXTEND OR ALTER THE "
             "COVERAGE AFFORDED BY THE POLICIES BELOW. THIS CERTIFICATE OF "
             "INSURANCE DOES NOT CONSTITUTE A CONTRACT BETWEEN THE ISSUING "
             "INSURER(S), AUTHORIZED REPRESENTATIVE OR PRODUCER, AND THE "
             "CERTIFICATE HOLDER.")
    disc2 = ("IMPORTANT: If the certificate holder is an ADDITIONAL INSURED, "
             "the policy(ies) must have ADDITIONAL INSURED provisions or be "
             "endorsed. If SUBROGATION IS WAIVED, subject to the terms and "
             "conditions of the policy, certain policies may require an "
             "endorsement. A statement on this certificate does not confer "
             "rights to the certificate holder in lieu of such endorsement(s).")

    def para(y0, s, bold=True, size=5.9, leading=7.4):
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        words, line, lines, maxw = s.split(), "", [], IW - 8
        for w_ in words:
            t = (line + " " + w_).strip()
            if c.stringWidth(t, "Helvetica-Bold", size) > maxw:
                lines.append(line); line = w_
            else:
                line = t
        lines.append(line)
        yy = y0 - 9
        for ln in lines:
            c.drawString(M + 4, yy, ln); yy -= leading
        return yy + leading - 4

    y2 = para(y, disc1)
    hline(y2); vline(M, y, y2); vline(W - M, y, y2)
    y3 = para(y2, disc2)
    hline(y3); vline(M, y2, y3); vline(W - M, y2, y3)

    # ---------- producer / contact + insured / insurers ----------
    midx = M + IW * 0.47
    row_y = y3
    blk_h = 1.02 * inch
    y4 = row_y - blk_h
    vline(M, row_y, y4); vline(W - M, row_y, y4); vline(midx, row_y, y4)
    tiny(M + 4, row_y - 9, "PRODUCER")
    text(M + 14, row_y - 24, _val(fields, "producer_name"), size=8,
         maxw=midx - M - 20)
    text(M + 14, row_y - 36, _val(fields, "producer_address"), size=8,
         maxw=midx - M - 20)
    if _val(fields, "producer_code"):
        tiny(M + 4, y4 + 6, f"PRODUCER CODE: {_val(fields,'producer_code')}")
    # contact sub-box
    tiny(midx + 4, row_y - 9, "CONTACT NAME:")
    tiny(midx + 4, row_y - 21, "PHONE (A/C, No, Ext):")
    text(midx + 78, row_y - 21, _val(fields, "producer_phone"), size=7)
    tiny(midx + 4, row_y - 33, "E-MAIL ADDRESS:")
    hline(y4)

    ins_h = 1.12 * inch
    y5 = y4 - ins_h
    vline(M, y4, y5); vline(W - M, y4, y5); vline(midx, y4, y5)
    tiny(M + 4, y4 - 9, "INSURED")
    text(M + 14, y4 - 26, _val(fields, "insured_name"), size=8.4,
         maxw=midx - M - 20)
    text(M + 14, y4 - 40, _val(fields, "insured_address"), size=8,
         maxw=midx - M - 20)
    # insurer table
    naic_w = 0.62 * inch
    tiny(midx + 4, y4 - 9, "INSURER(S) AFFORDING COVERAGE")
    tiny(W - M - naic_w + 4, y4 - 9, "NAIC #")
    vline(W - M - naic_w, y4 - 13, y5)
    rh = (ins_h - 13) / 6.0
    for i, letter_ in enumerate("ABCDEF"):
        ry = y4 - 13 - (i + 1) * rh
        hline(ry, midx, W - M, 0.4)
        tiny(midx + 4, ry + 2.5, f"INSURER {letter_} :")
        if i == 0:
            text(midx + 46, ry + 2, _val(fields, "carrier"), size=7,
                 maxw=(W - M - naic_w) - (midx + 48))
            text(W - M - naic_w + 4, ry + 2, _val(fields, "carrier_naic"),
                 size=7)
    hline(y5)

    # ---------- coverages banner ----------
    y6 = y5 - 0.18 * inch
    vline(M, y5, y6); vline(W - M, y5, y6)
    text(M + 4, y6 + 4, "COVERAGES", size=7.5, bold=True)
    text(M + 2.1 * inch, y6 + 4,
         f"CERTIFICATE NUMBER: {cert_number or ''}", size=7)
    text(M + 5.1 * inch, y6 + 4, "REVISION NUMBER:", size=7)
    hline(y6)
    cert_para = ("THIS IS TO CERTIFY THAT THE POLICIES OF INSURANCE LISTED "
                 "BELOW HAVE BEEN ISSUED TO THE INSURED NAMED ABOVE FOR THE "
                 "POLICY PERIOD INDICATED. NOTWITHSTANDING ANY REQUIREMENT, "
                 "TERM OR CONDITION OF ANY CONTRACT OR OTHER DOCUMENT WITH "
                 "RESPECT TO WHICH THIS CERTIFICATE MAY BE ISSUED OR MAY "
                 "PERTAIN, THE INSURANCE AFFORDED BY THE POLICIES DESCRIBED "
                 "HEREIN IS SUBJECT TO ALL THE TERMS, EXCLUSIONS AND "
                 "CONDITIONS OF SUCH POLICIES. LIMITS SHOWN MAY HAVE BEEN "
                 "REDUCED BY PAID CLAIMS.")
    y7 = para(y6, cert_para, bold=False, size=5.6, leading=6.8)
    hline(y7); vline(M, y6, y7); vline(W - M, y6, y7)

    # ---------- coverage grid ----------
    # columns: INSR LTR | TYPE | ADDL | SUBR | POLICY NUMBER | EFF | EXP | LIMITS
    c0 = M
    c1 = c0 + 0.28 * inch          # insr ltr
    c2 = c1 + 2.05 * inch          # type of insurance
    c3 = c2 + 0.30 * inch          # addl insd
    c4 = c3 + 0.30 * inch          # subr wvd
    c5 = c4 + 1.55 * inch          # policy number
    c6 = c5 + 0.70 * inch          # eff
    c7 = c6 + 0.70 * inch          # exp
    c8 = W - M                     # limits

    ghead = y7
    gh = 0.16 * inch
    y8 = ghead - gh
    for x in (c0, c1, c2, c3, c4, c5, c6, c7, c8):
        vline(x, ghead, y8, 0.5)
    tiny(c0 + 1.5, y8 + 4, "INSR"); tiny(c0 + 1.5, y8 + 9.5, " ")
    tiny(c1 + 3, y8 + 4, "TYPE OF INSURANCE")
    tiny(c2 + 1.5, y8 + 8, "ADDL"); tiny(c2 + 1.5, y8 + 2.5, "INSD")
    tiny(c3 + 1.5, y8 + 8, "SUBR"); tiny(c3 + 1.5, y8 + 2.5, "WVD")
    tiny(c4 + 3, y8 + 4, "POLICY NUMBER")
    tiny(c5 + 2, y8 + 8, "POLICY EFF"); tiny(c5 + 2, y8 + 2.5, "(MM/DD/YYYY)")
    tiny(c6 + 2, y8 + 8, "POLICY EXP"); tiny(c6 + 2, y8 + 2.5, "(MM/DD/YYYY)")
    tiny(c7 + 3, y8 + 4, "LIMITS")
    hline(y8, w=0.5)

    term = _val(fields, "policy_term")
    eff = exp = ""
    import re as _re
    m = _re.search(r"(\d{1,2}/\d{1,2}/\d{4})\s*(?:to|through|[-\u2013\u2014])\s*"
                   r"(\d{1,2}/\d{1,2}/\d{4})", term)
    if m:
        eff, exp = m.group(1), m.group(2)

    limits_gl = [
        ("EACH OCCURRENCE", _val(fields, "each_occurrence")),
        ("DAMAGE TO RENTED PREMISES (Ea occurrence)",
         _val(fields, "damage_rented")),
        ("MED EXP (Any one person)", _val(fields, "med_expense")),
        ("PERSONAL & ADV INJURY", _val(fields, "personal_adv_injury")),
        ("GENERAL AGGREGATE", _val(fields, "general_aggregate")),
        ("PRODUCTS - COMP/OP AGG", _val(fields, "products_aggregate")),
    ]
    gl_h = 6 * 0.148 * inch
    y9 = y8 - gl_h
    for x in (c0, c1, c2, c3, c4, c5, c6, c7, c8):
        vline(x, y8, y9, 0.5)
    text(c0 + 6, y8 - 0.5 * gl_h, "A", size=8)
    tiny(c1 + 3, y8 - 9, "COMMERCIAL GENERAL LIABILITY")
    tiny(c1 + 10, y8 - 20, "CLAIMS-MADE   [X] OCCUR")
    tiny(c1 + 3, y9 + 12, "GEN'L AGGREGATE LIMIT APPLIES PER:")
    tiny(c1 + 10, y9 + 4, "[X] POLICY    [ ] PROJECT    [ ] LOC")
    if _yes(fields, "additional_insured"):
        text(c2 + 9, y8 - 0.5 * gl_h, "Y", size=8)
    if _yes(fields, "waiver_subrogation"):
        text(c3 + 9, y8 - 0.5 * gl_h, "Y", size=8)
    text(c4 + 4, y8 - 0.5 * gl_h, _val(fields, "policy_number"), size=7,
         maxw=c5 - c4 - 8)
    text(c5 + 3, y8 - 0.5 * gl_h, eff, size=6.6)
    text(c6 + 3, y8 - 0.5 * gl_h, exp, size=6.6)
    lrh = gl_h / 6.0
    for i, (lab, v) in enumerate(limits_gl):
        ry = y8 - (i + 1) * lrh
        hline(ry, c7, c8, 0.35)
        tiny(c7 + 2, ry + 3, lab, maxw=1.42 * inch)
        c.setFont("Helvetica", 6.6)
        c.drawRightString(c8 - 3, ry + 2.6, f"$ {v}" if v else "$")
    hline(y9, w=0.5)

    # blank sections: Auto / Umbrella / Workers Comp
    blank_rows = [
        ("AUTOMOBILE LIABILITY",
         ["COMBINED SINGLE LIMIT (Ea accident)", "BODILY INJURY (Per person)",
          "BODILY INJURY (Per accident)", "PROPERTY DAMAGE (Per accident)"]),
        ("UMBRELLA LIAB          EXCESS LIAB",
         ["EACH OCCURRENCE", "AGGREGATE"]),
        ("WORKERS COMPENSATION AND EMPLOYERS' LIABILITY",
         ["E.L. EACH ACCIDENT", "E.L. DISEASE - EA EMPLOYEE",
          "E.L. DISEASE - POLICY LIMIT"]),
    ]
    yb = y9
    for title, labs in blank_rows:
        h_ = len(labs) * 0.142 * inch + 0.05 * inch
        yn = yb - h_
        for x in (c0, c1, c2, c3, c4, c5, c6, c7, c8):
            vline(x, yb, yn, 0.5)
        tiny(c1 + 3, yb - 9, title)
        lr = h_ / len(labs)
        for i, lab in enumerate(labs):
            ry = yb - (i + 1) * lr
            if i < len(labs) - 1:
                hline(ry, c7, c8, 0.35)
            tiny(c7 + 2, ry + 3, lab, maxw=1.42 * inch)
            c.setFont("Helvetica", 6.6)
            c.drawRightString(c8 - 3, ry + 2.6, "$")
        hline(yn, w=0.5)
        yb = yn

    # ---------- description of operations ----------
    ops_h = 0.85 * inch
    y10 = yb - ops_h
    vline(M, yb, y10); vline(W - M, yb, y10)
    tiny(M + 4, yb - 8, "DESCRIPTION OF OPERATIONS / LOCATIONS / VEHICLES "
         "(ACORD 101, Additional Remarks Schedule, may be attached if more "
         "space is required)")
    ops = fields.get("description_of_operations") or ""
    c.setFont("Helvetica", 6.6)
    words, line, lines = ops.split(), "", []
    for w_ in words:
        t = (line + " " + w_).strip()
        if c.stringWidth(t, "Helvetica", 6.6) > IW - 16:
            lines.append(line); line = w_
        else:
            line = t
    if line:
        lines.append(line)
    yy = yb - 20
    for ln in lines[:8]:
        c.drawString(M + 6, yy, ln); yy -= 8.2
    hline(y10)

    # ---------- certificate holder / cancellation ----------
    hold_h = 1.0 * inch
    y11 = y10 - hold_h
    vline(M, y10, y11); vline(W - M, y10, y11); vline(midx, y10, y11)
    tiny(M + 4, y10 - 9, "CERTIFICATE HOLDER")
    holder = _val(fields, "cert_holder")
    c.setFont("Helvetica", 7.6)
    hl, hline_, hls = holder.split(", "), "", []
    for part in hl:
        hls.append(part)
    yy = y10 - 26
    for part in hls[:4]:
        c.drawString(M + 14, yy, _clip(c, part, 7.6, midx - M - 22)); yy -= 11
    tiny(midx + 4, y10 - 9, "CANCELLATION")
    canc = ("SHOULD ANY OF THE ABOVE DESCRIBED POLICIES BE CANCELLED BEFORE "
            "THE EXPIRATION DATE THEREOF, NOTICE WILL BE DELIVERED IN "
            "ACCORDANCE WITH THE POLICY PROVISIONS.")
    c.setFont("Helvetica-Bold", 5.6)
    words, line, lines = canc.split(), "", []
    for w_ in words:
        t = (line + " " + w_).strip()
        if c.stringWidth(t, "Helvetica-Bold", 5.6) > (W - M - midx) - 12:
            lines.append(line); line = w_
        else:
            line = t
    lines.append(line)
    yy = y10 - 20
    for ln in lines:
        c.drawString(midx + 6, yy, ln); yy -= 7
    hline(yy - 2, midx, W - M, 0.4)
    tiny(midx + 4, yy - 10, "AUTHORIZED REPRESENTATIVE")
    text(midx + 8, yy - 24, _val(fields, "producer_name"), size=7.6)
    hline(y11)

    # ---------- footer ----------
    c.setFont("Helvetica", 5.4)
    c.drawString(M, y11 - 10,
                 "ACORD-25-compatible layout — sample rendering for software "
                 "prototype. Not an official ACORD form; no ACORD marks used.")
    c.drawRightString(W - M, y11 - 10,
                      "Provenance addendum: page 2 — every value's source and "
                      "supporting sentence.")

    # ================= PAGE 2: provenance addendum =================
    c.showPage()
    text(M, H - M - 10, "PROVENANCE ADDENDUM", size=11, bold=True)
    text(M, H - M - 24,
         "Every value on the certificate, with the source document and the "
         "exact sentence that supports it.", size=8)
    yy = H - M - 46
    for r in receipts:
        if yy < M + 40:
            c.showPage(); yy = H - M - 20
        text(M, yy, f"{r.get('label', r.get('field',''))}: "
             f"{r.get('value','')}", size=8, bold=True, maxw=IW)
        yy -= 11
        src = r.get('source', '')
        quote = r.get('quote', '')
        text(M + 12, yy, f"source: {src}" +
             (f' — "{quote}"' if quote else ""), size=7, maxw=IW - 12)
        yy -= 14
    if warnings:
        yy -= 6
        text(M, yy, "WARNINGS", size=9, bold=True); yy -= 12
        for w_ in warnings:
            text(M + 12, yy, "• " + w_, size=7.4, maxw=IW - 12); yy -= 11
    c.save()


# ---------------- adapter from engine results ----------------
FIELD_LABELS = {
 "producer_name": "Producer (agency)", "producer_address": "Producer address",
 "producer_phone": "Producer phone", "producer_code": "Producer code",
 "insured_name": "Named insured", "insured_address": "Insured address",
 "carrier": "Insurer (carrier)", "policy_number": "Policy number",
 "policy_term": "Policy period", "cert_holder": "Certificate holder",
 "each_occurrence": "Each occurrence", "general_aggregate": "General aggregate",
 "products_aggregate": "Products/completed ops",
 "personal_adv_injury": "Personal & adv injury",
 "damage_rented": "Damage to rented premises", "med_expense": "Medical expense",
 "additional_insured": "Additional insured (box)",
 "waiver_subrogation": "Waiver of subrogation (box)",
}
_SRC = {"policy": "the policy", "request": "the certificate request",
        "config": "the agency's settings"}


def _compose_ops(fields: dict) -> str:
    pn = fields.get("policy_number", "the policy")
    ai = str(fields.get("additional_insured", "")).strip().casefold()
    wv = str(fields.get("waiver_subrogation", "")).strip().casefold()
    parts = ["Certificate issued as evidence of Commercial General Liability "
             f"coverage under policy {pn}."]
    ai_y = ai in ("y", "yes", "true"); wv_y = wv in ("y", "yes", "true")
    if ai_y or wv_y:
        grants = []
        if ai_y:
            grants.append("Additional Insured status")
        if wv_y:
            grants.append("Waiver of Subrogation")
        parts.append("Certificate holder is granted " + " and ".join(grants)
                     + " per endorsement on the policy.")
    if (ai and not ai_y) or (wv and not wv_y):
        parts.append("No additional-insured endorsement and no waiver of "
                     "subrogation endorsement are on file for this policy; "
                     "the ADDL INSD and SUBR WVD columns are therefore not "
                     "marked.")
    return " ".join(parts)


def render_from_results(results, rules, path, issue_date=None):
    """Engine FieldResult list + Rule list -> faithful ACORD-25 PDF."""
    import hashlib
    from datetime import date as _d
    issue_date = issue_date or _d.today()
    fields, receipts = {}, []
    for r in results:
        if r.value is None:
            continue
        fields[r.name] = r.value
        receipts.append({
            "field": r.name,
            "label": FIELD_LABELS.get(r.name, r.name),
            "value": str(r.value),
            "source": _SRC.get(getattr(r, "source", ""),
                               getattr(r, "source", "")),
            "quote": getattr(r, "span", "") or "",
        })
    fields["description_of_operations"] = _compose_ops(fields)
    warnings = [ru.detail for ru in rules
                if getattr(ru, "severity", "") == "WARN" and not ru.passed]
    seed = fields.get("policy_number", "") + fields.get("insured_name", "")
    cert_no = "CERT-%d-%s" % (issue_date.year,
                              hashlib.md5(seed.encode()).hexdigest()[:6].upper())
    render_acord25(str(path), fields, receipts, warnings, issue_date, cert_no)
