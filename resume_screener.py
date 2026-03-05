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


def _normalize_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Extract text from a PDF. Returns empty string if no text."""
    if not pdf_bytes:
        return ""

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages: list[str] = []
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            if page_text:
                pages.append(page_text)

    return _normalize_text("\n".join(pages))


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
