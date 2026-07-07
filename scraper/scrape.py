#!/usr/bin/env python3
"""
M&A Tracker — collecte les offres (stages, spring weeks, insight programmes,
M&A / Corporate Finance) directement depuis les API publiques des ATS.

Sortie : docs/jobs.json (lu par le dashboard docs/index.html)
"""

from __future__ import annotations

import json
import re
import sys
import time
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "companies.yaml"
OUTPUT = ROOT / "docs" / "jobs.json"

HEADERS = {
    "User-Agent": "MA-Tracker/1.0 (personal job tracker; contact: you@example.com)",
    "Accept": "application/json",
}
TIMEOUT = 25

# ── Filtre métier ──────────────────────────────────────────────────
# Une offre est retenue si son titre (ou ses tags) contient au moins
# un de ces mots-clés. Couvre le M&A/CF ET les spring weeks / insight
# programmes du calendrier.
KEYWORDS = [
    # M&A / Corporate Finance
    r"\bm\s?&\s?a\b", r"mergers?", r"acquisitions?", r"fusions?[- ]acquisitions?",
    r"corporate finance", r"investment banking", r"banque d'affaires",
    r"transaction (services|advisory|diligence)", r"leveraged finance",
    r"private equity", r"capital markets?", r"\becm\b", r"\bdcm\b",
    r"debt advisory", r"restructuring", r"valuation", r"corporate development",
    r"equity research", r"coverage",
    # Spring weeks / programmes d'insight / stages structurés
    r"spring (insight|week|into|start|break)", r"insight (programme|program|week|day|experience)",
    r"discovery (programme|program|week|day)", r"summer (analyst|intern|internship)",
    r"off[- ]cycle", r"industrial placement", r"summer insight",
]
KEYWORDS_RE = re.compile("|".join(KEYWORDS), re.IGNORECASE)

CONTRACT_PATTERNS = [
    ("Spring/Insight", r"spring|insight (programme|program|week|day)|discovery"),
    ("Stage",          r"\bstage\b|\bstagiaire\b|\bintern(ship)?\b|praktik|off[- ]cycle|summer analyst|placement"),
    ("Alternance",     r"alternan|apprenti|apprenticeship|werkstudent"),
    ("Graduate",       r"graduate|trainee|vie\b"),
    ("CDI",            r"\banalyst\b|\bassociate\b|\bcdi\b|full[- ]time|charg[ée]"),
]


def detect_contract(title: str) -> str:
    for label, pat in CONTRACT_PATTERNS:
        if re.search(pat, title, re.IGNORECASE):
            return label
    return "Autre"


def matches(title: str, extra: str = "") -> bool:
    return bool(KEYWORDS_RE.search(f"{title} {extra}"))


def job_id(company: str, title: str, location: str) -> str:
    return hashlib.sha1(f"{company}|{title}|{location}".lower().encode()).hexdigest()[:12]


def make_job(company, title, location, url, posted_at=None, source="ats"):
    return {
        "id": job_id(company, title, location or ""),
        "company": company,
        "title": title.strip(),
        "location": (location or "").strip() or "—",
        "url": url,
        "contract": detect_contract(title),
        "posted_at": posted_at,
        "source": source,
        "detected_at": datetime.now(timezone.utc).isoformat(),
    }


def get(url, **kw):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


# ── Connecteurs ATS (API publiques, non authentifiées) ────────────

def scrape_greenhouse(company: str, token: str):
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    data = get(url).json()
    for j in data.get("jobs", []):
        title = j.get("title", "")
        if matches(title):
            yield make_job(company, title,
                           (j.get("location") or {}).get("name", ""),
                           j.get("absolute_url", ""),
                           j.get("updated_at"))


def scrape_lever(company: str, slug: str):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    for j in get(url).json():
        title = j.get("text", "")
        cats = j.get("categories") or {}
        extra = " ".join(str(v) for v in cats.values() if v)
        if matches(title, extra):
            ts = j.get("createdAt")
            posted = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat() if ts else None
            yield make_job(company, title, cats.get("location", ""), j.get("hostedUrl", ""), posted)


def scrape_smartrecruiters(company: str, slug: str):
    offset, limit = 0, 100
    while True:
        url = (f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
               f"?limit={limit}&offset={offset}")
        data = get(url).json()
        content = data.get("content", [])
        for j in content:
            title = j.get("name", "")
            if matches(title):
                loc = j.get("location") or {}
                location = ", ".join(filter(None, [loc.get("city"), loc.get("country")]))
                url_public = f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}"
                yield make_job(company, title, location, url_public, j.get("releasedDate"))
        offset += limit
        if offset >= data.get("totalFound", 0) or not content:
            break
        time.sleep(0.4)


def scrape_workday(company: str, tenant: str, wd: str, site: str):
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    offset = 0
    while True:
        payload = {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""}
        r = requests.post(api, json=payload, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        postings = data.get("jobPostings", [])
        for j in postings:
            title = j.get("title", "")
            if matches(title):
                path = j.get("externalPath", "")
                yield make_job(company, title, j.get("locationsText", ""),
                               f"{base}/en-US/{site}{path}", None)
        offset += 20
        if offset >= data.get("total", 0) or not postings:
            break
        time.sleep(0.4)


def scrape_recruitee(company: str, slug: str):
    url = f"https://{slug}.recruitee.com/api/offers/"
    for j in get(url).json().get("offers", []):
        title = j.get("title", "")
        if matches(title, j.get("department", "") or ""):
            yield make_job(company, title, j.get("location", ""),
                           j.get("careers_url", ""), j.get("published_at"))


def scrape_oraclecloud(company: str, host: str, site: str):
    """Oracle Cloud Recruiting (utilisé par J.P. Morgan, Lazard, etc.)
    GET https://<host>/hcmRestApi/resources/latest/recruitingCEJobRequisitions
    """
    offset = 0
    while True:
        url = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
               f"?onlyData=true&finder=findReqs;siteNumber={site},"
               f"limit=100,offset={offset},sortBy=POSTING_DATES_DESC")
        data = get(url).json()
        items = data.get("items", [])
        reqs = items[0].get("requisitionList", []) if items else []
        for j in reqs:
            title = j.get("Title", "")
            if matches(title):
                jid = j.get("Id")
                yield make_job(company, title, j.get("PrimaryLocation", ""),
                               f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{jid}",
                               j.get("PostedDate"))
        if len(reqs) < 100:
            break
        offset += 100
        time.sleep(0.4)


# ── API officielles agrégées (clés gratuites) ─────────────────────

def scrape_adzuna(cfg):
    for country in cfg.get("countries", ["fr"]):
        url = (f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
               f"?app_id={cfg['app_id']}&app_key={cfg['app_key']}"
               f"&results_per_page=50&what={requests.utils.quote(cfg.get('what', 'M&A'))}"
               f"&category=accounting-finance-jobs&content-type=application/json")
        for j in get(url).json().get("results", []):
            title = j.get("title", "")
            if matches(title):
                yield make_job(j.get("company", {}).get("display_name", "?"),
                               re.sub(r"</?\w+>", "", title),
                               (j.get("location") or {}).get("display_name", ""),
                               j.get("redirect_url", ""), j.get("created"),
                               source="adzuna")


def scrape_france_travail(cfg):
    tok = requests.post(
        "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
        "?realm=%2Fpartenaire",
        data={"grant_type": "client_credentials",
              "client_id": cfg["client_id"],
              "client_secret": cfg["client_secret"],
              "scope": "api_offresdemploiv2 o2dsoffre"},
        timeout=TIMEOUT).json()["access_token"]
    r = requests.get(
        "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search",
        params={"motsCles": cfg.get("keywords", "fusions acquisitions"),
                "typeContrat": "MIS,CDD,CDI", "range": "0-149"},
        headers={"Authorization": f"Bearer {tok}"}, timeout=TIMEOUT)
    for j in r.json().get("resultats", []):
        title = j.get("intitule", "")
        if matches(title, j.get("description", "")[:300]):
            yield make_job((j.get("entreprise") or {}).get("nom", "?"), title,
                           (j.get("lieuTravail") or {}).get("libelle", ""),
                           (j.get("origineOffre") or {}).get("urlOrigine", ""),
                           j.get("dateCreation"), source="france_travail")


# ── Orchestration ──────────────────────────────────────────────────

def main():
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    connectors = [
        ("greenhouse",      scrape_greenhouse),
        ("lever",           scrape_lever),
        ("smartrecruiters", scrape_smartrecruiters),
        ("workday",         scrape_workday),
        ("recruitee",       scrape_recruitee),
        ("oraclecloud",     scrape_oraclecloud),
    ]

    previous = {}
    if OUTPUT.exists():
        for j in json.loads(OUTPUT.read_text(encoding="utf-8")).get("jobs", []):
            previous[j["id"]] = j

    jobs, errors = {}, []
    for key, fn in connectors:
        for entry in cfg.get(key) or []:
            entry = dict(entry)
            company = entry.pop("company")
            try:
                found = 0
                for job in fn(company, **entry):
                    if job["id"] in previous:
                        job["detected_at"] = previous[job["id"]]["detected_at"]
                    jobs[job["id"]] = job
                    found += 1
                print(f"  OK {company:<30} {key:<16} {found} offre(s)")
            except Exception as e:
                errors.append(f"{company} ({key}): {e}")
                print(f"  !! {company:<30} {key:<16} {e}", file=sys.stderr)
            time.sleep(0.5)

    if (cfg.get("adzuna") or {}).get("enabled"):
        try:
            for job in scrape_adzuna(cfg["adzuna"]):
                jobs.setdefault(job["id"], job)
        except Exception as e:
            errors.append(f"adzuna: {e}")

    if (cfg.get("france_travail") or {}).get("enabled"):
        try:
            for job in scrape_france_travail(cfg["france_travail"]):
                jobs.setdefault(job["id"], job)
        except Exception as e:
            errors.append(f"france_travail: {e}")

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(jobs),
        "errors": errors,
        "jobs": sorted(jobs.values(), key=lambda j: j["detected_at"], reverse=True),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(jobs)} offres écrites dans {OUTPUT}")


if __name__ == "__main__":
    main()# Une offre est retenue si son titre (ou ses tags) contient au moins
# un de ces mots-clés.
KEYWORDS = [
    r"\bm\s?&\s?a\b", r"mergers?", r"acquisitions?", r"fusions?[- ]acquisitions?",
    r"corporate finance", r"investment banking", r"banque d'affaires",
    r"transaction (services|advisory|diligence)", r"leveraged finance",
    r"private equity", r"capital markets?", r"\becm\b", r"\bdcm\b",
    r"debt advisory", r"restructuring", r"valuation", r"corporate development",
]
KEYWORDS_RE = re.compile("|".join(KEYWORDS), re.IGNORECASE)

# Détection du type de contrat depuis le titre
CONTRACT_PATTERNS = [
    ("Stage",       r"\bstage\b|\bstagiaire\b|\bintern(ship)?\b|praktik|off[- ]cycle|summer analyst"),
    ("Alternance",  r"alternan|apprenti|apprenticeship|werkstudent"),
    ("Graduate",    r"graduate|trainee|vie\b"),
    ("CDI",         r"\banalyst\b|\bassociate\b|\bcdi\b|full[- ]time|charg[ée]"),
]


def detect_contract(title: str) -> str:
    for label, pat in CONTRACT_PATTERNS:
        if re.search(pat, title, re.IGNORECASE):
            return label
    return "Autre"


def matches(title: str, extra: str = "") -> bool:
    return bool(KEYWORDS_RE.search(f"{title} {extra}"))


def job_id(company: str, title: str, location: str) -> str:
    return hashlib.sha1(f"{company}|{title}|{location}".lower().encode()).hexdigest()[:12]


def make_job(company, title, location, url, posted_at=None, source="ats"):
    return {
        "id": job_id(company, title, location or ""),
        "company": company,
        "title": title.strip(),
        "location": (location or "").strip() or "—",
        "url": url,
        "contract": detect_contract(title),
        "posted_at": posted_at,          # ISO 8601 ou None
        "source": source,
        "detected_at": datetime.now(timezone.utc).isoformat(),
    }


def get(url, **kw):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


# ── Connecteurs ATS (API publiques, non authentifiées) ────────────

def scrape_greenhouse(company: str, token: str):
    """https://boards-api.greenhouse.io/v1/boards/<token>/jobs"""
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    data = get(url).json()
    for j in data.get("jobs", []):
        title = j.get("title", "")
        if matches(title):
            yield make_job(company, title,
                           (j.get("location") or {}).get("name", ""),
                           j.get("absolute_url", ""),
                           j.get("updated_at"))


def scrape_lever(company: str, slug: str):
    """https://api.lever.co/v0/postings/<slug>?mode=json"""
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    for j in get(url).json():
        title = j.get("text", "")
        cats = j.get("categories") or {}
        extra = " ".join(str(v) for v in cats.values() if v)
        if matches(title, extra):
            ts = j.get("createdAt")
            posted = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat() if ts else None
            yield make_job(company, title, cats.get("location", ""), j.get("hostedUrl", ""), posted)


def scrape_smartrecruiters(company: str, slug: str):
    """https://api.smartrecruiters.com/v1/companies/<slug>/postings (paginé)"""
    offset, limit = 0, 100
    while True:
        url = (f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
               f"?limit={limit}&offset={offset}")
        data = get(url).json()
        content = data.get("content", [])
        for j in content:
            title = j.get("name", "")
            if matches(title):
                loc = j.get("location") or {}
                location = ", ".join(filter(None, [loc.get("city"), loc.get("country")]))
                ref = j.get("ref", "") or f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}"
                # ref renvoie l'API ; l'URL candidat est :
                url_public = f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}"
                yield make_job(company, title, location, url_public, j.get("releasedDate"))
        offset += limit
        if offset >= data.get("totalFound", 0) or not content:
            break
        time.sleep(0.4)


def scrape_workday(company: str, tenant: str, wd: str, site: str):
    """POST https://<tenant>.<wd>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs"""
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    offset = 0
    while True:
        payload = {"appliedFacets": {}, "limit": 20, "offset": offset,
                   "searchText": ""}
        r = requests.post(api, json=payload, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        postings = data.get("jobPostings", [])
        for j in postings:
            title = j.get("title", "")
            if matches(title):
                path = j.get("externalPath", "")
                yield make_job(company, title, j.get("locationsText", ""),
                               f"{base}/en-US/{site}{path}",
                               None)  # Workday ne donne que "posted X days ago"
        offset += 20
        if offset >= data.get("total", 0) or not postings:
            break
        time.sleep(0.4)


def scrape_recruitee(company: str, slug: str):
    """https://<slug>.recruitee.com/api/offers/"""
    url = f"https://{slug}.recruitee.com/api/offers/"
    for j in get(url).json().get("offers", []):
        title = j.get("title", "")
        if matches(title, j.get("department", "") or ""):
            yield make_job(company, title, j.get("location", ""),
                           j.get("careers_url", ""), j.get("published_at"))


# ── API officielles agrégées (clés gratuites) ─────────────────────

def scrape_adzuna(cfg):
    """API officielle Adzuna — https://developer.adzuna.com (gratuit)."""
    for country in cfg.get("countries", ["fr"]):
        url = (f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
               f"?app_id={cfg['app_id']}&app_key={cfg['app_key']}"
               f"&results_per_page=50&what={requests.utils.quote(cfg.get('what', 'M&A'))}"
               f"&category=accounting-finance-jobs&content-type=application/json")
        for j in get(url).json().get("results", []):
            title = j.get("title", "")
            if matches(title):
                yield make_job(j.get("company", {}).get("display_name", "?"),
                               re.sub(r"</?\w+>", "", title),
                               (j.get("location") or {}).get("display_name", ""),
                               j.get("redirect_url", ""), j.get("created"),
                               source="adzuna")


def scrape_france_travail(cfg):
    """API officielle France Travail — https://francetravail.io (gratuit)."""
    tok = requests.post(
        "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
        "?realm=%2Fpartenaire",
        data={"grant_type": "client_credentials",
              "client_id": cfg["client_id"],
              "client_secret": cfg["client_secret"],
              "scope": "api_offresdemploiv2 o2dsoffre"},
        timeout=TIMEOUT).json()["access_token"]
    r = requests.get(
        "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search",
        params={"motsCles": cfg.get("keywords", "fusions acquisitions"),
                "typeContrat": "MIS,CDD,CDI", "range": "0-149"},
        headers={"Authorization": f"Bearer {tok}"}, timeout=TIMEOUT)
    for j in r.json().get("resultats", []):
        title = j.get("intitule", "")
        if matches(title, j.get("description", "")[:300]):
            yield make_job((j.get("entreprise") or {}).get("nom", "?"), title,
                           (j.get("lieuTravail") or {}).get("libelle", ""),
                           (j.get("origineOffre") or {}).get("urlOrigine", ""),
                           j.get("dateCreation"), source="france_travail")


# ── Orchestration ──────────────────────────────────────────────────

def main():
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    connectors = [
        ("greenhouse",      scrape_greenhouse),
        ("lever",           scrape_lever),
        ("smartrecruiters", scrape_smartrecruiters),
        ("workday",         scrape_workday),
        ("recruitee",       scrape_recruitee),
    ]

    # Conserver l'historique : les offres déjà vues gardent leur detected_at
    previous = {}
    if OUTPUT.exists():
        for j in json.loads(OUTPUT.read_text(encoding="utf-8")).get("jobs", []):
            previous[j["id"]] = j

    jobs, errors = {}, []
    for key, fn in connectors:
        for entry in cfg.get(key) or []:
            entry = dict(entry)
            company = entry.pop("company")
            try:
                found = 0
                for job in fn(company, **entry):
                    if job["id"] in previous:      # déjà connue -> garder la 1re détection
                        job["detected_at"] = previous[job["id"]]["detected_at"]
                    jobs[job["id"]] = job
                    found += 1
                print(f"  ✓ {company:<28} {key:<16} {found} offre(s)")
            except Exception as e:
                errors.append(f"{company} ({key}): {e}")
                print(f"  ✗ {company:<28} {key:<16} {e}", file=sys.stderr)
            time.sleep(0.5)  # politesse

    if (cfg.get("adzuna") or {}).get("enabled"):
        try:
            for job in scrape_adzuna(cfg["adzuna"]):
                jobs.setdefault(job["id"], job)
        except Exception as e:
            errors.append(f"adzuna: {e}")

    if (cfg.get("france_travail") or {}).get("enabled"):
        try:
            for job in scrape_france_travail(cfg["france_travail"]):
                jobs.setdefault(job["id"], job)
        except Exception as e:
            errors.append(f"france_travail: {e}")

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(jobs),
        "errors": errors,
        "jobs": sorted(jobs.values(), key=lambda j: j["detected_at"], reverse=True),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(jobs)} offres écrites dans {OUTPUT}")


if __name__ == "__main__":
    main()
