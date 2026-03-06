from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pdfplumber
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from skills_catalog import SKILLS


_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+.#/-]{1,}")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_RE = re.compile(r"(?x)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}")
_URL_RE = re.compile(r"(?i)\bhttps?://\S+\b")


def _normalize_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_text_and_page_count_from_pdf_bytes(pdf_bytes: bytes) -> tuple[str, int]:
    """Extract text from a PDF along with page count.

    Returns:
        (text, page_count). Text can be empty if PDF has no extractable text.
    """
    if not pdf_bytes:
        return "", 0

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages: list[str] = []
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            if page_text:
                pages.append(page_text)

        page_count = len(pdf.pages)

    return _normalize_text("\n".join(pages)), int(page_count)


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Extract text from a PDF. Returns empty string if no text."""
    text, _page_count = extract_text_and_page_count_from_pdf_bytes(pdf_bytes)
    return text


def guess_name_from_resume(text: str) -> str:
    """Best-effort name guess from the first couple of lines."""
    if not text:
        return ""

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    head = lines[:5]

    # Prefer a line that looks like a human name (2-4 words, mostly letters).
    for ln in head:
        candidate = re.sub(r"[^A-Za-z .'-]", "", ln).strip()
        if not candidate:
            continue
        parts = [p for p in candidate.split() if p]
        if 2 <= len(parts) <= 4 and all(len(p) >= 2 for p in parts):
            return candidate

    return head[0] if head else ""


def _compile_skill_patterns(skills: Iterable[str]) -> list[tuple[str, re.Pattern[str]]]:
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for s in skills:
        skill = s.strip().lower()
        if not skill:
            continue

        # Word-boundary for normal words; special-case languages like C++, C#.
        if skill in {"c++", "c#"}:
            pat = re.compile(rf"(?i)(?<!\w){re.escape(skill)}(?!\w)")
        else:
            pat = re.compile(rf"(?i)\b{re.escape(skill)}\b")

        patterns.append((skill, pat))

    # Sort longer skills first to reduce overlaps (e.g., 'machine learning' before 'learning')
    patterns.sort(key=lambda x: len(x[0]), reverse=True)
    return patterns


_SKILL_PATTERNS = _compile_skill_patterns(SKILLS)


def extract_skills(text: str) -> list[str]:
    """Extract skills using curated phrase matching + light normalization."""
    if not text:
        return []

    lower = text.lower()
    found: list[str] = []
    seen: set[str] = set()

    for skill, pat in _SKILL_PATTERNS:
        if pat.search(lower) and skill not in seen:
            seen.add(skill)
            found.append(skill)

    # Extra: catch common tokens like "ml", "ai" if present as standalone tokens.
    tokens = set(_WORD_RE.findall(lower))
    extras = [
        ("ml", "machine learning"),
        ("ai", "machine learning"),
        ("nlp", "nlp"),
    ]
    for short, mapped in extras:
        if short in tokens and mapped not in seen:
            seen.add(mapped)
            found.append(mapped)

    return found


@dataclass(frozen=True)
class ResumeResult:
    filename: str
    name_guess: str
    similarity: float
    skill_overlap_count: int
    jd_skills_count: int
    score: float
    skills: list[str]
    matched_skills: list[str]


@dataclass(frozen=True)
class ATSCheck:
    status: str  # "good" | "warn"
    label: str
    detail: str


@dataclass(frozen=True)
class ATSReport:
    filename: str
    page_count: int
    word_count: int
    score: int
    similarity: float | None
    skills: list[str]
    matched_skills: list[str]
    missing_skills: list[str]
    checks: list[ATSCheck]


def _first_match(regex: re.Pattern[str], text: str) -> str:
    m = regex.search(text or "")
    return (m.group(0).strip() if m else "")


def _collect_links(text: str) -> list[str]:
    if not text:
        return []
    links = []
    for m in _URL_RE.finditer(text):
        url = m.group(0).strip().rstrip(").,;]")
        if url and url not in links:
            links.append(url)
    return links[:6]


def _parse_sections(text: str) -> dict[str, list[str]]:
    """Best-effort parse into sections based on common headings."""
    sections: dict[str, list[str]] = {
        "summary": [],
        "skills": [],
        "experience": [],
        "projects": [],
        "education": [],
        "other": [],
    }

    current = "other"
    lines = [ln.strip() for ln in (text or "").splitlines()]
    for ln in lines:
        if not ln:
            continue
        low = re.sub(r"\s+", " ", ln).strip().lower()

        # Detect headings.
        if any(re.fullmatch(rf"{re.escape(a)}", low) for a in _SECTION_ALIASES["experience"]):
            current = "experience"
            continue
        if any(re.fullmatch(rf"{re.escape(a)}", low) for a in _SECTION_ALIASES["education"]):
            current = "education"
            continue
        if any(re.fullmatch(rf"{re.escape(a)}", low) for a in _SECTION_ALIASES["skills"]):
            current = "skills"
            continue
        if any(re.fullmatch(rf"{re.escape(a)}", low) for a in _SECTION_ALIASES["projects"]):
            current = "projects"
            continue
        if low in {"summary", "professional summary", "profile", "objective"}:
            current = "summary"
            continue

        sections[current].append(ln)

    return sections


def _to_bullets(lines: list[str], *, max_items: int = 12) -> list[str]:
    out: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue

        if s.startswith(("- ", "•", "* ", "\u2022")):
            bullet = s
        else:
            bullet = f"- {s}"

        out.append(bullet)
        if len(out) >= max_items:
            break
    return out


def generate_ats_optimized_resume(
    *,
    filename: str,
    resume_text: str,
    job_description: str | None = None,
) -> tuple[str, ATSReport, list[str]]:
    """Generate an ATS-optimized draft using ONLY extracted resume data.

    Returns:
        (draft_text, draft_report, suggestions)
    """
    text = resume_text or ""
    normalized = _normalize_text(text)
    name = guess_name_from_resume(text)
    email = _first_match(_EMAIL_RE, normalized)
    phone = _first_match(_PHONE_RE, normalized)
    links = _collect_links(normalized)

    sections = _parse_sections(text)
    extracted_skills = extract_skills(normalized)

    # Build a clean, ATS-friendly plain-text resume.
    header_lines: list[str] = []
    if name:
        header_lines.append(name)

    contact_bits: list[str] = []
    if email:
        contact_bits.append(email)
    if phone:
        contact_bits.append(phone)
    if links:
        # Keep 1-2 most important.
        contact_bits.extend(links[:2])

    if contact_bits:
        header_lines.append(" | ".join(contact_bits))

    # Summary: keep short and non-fictional.
    summary_lines = sections.get("summary") or []
    summary = ""
    if summary_lines:
        summary = " ".join(summary_lines[:3])
    elif extracted_skills:
        summary = "Key skills: " + ", ".join(extracted_skills[:10])

    parts: list[str] = []
    if header_lines:
        parts.extend(header_lines)
        parts.append("")

    if summary:
        parts.append("SUMMARY")
        parts.append(summary)
        parts.append("")

    parts.append("SKILLS")
    if extracted_skills:
        parts.append(", ".join(extracted_skills))
    else:
        parts.append("(Add your technical skills here.)")
    parts.append("")

    # Experience / Projects / Education: preserve original lines, but bulletize for ATS readability.
    exp_bullets = _to_bullets(sections.get("experience") or [])
    proj_bullets = _to_bullets(sections.get("projects") or [])
    edu_bullets = _to_bullets(sections.get("education") or [])
    other_bullets = _to_bullets(sections.get("other") or [], max_items=10)

    if exp_bullets:
        parts.append("EXPERIENCE")
        parts.extend(exp_bullets)
        parts.append("")

    if proj_bullets:
        parts.append("PROJECTS")
        parts.extend(proj_bullets)
        parts.append("")

    if edu_bullets:
        parts.append("EDUCATION")
        parts.extend(edu_bullets)
        parts.append("")

    if other_bullets and not (exp_bullets or proj_bullets or edu_bullets):
        parts.append("DETAILS")
        parts.extend(other_bullets)
        parts.append("")

    draft_text = "\n".join(parts).strip() + "\n"

    # Suggestions (do not insert missing skills automatically).
    suggestions: list[str] = []
    if not email:
        suggestions.append("Add an email at the top header.")
    if not phone:
        suggestions.append("Add a phone number at the top header.")
    if not links:
        suggestions.append("Add LinkedIn/GitHub/portfolio links.")
    if not exp_bullets:
        suggestions.append("Add an EXPERIENCE section with 3–6 bullet points per role.")
    if not edu_bullets:
        suggestions.append("Add an EDUCATION section (degree, college, year).")

    draft_report = ats_scan_resume(
        filename=f"ATS_Optimized_{filename}",
        resume_text=draft_text,
        page_count=1,
        job_description=job_description,
    )

    return draft_text, draft_report, suggestions


_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "experience": ("experience", "work experience", "employment", "professional experience"),
    "education": ("education", "academics", "academic background"),
    "skills": ("skills", "technical skills", "core skills", "skill set"),
    "projects": ("projects", "project", "personal projects"),
}


def _has_section(text: str, section_key: str) -> bool:
    if not text:
        return False
    aliases = _SECTION_ALIASES.get(section_key, (section_key,))
    for a in aliases:
        if re.search(rf"(?im)^\s*{re.escape(a)}\s*$", text):
            return True
        # Some PDFs merge headings inline; allow colon-based headings.
        if re.search(rf"(?im)^\s*{re.escape(a)}\s*:\s+", text):
            return True
    return False


def _has_bullets(text: str) -> bool:
    if not text:
        return False
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    bullet_like = 0
    for ln in lines[:200]:
        if ln.startswith(("- ", "•", "* ", "\u2022")):
            bullet_like += 1
    return bullet_like >= 3


def ats_scan_resume(
    *,
    filename: str,
    resume_text: str,
    page_count: int = 0,
    job_description: str | None = None,
) -> ATSReport:
    """Generate a lightweight ATS-style scan report.

    Note: This is a heuristic + NLP-based report (skills matching + similarity), not a guarantee of passing any ATS.
    """
    text = resume_text or ""
    normalized = _normalize_text(text)
    word_count = len(re.findall(r"\b\w+\b", normalized))
    skills = extract_skills(normalized)

    checks: list[ATSCheck] = []
    score = 100

    has_email = bool(_EMAIL_RE.search(normalized))
    has_phone = bool(_PHONE_RE.search(normalized))
    has_url = bool(_URL_RE.search(normalized))

    if has_email:
        checks.append(ATSCheck("good", "Email detected", "ATS can identify contact email."))
    else:
        score -= 12
        checks.append(ATSCheck("warn", "Email missing", "Add a visible email in the header."))

    if has_phone:
        checks.append(ATSCheck("good", "Phone detected", "ATS can identify contact phone."))
    else:
        score -= 10
        checks.append(ATSCheck("warn", "Phone missing", "Add a phone number (with country code if possible)."))

    if has_url:
        checks.append(ATSCheck("good", "Links detected", "LinkedIn/GitHub links improve verification."))
    else:
        checks.append(ATSCheck("warn", "No links found", "Consider adding LinkedIn/GitHub/portfolio URL."))
        score -= 3

    if page_count <= 0:
        # Keep neutral.
        pass
    elif page_count <= 2:
        checks.append(ATSCheck("good", "Length looks OK", f"{page_count} page(s) is typically ATS-friendly."))
    else:
        score -= 6
        checks.append(ATSCheck("warn", "Resume may be long", f"{page_count} pages; consider trimming to 1–2 pages."))

    if word_count < 150:
        score -= 20
        checks.append(ATSCheck("warn", "Too little content", "Add more detail (impact, numbers, projects)."))
    elif word_count < 300:
        score -= 6
        checks.append(ATSCheck("warn", "Content is short", "More quantified bullet points can help."))
    else:
        checks.append(ATSCheck("good", "Content volume OK", f"~{word_count} words detected."))

    # Section checks
    for key, label, penalty in (
        ("experience", "Experience section", 12),
        ("education", "Education section", 8),
        ("skills", "Skills section", 8),
    ):
        if _has_section(text, key):
            checks.append(ATSCheck("good", label, "Section heading detected."))
        else:
            score -= penalty
            checks.append(ATSCheck("warn", label, "Add a clear heading (ATS reads standard headings best)."))

    if _has_bullets(text):
        checks.append(ATSCheck("good", "Bullet points detected", "Bullets improve skimmability and keyword density."))
    else:
        score -= 5
        checks.append(ATSCheck("warn", "Few bullet points", "Use bullets for achievements under each role/project."))

    # JD matching (AI/NLP part)
    similarity: float | None = None
    matched_skills: list[str] = []
    missing_skills: list[str] = []

    jd = _normalize_text(job_description or "")
    if jd:
        jd_skills = extract_skills(jd)
        resume_skill_set = set(skills)
        jd_skill_set = set(jd_skills)
        matched_skills = sorted(resume_skill_set.intersection(jd_skill_set))
        missing_skills = sorted(jd_skill_set.difference(resume_skill_set))

        # Similarity using TF-IDF cosine
        vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=20000)
        tfidf = vectorizer.fit_transform([jd, normalized])
        similarity = float(cosine_similarity(tfidf[1:2], tfidf[0:1]).reshape(-1)[0])

        # Reward reasonable match; keep bounded.
        score += int(round(min(10.0, max(0.0, similarity * 10.0))))

        if len(matched_skills) >= 6:
            checks.append(ATSCheck("good", "Good keyword match", f"Matched {len(matched_skills)} skills from JD."))
        elif len(matched_skills) >= 3:
            checks.append(ATSCheck("warn", "Medium keyword match", f"Matched {len(matched_skills)} skills; add more JD keywords naturally."))
            score -= 4
        else:
            checks.append(ATSCheck("warn", "Low keyword match", "Tailor resume: mirror JD skills in Skills + Experience bullets."))
            score -= 10

    score = max(0, min(100, int(score)))

    return ATSReport(
        filename=filename,
        page_count=int(page_count),
        word_count=int(word_count),
        score=int(score),
        similarity=similarity,
        skills=skills,
        matched_skills=matched_skills,
        missing_skills=missing_skills,
        checks=checks,
    )


def rank_resumes(
    *,
    job_description: str,
    resumes: list[tuple[str, str]],
    w_similarity: float = 0.7,
    w_skill_overlap: float = 0.3,
) -> list[ResumeResult]:
    """Rank resumes.

    Args:
        job_description: JD text.
        resumes: list of (filename, resume_text).
        w_similarity: weight for TF-IDF cosine similarity.
        w_skill_overlap: weight for skill overlap ratio.
    """
    jd = _normalize_text(job_description or "")
    if not jd:
        raise ValueError("Job description is required")
    if not resumes:
        raise ValueError("At least one resume is required")

    if w_similarity < 0 or w_skill_overlap < 0 or (w_similarity + w_skill_overlap) <= 0:
        raise ValueError("Invalid weights")

    jd_skills = extract_skills(jd)
    jd_skill_set = set(jd_skills)
    jd_skill_count = len(jd_skill_set)

    corpus = [jd] + [_normalize_text(t) for _, t in resumes]
    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=25000)
    tfidf = vectorizer.fit_transform(corpus)

    jd_vec = tfidf[0:1]
    resume_vecs = tfidf[1:]
    sims = cosine_similarity(resume_vecs, jd_vec).reshape(-1)

    results: list[ResumeResult] = []
    for (filename, text), sim in zip(resumes, sims, strict=True):
        skills = extract_skills(text)
        skill_set = set(skills)
        matched = sorted(skill_set.intersection(jd_skill_set)) if jd_skill_set else []
        overlap = len(matched)
        overlap_ratio = (overlap / jd_skill_count) if jd_skill_count else 0.0

        sim_f = float(sim)
        score = float((w_similarity * sim_f) + (w_skill_overlap * overlap_ratio))

        results.append(
            ResumeResult(
                filename=filename,
                name_guess=guess_name_from_resume(text),
                similarity=sim_f,
                skill_overlap_count=int(overlap),
                jd_skills_count=int(jd_skill_count),
                score=score,
                skills=skills,
                matched_skills=matched,
            )
        )

    results.sort(key=lambda r: r.score, reverse=True)
    return results
