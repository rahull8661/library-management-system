# Bookkeeper — Library Management System

A full-stack Library Management System built for a college project, using:

- **Backend:** Python (Flask)
- **Database:** SQLite
- **Frontend:** HTML + CSS, rendered server-side with Jinja2 templates

## Features

**Librarian (admin) portal**
- Session-based login with hashed passwords
- Dashboard with live stats
- Add / edit / delete books, each with a unique **Book ID** and an optional **PDF** upload
- Add / delete members, optionally with student-portal login credentials
- Issue / return books on behalf of any member
- Full transaction log, including Book ID

**Student portal**
- Self-service signup and login
- Browse/search the catalogue by Book ID, title, author, or category
- **Read** any book that has a PDF attached, right in the browser
- Borrow a book directly (max 2 at a time by default)
- View current loans and history, return books themselves
- Automatic ₹5/day late fine

## How to run it

```bash
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

- Librarian: `admin` / `admin123`
- Demo student: `aisha` / `student123`

## Adding a PDF to a book

When adding or editing a book, there's an optional **Book PDF** field. Upload a `.pdf` file (max 25 MB) and a **Read** link appears next to that book for both the librarian and students — it opens in a new browser tab.

PDFs are stored in an `uploads/` folder (auto-created) and are **not** tracked by git (see `.gitignore`) — don't commit large PDFs to your repository. On Render's free tier, uploaded files and the database both reset on redeploy, same as discussed for the database.

## Deploying to Render

1. Push this project to GitHub (don't commit `library.db` or `uploads/`).
2. On Render: New Web Service → connect your repo.
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`
3. `init_db()` runs automatically on import, so tables are created the moment the app starts — no extra step needed.

## Project structure

```
library_management_system/
├── app.py
├── requirements.txt
├── .gitignore
├── library.db          (auto-created)
├── uploads/             (auto-created, holds book PDFs)
├── templates/
└── static/
    └── style.css
```
