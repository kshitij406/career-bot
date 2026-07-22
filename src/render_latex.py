"""LaTeX CV generation and PDF compilation.

Manual-only. Never invoked by run.py or the workflow.

Two things live here:

  1. A prompt that makes the model emit LaTeX body content, and a fixed
     preamble we wrap it in. Letting the model emit a whole document invites
     \\usepackage lines for things the local TeX install doesn't have, and a
     missing package is a hard compile failure with a cryptic log. Owning the
     preamble means the only way a run fails is bad body markup. If the model
     does return a full document (it sometimes will), that's detected and used
     as-is rather than double-wrapped.

  2. Compilation, against whichever engine is installed. There is no bundled
     TeX and no attempt to install one — if nothing is found, the .tex is
     still written and the caller can hand it to Overleaf instead.

ATS note: the preamble deliberately stays in the boring subset — article
class, no multicol, no tikz, no custom fonts. Fancy CV classes look better to
a human and extract worse to a parser, and the parser reads it first.
"""

import os
import re
import sys
import shutil
import subprocess

# Ordered by preference. tectonic downloads what a document needs on demand,
# so it's the most likely to succeed on a machine with no full TeX install;
# latexmk handles multi-pass runs for us; the rest are plain single-pass.
ENGINES = ("tectonic", "latexmk", "pdflatex", "xelatex", "lualatex")

JSON_PROMPT = """You are tailoring a candidate's CV to a job description.

Return ONLY a JSON object. No LaTeX, no Markdown, no code fences, no commentary.
The document layout is already fixed and is not yours to write — your only job
is choosing and rewording the content that goes into it.

Schema (omit any section the CV has nothing for):
{"name": "...",
 "contact": ["Canterbury, UK", "email@example.com", "github.com/x"],
 "summary": "2-3 sentences",
 "experience": [{"org": "...", "role": "...", "dates": "...",
                 "bullets": ["...", "..."]}],
 "projects": [{"name": "...", "kind": "Personal Project",
               "link": "github.com/x/y", "bullets": ["...", "..."]}],
 "skills": {"Hard skills": "...", "Soft skills": "..."},
 "education": [{"org": "...", "detail": "..."}]}

Rules:
- Plain text in every field. Do NOT escape anything, do NOT use backslashes,
  do NOT use Markdown. Write "C#/.NET 8" and "R&D" exactly like that.
- Reorder and reword only. Never invent an employer, date, metric, or skill
  that is not in the CV below.
- Drop bullets irrelevant to this job rather than padding it. Aim for one
  page: at most 3 bullets per role.

Candidate CV:
{cv}

Job description:
{jd}

Return only the JSON object."""

LATEX_PROMPT = r"""You are tailoring a candidate's CV to a job description, as LaTeX.

Reorder and reframe only — never invent experience, metrics, or skills not present in the CV.

Output LaTeX BODY CONTENT ONLY: no \documentclass, no \usepackage, no
\begin{document} or \end{document}. Assume article class with these already
available: \section, \subsection, itemize, \textbf, \emph, \hfill, hyperref.

Structure it as: a name heading, contact line, then \section blocks for
Summary, Experience, Projects, Skills, Education — omitting any the CV has
nothing for. Use itemize for bullets. Keep it to one page of content.

Escape LaTeX special characters in any literal text: & % $ # _ { } ~ ^ \
(for example, "C#" must be written as "C\#", "R&D" as "R\&D"). Do not put a
backslash before ordinary words — ".NET" stays ".NET", never ".\NET".

Use LaTeX markup only, never Markdown: \href{url}{text} for links (not
[text](url)), \textbf{x} for bold (not **x**), and \begin{itemize} with
\item for bullets (not lines starting with "-").

Prioritize and reorder the candidate's existing experience and skills to match
the job description. Do not add anything that isn't already in the CV.

Candidate CV:
{cv}

Job description:
{jd}

Return only the LaTeX body content, nothing else."""

PREAMBLE = r"""\documentclass[11pt,a4paper]{article}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage[margin=0.75in]{geometry}
\usepackage{enumitem}
\PassOptionsToPackage{hyphens}{url}
\usepackage[hidelinks]{hyperref}
\usepackage{titlesec}

\setlist[itemize]{leftmargin=1.2em,itemsep=1pt,topsep=2pt,parsep=0pt}
\titleformat{\section}{\large\bfseries}{}{0em}{}[\titlerule]
\titlespacing{\section}{0pt}{10pt}{5pt}
\pagestyle{empty}
\setlength{\parindent}{0pt}
% A CV contact line is one long unbreakable run of URLs; without these it
% overflows the right margin as an Overfull \hbox.
\sloppy
\hypersetup{breaklinks=true}

\begin{document}
"""

POSTAMBLE = "\n\\end{document}\n"

_LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape_latex(text):
    """Escape LaTeX specials in literal text.

    Only for text we insert ourselves — model output is already expected to be
    valid LaTeX, and escaping it would turn its markup into visible garbage.
    """
    out = []
    for ch in text or "":
        out.append(_LATEX_SPECIALS.get(ch, ch))
    return "".join(out)


def _strip_code_fence(text):
    """Models wrap LaTeX in ```latex fences often enough to handle it here."""
    stripped = (text or "").strip()
    fence = re.match(r"^```(?:latex|tex)?\s*\n(.*?)\n?```$", stripped, re.DOTALL)
    return fence.group(1).strip() if fence else stripped


# Every command the preamble defines or loads, plus the body commands the
# prompt asks for. Because the preamble is ours, this set is knowable — which
# is what makes repairing model output tractable rather than guesswork.
KNOWN_COMMANDS = {
    # structure
    "documentclass", "usepackage", "begin", "end", "item", "section", "subsection",
    "subsubsection", "paragraph", "newpage", "clearpage",
    # text formatting
    "textbf", "textit", "texttt", "emph", "underline", "textsc", "textrm", "textsf",
    "bfseries", "itshape", "ttfamily", "rmfamily", "sffamily", "normalfont",
    "tiny", "scriptsize", "footnotesize", "small", "normalsize", "large", "Large",
    "LARGE", "huge", "Huge",
    # layout
    "hfill", "vfill", "hspace", "vspace", "smallskip", "medskip", "bigskip",
    "centering", "raggedright", "raggedleft", "noindent", "par", "newline",
    "linebreak", "pagebreak", "sloppy", "titlerule", "hrule", "hrulefill", "rule",
    # links and escapes
    "href", "url", "textbackslash", "textasciitilde", "textasciicircum", "LaTeX", "TeX",
    # preamble machinery
    "setlist", "titleformat", "titlespacing", "pagestyle", "setlength", "parindent",
    "parskip", "baselinestretch", "arraystretch", "hypersetup", "PassOptionsToPackage",
    "renewcommand", "newcommand", "def", "label", "ref", "quad", "qquad",
}

_COMMAND_RE = re.compile(r"\\([a-zA-Z]+)")


def sanitize_latex(body):
    """Strip backslashes from control sequences no known package defines.

    Local models reliably over-escape: asked to write "C#/.NET" with the #
    escaped, an 8B model produced "C\\#/.\\NET", and \\NET is an undefined
    control sequence — a hard compile failure with a log that points at the
    line but not the cause.

    Anything outside KNOWN_COMMANDS is treated as text that picked up a stray
    backslash, so "\\NET" becomes "NET". That is the right call for this input:
    a genuinely needed command outside the preamble's packages would fail to
    compile anyway, and losing a backslash degrades to plain text rather than
    to an error the user has to read a TeX log to understand.

    Returns (repaired_text, sorted_list_of_repaired_commands).
    """
    repaired = set()

    def replace(match):
        name = match.group(1)
        if name in KNOWN_COMMANDS:
            return match.group(0)
        repaired.add(name)
        return name

    return _COMMAND_RE.sub(replace, body or ""), sorted(repaired)


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_MD_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_MD_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")


def markdown_to_latex(body):
    """Convert the Markdown that models emit anyway into real LaTeX.

    Asked for LaTeX, a local model still reaches for Markdown habits: it wrote
    "[github.com/x](https://github.com/x)" and "- bullet" instead of \\href and
    itemize. That compiles — Markdown is just text to TeX — so it fails
    silently, producing a PDF with literal brackets, raw URLs, and hyphens
    where bullets should be. The overfull-hbox warnings were the only symptom.

    Handled: inline links, ** bold **, and runs of "- " lines becoming a real
    itemize block. Anything already valid LaTeX passes through untouched.
    """
    text = _MD_LINK_RE.sub(lambda m: rf"\href{{{m.group(2)}}}{{{m.group(1)}}}", body or "")
    text = _MD_BOLD_RE.sub(lambda m: rf"\textbf{{{m.group(1)}}}", text)

    out, bullets = [], []

    def flush():
        if bullets:
            out.append("\\begin{itemize}")
            out.extend(rf"  \item {b}" for b in bullets)
            out.append("\\end{itemize}")
            bullets.clear()

    for line in text.splitlines():
        match = _MD_BULLET_RE.match(line)
        # A line already inside a real itemize keeps its \item and must not be
        # swept into a second, nested list.
        if match and "\\item" not in line:
            bullets.append(match.group(1).rstrip())
            continue
        flush()
        out.append(line)
    flush()
    return "\n".join(out)


_URLISH_RE = re.compile(r"^(?:https?://)?(?:www\.)?[\w.-]+\.[a-z]{2,}(?:/\S*)?$", re.I)


def _link(text):
    """Render a contact/project string, linkifying URLs and emails.

    The model supplies plain text, so this is where a URL becomes a link —
    it never writes \\href itself, which is why it can't get it wrong.
    """
    text = (text or "").strip()
    if "@" in text and " " not in text and _URLISH_RE.match(text.split("@")[-1]):
        return rf"\href{{mailto:{text}}}{{{escape_latex(text)}}}"
    if _URLISH_RE.match(text):
        url = text if text.startswith("http") else f"https://{text}"
        return rf"\href{{{url}}}{{{escape_latex(text)}}}"
    return escape_latex(text)


def _bullets(items):
    lines = [r"\begin{itemize}"]
    lines += [rf"  \item {escape_latex(str(b).strip())}" for b in items if str(b).strip()]
    lines.append(r"\end{itemize}")
    return "\n".join(lines) if len(lines) > 2 else ""


def render_cv_latex(data):
    """Render tailored CV content into the fixed template.

    This is the whole point of the JSON path: the model returns content, and
    every field goes through escape_latex on the way in, so "C#/.NET" and
    "R&D" cannot produce a broken document. There is no model-authored markup
    left to repair, and the layout is identical between runs.
    """
    out = []

    name = escape_latex(str(data.get("name", "")).strip())
    if name:
        out.append(rf"{{\LARGE \textbf{{{name}}}}}\\[2pt]")
    contact = [c for c in (data.get("contact") or []) if str(c).strip()]
    if contact:
        out.append(" $|$ ".join(_link(str(c)) for c in contact) + r"\\[4pt]")

    if str(data.get("summary", "")).strip():
        out.append(r"\section{Summary}")
        out.append(escape_latex(str(data["summary"]).strip()))

    if data.get("experience"):
        out.append(r"\section{Experience}")
        for role in data["experience"]:
            head = escape_latex(str(role.get("org", "")).strip())
            parts = [p for p in (role.get("role"), role.get("dates")) if str(p or "").strip()]
            line = rf"\textbf{{{head}}}"
            if parts:
                line += " — " + " $|$ ".join(escape_latex(str(p).strip()) for p in parts)
            out.append(line)
            out.append(_bullets(role.get("bullets") or []))

    if data.get("projects"):
        out.append(r"\section{Projects}")
        for project in data["projects"]:
            line = rf"\textbf{{{escape_latex(str(project.get('name', '')).strip())}}}"
            extras = []
            if str(project.get("kind", "")).strip():
                extras.append(escape_latex(str(project["kind"]).strip()))
            if str(project.get("link", "")).strip():
                extras.append(_link(str(project["link"])))
            if extras:
                line += " — " + " $|$ ".join(extras)
            out.append(line)
            out.append(_bullets(project.get("bullets") or []))

    skills = data.get("skills") or {}
    if skills:
        out.append(r"\section{Skills}")
        # One category per line: run together, "Hard skills:" and "Soft
        # skills:" read as a single paragraph and the boundary disappears.
        out.append(" \\\\\n".join(
            rf"\textbf{{{escape_latex(str(k))}:}} {escape_latex(str(v))}"
            for k, v in skills.items() if str(v).strip()
        ))

    if data.get("education"):
        out.append(r"\section{Education}")
        for edu in data["education"]:
            line = rf"\textbf{{{escape_latex(str(edu.get('org', '')).strip())}}}"
            if str(edu.get("detail", "")).strip():
                line += " — " + escape_latex(str(edu["detail"]).strip())
            out.append(line)

    return "\n\n".join(part for part in out if part)


def extract_json(text):
    """Pull a JSON object out of a model response, fences and all."""
    import json

    stripped = _strip_code_fence(text or "")
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(stripped[start:end + 1])
        except ValueError:
            return None
    return None


def build_document(body):
    """Wrap model-produced body content in the fixed preamble.

    A full document from the model is passed through untouched — wrapping it
    would produce a nested \\documentclass and a guaranteed compile failure.
    """
    # Preferred path: the model returned JSON content, which is rendered into
    # the fixed template with every field escaped. Freeform LaTeX is only the
    # fallback for a model that ignored the schema, and needs repairing.
    data = extract_json(body)
    if isinstance(data, dict) and any(
        k in data for k in ("name", "summary", "experience", "projects", "education")
    ):
        return PREAMBLE + render_cv_latex(data) + POSTAMBLE

    print("note: model did not return JSON; falling back to repairing raw LaTeX", file=sys.stderr)
    body = _strip_code_fence(body)
    body = markdown_to_latex(body)
    body, repaired = sanitize_latex(body)
    if repaired:
        print(f"note: repaired stray control sequences: {', '.join(repaired)}", file=sys.stderr)
    if "\\documentclass" in body:
        return body if "\\end{document}" in body else body + POSTAMBLE
    return PREAMBLE + body + POSTAMBLE


def find_engine():
    """Return (name, path) of the first available engine, or None."""
    for name in ENGINES:
        path = shutil.which(name)
        if path:
            return name, path
    return None


def _command_for(engine, tex_path, out_dir):
    if engine == "tectonic":
        return [engine, "--outdir", out_dir, "--keep-logs", tex_path]
    if engine == "latexmk":
        return [engine, "-pdf", "-interaction=nonstopmode", f"-outdir={out_dir}", tex_path]
    return [engine, "-interaction=nonstopmode", "-halt-on-error",
            f"-output-directory={out_dir}", tex_path]


class LatexError(RuntimeError):
    pass


def compile_pdf(tex_path, out_dir=None, timeout=120):
    """Compile tex_path to PDF. Raises LatexError with a useful message.

    Runs twice for the plain engines so cross-references settle; tectonic and
    latexmk already handle multi-pass internally.
    """
    found = find_engine()
    if not found:
        raise LatexError(
            "No LaTeX engine found (looked for: " + ", ".join(ENGINES) + ").\n"
            "Install one — 'tectonic' is the smallest, or a full TeX Live — "
            "or use the Overleaf button to compile in the browser instead."
        )
    engine, _ = found
    out_dir = out_dir or os.path.dirname(os.path.abspath(tex_path))
    os.makedirs(out_dir, exist_ok=True)

    passes = 1 if engine in ("tectonic", "latexmk") else 2
    result = None
    for _ in range(passes):
        result = subprocess.run(
            _command_for(engine, tex_path, out_dir),
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            break

    pdf_path = os.path.join(out_dir, os.path.splitext(os.path.basename(tex_path))[0] + ".pdf")
    if result.returncode != 0 or not os.path.exists(pdf_path):
        # TeX logs are long and the useful line is buried; surface the tail.
        log = (result.stdout or "") + (result.stderr or "")
        tail = "\n".join(log.strip().splitlines()[-15:])
        raise LatexError(f"{engine} failed:\n{tail}")
    return pdf_path


def write_tex(body, tex_path):
    os.makedirs(os.path.dirname(os.path.abspath(tex_path)), exist_ok=True)
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(build_document(body))
    return tex_path
