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
import shutil
import subprocess

# Ordered by preference. tectonic downloads what a document needs on demand,
# so it's the most likely to succeed on a machine with no full TeX install;
# latexmk handles multi-pass runs for us; the rest are plain single-pass.
ENGINES = ("tectonic", "latexmk", "pdflatex", "xelatex", "lualatex")

LATEX_PROMPT = r"""You are tailoring a candidate's CV to a job description, as LaTeX.

Reorder and reframe only — never invent experience, metrics, or skills not present in the CV.

Output LaTeX BODY CONTENT ONLY: no \documentclass, no \usepackage, no
\begin{document} or \end{document}. Assume article class with these already
available: \section, \subsection, itemize, \textbf, \emph, \hfill, hyperref.

Structure it as: a name heading, contact line, then \section blocks for
Summary, Experience, Projects, Skills, Education — omitting any the CV has
nothing for. Use itemize for bullets. Keep it to one page of content.

Escape LaTeX special characters in any literal text: & % $ # _ { } ~ ^ \
(for example, "C#" must be written as "C\#", "R&D" as "R\&D").

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
\usepackage[hidelinks]{hyperref}
\usepackage{titlesec}

\setlist[itemize]{leftmargin=1.2em,itemsep=1pt,topsep=2pt,parsep=0pt}
\titleformat{\section}{\large\bfseries}{}{0em}{}[\titlerule]
\titlespacing{\section}{0pt}{10pt}{5pt}
\pagestyle{empty}
\setlength{\parindent}{0pt}

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


def build_document(body):
    """Wrap model-produced body content in the fixed preamble.

    A full document from the model is passed through untouched — wrapping it
    would produce a nested \\documentclass and a guaranteed compile failure.
    """
    body = _strip_code_fence(body)
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
