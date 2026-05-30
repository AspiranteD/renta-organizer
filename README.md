# renta-organizer

Automated invoice and expense organizer for the Spanish income tax return (Declaración de la Renta / IRPF).

Scans your Gmail accounts for invoices, parses your bank statements, classifies every expense into fiscal categories using AI, and generates an organized Excel + PDF folder ready to hand to your accountant.

## What it does

```
Gmail (N accounts) ──► Download invoice PDFs ──► Extract data (text + AI vision)
                                                        │
Bank CSVs ──────────► Parse & normalize ──────► Classify expenses (LLM)
                                                        │
                                                Cross-reference invoices ↔ bank movements
                                                        │
                                        ┌───────────────┼───────────────┐
                                        ▼               ▼               ▼
                                  Excel summary    Organized PDFs   Google Drive
                                  (by category)    (by category)    (optional)
```

## Features

- **Multi-account Gmail scanning** — searches all your email accounts for PDF invoices and receipts
- **Bank statement parsing** — supports Sabadell, BBVA, Bankinter, Revolut, and generic CSV/Excel formats
- **AI-powered classification** — GPT-4o-mini classifies each expense into fiscal categories (medical, education, donations, etc.)
- **High recall mode** — when in doubt, marks expenses as "potentially deductible" so you never miss a deduction
- **Invoice ↔ transaction matching** — cross-references invoices with bank movements by amount and date
- **PDF data extraction** — uses text parsing first, falls back to GPT-4o vision for scanned/image PDFs
- **Organized output** — Excel with tabs per category + PDFs sorted into folders
- **Google Drive upload** — optionally uploads everything to your Drive
- **Reusable year after year** — just change `--year 2026`

## Fiscal categories

| Category | Examples |
|----------|----------|
| Sanitario | Medical, dental, pharmacy, optical, physiotherapy |
| Formación | Courses, books, certifications |
| Donaciones | NGOs, foundations, charities |
| Seguros | Health insurance, life insurance |
| Suministros | Electricity, water, gas, internet, mobile |
| Transporte | Fuel, tolls, public transport |
| Vivienda | Home repairs, energy efficiency |
| Potencialmente deducible | Unclear — flagged for manual review |
| No deducible | Leisure, restaurants, groceries |

## Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USER/renta-organizer.git
cd renta-organizer
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Gmail OAuth credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or reuse an existing one)
3. Enable **Gmail API** and **Google Drive API**
4. Create OAuth 2.0 credentials (Desktop app)
5. Download the JSON and save as `gmail_oauth_credentials.json` in the project root

### 3. Configure email accounts

```bash
cp config/emails.yaml.example config/emails.yaml
```

Edit `config/emails.yaml` with your Gmail accounts:

```yaml
accounts:
  - email: your-email@gmail.com
    label: personal
    token_path: tokens/personal.json
  - email: work@gmail.com
    label: work
    token_path: tokens/work.json
```

### 4. Environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
OPENAI_API_KEY=sk-...
DEFAULT_YEAR=2025
DRIVE_UPLOAD=false
```

### 5. Bank statements

Download CSV exports from your banks for the fiscal year and place them in `input/bancos/`.

Name them with the bank prefix for auto-detection:

```
input/bancos/
  sabadell_2025.csv
  bbva_movimientos_2025.csv
  revolut_2025.csv
  bankinter_2025.xlsx
```

## Usage

```bash
# Full pipeline (scan emails + parse banks + classify + generate)
python -m renta_organizer --year 2025

# Skip email scan (reuse already-downloaded PDFs)
python -m renta_organizer --year 2025 --skip-scan

# Skip bank parsing (only process invoices)
python -m renta_organizer --year 2025 --skip-bank

# Only upload existing output to Google Drive
python -m renta_organizer --year 2025 --upload-only

# Verbose logging
python -m renta_organizer --year 2025 -v
```

## Output

```
output/2025/
  resumen_fiscal_2025.xlsx      ← Main Excel for your accountant
  raw_pdfs/                     ← All downloaded PDFs (original names)
  raw_pdfs_index.csv            ← Index of all downloaded PDFs
  revision_manual.csv           ← Emails with invoice links (need manual download)
  facturas/                     ← PDFs organized by category
    sanitario/
    formacion/
    donaciones/
    suministros/
    sin_clasificar/
```

### Excel tabs

| Tab | Content |
|-----|---------|
| Resumen | Totals by category |
| Sanitario | All medical expenses with dates, amounts, invoices |
| Formación | Education expenses |
| ... | One tab per category with data |
| Todos movimientos | All bank movements normalized |
| Sin factura | Deductible expenses without a matching invoice |
| Facturas sin match | Invoices that don't match any bank movement |

## Cost

- Gmail scanning: free (API)
- Expense classification: ~$0.01 per 25 transactions (GPT-4o-mini)
- PDF text extraction: free (pdfplumber)
- PDF vision extraction: ~$0.01 per PDF (GPT-4o, only for scanned PDFs)
- Typical run (~200 transactions, ~50 PDFs): **< $0.50 total**

## Project structure

```
renta-organizer/
  config/
    emails.yaml              # Your Gmail accounts
  input/
    bancos/                  # Drop your bank CSVs here
  renta_organizer/
    __init__.py
    __main__.py              # CLI entry point
    config.py                # Settings loader
    gmail_scanner.py         # Multi-account Gmail scanner
    bank_parser.py           # Bank CSV/Excel parser
    pdf_extractor.py         # Invoice data extraction
    classifier.py            # LLM expense classifier
    cross_reference.py       # Invoice ↔ bank matching
    excel_generator.py       # Excel report generator
    drive_uploader.py        # Google Drive upload
    pipeline.py              # Main orchestration
  .env.example
  requirements.txt
  README.md
```

## Tech stack

Python · Gmail API · Google Drive API · OpenAI GPT-4o · pdfplumber · openpyxl

## License

MIT
