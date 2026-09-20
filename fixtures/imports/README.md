# IntelliQ v4 import fixtures

These files are the live Warsaw demo pack for import and knowledge review.
They include five projects at real street coordinates, sixteen tasks, eight
worker references, seven specialty references, additive assignments, remaining
effort and dependency assumptions, and one confirmed donor-to-target transfer.

`timecueMemberId` and `timecueSpecialtyId` values must be exact IDs from the
authorized Timecue organization. IntelliQ never creates accounts or silently
matches by name. The committed `portfolio.xlsx` IDs are the current local demo
organization's members and specialties; regenerate them if the org changes.

`weatherCoverage=synthetic_demo_mock` is a display label only. The task rows
do not contain confirmed weather rules, so the demo does not block on absent
weather keys. Live weather and routing use the project coordinates.

Files:

- `template.xlsx` — blank workbook with the six supported sheets.
- `portfolio.xlsx` — populated Warsaw workbook generated from the CSVs.
- `mock-portfolio.xlsx` — distant-city rehearsal pack (Harbor Clinic, Nowy Targ
  School Gym, Widzew Cold Storage). Member and specialty IDs are placeholders.
- `mock/*.csv` — sheet-equivalent source files for `mock-portfolio.xlsx`.
- `projects.csv`, `tasks.csv`, `workers.csv`, `skills.csv`, `assignments.csv`,
  `planning.csv` — deterministic sheet-equivalent source files.
- `delivery_note.txt` — Dobra 56 delivery note.
- `annotated_field_note.png` — generated image fixture.
- `../files/` — ready-to-use import workbook plus knowledge PDFs/DOCX for the
  live demo. See `fixtures/files/README.md`.

The binary files can be regenerated with:

```sh
python fixtures/imports/generate_demo.py
```

The same story is created in Timecue by:

```sh
pnpm seed:timecue
```
