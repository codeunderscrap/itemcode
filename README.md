# Item Code Studio 🏭

A beautiful, robust web application designed to standardise and manage 17-character item codes for MiniMines. Item Code Studio acts as the central source of truth for the company's item master, enforcing a strict hierarchical taxonomy while providing an intuitive UI for code generation, approval workflows, and dictionary management.

![Item Code Studio UI Placeholder](assets/ui-preview.png)

## 🌟 Key Features

*   **Strict Taxonomy Enforcer:** Guarantees every generated code adheres strictly to the `[HEAD][SUB-HEAD][GROUP][SPEC1-5][VENDOR]` schema.
*   **Centralised Dictionary:** Manage Heads, Sub-Heads, Groups, and their available Specifications all from one place.
*   **Version Control & Audit:** Every item edit creates an immutable revision history. The system logs all structural changes, group migrations, and name updates.
*   **Multi-Tier Architecture:** Built to run on a central cloud server (Tier 1), local hub (Tier 2), or local offline clients (Tier 3), all syncing against a central SQLite database.
*   **AI-Assisted Invoice Parsing:** Upload invoices (PDFs/Images) directly, and the built-in LLM pipeline automatically extracts items, matches them against your taxonomy, and proposes the correct item codes!

## 🚀 Quick Start Guide

### Prerequisites
- Python 3.10+
- (Optional but recommended) Google Chrome or Edge for the best web experience.

### 1. Installation

Clone the repository and install dependencies:

```bash
git clone https://github.com/codeunderscrap/itemcode.git
cd itemcode
pip install -r requirements.txt
```

### 2. First Run & Configuration
The application stores everything in a local SQLite file (`data/itemcode.db`). This repository includes a pre-seeded database with over 60 configured groups, so you don't need to start from scratch!

To launch the application:
```bash
python server.py
```

*   **Local Access:** Open `http://localhost:8756` in your browser.
*   **Network Access:** Open `http://<your-local-ip>:8756` from any device on the same Wi-Fi.

### 3. Account Management
There are no self-signups. An administrator must provision accounts using the CLI:

```bash
# Add an admin user
python manage.py adduser admin --name "System Admin" --admin

# List all users
python manage.py listusers

# Reset a password
python manage.py resetpw admin
```

*(Note: If you run `python server.py` on a completely fresh database, an initial `admin` account is created and the temporary password is printed to the terminal.)*

## 🛠️ How It Works (The Architecture)

Item Code Studio is designed for reliability and strict data integrity.

### The Code Grammar
Every item code is 17 characters long, parsed from right to left:
*   `HEAD (2)` - e.g. "RM" (Raw Materials)
*   `SUB (2)` - e.g. "ME" (Metal Enrich Powder)
*   `GROUP (3)` - e.g. "001" (LCO)
*   `S1-S4 (8)` - Up to 4 two-digit specifications (e.g., Grade, Dimension, Type)
*   `VENDOR (2)` - A 2-digit vendor or brand code

### The Tech Stack
*   **Backend:** Pure Python 3 using standard library modules (plus `rapidfuzz` for dictionary matching, and `paddleocr`/`pymupdf` for invoice AI).
*   **Frontend:** Vanilla JS (`app.js`, `master.js`) and pure CSS (`style.css`), designed with premium, modern web aesthetics. No heavy JS frameworks required.
*   **Database:** SQLite (`itemcode.db`) running in WAL mode, ensuring safe, concurrent reads and writes without complex database server setups.

### Smart Group Management (Retiring vs Deleting)
Because item codes are permanently issued, the system follows a **Freeze-on-First-Use** rule. 
If you need to remove an Item Group, the system will *Retire* it instead of deleting it, safely parking the sequence number for future use and preventing existing items from breaking. 

## ☁️ Deployment (Cloud/VPS)
The application can be deployed to a standard Ubuntu/Debian VPS using Caddy for automatic HTTPS and systemd for process management. All deployment scripts are located in `install/vps/`.

## 📦 Backups
Built-in tools exist to safely backup the live database and export it to Excel:
```bash
python install/backup.py run
```
