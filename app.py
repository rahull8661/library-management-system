"""
Library Management System
--------------------------
A simple full-stack college project built with:
  - Python (Flask) for the backend
  - SQLite for the database
  - HTML/CSS (Jinja2 templates) for the frontend

Run with:  python app.py
Then open: http://127.0.0.1:5000
Default admin login -> username: admin | password: admin123
Demo student login  -> username: aisha | password: student123
"""

from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import sqlite3
import os
import uuid

app = Flask(__name__)
app.secret_key = "change-this-secret-key-in-production"  # needed for session/flash

DB_NAME = os.path.join(os.path.dirname(__file__), "library.db")
FINE_PER_DAY = 5          # rupees fined per day overdue
LOAN_PERIOD_DAYS = 14     # number of days a book can be borrowed for
MAX_ACTIVE_LOANS = 2      # how many books a student can have out at once

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
MAX_PDF_SIZE_MB = 25
app.config["MAX_CONTENT_LENGTH"] = MAX_PDF_SIZE_MB * 1024 * 1024
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def save_pdf(file_storage):
    """Save an uploaded PDF with a random filename. Returns the stored filename, or None."""
    if not file_storage or file_storage.filename == "":
        return None
    original = secure_filename(file_storage.filename)
    if not original.lower().endswith(".pdf"):
        raise ValueError("Only PDF files are allowed.")
    stored_name = f"{uuid.uuid4().hex}.pdf"
    file_storage.save(os.path.join(UPLOAD_FOLDER, stored_name))
    return stored_name


# ---------------------------------------------------------------------------
# DATABASE HELPERS
# ---------------------------------------------------------------------------

def get_db():
    """Open a new database connection with rows returned as dict-like objects."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Real stock = total copies minus copies currently on loan, worked out from the
# transactions table every time. This can never get out of step between accounts.
LIVE_BOOKS_SQL = """
    SELECT b.id, b.book_code, b.title, b.author, b.isbn, b.category,
           b.total_copies, b.pdf_filename,
           MAX(b.total_copies - (
               SELECT COUNT(*) FROM transactions t
               WHERE t.book_id = b.id AND t.return_date IS NULL
           ), 0) AS available_copies
    FROM books b
"""


def sync_available(conn, book_id):
    """Keep the stored stock column equal to the real number."""
    conn.execute("""
        UPDATE books SET available_copies = MAX(total_copies - (
            SELECT COUNT(*) FROM transactions
            WHERE book_id = books.id AND return_date IS NULL), 0)
        WHERE id = ?
    """, (book_id,))


@app.after_request
def no_cache_for_pages(response):
    """Never let the browser show an old copy of a page (so stock is always fresh)."""
    if response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def init_db():
    """Create tables (if they don't already exist) and seed default accounts."""
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_code TEXT UNIQUE,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            isbn TEXT,
            category TEXT,
            total_copies INTEGER NOT NULL DEFAULT 1,
            available_copies INTEGER NOT NULL DEFAULT 1,
            pdf_filename TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            username TEXT UNIQUE,
            password TEXT,
            joined_on TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            issue_date TEXT NOT NULL,
            due_date TEXT NOT NULL,
            return_date TEXT,
            fine INTEGER DEFAULT 0,
            FOREIGN KEY (book_id) REFERENCES books (id),
            FOREIGN KEY (member_id) REFERENCES members (id)
        )
    """)

    # Seed a default admin account if none exists yet
    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        cur.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)",
            ("admin", generate_password_hash("admin123"))
        )

    # Seed a couple of sample books so the app isn't empty on first run
    cur.execute("SELECT COUNT(*) FROM books")
    if cur.fetchone()[0] == 0:
        sample_books = [
            ("BK001", "The Pragmatic Programmer", "Andrew Hunt", "9780135957059", "Technology", 3, 3),
            ("BK002", "Introduction to Algorithms", "Thomas H. Cormen", "9780262046305", "Technology", 2, 2),
            ("BK003", "Sapiens", "Yuval Noah Harari", "9780062316097", "History", 4, 4),
            ("BK004", "Wings of Fire", "A.P.J. Abdul Kalam", "9788173711466", "Biography", 2, 2),
        ]
        cur.executemany(
            "INSERT INTO books (book_code, title, author, isbn, category, total_copies, available_copies) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            sample_books
        )

    # Seed a demo student account so the student portal can be tried immediately
    cur.execute("SELECT COUNT(*) FROM members")
    if cur.fetchone()[0] == 0:
        cur.execute(
            "INSERT INTO members (name, email, phone, username, password, joined_on) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Aisha Khan", "aisha@example.com", "9876543210", "aisha",
             generate_password_hash("student123"), datetime.now().strftime("%Y-%m-%d"))
        )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# AUTH HELPERS
# ---------------------------------------------------------------------------

def login_required(view_func):
    """Decorator that redirects to login if the admin isn't authenticated."""
    def wrapper(*args, **kwargs):
        if "user" not in session:
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


def student_login_required(view_func):
    """Decorator that redirects to the student login if not authenticated."""
    def wrapper(*args, **kwargs):
        if "student_id" not in session:
            flash("Please log in first.", "error")
            return redirect(url_for("student_login"))
        return view_func(*args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


# ---------------------------------------------------------------------------
# PORTAL / AUTH ROUTES
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    if "user" in session:
        return redirect(url_for("dashboard"))
    if "student_id" in session:
        return redirect(url_for("student_dashboard"))
    return render_template("portal.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session["user"] = username
            flash(f"Welcome back, {username}!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db()
    total_books = conn.execute("SELECT COALESCE(SUM(total_copies), 0) FROM books").fetchone()[0]
    total_members = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
    books_issued = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE return_date IS NULL"
    ).fetchone()[0]
    overdue = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE return_date IS NULL AND due_date < ?",
        (datetime.now().strftime("%Y-%m-%d"),)
    ).fetchone()[0]
    recent = conn.execute("""
        SELECT t.*, b.title AS book_title, b.book_code AS book_code, m.name AS member_name
        FROM transactions t
        JOIN books b ON t.book_id = b.id
        JOIN members m ON t.member_id = m.id
        ORDER BY t.id DESC LIMIT 5
    """).fetchall()
    conn.close()

    return render_template(
        "dashboard.html",
        total_books=total_books,
        total_members=total_members,
        books_issued=books_issued,
        overdue=overdue,
        recent=recent
    )


# ---------------------------------------------------------------------------
# BOOKS
# ---------------------------------------------------------------------------

@app.route("/books")
@login_required
def books():
    query = request.args.get("q", "").strip()
    conn = get_db()
    if query:
        like = f"%{query}%"
        rows = conn.execute(
            LIVE_BOOKS_SQL + " WHERE b.title LIKE ? OR b.author LIKE ? OR b.category LIKE ? "
            "OR b.book_code LIKE ? ORDER BY b.title", (like, like, like, like)
        ).fetchall()
    else:
        rows = conn.execute(LIVE_BOOKS_SQL + " ORDER BY b.title").fetchall()
    conn.close()
    return render_template("books.html", books=rows, query=query)


@app.route("/books/add", methods=["GET", "POST"])
@login_required
def add_book():
    if request.method == "POST":
        book_code = request.form["book_code"].strip()
        title = request.form["title"].strip()
        author = request.form["author"].strip()
        isbn = request.form.get("isbn", "").strip()
        category = request.form.get("category", "").strip()
        copies = int(request.form.get("copies", 1))

        try:
            pdf_filename = save_pdf(request.files.get("pdf_file"))
        except ValueError as e:
            flash(str(e), "error")
            return render_template("add_book.html", form=request.form)

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO books (book_code, title, author, isbn, category, total_copies, "
                "available_copies, pdf_filename) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (book_code, title, author, isbn, category, copies, copies, pdf_filename)
            )
            conn.commit()
            flash(f'"{title}" was added to the catalogue.', "success")
            return redirect(url_for("books"))
        except sqlite3.IntegrityError:
            flash(f'Book ID "{book_code}" is already in use — pick another.', "error")
            return render_template("add_book.html", form=request.form)
        finally:
            conn.close()

    return render_template("add_book.html", form={})


@app.route("/books/<int:book_id>/edit", methods=["GET", "POST"])
@login_required
def edit_book(book_id):
    conn = get_db()
    book = conn.execute(LIVE_BOOKS_SQL + " WHERE b.id = ?", (book_id,)).fetchone()

    if book is None:
        conn.close()
        flash("Book not found.", "error")
        return redirect(url_for("books"))

    if request.method == "POST":
        book_code = request.form["book_code"].strip()
        title = request.form["title"].strip()
        author = request.form["author"].strip()
        isbn = request.form.get("isbn", "").strip()
        category = request.form.get("category", "").strip()
        new_total = int(request.form.get("copies", 1))
        remove_pdf = request.form.get("remove_pdf") == "1"

        # Keep available_copies consistent when total_copies changes
        issued_out = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE book_id = ? AND return_date IS NULL",
            (book_id,)
        ).fetchone()[0]
        new_available = max(new_total - issued_out, 0)

        try:
            new_pdf_filename = save_pdf(request.files.get("pdf_file"))
        except ValueError as e:
            flash(str(e), "error")
            conn.close()
            return render_template("edit_book.html", book=book)

        old_pdf_path = None
        pdf_filename = book["pdf_filename"]
        if new_pdf_filename:
            if book["pdf_filename"]:
                old_pdf_path = os.path.join(UPLOAD_FOLDER, book["pdf_filename"])
            pdf_filename = new_pdf_filename
        elif remove_pdf and book["pdf_filename"]:
            old_pdf_path = os.path.join(UPLOAD_FOLDER, book["pdf_filename"])
            pdf_filename = None

        try:
            conn.execute(
                "UPDATE books SET book_code=?, title=?, author=?, isbn=?, category=?, total_copies=?, "
                "available_copies=?, pdf_filename=? WHERE id=?",
                (book_code, title, author, isbn, category, new_total, new_available, pdf_filename, book_id)
            )
            conn.commit()
            if old_pdf_path and os.path.exists(old_pdf_path):
                os.remove(old_pdf_path)
            flash(f'"{title}" was updated.', "success")
            return redirect(url_for("books"))
        except sqlite3.IntegrityError:
            flash(f'Book ID "{book_code}" is already in use — pick another.', "error")
        finally:
            conn.close()
        return render_template("edit_book.html", book=book)

    conn.close()
    return render_template("edit_book.html", book=book)


@app.route("/books/<int:book_id>/delete", methods=["POST"])
@login_required
def delete_book(book_id):
    conn = get_db()
    book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    active_loan = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE book_id = ? AND return_date IS NULL",
        (book_id,)
    ).fetchone()[0]

    if active_loan > 0:
        flash("Can't delete this book — a copy is currently issued to a member.", "error")
    else:
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        conn.commit()
        if book and book["pdf_filename"]:
            pdf_path = os.path.join(UPLOAD_FOLDER, book["pdf_filename"])
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
        flash("Book removed from the catalogue.", "success")

    conn.close()
    return redirect(url_for("books"))


@app.route("/books/<int:book_id>/read")
def read_book_pdf(book_id):
    # Admin can always view any book's PDF
    if "user" in session:
        pass
    # A student can only view it if they currently have that book on loan
    elif "student_id" in session:
        conn = get_db()
        has_active_loan = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE member_id = ? AND book_id = ? AND return_date IS NULL",
            (session["student_id"], book_id)
        ).fetchone()[0]
        conn.close()
        if has_active_loan == 0:
            flash("Borrow this book first to read it.", "error")
            return redirect(url_for("student_books"))
    else:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    conn = get_db()
    book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    conn.close()

    if book is None or not book["pdf_filename"]:
        abort(404)

    return send_from_directory(
        UPLOAD_FOLDER,
        book["pdf_filename"],
        as_attachment=False,
        download_name=f"{book['title']}.pdf"
    )


# ---------------------------------------------------------------------------
# MEMBERS
# ---------------------------------------------------------------------------

@app.route("/members")
@login_required
def members():
    conn = get_db()
    rows = conn.execute("SELECT * FROM members ORDER BY name").fetchall()
    conn.close()
    return render_template("members.html", members=rows)


@app.route("/members/add", methods=["GET", "POST"])
@login_required
def add_member():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        username = request.form.get("username", "").strip() or None
        password = request.form.get("password", "").strip()
        joined_on = datetime.now().strftime("%Y-%m-%d")
        hashed = generate_password_hash(password) if (username and password) else None

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO members (name, email, phone, username, password, joined_on) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, email, phone, username, hashed, joined_on)
            )
            conn.commit()
            flash(f"{name} was added as a member.", "success")
            return redirect(url_for("members"))
        except sqlite3.IntegrityError:
            flash("That username is already taken — pick another.", "error")
            return render_template("add_member.html", form=request.form)
        finally:
            conn.close()

    return render_template("add_member.html", form={})


@app.route("/members/<int:member_id>/delete", methods=["POST"])
@login_required
def delete_member(member_id):
    conn = get_db()
    active_loan = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE member_id = ? AND return_date IS NULL",
        (member_id,)
    ).fetchone()[0]

    if active_loan > 0:
        flash("Can't remove this member — they still have a book issued.", "error")
    else:
        conn.execute("DELETE FROM members WHERE id = ?", (member_id,))
        conn.commit()
        flash("Member removed.", "success")

    conn.close()
    return redirect(url_for("members"))


# ---------------------------------------------------------------------------
# ISSUE / RETURN BOOKS (ADMIN)
# ---------------------------------------------------------------------------

@app.route("/issue", methods=["GET", "POST"])
@login_required
def issue_book():
    conn = get_db()

    if request.method == "POST":
        book_id = int(request.form["book_id"])
        member_id = int(request.form["member_id"])

        conn.execute("BEGIN IMMEDIATE")  # lock, so the last copy can't be given out twice
        book = conn.execute(LIVE_BOOKS_SQL + " WHERE b.id = ?", (book_id,)).fetchone()

        if book is None or book["available_copies"] < 1:
            flash("That book is not currently available.", "error")
        else:
            issue_date = datetime.now()
            due_date = issue_date + timedelta(days=LOAN_PERIOD_DAYS)

            conn.execute(
                "INSERT INTO transactions (book_id, member_id, issue_date, due_date) "
                "VALUES (?, ?, ?, ?)",
                (book_id, member_id, issue_date.strftime("%Y-%m-%d"), due_date.strftime("%Y-%m-%d"))
            )
            sync_available(conn, book_id)
            conn.commit()
            flash(f'"{book["title"]}" issued. Due back on {due_date.strftime("%d %b %Y")}.', "success")
        conn.close()
        return redirect(url_for("issue_book"))

    available_books = conn.execute(
        "SELECT * FROM (" + LIVE_BOOKS_SQL + ") WHERE available_copies > 0 ORDER BY title"
    ).fetchall()
    all_members = conn.execute("SELECT * FROM members ORDER BY name").fetchall()
    conn.close()

    return render_template("issue_book.html", books=available_books, members=all_members)


@app.route("/return", methods=["GET", "POST"])
@login_required
def return_book():
    conn = get_db()

    if request.method == "POST":
        transaction_id = int(request.form["transaction_id"])
        txn = conn.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()

        if txn is None or txn["return_date"] is not None:
            flash("This loan has already been closed.", "error")
        else:
            return_date = datetime.now()
            due_date = datetime.strptime(txn["due_date"], "%Y-%m-%d")
            days_late = max((return_date - due_date).days, 0)
            fine = days_late * FINE_PER_DAY

            conn.execute(
                "UPDATE transactions SET return_date = ?, fine = ? WHERE id = ?",
                (return_date.strftime("%Y-%m-%d"), fine, transaction_id)
            )
            sync_available(conn, txn["book_id"])
            conn.commit()

            if fine > 0:
                flash(f"Book returned {days_late} day(s) late. Fine: ₹{fine}.", "error")
            else:
                flash("Book returned on time. No fine.", "success")

        conn.close()
        return redirect(url_for("return_book"))

    active_loans = conn.execute("""
        SELECT t.*, b.title AS book_title, b.book_code AS book_code, m.name AS member_name
        FROM transactions t
        JOIN books b ON t.book_id = b.id
        JOIN members m ON t.member_id = m.id
        WHERE t.return_date IS NULL
        ORDER BY t.due_date
    """).fetchall()
    conn.close()

    today = datetime.now().strftime("%Y-%m-%d")
    return render_template("return_book.html", loans=active_loans, today=today)


@app.route("/transactions")
@login_required
def transactions():
    conn = get_db()
    rows = conn.execute("""
        SELECT t.*, b.title AS book_title, b.book_code AS book_code, m.name AS member_name
        FROM transactions t
        JOIN books b ON t.book_id = b.id
        JOIN members m ON t.member_id = m.id
        ORDER BY t.id DESC
    """).fetchall()
    conn.close()
    return render_template("transactions.html", transactions=rows, today=datetime.now().strftime("%Y-%m-%d"))


# ---------------------------------------------------------------------------
# STUDENT PORTAL — AUTH
# ---------------------------------------------------------------------------

@app.route("/student/signup", methods=["GET", "POST"])
def student_signup():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        username = request.form["username"].strip()
        password = request.form["password"]
        joined_on = datetime.now().strftime("%Y-%m-%d")

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO members (name, email, phone, username, password, joined_on) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, email, phone, username, generate_password_hash(password), joined_on)
            )
            conn.commit()
            flash("Account created — log in to continue.", "success")
            return redirect(url_for("student_login"))
        except sqlite3.IntegrityError:
            flash("That username is already taken — pick another.", "error")
            return render_template("student_signup.html", form=request.form)
        finally:
            conn.close()

    return render_template("student_signup.html", form={})


@app.route("/student/login", methods=["GET", "POST"])
def student_login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        conn = get_db()
        member = conn.execute(
            "SELECT * FROM members WHERE username = ?", (username,)
        ).fetchone()
        conn.close()

        if member and member["password"] and check_password_hash(member["password"], password):
            session["student_id"] = member["id"]
            session["student_name"] = member["name"]
            flash(f"Welcome back, {member['name']}!", "success")
            return redirect(url_for("student_dashboard"))

        flash("Invalid username or password.", "error")

    return render_template("student_login.html")


@app.route("/student/logout")
def student_logout():
    session.pop("student_id", None)
    session.pop("student_name", None)
    flash("You have been logged out.", "success")
    return redirect(url_for("student_login"))


# ---------------------------------------------------------------------------
# STUDENT PORTAL — DASHBOARD
# ---------------------------------------------------------------------------

@app.route("/student/dashboard")
@student_login_required
def student_dashboard():
    member_id = session["student_id"]
    conn = get_db()

    active_loans = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE member_id = ? AND return_date IS NULL",
        (member_id,)
    ).fetchone()[0]

    overdue = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE member_id = ? AND return_date IS NULL AND due_date < ?",
        (member_id, datetime.now().strftime("%Y-%m-%d"))
    ).fetchone()[0]

    total_fines = conn.execute(
        "SELECT COALESCE(SUM(fine), 0) FROM transactions WHERE member_id = ? AND return_date IS NOT NULL",
        (member_id,)
    ).fetchone()[0]

    loans = conn.execute("""
        SELECT t.*, b.title AS book_title, b.book_code AS book_code, b.pdf_filename AS pdf_filename
        FROM transactions t JOIN books b ON t.book_id = b.id
        WHERE t.member_id = ? AND t.return_date IS NULL
        ORDER BY t.due_date
    """, (member_id,)).fetchall()

    conn.close()

    return render_template(
        "student_dashboard.html",
        active_loans=active_loans,
        overdue=overdue,
        total_fines=total_fines,
        max_loans=MAX_ACTIVE_LOANS,
        loans=loans,
        today=datetime.now().strftime("%Y-%m-%d")
    )


# ---------------------------------------------------------------------------
# STUDENT PORTAL — BROWSE & SELF-ISSUE
# ---------------------------------------------------------------------------

@app.route("/student/books")
@student_login_required
def student_books():
    member_id = session["student_id"]
    query = request.args.get("q", "").strip()
    conn = get_db()

    if query:
        like = f"%{query}%"
        rows = conn.execute(
            LIVE_BOOKS_SQL + " WHERE b.title LIKE ? OR b.author LIKE ? OR b.category LIKE ? "
            "OR b.book_code LIKE ? ORDER BY b.title", (like, like, like, like)
        ).fetchall()
    else:
        rows = conn.execute(LIVE_BOOKS_SQL + " ORDER BY b.title").fetchall()

    active_loans = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE member_id = ? AND return_date IS NULL",
        (member_id,)
    ).fetchone()[0]

    already_borrowed = {
        row["book_id"] for row in conn.execute(
            "SELECT book_id FROM transactions WHERE member_id = ? AND return_date IS NULL",
            (member_id,)
        ).fetchall()
    }

    conn.close()

    return render_template(
        "student_books.html",
        books=rows,
        query=query,
        active_loans=active_loans,
        max_loans=MAX_ACTIVE_LOANS,
        already_borrowed=already_borrowed
    )


@app.route("/student/books/<int:book_id>/issue", methods=["POST"])
@student_login_required
def student_issue(book_id):
    member_id = session["student_id"]
    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")  # lock, so two students can't take the last copy at once

    book = conn.execute(LIVE_BOOKS_SQL + " WHERE b.id = ?", (book_id,)).fetchone()

    active_loans = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE member_id = ? AND return_date IS NULL",
        (member_id,)
    ).fetchone()[0]

    already_has_it = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE member_id = ? AND book_id = ? AND return_date IS NULL",
        (member_id, book_id)
    ).fetchone()[0]

    if book is None or book["available_copies"] < 1:
        flash("That book is not currently available.", "error")
    elif already_has_it > 0:
        flash("You already have a copy of that book on loan.", "error")
    elif active_loans >= MAX_ACTIVE_LOANS:
        flash(f"You've reached the limit of {MAX_ACTIVE_LOANS} books on loan at once.", "error")
    else:
        issue_date = datetime.now()
        due_date = issue_date + timedelta(days=LOAN_PERIOD_DAYS)

        conn.execute(
            "INSERT INTO transactions (book_id, member_id, issue_date, due_date) VALUES (?, ?, ?, ?)",
            (book_id, member_id, issue_date.strftime("%Y-%m-%d"), due_date.strftime("%Y-%m-%d"))
        )
        sync_available(conn, book_id)
        conn.commit()
        flash(f'"{book["title"]}" is yours. Due back on {due_date.strftime("%d %b %Y")}.', "success")

    conn.close()
    return redirect(url_for("student_books"))


# ---------------------------------------------------------------------------
# STUDENT PORTAL — MY LOANS & SELF-RETURN
# ---------------------------------------------------------------------------

@app.route("/student/loans")
@student_login_required
def student_loans():
    member_id = session["student_id"]
    conn = get_db()

    active = conn.execute("""
        SELECT t.*, b.title AS book_title, b.book_code AS book_code, b.pdf_filename AS pdf_filename
        FROM transactions t JOIN books b ON t.book_id = b.id
        WHERE t.member_id = ? AND t.return_date IS NULL
        ORDER BY t.due_date
    """, (member_id,)).fetchall()

    history = conn.execute("""
        SELECT t.*, b.title AS book_title, b.book_code AS book_code
        FROM transactions t JOIN books b ON t.book_id = b.id
        WHERE t.member_id = ? AND t.return_date IS NOT NULL
        ORDER BY t.id DESC
    """, (member_id,)).fetchall()

    conn.close()

    return render_template(
        "student_loans.html",
        active=active,
        history=history,
        today=datetime.now().strftime("%Y-%m-%d")
    )


@app.route("/student/loans/<int:transaction_id>/return", methods=["POST"])
@student_login_required
def student_return(transaction_id):
    member_id = session["student_id"]
    conn = get_db()

    txn = conn.execute(
        "SELECT * FROM transactions WHERE id = ? AND member_id = ?",
        (transaction_id, member_id)
    ).fetchone()

    if txn is None or txn["return_date"] is not None:
        flash("That loan couldn't be found.", "error")
    else:
        return_date = datetime.now()
        due_date = datetime.strptime(txn["due_date"], "%Y-%m-%d")
        days_late = max((return_date - due_date).days, 0)
        fine = days_late * FINE_PER_DAY

        conn.execute(
            "UPDATE transactions SET return_date = ?, fine = ? WHERE id = ?",
            (return_date.strftime("%Y-%m-%d"), fine, transaction_id)
        )
        sync_available(conn, txn["book_id"])
        conn.commit()

        if fine > 0:
            flash(f"Returned {days_late} day(s) late. Fine: ₹{fine}.", "error")
        else:
            flash("Returned on time. No fine.", "success")

    conn.close()
    return redirect(url_for("student_loans"))


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
