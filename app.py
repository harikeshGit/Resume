from __future__ import annotations

import datetime
import os
import sqlite3
import re
import io
from importlib import import_module

from flask import Flask, abort, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from resume_screener import (
    ats_scan_resume,
    extract_text_and_page_count_from_pdf_bytes,
    extract_text_from_pdf_bytes,
    generate_ats_optimized_resume,
    rank_resumes,
)

from db import (
    default_db_path,
    get_user_by_email,
    get_user_by_id,
    get_user_by_username,
    init_db,
)


def create_app() -> Flask:
    app = Flask(__name__)

    # For a demo project this is fine. For production, set SECRET_KEY to a strong random value.
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    app_name = os.environ.get("APP_NAME", "AI Resume Screening")
    developer_name = os.environ.get("DEVELOPER_NAME", "Harikesh Kumar")
    developer_email = os.environ.get("DEVELOPER_EMAIL", "").strip()
    github_url = os.environ.get("GITHUB_URL", "https://github.com/harikeshGit").strip()
    linkedin_url = os.environ.get(
        "LINKEDIN_URL", "https://www.linkedin.com/in/harikesh-kumar-70062a258"
    ).strip()
    instagram_url = os.environ.get("INSTAGRAM_URL", "https://www.instagram.com/rikumar6940/?hl=en").strip()
    db_path = default_db_path()
    init_db(db_path)

    def current_user():
        user_id = session.get("user_id")
        if not user_id:
            return None
        row = get_user_by_id(db_path, int(user_id))
        if not row:
            session.pop("user_id", None)
            session.pop("username", None)
            return None
        return {
            "id": int(row["id"]),
            "username": str(row["username"]),
            "email": (str(row["email"]) if row["email"] is not None else ""),
        }

    _EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    def safe_next(next_url: str | None) -> str:
        if not next_url:
            return url_for("screening")
        # Avoid open-redirects: only allow same-site relative paths.
        if next_url.startswith("/") and not next_url.startswith("//"):
            return next_url
        return url_for("screening")

    def _safe_download_stem(value: str) -> str:
        stem = re.sub(r"\.pdf$", "", (value or ""), flags=re.IGNORECASE).strip()
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_.-")
        return (stem[:80] if stem else "resume")

    def _draft_text_to_pdf_bytes(draft_text: str, *, title: str | None = None) -> io.BytesIO:
        # Import lazily to avoid editor diagnostics issues and to keep startup light.
        pagesizes = import_module("reportlab.lib.pagesizes")
        units = import_module("reportlab.lib.units")
        pdfmetrics = import_module("reportlab.pdfbase.pdfmetrics")
        canvas_mod = import_module("reportlab.pdfgen.canvas")

        letter = pagesizes.letter
        inch = units.inch
        Canvas = canvas_mod.Canvas

        buf = io.BytesIO()
        page_w, page_h = letter
        margin = 0.75 * inch
        max_w = page_w - 2 * margin
        y = page_h - margin

        body_font = "Helvetica"
        body_size = 11
        head_font = "Helvetica-Bold"
        head_size = 12
        line_h = 14

        c = Canvas(buf, pagesize=letter)

        def new_page():
            nonlocal y
            c.showPage()
            y = page_h - margin

        def ensure_space(lines: int = 1):
            nonlocal y
            if y - (lines * line_h) < margin:
                new_page()

        def wrap_line(text: str, font: str, size: int) -> list[str]:
            words = (text or "").split()
            if not words:
                return [""]
            out: list[str] = []
            cur = ""
            for w in words:
                cand = (cur + " " + w).strip()
                if pdfmetrics.stringWidth(cand, font, size) <= max_w:
                    cur = cand
                else:
                    if cur:
                        out.append(cur)
                        cur = w
                    else:
                        # Single very long token; hard-split.
                        out.append(cand[:160])
                        cur = ""
            if cur:
                out.append(cur)
            return out

        # Optional title
        if title:
            ensure_space(2)
            c.setFont(head_font, 14)
            c.drawString(margin, y, title)
            y -= line_h * 1.5

        for raw in (draft_text or "").splitlines():
            line = raw.rstrip()
            if not line.strip():
                ensure_space(1)
                y -= line_h
                continue

            is_heading = line.strip() == line.strip().upper() and len(line.strip()) <= 32
            font = head_font if is_heading else body_font
            size = head_size if is_heading else body_size
            c.setFont(font, size)

            wrapped = wrap_line(line, font, size)
            ensure_space(len(wrapped))
            for wline in wrapped:
                c.drawString(margin, y, wline)
                y -= line_h

        c.save()
        buf.seek(0)
        return buf

    @app.context_processor
    def inject_globals():
        return {
            "app_name": app_name,
            "developer_name": developer_name,
            "developer_email": developer_email,
            "github_url": github_url,
            "linkedin_url": linkedin_url,
            "instagram_url": instagram_url,
            "current_year": datetime.datetime.now().year,
            "user": current_user(),
        }

    # 16 MB default; adjust if you expect large resumes.
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))

    @app.get("/")
    def welcome():
        return render_template("welcome.html")

    @app.get("/app")
    def app_home():
        return redirect(url_for("screening"))

    @app.get("/screening")
    def screening():
        return render_template(
            "screening.html",
            results=None,
            error=None,
            job_description="",
            top_k=15,
        )

    @app.get("/ats")
    def ats():
        return render_template(
            "ats.html",
            ats_report=None,
            ats_error=None,
            ats_job_description="",
            ats_draft=None,
            ats_draft_report=None,
            ats_draft_suggestions=None,
        )

    @app.get("/auth")
    def auth():
        if current_user():
            return redirect(url_for("screening"))

        next_url = request.args.get("next") or "/screening"
        return render_template(
            "auth.html",
            error=None,
            next=next_url,
            login_username="",
            reg_username="",
        )

    @app.post("/login")
    def login():
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        next_url = request.form.get("next")
        if not username or not password:
            return render_template(
                "auth.html",
                error="Please enter username and password.",
                next=next_url or "/",
                login_username=username,
                reg_username="",
            )

        # Allow login via username OR email.
        row = get_user_by_username(db_path, username) or get_user_by_email(db_path, username)
        if not row or not check_password_hash(str(row["password_hash"]), password):
            return render_template(
                "auth.html",
                error="Invalid username or password.",
                next=next_url or "/",
                login_username=username,
                reg_username="",
            )

        session["user_id"] = int(row["id"])
        session["username"] = str(row["username"])

        # Persist login event (audit only).
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute(
                    "INSERT INTO auth_events (user_id, event_type) VALUES (?, 'login')",
                    (int(row["id"]),),
                )
        except Exception:
            # Keep UX simple; audit logging must not block auth.
            pass

        return redirect(safe_next(next_url))

    @app.post("/register")
    def register():
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""
        accept_terms = request.form.get("accept_terms")
        next_url = request.form.get("next")

        if not username or not password:
            return render_template(
                "auth.html",
                error="Please enter username and password to create an account.",
                next=next_url or "/",
                login_username="",
                reg_username=username,
            )

        if not email:
            return render_template(
                "auth.html",
                error="Please enter your Gmail address.",
                next=next_url or "/",
                login_username="",
                reg_username=username,
                reg_email=email,
            )

        if not _EMAIL_RE.match(email) or not email.endswith("@gmail.com"):
            return render_template(
                "auth.html",
                error="Please enter a valid Gmail address (example@gmail.com).",
                next=next_url or "/",
                login_username="",
                reg_username=username,
                reg_email=email,
            )

        if password != password2:
            return render_template(
                "auth.html",
                error="Passwords do not match.",
                next=next_url or "/",
                login_username="",
                reg_username=username,
                reg_email=email,
            )

        if not accept_terms:
            return render_template(
                "auth.html",
                error="Please accept the Terms & Conditions.",
                next=next_url or "/",
                login_username="",
                reg_username=username,
                reg_email=email,
            )

        if len(password) < 8:
            return render_template(
                "auth.html",
                error="Password must be at least 8 characters.",
                next=next_url or "/",
                login_username="",
                reg_username=username,
                reg_email=email,
            )

        # Basic username normalization
        if len(username) < 3:
            return render_template(
                "auth.html",
                error="Username must be at least 3 characters.",
                next=next_url or "/",
                login_username="",
                reg_username=username,
                reg_email=email,
            )

        if get_user_by_username(db_path, username):
            return render_template(
                "auth.html",
                error="Username already exists. Please login.",
                next=next_url or "/",
                login_username=username,
                reg_username="",
            )

        if get_user_by_email(db_path, email):
            return render_template(
                "auth.html",
                error="Email already exists. Please login.",
                next=next_url or "/",
                login_username=email,
                reg_username=username,
                reg_email=email,
            )

        pw_hash = generate_password_hash(password)

        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                (username, email, pw_hash),
            )
            user_id = int(cur.lastrowid)

        session["user_id"] = user_id
        session["username"] = username
        return redirect(safe_next(next_url))

    @app.post("/logout")
    def logout():
        user_id = session.get("user_id")

        # Persist logout event (audit only).
        if user_id:
            try:
                with sqlite3.connect(db_path) as conn:
                    conn.execute("PRAGMA foreign_keys = ON")
                    conn.execute(
                        "INSERT INTO auth_events (user_id, event_type) VALUES (?, 'logout')",
                        (int(user_id),),
                    )
            except Exception:
                pass

        session.pop("user_id", None)
        session.pop("username", None)
        return redirect(url_for("welcome"))

    @app.post("/screen")
    def screen():
        user = current_user()
        if not user:
            return redirect(url_for("auth", next="/screening#screen"))

        job_description = (request.form.get("job_description") or "").strip()
        top_k_raw = (request.form.get("top_k") or "15").strip()
        try:
            top_k = max(3, min(50, int(top_k_raw)))
        except ValueError:
            top_k = 15

        files = request.files.getlist("resumes")
        if not files:
            return render_template(
                "screening.html",
                results=None,
                error="Please upload at least one PDF resume.",
                job_description=job_description,
                top_k=top_k,
            )

        resumes: list[tuple[str, str]] = []
        for f in files:
            if not f or not f.filename:
                continue

            filename = f.filename
            if not filename.lower().endswith(".pdf"):
                return render_template(
                    "screening.html",
                    results=None,
                    error=f"Only PDF files are supported. Problem file: {filename}",
                    job_description=job_description,
                    top_k=top_k,
                )

            pdf_bytes = f.read()
            text = extract_text_from_pdf_bytes(pdf_bytes)
            if not text:
                # Keep it, but it will likely score low; also helps user notice scanned PDFs.
                text = ""
            resumes.append((filename, text))

        try:
            results = rank_resumes(job_description=job_description, resumes=resumes)
        except Exception as e:  # keep UX simple for a college demo
            return render_template(
                "screening.html",
                results=None,
                error=str(e),
                job_description=job_description,
                top_k=top_k,
            )

        return render_template(
            "screening.html",
            results=results,
            error=None,
            job_description=job_description,
            top_k=top_k,
        )

    @app.post("/ats-scan")
    def ats_scan():
        """Free ATS resume scan (heuristics + NLP)."""
        f = request.files.get("resume")
        ats_job_description = (request.form.get("ats_job_description") or "").strip()

        if not f or not f.filename:
            return render_template(
                "ats.html",
                ats_report=None,
                ats_error="Please upload a PDF resume to scan.",
                ats_job_description=ats_job_description,
                ats_draft=None,
                ats_draft_report=None,
                ats_draft_suggestions=None,
            )

        filename = f.filename
        if not filename.lower().endswith(".pdf"):
            return render_template(
                "ats.html",
                ats_report=None,
                ats_error="Only PDF files are supported for ATS scan.",
                ats_job_description=ats_job_description,
                ats_draft=None,
                ats_draft_report=None,
                ats_draft_suggestions=None,
            )

        pdf_bytes = f.read()
        text, page_count = extract_text_and_page_count_from_pdf_bytes(pdf_bytes)
        if not text:
            return render_template(
                "ats.html",
                ats_report=None,
                ats_error="Could not extract text from this PDF (may be scanned/image-only).",
                ats_job_description=ats_job_description,
                ats_draft=None,
                ats_draft_report=None,
                ats_draft_suggestions=None,
            )

        report = ats_scan_resume(
            filename=filename,
            resume_text=text,
            page_count=page_count,
            job_description=ats_job_description or None,
        )

        ats_draft = None
        ats_draft_report = None
        ats_draft_suggestions = None
        if int(report.score) < 85:
            ats_draft, ats_draft_report, ats_draft_suggestions = generate_ats_optimized_resume(
                filename=filename,
                resume_text=text,
                job_description=ats_job_description or None,
            )

        return render_template(
            "ats.html",
            ats_report=report,
            ats_error=None,
            ats_job_description=ats_job_description,
            ats_draft=ats_draft,
            ats_draft_report=ats_draft_report,
            ats_draft_suggestions=ats_draft_suggestions,
        )

    @app.post("/ats-draft-pdf")
    def ats_draft_pdf():
        draft = (request.form.get("draft") or "").strip()
        if not draft:
            abort(400)

        stem = _safe_download_stem(request.form.get("filename") or "")
        pdf_buf = _draft_text_to_pdf_bytes(draft, title=None)

        return send_file(
            pdf_buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"ATS_Optimized_{stem}.pdf",
        )

    return app


# Expose a module-level WSGI app for production servers like Gunicorn.
# This enables both `gunicorn app:app` (Render default patterns) and local imports.
app = create_app()


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5000")),
        debug=True,
        use_reloader=False,
    )
