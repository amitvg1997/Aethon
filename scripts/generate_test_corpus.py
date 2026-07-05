#!/usr/bin/env python3
"""Generate a synthetic 'noisy enterprise corpus' + labeled test set for Aethon.

The corpus reproduces, in a controlled way, exactly the failure modes the
metadata filter targets:

  * Near-duplicates / ambiguity — the same data-retention policy for Germany,
    France and Spain: identical boilerplate, different key facts (retention
    period, supervisory authority, fines, deadlines). Plain vector similarity
    struggles to keep them apart; the discriminative metadata terms separate
    them.
  * Exact duplicate — a byte-identical copy of the German policy under a
    different filename. The ingestion pipeline should detect it via the
    content hash and mark it SKIPPED_DUPLICATE (never calling the LLM or
    entering the Knowledge Base).
  * Versioned redundancy — two editions of the same employee handbook (2023
    vs 2026) that disagree on vacation days, remote work and probation.

Also writes `testset.json`, a labeled question set for the eval page's batch
runner: each case names the expected source document (by filename, so labels
survive re-uploads) and a reference answer for the blind LLM judge.

Zero dependencies — PDFs are written directly (PDF 1.4, Helvetica).

Usage:
    python3 scripts/generate_test_corpus.py [--out ./corpus]
"""

import argparse
import json
import textwrap
from pathlib import Path


# ── Minimal PDF writer (text-only, Helvetica, US Letter) ──────────────────

PAGE_WIDTH = 612
PAGE_HEIGHT = 792
FONT_SIZE = 11
LEADING = 14
MARGIN = 72
LINES_PER_PAGE = (PAGE_HEIGHT - 2 * MARGIN) // LEADING  # 46


def _escape(line):
    return line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream(lines):
    ops = [f"BT /F1 {FONT_SIZE} Tf {MARGIN} {PAGE_HEIGHT - MARGIN} Td {LEADING} TL"]
    for line in lines:
        ops.append(f"({_escape(line)}) Tj T*")
    ops.append("ET")
    return "\n".join(ops).encode("latin-1")


def write_pdf(path, lines):
    pages = [
        lines[start:start + LINES_PER_PAGE]
        for start in range(0, len(lines), LINES_PER_PAGE)
    ] or [[""]]

    # Objects: 1 catalog, 2 pages tree, 3 font, then (page, content) per page.
    page_object_numbers = [4 + 2 * index for index in range(len(pages))]
    objects = {}

    kids = " ".join(f"{number} 0 R" for number in page_object_numbers)
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("latin-1")
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    for page_number, page_lines in zip(page_object_numbers, pages):
        stream = _content_stream(page_lines)
        objects[page_number] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_number + 1} 0 R >>"
        ).encode("latin-1")
        objects[page_number + 1] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
            + stream
            + b"\nendstream"
        )

    buffer = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(buffer)
        buffer += f"{number} 0 obj\n".encode("latin-1")
        buffer += objects[number]
        buffer += b"\nendobj\n"

    xref_offset = len(buffer)
    object_count = len(objects) + 1
    buffer += f"xref\n0 {object_count}\n".encode("latin-1")
    buffer += b"0000000000 65535 f \n"
    for number in sorted(objects):
        buffer += f"{offsets[number]:010d} 00000 n \n".encode("latin-1")
    buffer += (
        f"trailer\n<< /Size {object_count} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("latin-1")

    Path(path).write_bytes(bytes(buffer))


def to_lines(text):
    # The base-14 Helvetica font uses a latin-1 text encoding; transliterate
    # anything outside it so the writer never chokes on smart punctuation.
    for fancy, plain in {"—": "-", "–": "-", "’": "'", "‘": "'", "“": '"', "”": '"'}.items():
        text = text.replace(fancy, plain)
    text = text.encode("latin-1", errors="replace").decode("latin-1")

    lines = []
    for paragraph in text.strip().split("\n\n"):
        paragraph = " ".join(paragraph.split())
        lines.extend(textwrap.wrap(paragraph, width=92) or [""])
        lines.append("")
    return lines


# ── Corpus definitions ─────────────────────────────────────────────────────

POLICY_TEMPLATE = """
DATA RETENTION AND PRIVACY POLICY — {country_upper}

Meridian Logistics Group — Corporate Compliance Series, Document {doc_code}

1. Purpose and Scope

This policy defines how Meridian Logistics Group collects, stores, retains and deletes
personal and business records within its {country} operations. It applies to all employees,
contractors and third-party processors handling data on behalf of the company in {country}.
The policy exists to ensure lawful processing, to limit storage to what is necessary, and to
guarantee that records are disposed of securely at the end of their lifecycle.

2. Definitions

Personal data means any information relating to an identified or identifiable natural person.
Business records include contracts, invoices, correspondence, audit trails and operational
logs produced in the course of business. A data breach is any security incident leading to
accidental or unlawful destruction, loss, alteration or unauthorized disclosure of data.

3. Key Compliance Facts — {country}

Customer records retention period: {retention}.
Supervisory authority: {authority}.
Maximum fine for non-compliance: {fine}.
Data breach notification deadline: {breach_window}.
Appointed Data Protection Officer: {dpo}.
Annual policy review month: {review_month}.

4. Storage and Security

All records must be stored on approved company systems with access restricted on a
need-to-know basis. Encryption at rest and in transit is mandatory for personal data.
Backup copies follow the same retention limits as primary records and are verified
quarterly by the infrastructure team.

5. Deletion Procedure

When the retention period defined in section 3 expires, records are deleted during the
monthly disposal window. Deletion requests are logged, and a certificate of destruction is
produced for physical media. Exceptions require written approval from the Data Protection
Officer and must reference an active legal hold.

6. Breach Response

Suspected breaches must be reported to the security desk immediately. The Data Protection
Officer assesses severity and notifies the supervisory authority named in section 3 within
the deadline defined in section 3 when the breach poses a risk to data subjects. Affected
individuals are informed without undue delay when the risk is high.
"""

HANDBOOK_TEMPLATE = """
EMPLOYEE HANDBOOK — {edition} EDITION

Meridian Logistics Group — Human Resources, Document {doc_code}

1. Introduction

This handbook summarizes the employment terms, benefits and workplace rules for all
employees of Meridian Logistics Group. It is reviewed by Human Resources and replaces all
earlier editions. Where an employment contract differs from this handbook, the contract
prevails.

2. Working Hours and Remote Work

Standard working time is 40 hours per week, Monday to Friday. Employees may work remotely
up to {remote_days} days per week with their manager's approval. Core collaboration hours
are 10:00 to 15:00, during which employees are expected to be reachable.

3. Leave and Vacation

Full-time employees receive {vacation_days} paid vacation days per calendar year, in
addition to public holidays. Unused vacation days may be carried into the first quarter of
the following year. Sick leave requires a medical certificate from the third consecutive
day of absence.

4. Probation and Notice Periods

The probation period for new employees is {probation}. During probation, either party may
terminate employment with two weeks' notice. After probation, statutory notice periods
apply.

5. Offices

The company maintains offices in {offices}. Desk booking is required for all locations and
available through the facilities portal.

6. Conduct

Employees are expected to act with integrity, protect confidential information, and follow
the IT acceptable-use policy. Violations are handled by Human Resources and may lead to
disciplinary measures.
"""


def build_corpus():
    policies = [
        {
            "filename": "germany_data_retention_policy.pdf",
            "country": "Germany",
            "doc_code": "CP-DE-401",
            "retention": "10 years from the end of the business relationship",
            "authority": (
                "the Federal Commissioner for Data Protection and Freedom of "
                "Information (BfDI)"
            ),
            "fine": "EUR 20 million",
            "breach_window": "72 hours after becoming aware of the breach",
            "dpo": "Dr. Anna Weber",
            "review_month": "March",
        },
        {
            "filename": "france_data_retention_policy.pdf",
            "country": "France",
            "doc_code": "CP-FR-402",
            "retention": "5 years from the end of the business relationship",
            "authority": "the Commission Nationale de l'Informatique et des Libertes (CNIL)",
            "fine": "EUR 15 million",
            "breach_window": "48 hours after becoming aware of the breach",
            "dpo": "Marie Laurent",
            "review_month": "July",
        },
        {
            "filename": "spain_data_retention_policy.pdf",
            "country": "Spain",
            "doc_code": "CP-ES-403",
            "retention": "6 years from the end of the business relationship",
            "authority": "the Agencia Espanola de Proteccion de Datos (AEPD)",
            "fine": "EUR 10 million",
            "breach_window": "96 hours after becoming aware of the breach",
            "dpo": "Carlos Ortega",
            "review_month": "November",
        },
    ]

    handbooks = [
        {
            "filename": "employee_handbook_2023.pdf",
            "edition": "2023",
            "doc_code": "HR-HB-2023",
            "remote_days": "2",
            "vacation_days": "24",
            "probation": "6 months",
            "offices": "Berlin",
        },
        {
            "filename": "employee_handbook_2026.pdf",
            "edition": "2026",
            "doc_code": "HR-HB-2026",
            "remote_days": "3",
            "vacation_days": "30",
            "probation": "3 months",
            "offices": "Berlin and Munich",
        },
    ]

    documents = []
    for policy in policies:
        text = POLICY_TEMPLATE.format(
            country_upper=policy["country"].upper(), **policy
        )
        documents.append((policy["filename"], text))

    # Byte-identical copy of the German policy under another filename — the
    # ingestion pipeline should flag it as SKIPPED_DUPLICATE via content hash.
    documents.append(("germany_data_retention_policy_copy.pdf", documents[0][1]))

    for handbook in handbooks:
        documents.append(
            (handbook["filename"], HANDBOOK_TEMPLATE.format(**handbook))
        )

    return documents


TESTSET = [
    {
        "question": "How long must customer records be retained under the German data retention policy?",
        "expected_document_filename": "germany_data_retention_policy.pdf",
        "expected_answer": "Customer records must be retained for 10 years from the end of the business relationship.",
    },
    {
        "question": "How long must customer records be retained under the French data retention policy?",
        "expected_document_filename": "france_data_retention_policy.pdf",
        "expected_answer": "Customer records must be retained for 5 years from the end of the business relationship.",
    },
    {
        "question": "Which supervisory authority oversees data protection compliance in France?",
        "expected_document_filename": "france_data_retention_policy.pdf",
        "expected_answer": "The Commission Nationale de l'Informatique et des Libertes (CNIL).",
    },
    {
        "question": "What is the maximum fine for non-compliance under the Spanish data retention policy?",
        "expected_document_filename": "spain_data_retention_policy.pdf",
        "expected_answer": "EUR 10 million.",
    },
    {
        "question": "Within what deadline must a data breach be reported under the Spanish policy?",
        "expected_document_filename": "spain_data_retention_policy.pdf",
        "expected_answer": "Within 96 hours after becoming aware of the breach.",
    },
    {
        "question": "Who is the Data Protection Officer named in the German policy?",
        "expected_document_filename": "germany_data_retention_policy.pdf",
        "expected_answer": "Dr. Anna Weber.",
    },
    {
        "question": "How many paid vacation days do employees get according to the 2026 employee handbook?",
        "expected_document_filename": "employee_handbook_2026.pdf",
        "expected_answer": "30 paid vacation days per calendar year.",
    },
    {
        "question": "How many days per week could employees work remotely under the 2023 handbook?",
        "expected_document_filename": "employee_handbook_2023.pdf",
        "expected_answer": "Up to 2 days per week with manager approval.",
    },
    {
        "question": "What is the probation period in the 2026 edition of the employee handbook?",
        "expected_document_filename": "employee_handbook_2026.pdf",
        "expected_answer": "3 months.",
    },
    {
        "question": "In which month is the French data retention policy reviewed each year?",
        "expected_document_filename": "france_data_retention_policy.pdf",
        "expected_answer": "July.",
    },
]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", default="./corpus", help="Output directory (default: ./corpus)"
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    documents = build_corpus()
    for filename, text in documents:
        write_pdf(out_dir / filename, to_lines(text))
        print(f"  wrote {out_dir / filename}")

    testset_path = out_dir / "testset.json"
    testset_path.write_text(json.dumps(TESTSET, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote {testset_path}")

    print(
        f"""
Generated {len(documents)} PDFs + a labeled test set of {len(TESTSET)} questions.

Demo walkthrough:
  1. Upload every PDF EXCEPT germany_data_retention_policy_copy.pdf on the
     upload page and wait until metadata extraction completes.
  2. Upload germany_data_retention_policy_copy.pdf afterwards — it should be
     flagged as a duplicate (SKIPPED_DUPLICATE) without any LLM/ingestion cost.
     (Uploading it simultaneously with the original can race past the check.)
  3. Click "Sync Knowledge Base" and wait for SYNCED.
  4. On the eval page, choose {testset_path.name} under "Batch test set" and
     run it. Compare filtered vs unfiltered precision, hit rate, answer
     accuracy and source-document counts.
"""
    )


if __name__ == "__main__":
    main()
