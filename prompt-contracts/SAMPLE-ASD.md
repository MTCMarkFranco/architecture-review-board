# SAMPLE-ASD — Prompt Contract

## Intent

Replace the opaque, hand-built `sampleasd.pdf` with a programmatically-generated `sample_asd.docx` that exercises every section and table header consumed by `back-end/file_processing/parsing.py`. Extend the parser with a `.docx` variant so the back-end can ingest both formats and the front-end (which already accepts `.docx`) is unblocked.

## Linked issue

**#16** — Sample ASD Word doc honouring `parsing.py` section/table headers

## Inputs

- `parsing.py` constants (treated as the canonical schema): `summary_headers`, `requirement_headers`, `solution_headers`, `ec2_table_headers`, `servers_table_headers`, `deployment_details_headers`.
- `python-docx` library.

## Outputs

- `back-end/file_processing/build_sample_asd.py` — produces the docx.
- `back-end/file_processing/data/sample_asd.docx` — the generated file.
- `back-end/file_processing/parsing.py` — extended with `parse_arb_docx(docx_path=None, docx_file=None, local=False)` returning the same dict shape as `parse_arb`.
- `back-end/app.py` — dispatches on file extension (`.pdf` → `parse_arb`, `.docx` → `parse_arb_docx`).

## Section + table content (exact strings)

In this order, using literal strings from `parsing.py`:

1. **Heading**: `Summary`  
   Subheadings: `Introduction`, `Key Functionalities/Capabilities`, `Assumptions/Constraints/Recommendations` (each followed by 1–2 paragraphs of fictional content).
2. **Heading**: `Solution Requirements`  
   Subheadings (all 12): `User/Usage Requirements`, `Interface Requirements`, `Security Requirements`, `Network Requirements`, `Software Requirements`, `Performance Requirements`, `Supportability Requirements`, `Storage Requirements`, `Database Requirements`, `Disaster Recovery Requirements`, `Compliance Requirements`, `Licensing Requirements`.
3. **Heading**: `Affinity/Anti-Affinity Requirements` (ending marker; can contain a sentence).
4. **Heading**: `Proposed Solution`  
   Subheadings: `Proposed New Architecture`, `Pre-Production Architecture`, `Production/DR Architecture`.
5. **Heading**: `EC2 Sizing/Specifications (Guidance on OS Volumes & MS Office Support)`  
   Followed by a Word **table** whose first row contains all 15 EC2 headers exactly as defined, plus 2 fictional rows.
6. **Heading**: `On-Prem Servers Sizing/Specifications`  
   Followed by a table with the 12 servers headers + 2 rows.
7. **Heading**: `Proposed Server Details` (ending marker).
8. **Heading**: `Hosted Location`  
   Followed by a table with the 3 deployment-details headers + 1 row.
9. **Heading**: `Miscellaneous Information` (ending marker).

## Edge cases & clarifications

1. **`parse_arb` PDF call path must keep working** — do not regress existing behaviour.
2. **`parse_arb_docx` must not require the file to be on disk** — accept a `BytesIO`-like object.
3. **Mixed casing** — section names must match the exact case of the constants.
4. **Empty subsection** — parser must accept a section header followed by no body text without raising.
5. **Tables containing merged cells** — generator must not emit merged cells (parser assumes a flat grid).
6. **Unicode** — generator must write UTF-8 content; parser must read UTF-8.
7. **Frontmatter / footers** — generator must not add page numbers or running headers that would break section detection.
8. **Large files** — generator stays under 200 KB; parser handles ≥50-page docs by streaming.
9. **`.docx` upload with `.PDF` extension by mistake** — `app.py` returns HTTP 400 with a clear message.
10. **`python-docx` not installed** — `app.py` returns HTTP 500 with `ImportError` text once.

## Acceptance criteria

- [ ] `python back-end/file_processing/build_sample_asd.py` writes `sample_asd.docx` deterministically.
- [ ] `parse_arb_docx` returns a dict containing keys: every summary subheader, every requirement subheader, `Proposed Solution`, `EC2 Sizing/Specifications`, `On-Prem Servers Sizing/Specification`, `Deployment Details`.
- [ ] `EC2 Sizing/Specifications` is a list of dicts of length 2.
- [ ] `On-Prem Servers Sizing/Specification` is a list of dicts of length 2.
- [ ] `Deployment Details` is a list of dicts of length 1.
- [ ] `app.py` dispatches on extension.
- [ ] Document content is fictional (no real customer names, IPs, or proprietary data).
