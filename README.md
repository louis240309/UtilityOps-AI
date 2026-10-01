# UtilityOps AI - Phase 3

AI-powered utility bill management platform.

Phase 3 introduces the **Bill Management UI and Document Upload** workflow to UtilityOps AI, allowing supervisors to submit bills with image/PDF statements, persisting records in SQLite with initial `VERIFY` status, providing interactive bill lists and detailed statement inspections, and enabling admin oversight.

---

## Project Structure

```text
Utilite/
├── app.py                  # Core Flask application and route handling
├── database.py             # SQLite data layer, schema, and idempotent seeding
├── requirements.txt        # Python package dependencies
├── .env.example            # Environment variables template
├── .gitignore              # Git ignore rules
├── README.md               # Setup and execution guide
├── uploads/                # Directory for uploaded bill statements (.jpg, .png, .pdf)
├── templates/
│   ├── login.html          # Modern login page with multi-role demo autofill
│   ├── dashboard.html      # Metrics dashboard with supervisor/admin workflows
│   ├── submit_bill.html    # Bill submission form with file upload support
│   ├── bills.html          # Chronological bills overview table
│   └── bill_details.html   # Detailed statement view with safe file download
└── static/
    ├── css/
    │   └── style.css       # Design system, metrics grid, tables, and form styles
    └── js/
        └── main.js         # Client-side micro-interactions
```

---

## Demo Credentials

| Role | Username | Password | Access Capabilities |
| :--- | :--- | :--- | :--- |
| **Admin** | `admin` | `admin123` | Dashboard metrics, Review Attention Queue, All Bills, Bill Details |
| **Supervisor** | `supervisor` | `super123` | Dashboard metrics, Submit Bills, All Bills, Bill Details |

---

## Phase 3 Features

- **Dynamic Dashboard Metrics**: Real-time SQLite counts for *Total Bills*, *Processed*, *Verification Required*, and *Anomalies*.
- **Admin Attention Queue**: Dedicated table on the admin dashboard highlighting statements queued for verification (`VERIFY` / `pending`).
- **Bill Submission Workflow**:
  - Store selection dropdown dynamically populated from SQLite `stores`.
  - Utility type classification (`Electricity`, `Water`, `Gas`, `Diesel`).
  - Required billing period and optional fields (`bill_number`, `units`, `unit_rate`, `amount`, `bill_date`).
  - Secure document uploads supporting `.jpg`, `.jpeg`, `.png`, and `.pdf`.
  - Initial intake status set to `VERIFY`.
- **Chronological Bills View**: Filterable list sorted newest-first with formatted currency/units and graceful `N/A` handling for omitted fields.
- **Bill Statement Details**: Comprehensive breakdown of store details, consumption metrics, and a secure download link for attached statement files.

---

## Setup & Running Instructions

### 1. Create and Activate a Virtual Environment

```bash
# Create virtual environment
python3 -m venv .venv

# Activate virtual environment
# On macOS / Linux:
source .venv/bin/activate
# On Windows (cmd/powershell):
# .venv\Scripts\activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. (Optional) Configure Environment Variables

```bash
cp .env.example .env
```

### 4. Run the Application

```bash
python app.py
```

Open your browser and navigate to:
```
http://127.0.0.1:5001/
```
(or `http://127.0.0.1:5000/` if available).
