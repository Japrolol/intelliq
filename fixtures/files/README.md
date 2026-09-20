# Demo files for live import and knowledge

Use these during the hackathon demo. They match the Timecue seed
(`pnpm seed:timecue`) and the live import CSVs under `fixtures/imports`.

## Import spreadsheet

`import/portfolio.xlsx` is the packed workbook. In IntelliQ, use **Import spreadsheet**
and pick this file. It contains five Warsaw jobs with real street coordinates so
weather and routing can resolve:

| Project | Address | Role |
| --- | --- | --- |
| Powisle · Kamienica Dobra 56 | ul. Dobra 56, 00-312 Warszawa | Target, electrical blocking walls |
| Wola · Hala Kolejowa 47 | ul. Kolejowa 47, 01-210 Warszawa | Donor, shared electricians |
| Praga · Loft Zabkowska 27 | ul. Zabkowska 27, 03-736 Warszawa | Control, rooftop HVAC |
| Mokotow · Willa Pulawska 17 | ul. Pulawska 17, 02-515 Warszawa | Bathroom package, Friday 16:00 handover |
| Ochota · Przychodnia Banacha 1A | ul. Banacha 1A, 02-097 Warszawa | Clinic reception |

`import/template.xlsx` is the blank six-sheet template.
`import/mock-portfolio.xlsx` is the Gdańsk / Kraków / Łódź rehearsal pack with
placeholder member IDs.

CSV copies of the live pack are in this folder for inspection. Source of truth
remains `fixtures/imports/*.csv`.

`timecueMemberId` and `timecueSpecialtyId` must match the signed-in Timecue
organization. The committed IDs are the current demo org; regenerate them if
the org changes.

## Knowledge uploads

Upload these into the knowledge graph after the portfolio is imported/seeded.
They are synthetic demo documents labelled with real site names. Confirm claims
before they change planning.

| File | What it should extract |
| --- | --- |
| `knowledge/powisle-cable-tray-delivery.pdf` | 18 cable trays delivered |
| `knowledge/powisle-electrical-blocker.pdf` | walls cannot be closed |
| `knowledge/powisle-inspection-punch.pdf` | inspection still blocking walls |
| `knowledge/wola-weather-stop.pdf` | heavy rain stopped exterior access |
| `knowledge/wola-material-arrived.pdf` | plasterboard arrived, wait cleared |
| `knowledge/praga-rooftop-weather.pdf` | rooftop HVAC delayed by weather |
| `knowledge/praga-client-change.pdf` | possible partition change, unconfirmed |
| `knowledge/banacha-access-permit.pdf` | permit approved, delay cleared |
| `knowledge/mokotow-quality-rework.pdf` | slower progress, Friday handover unchanged |
| `knowledge/powisle-delivery-note.docx` | same cable-tray delivery as Word |
| `knowledge/powisle-delivery-note.txt` | plain-text delivery note |
| `knowledge/powisle-whatsapp-dump.txt` | prompt-injection junk, ignore |
| `knowledge/annotated_field_note.png` | image fixture |

Regenerate binaries:

```sh
python fixtures/imports/generate_demo.py
```
