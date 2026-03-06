from __future__ import annotations

import datetime
import os
import json
import sqlite3
import re

from flask import Flask, redirect, render_template, request, session, url_for
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

    @app.context_processor
    def inject_globals():
        return {
            "app_name": app_name,
            "developer_name": developer_name,
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

        # Persist run + results for auditability.
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO screening_runs (user_id, job_description) VALUES (?, ?)",
                (int(user["id"]), job_description),
            )
            run_id = int(cur.lastrowid)

            cur.executemany(
                """
                INSERT INTO screening_results (
                    run_id, filename, name_guess, score, similarity,
                    skill_overlap_count, jd_skills_count,
                    matched_skills_json, skills_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """.strip(),
                [
                    (
                        run_id,
                        r.filename,
                        r.name_guess,
                        float(r.score),
                        float(r.similarity),
                        int(r.skill_overlap_count),
                        int(r.jd_skills_count),
                        json.dumps(r.matched_skills),
                        json.dumps(r.skills),
                    )
                    for r in results
                ],
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
