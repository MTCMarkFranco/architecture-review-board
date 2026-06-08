# POLICIES-DOC — Prompt Contract

## Intent

Produce a reproducible default Azure infrastructure policies document — `azure_policies.docx` — whose 15 section headers are recognised by `extract_policies()` (UPPERCASE lines that begin with a digit). The doc seeds the policy search index when an organisation does not yet have their own.

## Linked issue

**#17** — Default Azure infrastructure policies doc

## Inputs

- `python-docx`.
- `parsing.py:extract_policies` (treated as the canonical extractor for tests).

## Outputs

- `back-end/file_processing/build_azure_policies.py` — generator script.
- `back-end/file_processing/data/azure_policies.docx` — generated file.
- (Optional) `back-end/file_processing/parsing.py` extended with `extract_policies_docx(docx_path)` returning the same `[{"header","content"}, …]` shape.

## Section list (exact strings)

```
1. IDENTITY AND ACCESS MANAGEMENT
2. NETWORK SECURITY AND SEGMENTATION
3. DATA PROTECTION AND ENCRYPTION
4. STORAGE BEST PRACTICES
5. COMPUTE AND VM HARDENING
6. CONTAINER AND KUBERNETES SECURITY
7. SERVERLESS AND APP SERVICE GUIDANCE
8. DATABASE AND DATA PLATFORM
9. OBSERVABILITY MONITORING AND LOGGING
10. BACKUP DISASTER RECOVERY AND BUSINESS CONTINUITY
11. COST MANAGEMENT AND TAGGING
12. NAMING CONVENTIONS AND RESOURCE ORGANIZATION
13. DEVOPS CI CD AND INFRASTRUCTURE AS CODE
14. COMPLIANCE GOVERNANCE AND POLICY
15. AI AND AGENT WORKLOADS GOVERNANCE
```

Each section: 300–800 words of original, Microsoft Well-Architected-aligned guidance. Voice should match Azure documentation style without being copied.

## Edge cases & clarifications

1. **Header must be on a line by itself** — no leading whitespace, no trailing punctuation, all uppercase, starts with a digit. (`extract_policies` matches `line.isupper() and line.startswith(('0'…'9'))`.)
2. **Footer / page-number lines** — never emit text containing `INTERNAL` on a line that would collide with the filter `'INTERNAL' not in line`.
3. **Bullet glyphs** — use ASCII `-` or word `Note:` rather than Wingdings glyphs to avoid Unicode pollution.
4. **Tables** — none. Plain paragraphs only, to keep `extract_policies` line-oriented.
5. **Hyperlinks** — avoid them; if necessary, render as plain text URLs.
6. **Page count** — must stay under 50 pages at default 11pt Calibri.
7. **Reproducibility** — running the script twice produces a byte-identical file (no embedded timestamps).
8. **Source attribution** — content is original; no copy-paste from Microsoft Learn or third-party docs.
9. **Encoding** — UTF-8 throughout; no smart quotes that would break downstream JSON.
10. **Heading style** — `Heading 1` paragraph style applied so the docx renders well even though the parser keys only on text content.

## Acceptance criteria

- [ ] `python build_azure_policies.py` writes `azure_policies.docx`.
- [ ] `extract_policies` (after PDF conversion) **or** new `extract_policies_docx` returns ≥15 records with the exact section headers above.
- [ ] The file is under 50 pages (verify via `len(doc.element.body.iter('{*}sectPr'))` or page-count probe).
- [ ] No headers contain the literal string `INTERNAL`.
- [ ] No real proprietary content; original prose only.
