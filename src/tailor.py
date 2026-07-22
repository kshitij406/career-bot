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

from src.score import _is_openrouter, _resolve_api_base, _resolve_model

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


def _scoring_cfg():
    try:
        with open("config/profile.yml", "r", encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("scoring", {})
    except FileNotFoundError:
        return {}


def tailor_cv(cv_text, jd_text, fmt="html"):
    """Tailor the CV to a JD. fmt="html" for the web/PDF path, "latex" for a
    .tex body the caller wraps in render_latex's preamble."""
    if fmt == "latex":
        from src.render_latex import LATEX_PROMPT

        prompt = LATEX_PROMPT.replace("{cv}", cv_text).replace("{jd}", jd_text)
    else:
        prompt = TAILOR_PROMPT.format(cv=cv_text, jd=jd_text)

    scoring_cfg = _scoring_cfg()
    api_base = _resolve_api_base(scoring_cfg)
    # Tailoring is the iteration-heavy path — same JD reworked several times —
    # so it's the one that benefits most from pointing at a local model.
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if _is_openrouter(api_base) and not api_key:
        raise SystemExit(
            "OPENROUTER_API_KEY is not set. Either export it, or point "
            "scoring.api_base (or $CAREER_BOT_API_BASE) at a local "
            "OpenAI-compatible server such as http://localhost:11434/v1"
        )

    body = json.dumps(
        {
            "model": _resolve_model(scoring_cfg),
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4096,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if _is_openrouter(api_base):
        headers["HTTP-Referer"] = "https://github.com/career-bot"
        headers["X-Title"] = "career-bot"
    req = urllib.request.Request(
        f"{api_base}/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def main():
    positional = [a for a in sys.argv[1:] if not a.startswith("-")]
    if len(positional) != 1:
        print("usage: python -m src.tailor <path-to-jd.txt> [--docx] [--latex]", file=sys.stderr)
        sys.exit(1)

    jd_path = positional[0]
    with open(jd_path, "r", encoding="utf-8") as f:
        jd_text = f.read()
    with open("cv.md", "r", encoding="utf-8") as f:
        cv_text = f.read()

    os.makedirs("output", exist_ok=True)
    jd_name = os.path.splitext(os.path.basename(jd_path))[0]

    # LaTeX is a separate output path, not a post-processing step on the HTML:
    # the model produces .tex directly, so it is asked for LaTeX from the start.
    if "--latex" in sys.argv:
        from src.render_latex import LatexError, compile_pdf, write_tex

        tex_path = write_tex(
            tailor_cv(cv_text, jd_text, fmt="latex"),
            os.path.join("output", f"cv-tailored-{jd_name}.tex"),
        )
        print(f"wrote {tex_path}")
        try:
            print(f"wrote {compile_pdf(tex_path)}")
        except LatexError as e:
            print(f"note: {e}", file=sys.stderr)
        return

    content_html = tailor_cv(cv_text, jd_text)

    with open("templates/cv-template.html", "r", encoding="utf-8") as f:
        template = f.read()
    full_html = template.replace("{{CONTENT}}", content_html)

    html_path = os.path.join("output", f"cv-tailored-{jd_name}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(full_html)
    print(f"wrote {html_path}")

    browser = _find_browser()
    if browser:
        pdf_path = os.path.join("output", f"cv-tailored-{jd_name}.pdf")
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

    # DOCX is opt-in: some portals parse .docx reliably and mangle
    # headless-printed PDFs, but python-docx is an optional extra, so don't
    # fail the run for anyone who hasn't installed it.
    if "--docx" in sys.argv or os.environ.get("CAREER_BOT_DOCX"):
        from src.render_docx import render_docx

        docx_path = os.path.join("output", f"cv-tailored-{jd_name}.docx")
        render_docx(content_html, docx_path)
        print(f"wrote {docx_path}")


if __name__ == "__main__":
    main()
