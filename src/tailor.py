"""Manual CLI: tailor cv.md to a job description and render CV + PDF.

Usage: python -m src.tailor <path-to-jd.txt>

This module is never imported or invoked by run.py or the GitHub Actions
workflow. It is a manual, human-triggered tool only.
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.request

import yaml

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

TAILOR_PROMPT = """You are tailoring a candidate's CV to a specific job description.

Reorder and reframe only — never invent experience, metrics, or skills not present in the CV.

Produce ATS-friendly HTML for the BODY CONTENT ONLY (no <html>/<head>/<body> tags,
just semantic content using h1/h2/ul/li/p as appropriate). Prioritize and reorder
the candidate's existing experience/skills to match the job description. Do not
add anything that isn't already in the CV.

Candidate CV:
{cv}

Job description:
{jd}

Return only the HTML body content, nothing else."""

WINDOWS_BROWSER_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _find_browser():
    for path in WINDOWS_BROWSER_PATHS:
        if os.path.exists(path):
            return path
    for name in ("chrome", "msedge", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _model():
    try:
        with open("config/profile.yml", "r", encoding="utf-8") as f:
            profile = yaml.safe_load(f)
        return profile.get("scoring", {}).get("model", "openai/gpt-oss-20b:free")
    except FileNotFoundError:
        return "openai/gpt-oss-20b:free"


def tailor_cv(cv_text, jd_text):
    body = json.dumps(
        {
            "model": _model(),
            "messages": [
                {"role": "user", "content": TAILOR_PROMPT.format(cv=cv_text, jd=jd_text)}
            ],
            "max_tokens": 4096,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/career-bot",
            "X-Title": "career-bot",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def main():
    if len(sys.argv) != 2:
        print("usage: python -m src.tailor <path-to-jd.txt>", file=sys.stderr)
        sys.exit(1)

    jd_path = sys.argv[1]
    with open(jd_path, "r", encoding="utf-8") as f:
        jd_text = f.read()
    with open("cv.md", "r", encoding="utf-8") as f:
        cv_text = f.read()

    content_html = tailor_cv(cv_text, jd_text)

    with open("templates/cv-template.html", "r", encoding="utf-8") as f:
        template = f.read()
    full_html = template.replace("{{CONTENT}}", content_html)

    os.makedirs("output", exist_ok=True)
    jd_basename = os.path.splitext(os.path.basename(jd_path))[0]
    html_path = os.path.join("output", f"cv-tailored-{jd_basename}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(full_html)
    print(f"wrote {html_path}")

    browser = _find_browser()
    if browser:
        pdf_path = os.path.join("output", f"cv-tailored-{jd_basename}.pdf")
        abs_html = os.path.abspath(html_path)
        abs_pdf = os.path.abspath(pdf_path)
        subprocess.run(
            [
                browser,
                "--headless",
                f"--print-to-pdf={abs_pdf}",
                abs_html,
            ],
            check=True,
        )
        print(f"wrote {pdf_path}")
    else:
        print("no Chrome/Edge found — open the HTML and print to PDF")


if __name__ == "__main__":
    main()
