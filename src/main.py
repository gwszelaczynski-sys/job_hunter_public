import os
import re
import json
import imaplib
import smtplib
import email
import time
import json
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path
from typing import List

import requests
from bs4 import BeautifulSoup
from jinja2 import Template
from weasyprint import HTML
from pydantic import BaseModel
from google import genai
from google.genai import types

# Konfiguracja
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
MIN_SCORE = 7

MODEL_SCORING = "gemini-3.8-flash"
MODEL_CV = "gemini-3.8-pro"  # z bezpiecznym fallbackiem do Flash w razie innej nazwy w API

SKIP_URL_PATTERNS = [
    "unsubscribe", "privacy", "terms", "facebook.com", "instagram.com",
    "twitter.com", "x.com", "youtube.com", "tiktok.com", "help", "settings",
    "preferences", "mailto:", ".png", ".jpg", ".gif", ".css"
]

TRACKING_DOMAINS = [
    "pracuj.pl", "nofluffjobs.com", "justjoin.it", "rocketjobs.pl", "linkedin.com", "bulldogjob.pl", "bulldogjob.com", "careers.ey.com",
    "click.", "email.", "links.", "trk.", "sendgrid", "mailgun", "braze", "customeriomail"
]


def resolve_url(url: str) -> str:
    """Rozwija linki śledzące (redirecty) do docelowego URL-a oferty."""
    if any(domain in url for domain in JOB_DOMAINS):
        return url.split("?")[0].rstrip("/")
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.head(url, allow_redirects=True, timeout=8, headers=headers)
        if resp.status_code in (403, 405):
            resp = requests.get(url, allow_redirects=True, timeout=8, headers=headers, stream=True)
            resp.close()
        return resp.url.split("?")[0].rstrip("/")
    except Exception:
        return url.split("?")[0].rstrip("/")

JOB_DOMAINS = [
    "linkedin.com/jobs/view",
    "linkedin.com/comm/jobs/view",
    "pracuj.pl/praca/",
    "nofluffjobs.com/pl/job/",
    "nofluffjobs.com/job/",
    "justjoin.it/job-offer/",
    "rocketjobs.pl/oferta-pracy/",
    "bulldogjob.pl/companies/jobs/",
    "bulldogjob.com/companies/jobs/",
    "https://careers.ey.com/",
]

client = genai.Client(api_key=GEMINI_API_KEY)

def call_gemini_with_retry(model: str, contents: str, config: types.GenerateContentConfig, retries: int = 4):
    for attempt in range(retries):
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as e:
            if ("503" in str(e) or "429" in str(e)) and attempt < retries - 1:
                wait = 5 * (2 ** attempt)  # 5s, 10s, 20s
                print(f"Chwilowy błąd API ({attempt + 1}/{retries}), ponawiam za {wait}s...")
                time.sleep(wait)
            else:
                raise


# Schematy wyjściowe dla Gemini
class JobScore(BaseModel):
    company: str
    role: str
    score: int
    language: str  # "PL" lub "EN"
    reasoning: str
    red_flags: List[str]


class ExperienceItem(BaseModel):
    company: str
    role: str
    period: str
    bullets: List[str]


class EducationItem(BaseModel):
    institution: str
    degree: str
    period: str


class TailoredCV(BaseModel):
    headline: str
    summary: str
    skills: List[str]
    experience: List[ExperienceItem]
    education: List[EducationItem]
    languages: str


def load_seen_jobs() -> set:
    path = Path("data/seen_jobs.json")
    if path.exists():
        return set(json.loads(path.read_text(encoding="utf-8")))
    return set()


def save_seen_jobs(seen: set):
    path = Path("data/seen_jobs.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(list(seen)), indent=2), encoding="utf-8")


def fetch_urls_from_gmail() -> List[str]:
    urls = set()
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    status, _ = mail.select("JobAlerts")
    if status != "OK":
        print("Nie znaleziono etykiety JobAlerts w Gmailu.")
        return []

    _, msg_nums = mail.search(None, "UNSEEN")
    for num in msg_nums[0].split():
        _, data = mail.fetch(num, "(RFC822)")
        msg = email.message_from_bytes(data[0][1])
        for part in msg.walk():
            if part.get_content_type() in ("text/html", "text/plain"):
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                text = payload.decode(errors="ignore")
                soup = BeautifulSoup(text, "html.parser")
                links = [a["href"] for a in soup.find_all("a", href=True)]
                links += re.findall(r"https?://[^\s\"'<>]+", text)
                for link in links:
                    link_lower = link.lower()
                    if any(skip in link_lower for skip in SKIP_URL_PATTERNS):
                        continue
                    if not any(td in link_lower for td in TRACKING_DOMAINS):
                        continue
                    final_url = resolve_url(link)
                    if any(domain in final_url for domain in JOB_DOMAINS):
                        urls.add(final_url)
    mail.logout()
    return list(urls)


def fetch_offer_markdown(url: str) -> str:
    # 1. Specjalna ścieżka dla LinkedIna (omija większość Login Walli)
    li_match = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?]+-)?(\d+)", url)
    if li_match:
        job_id = li_match.group(1)
        guest_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        try:
            resp = requests.get(guest_url, headers=headers, timeout=15)
            if resp.status_code == 200 and len(resp.text) > 500:
                soup = BeautifulSoup(resp.text, "html.parser")
                text = soup.get_text(separator="\n", strip=True)
                if len(text) > 300:
                    return text[:25000]
        except Exception:
            pass  # fallback do Jiny poniżej

    # 2. Standardowe pobranie przez Jina Reader (inne portale + fallback)
    resp = requests.get(f"https://r.jina.ai/{url}", timeout=30)
    resp.raise_for_status()
    return resp.text[:25000]


def score_offer(master_profile: str, offer_md: str) -> JobScore:
    prompt = Path("prompts/score_job.md").read_text(encoding="utf-8")
    response = call_gemini_with_retry(
        model=MODEL_SCORING,
        contents=f"{prompt}\n\n=== MASTER PROFILE ===\n{master_profile}\n\n=== JOB OFFER ===\n{offer_md}",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=JobScore,
            temperature=0.1,
        ),
    )
    return JobScore.model_validate_json(response.text)

def load_candidate_config() -> dict:
    return json.loads(Path("profile/candidate.json").read_text(encoding="utf-8"))

def render_docx(candidate: dict, cv: TailoredCV, lang: str, output_path: Path) -> None:
    doc = Document()
    is_pl = lang.upper() == "PL"

    for section in doc.sections:
        section.top_margin = Inches(0.6)
        section.bottom_margin = Inches(0.6)
        section.left_margin = Inches(0.7)
        section.right_margin = Inches(0.7)

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10)

    name_p = doc.add_paragraph()
    name_run = name_p.add_run(candidate["full_name"])
    name_run.bold = True
    name_run.font.size = Pt(18)
    name_p.paragraph_format.space_after = Pt(1)

    if cv.headline:
        hl_p = doc.add_paragraph()
        hl_run = hl_p.add_run(cv.headline)
        hl_run.bold = True
        hl_run.font.size = Pt(11)
        hl_run.font.color.rgb = RGBColor(25, 55, 109)
        hl_p.paragraph_format.space_after = Pt(3)

    contact_p = doc.add_paragraph(
        f"{candidate['location']} | {candidate['email']} | {candidate['phone']} | {candidate['linkedin']}"
    )
    contact_p.runs[0].font.size = Pt(9)
    contact_p.runs[0].font.color.rgb = RGBColor(90, 90, 90)
    contact_p.paragraph_format.space_after = Pt(8)

    def add_section_heading(title: str):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after = Pt(3)
        run = p.add_run(title.upper())
        run.bold = True
        run.font.size = Pt(11)
        run.font.color.rgb = RGBColor(25, 55, 109)

    add_section_heading("Podsumowanie" if is_pl else "Executive Summary")
    doc.add_paragraph(cv.summary)

    if cv.skills:
        add_section_heading("Kluczowe kompetencje" if is_pl else "Core Competencies")
        doc.add_paragraph(" • ".join(cv.skills))

    add_section_heading("Doświadczenie" if is_pl else "Experience")
    for exp in cv.experience:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(1)
        r_role = p.add_run(f"{exp.role} — {exp.company}")
        r_role.bold = True
        r_dates = p.add_run(f"  ({exp.period})")
        r_dates.font.color.rgb = RGBColor(100, 100, 100)
        for bullet in exp.bullets:
            bp = doc.add_paragraph(bullet, style="List Bullet")
            bp.paragraph_format.space_after = Pt(1)

    if cv.education:
        add_section_heading("Wykształcenie" if is_pl else "Education")
        for edu in cv.education:
            ep = doc.add_paragraph()
            ep.paragraph_format.space_after = Pt(2)
            r_inst = ep.add_run(edu.institution)
            r_inst.bold = True
            ep.add_run(f" — {edu.degree}")
            r_per = ep.add_run(f"  ({edu.period})")
            r_per.font.color.rgb = RGBColor(100, 100, 100)

    if cv.languages:
        add_section_heading("Języki" if is_pl else "Languages")
        doc.add_paragraph(cv.languages)

    gdpr = candidate["gdpr_clause_pl"] if is_pl else candidate["gdpr_clause_en"]
    footer_p = doc.add_paragraph(gdpr)
    footer_p.paragraph_format.space_before = Pt(10)
    footer_p.runs[0].font.size = Pt(7.5)
    footer_p.runs[0].font.color.rgb = RGBColor(120, 120, 120)

    doc.save(output_path)

def generate_tailored_cv(master_profile: str, offer_md: str) -> TailoredCV:
    prompt = Path("prompts/tailor_cv.md").read_text(encoding="utf-8")
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=TailoredCV,
        temperature=0.2,
    )
    try:
        response = call_gemini_with_retry(
            model=MODEL_CV,
            contents=f"{prompt}\n\n=== MASTER PROFILE ===\n{master_profile}\n\n=== JOB OFFER ===\n{offer_md}",
            config=cfg,
        )
    except Exception as e:
        if "404" in str(e):
            print(f"Model {MODEL_CV} niedostępny, przełączam na {MODEL_SCORING}.")
            response = call_gemini_with_retry(
                model=MODEL_SCORING,
                contents=f"{prompt}\n\n=== MASTER PROFILE ===\n{master_profile}\n\n=== JOB OFFER ===\n{offer_md}",
                config=cfg,
            )
        else:
            raise
    return TailoredCV.model_validate_json(response.text)


def sanitize_name(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", text).strip("_")[:40]


def send_summary_email(matches: list, candidate: dict):
    if not matches:
        return
    first_name = candidate["full_name"].split()[0]
    lines = [f"Cześć {first_name},\n\nOto dzisiejsze dopasowane oferty pracy:\n"]
    for m in matches:
        gh_link = f"https://github.com/{GITHUB_REPO}/tree/{GITHUB_BRANCH}/{m['folder']}" if GITHUB_REPO else m["folder"]
        lines.append(
            f"• [{m['score']}/10] {m['company']} — {m['role']}\n"
            f"  Uzasadnienie: {m['reasoning']}\n"
            f"  Oferta: {m['url']}\n"
            f"  Gotowe CV (GitHub): {gh_link}\n"
        )
    msg = MIMEText("\n".join(lines), "plain", "utf-8")
    msg["Subject"] = f"[Job Hunter] {len(matches)} nowe dopasowane oferty ({datetime.now().strftime('%Y-%m-%d')})"
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = GMAIL_ADDRESS

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)


def main():
    master_profile = Path("profile/master_profile.md").read_text(encoding="utf-8")
    candidate = load_candidate_config()  # <--- przenieś tutaj z bloku if
    template_html = Template(Path("templates/cv_template.html").read_text(encoding="utf-8"))
    seen_jobs = load_seen_jobs()
    urls = fetch_urls_from_gmail()
    matches = []

    for url in urls:
        if url in seen_jobs:
            continue
       
        try:
            offer_md = fetch_offer_markdown(url)
            score_res = score_offer(master_profile, offer_md)
            print(f"{score_res.score}/10 | {score_res.company} - {score_res.role}")
         

            if score_res.score >= MIN_SCORE:
                candidate = load_candidate_config()
                cv_data = generate_tailored_cv(master_profile, offer_md)
                date_str = datetime.now().strftime("%Y-%m-%d")
                folder_name = f"applications/{date_str}_{sanitize_name(score_res.company)}_{sanitize_name(score_res.role)}"
                folder = Path(folder_name)
                folder.mkdir(parents=True, exist_ok=True)

                (folder / "offer.md").write_text(f"URL: {url}\n\n{offer_md}", encoding="utf-8")
                (folder / "match_report.json").write_text(score_res.model_dump_json(indent=2), encoding="utf-8")

                base_name = f"CV_{candidate['file_slug']}"
                rendered_html = template_html.render(
                    lang=score_res.language,
                    candidate=candidate,
                    **cv_data.model_dump(),
                )
                HTML(string=rendered_html).write_pdf(str(folder / f"{base_name}.pdf"))
                render_docx(candidate, cv_data, score_res.language, folder / f"{base_name}.docx")

                matches.append({
                    "company": score_res.company,
                    "role": score_res.role,
                    "score": score_res.score,
                    "reasoning": score_res.reasoning,
                    "url": url,
                    "folder": folder_name,
                })
            seen_jobs.add(url) # <-- dopiero po udanym scoringu
        except Exception as e:
            print(f"Błąd przy przetwarzaniu {url}: {e}")

    time.sleep(2)
    
    save_seen_jobs(seen_jobs)
    send_summary_email(matches, candidate)


if __name__ == "__main__":
    main()
