"""Local triage UI. Manual-only — never invoked by run.py or the workflow.

    python -m src.ui          # then open http://127.0.0.1:8765

Why this exists: Discord is a good push channel and a terrible review surface.
It only ever shows jobs at or above the score threshold, so everything the
heuristic scored 40-59 is invisible — on the current store that's 50 of 54
jobs. It also can't record what happened next.

This serves the other half: see everything the scanner found (not just what
cleared the bar), record status, and generate a tailored CV against whatever
endpoint scoring.api_base points at. That last part is why this is a server
and not a generated static page — when you're at the machine with a local
model running, tailoring is free and unlimited, which is exactly when this is
worth opening.

The LaTeX path is the reviewable one: the model emits .tex, you edit it in the
browser, recompile, and see the PDF. If no TeX engine is installed the .tex is
still written and the Overleaf button hands it to their compiler instead.

PRIVACY: the Overleaf button is the one action here that sends CV content off
the machine, to overleaf.com. It only fires on an explicit click. Everything
else stays local except the model call itself, which goes wherever
scoring.api_base points (nowhere, if that's localhost).

Deliberately stdlib http.server: no dependency, no build step, nothing to
deploy. Binds to loopback only; meant to be run for a few minutes and closed.
"""

import json
import os
import re
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.applications import load_applications, record_application, save_applications
from src.seen import load_seen

HOST = "127.0.0.1"
PORT = int(os.environ.get("CAREER_BOT_UI_PORT", "8765"))
OUTPUT_DIR = "output"

# Serialize writes: two browser tabs posting at once would otherwise
# read-modify-write applications.json over each other.
_WRITE_LOCK = threading.Lock()

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_CONTENT_TYPES = {".pdf": "application/pdf", ".html": "text/html; charset=utf-8",
                  ".tex": "text/plain; charset=utf-8", ".docx":
                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def _slug(value, fallback="job"):
    slug = _SLUG_RE.sub("-", (value or "").lower()).strip("-")
    return slug[:60] or fallback


def _safe_output_path(name):
    """Resolve name inside OUTPUT_DIR, or None if it escapes.

    The browser supplies this, so a bare basename check isn't enough — resolve
    and confirm containment.
    """
    base = os.path.abspath(OUTPUT_DIR)
    target = os.path.abspath(os.path.join(base, os.path.basename(name or "")))
    return target if target.startswith(base + os.sep) else None


def build_rows():
    """Merge the dedup store with application state into one view model."""
    seen = load_seen()
    applications = load_applications()
    rows = []
    for url, entry in seen.items():
        app = applications.get(url, {})
        rows.append({
            "url": url,
            "title": entry.get("title", ""),
            "company": entry.get("company", ""),
            "score": entry.get("score", 0),
            "first_seen": entry.get("first_seen", ""),
            "status": app.get("status", ""),
            "history": app.get("history", []),
        })
    rows.sort(key=lambda r: (r["score"], r["first_seen"]), reverse=True)
    return rows


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>career-bot triage</title>
<style>
:root{--bg:#fbfbfa;--fg:#1a1a18;--mut:#6b6b66;--line:#e3e3df;--card:#fff;--accent:#3a6ea5;--warn:#9a5b1a;}
@media(prefers-color-scheme:dark){:root{--bg:#16161a;--fg:#e8e8e4;--mut:#96968f;--line:#2c2c33;--card:#1e1e24;--accent:#7aa7d4;--warn:#d8a05a;}}
*{box-sizing:border-box}
body{margin:0;font:15px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
h1{font-size:17px;margin:0;font-weight:600;letter-spacing:-.01em}
.meta{color:var(--mut);font-size:13px}
main{padding:20px 24px;max-width:1100px}
.controls{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}
input,select,button,textarea{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:7px;padding:7px 10px}
input[type=search]{min-width:230px}
button{cursor:pointer}button:hover{border-color:var(--accent)}
button[disabled]{opacity:.5;cursor:default}
.job{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:13px 15px;margin-bottom:9px}
.job-top{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
.score{font-variant-numeric:tabular-nums;font-weight:600;min-width:2.2em}
.s-hi{color:#1c7c4a}.s-mid{color:#9a6a1a}.s-lo{color:var(--mut)}
@media(prefers-color-scheme:dark){.s-hi{color:#5fbd87}.s-mid{color:#d0a34e}}
.title{font-weight:550}.co{color:var(--mut)}
.row2{margin-top:7px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.tag{font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:var(--mut)}
.tag.set{border-color:var(--accent);color:var(--accent)}
a{color:var(--accent)}
.hist{font-size:12px;color:var(--mut);margin-top:6px}
.empty{color:var(--mut);padding:30px 0}
.err{color:#c0392b;white-space:pre-wrap}
@media(prefers-color-scheme:dark){.err{color:#e8776a}}
dialog{border:1px solid var(--line);border-radius:11px;background:var(--card);color:var(--fg);padding:0;max-width:none;width:min(1500px,96vw);height:92vh}
dialog::backdrop{background:rgba(0,0,0,.5)}
.dlg{display:flex;flex-direction:column;height:100%}
.dlg-head{padding:14px 18px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.dlg-body{flex:1;display:flex;min-height:0}
.pane{flex:1;display:flex;flex-direction:column;min-width:0}
.pane+.pane{border-left:1px solid var(--line)}
.pane-head{padding:7px 14px;border-bottom:1px solid var(--line);font-size:12px;color:var(--mut);display:flex;gap:10px;align-items:center}
textarea{flex:1;width:100%;border:0;border-radius:0;resize:none;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
textarea:focus{outline:0}
iframe{flex:1;width:100%;border:0;background:#fff}
.pane-msg{padding:16px;color:var(--mut);font-size:13px;overflow:auto}
.note{color:var(--warn);font-size:12px}
</style></head><body>
<header>
  <h1>career-bot triage</h1>
  <span class="meta" id="summary"></span>
  <span class="meta" id="endpoint"></span>
</header>
<main>
  <div class="controls">
    <input type="search" id="q" placeholder="filter title or company">
    <select id="status"><option value="">any status</option><option value="none">untracked</option>
      <option>interested</option><option>applied</option><option>screening</option>
      <option>interview</option><option>offer</option><option>rejected</option><option>withdrawn</option></select>
    <select id="minscore"><option value="0">any score</option><option value="40">40+</option>
      <option value="50">50+</option><option value="60">60+ (notified)</option></select>
  </div>
  <div id="list"></div>
</main>

<dialog id="tailor"><div class="dlg">
  <div class="dlg-head">
    <strong id="tailor-job"></strong>
    <span class="meta" id="tailor-status"></span>
    <span style="margin-left:auto;display:flex;gap:8px">
      <button id="btn-generate">Generate LaTeX</button>
      <button id="btn-compile">Recompile</button>
      <button id="btn-overleaf" title="Sends this CV to overleaf.com">Open in Overleaf ↗</button>
      <button onclick="document.getElementById('tailor').close()">Close</button>
    </span>
  </div>
  <div class="dlg-body">
    <div class="pane">
      <div class="pane-head">job description — paste it here first</div>
      <textarea id="jd" placeholder="Paste the job description.

Descriptions aren't stored in seen.json (it's committed every run and would bloat it), so paste the JD you want to tailor against."></textarea>
    </div>
    <div class="pane">
      <div class="pane-head">LaTeX source — editable, then Recompile</div>
      <textarea id="tex" spellcheck="false" placeholder="Generated .tex appears here."></textarea>
    </div>
    <div class="pane">
      <div class="pane-head">PDF preview</div>
      <div id="pdf-wrap" style="flex:1;display:flex;min-height:0">
        <div class="pane-msg" id="pdf-msg">Nothing compiled yet.</div>
      </div>
    </div>
  </div>
</div></dialog>

<form id="overleaf-form" method="post" action="https://www.overleaf.com/docs" target="_blank" style="display:none">
  <input type="hidden" name="snip" id="overleaf-snip">
  <input type="hidden" name="snip_name" id="overleaf-name">
</form>

<script>
const STATUSES=["interested","applied","screening","interview","offer","rejected","withdrawn"];
let rows=[],tailorUrl=null,texName=null;
const el=s=>document.querySelector(s);
const esc=s=>(s||"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

async function load(){
  const d=await (await fetch("/api/jobs")).json();
  rows=d.rows; el("#endpoint").textContent=d.endpoint_label; render();
}
const scoreClass=s=>s>=60?"s-hi":s>=45?"s-mid":"s-lo";
function render(){
  const q=el("#q").value.toLowerCase(),st=el("#status").value,ms=+el("#minscore").value;
  const shown=rows.filter(r=>(!q||(r.title+" "+r.company).toLowerCase().includes(q))
    &&(!st||(st==="none"?!r.status:r.status===st))&&r.score>=ms);
  el("#summary").textContent=`${shown.length} of ${rows.length} jobs · ${rows.filter(r=>r.status).length} tracked`;
  el("#list").innerHTML=shown.length?shown.map(card).join(""):'<div class="empty">nothing matches that filter</div>';
}
function card(r){
  const hist=r.history?.length?`<div class="hist">${r.history.map(h=>`${h.date} → ${h.status}${h.note?" · "+esc(h.note):""}`).join(" &nbsp;|&nbsp; ")}</div>`:"";
  return `<div class="job"><div class="job-top">
      <span class="score ${scoreClass(r.score)}">${r.score}</span>
      <span class="title">${esc(r.title)||"(untitled)"}</span>
      <span class="co">${esc(r.company)}</span>
      <span class="meta">first seen ${r.first_seen}</span></div>
    <div class="row2">
      <a href="${esc(r.url)}" target="_blank" rel="noopener">open posting ↗</a>
      <span class="tag ${r.status?"set":""}">${r.status||"untracked"}</span>
      <select onchange="setStatus('${esc(r.url)}',this.value)">
        <option value="">set status…</option>
        ${STATUSES.map(s=>`<option ${s===r.status?"selected":""}>${s}</option>`).join("")}
      </select>
      <button onclick="openTailor('${esc(r.url)}')">tailor CV</button>
    </div>${hist}</div>`;
}
async function setStatus(url,status){
  if(!status)return;
  await fetch("/api/status",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url,status})});
  await load();
}
function openTailor(url){
  tailorUrl=url;texName=null;
  const r=rows.find(x=>x.url===url);
  el("#tailor-job").textContent=`${r.title} · ${r.company}`;
  el("#tailor-status").textContent="";el("#jd").value="";el("#tex").value="";
  el("#pdf-wrap").innerHTML='<div class="pane-msg" id="pdf-msg">Nothing compiled yet.</div>';
  el("#tailor").showModal();
}
function status(msg,isErr){el("#tailor-status").innerHTML=isErr?`<span class="err">${esc(msg)}</span>`:esc(msg)}
function showPdf(url){el("#pdf-wrap").innerHTML=`<iframe src="${esc(url)}#toolbar=1"></iframe>`}
function showPdfMsg(msg){el("#pdf-wrap").innerHTML=`<div class="pane-msg err">${esc(msg)}</div>`}

el("#btn-generate").onclick=async()=>{
  const jd=el("#jd").value.trim();
  if(!jd)return status("paste a job description first",true);
  status("generating…");el("#btn-generate").disabled=true;
  try{
    const d=await (await fetch("/api/tailor",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({url:tailorUrl,jd,fmt:"latex"})})).json();
    if(!d.ok){status(d.error,true)}
    else{
      el("#tex").value=d.tex;texName=d.name;
      status(`wrote ${d.tex_path}`);
      if(d.pdf_url){showPdf(d.pdf_url)}else{showPdfMsg(d.compile_error||"not compiled")}
    }
  }catch(e){status(String(e),true)}
  el("#btn-generate").disabled=false;
};
el("#btn-compile").onclick=async()=>{
  if(!texName)return status("generate first",true);
  status("compiling…");el("#btn-compile").disabled=true;
  try{
    const d=await (await fetch("/api/compile",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({name:texName,tex:el("#tex").value})})).json();
    if(d.ok){showPdf(d.pdf_url);status("compiled")}
    else{showPdfMsg(d.error);status("compile failed",true)}
  }catch(e){status(String(e),true)}
  el("#btn-compile").disabled=false;
};
el("#btn-overleaf").onclick=()=>{
  const tex=el("#tex").value.trim();
  if(!tex)return status("nothing to send — generate first",true);
  el("#overleaf-snip").value=tex;
  el("#overleaf-name").value=(texName||"cv")+".tex";
  el("#overleaf-form").submit();
  status("sent to overleaf.com in a new tab");
};
["#q","#status","#minscore"].forEach(s=>el(s).addEventListener("input",render));
load();
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # default per-request stderr spam is noise for a local tool

    def _send(self, code, body, content_type="application/json"):
        payload = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    # -- GET ---------------------------------------------------------------

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "/api/jobs":
            from src.score import _is_openrouter, _resolve_api_base, _resolve_model

            cfg = _load_scoring_cfg()
            base = _resolve_api_base(cfg)
            # Show the model too: pointing at Ollama while still sending an
            # OpenRouter slug fails confusingly, and this makes it obvious.
            label = f"{_resolve_model(cfg)} @ {base}" + ("" if _is_openrouter(base) else "  (local)")
            return self._send(200, json.dumps({"rows": build_rows(), "endpoint_label": label}))
        if path.startswith("/output/"):
            return self._serve_output(path[len("/output/"):])
        return self._send(404, json.dumps({"error": "not found"}))

    def _serve_output(self, name):
        target = _safe_output_path(urllib.parse.unquote(name))
        if not target or not os.path.isfile(target):
            return self._send(404, json.dumps({"error": "not found"}))
        ext = os.path.splitext(target)[1].lower()
        with open(target, "rb") as f:
            self._send(200, f.read(), _CONTENT_TYPES.get(ext, "application/octet-stream"))

    # -- POST --------------------------------------------------------------

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            data = self._json_body()
        except (ValueError, json.JSONDecodeError):
            return self._send(400, json.dumps({"ok": False, "error": "invalid JSON"}))
        if path == "/api/status":
            return self._handle_status(data)
        if path == "/api/tailor":
            return self._handle_tailor(data)
        if path == "/api/compile":
            return self._handle_compile(data)
        return self._send(404, json.dumps({"ok": False, "error": "not found"}))

    def _handle_status(self, data):
        url, status = data.get("url"), data.get("status")
        if not url or not status:
            return self._send(400, json.dumps({"ok": False, "error": "url and status required"}))
        known = load_seen().get(url, {})
        with _WRITE_LOCK:
            applications = load_applications()
            record_application(
                {"url": url, "title": known.get("title", ""),
                 "company": known.get("company", ""), "score": known.get("score", 0)},
                applications, status, data.get("note", ""),
            )
            save_applications(applications)
        return self._send(200, json.dumps({"ok": True}))

    def _handle_tailor(self, data):
        jd = (data.get("jd") or "").strip()
        if not jd:
            return self._send(400, json.dumps({"ok": False, "error": "job description required"}))

        from src.render_latex import LatexError, compile_pdf, write_tex
        from src.tailor import tailor_cv

        seen = load_seen().get(data.get("url"), {})
        name = _slug(f"{seen.get('company', '')}-{seen.get('title', '')}")
        try:
            with open("cv.md", "r", encoding="utf-8") as f:
                cv_text = f.read()
            body = tailor_cv(cv_text, jd, fmt="latex")
        except SystemExit as e:  # missing API key on the OpenRouter path
            return self._send(200, json.dumps({"ok": False, "error": str(e)}))
        except Exception as e:  # noqa: BLE001 - surface it in the UI, don't 500
            return self._send(200, json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))

        tex_path = write_tex(body, os.path.join(OUTPUT_DIR, f"{name}.tex"))
        with open(tex_path, "r", encoding="utf-8") as f:
            tex = f.read()

        payload = {"ok": True, "name": name, "tex": tex, "tex_path": tex_path}
        # A missing TeX engine is not a failure of the run: the .tex is the
        # deliverable, and Overleaf can compile it.
        try:
            compile_pdf(tex_path, OUTPUT_DIR)
            payload["pdf_url"] = f"/output/{name}.pdf"
        except LatexError as e:
            payload["compile_error"] = str(e)
        except Exception as e:  # noqa: BLE001 - a broken engine must not lose the .tex
            payload["compile_error"] = f"{type(e).__name__}: {e}"
        return self._send(200, json.dumps(payload))

    def _handle_compile(self, data):
        """Recompile edited .tex from the browser."""
        from src.render_latex import LatexError, compile_pdf

        name, tex = _slug(data.get("name") or ""), data.get("tex") or ""
        target = _safe_output_path(f"{name}.tex")
        if not target:
            return self._send(400, json.dumps({"ok": False, "error": "bad name"}))
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(tex)
        try:
            compile_pdf(target, OUTPUT_DIR)
        except LatexError as e:
            return self._send(200, json.dumps({"ok": False, "error": str(e)}))
        except Exception as e:  # noqa: BLE001
            return self._send(200, json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
        # Cache-bust so the iframe reloads the new PDF rather than the old one.
        return self._send(200, json.dumps({
            "ok": True, "pdf_url": f"/output/{name}.pdf?v={os.path.getmtime(target):.0f}"}))


def _load_scoring_cfg():
    try:
        import yaml

        with open("config/profile.yml", "r", encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("scoring", {})
    except FileNotFoundError:
        return {}


def main():
    if not os.path.exists("seen.json"):
        print("warning: no seen.json here — run this from the repo root", file=sys.stderr)
    from src.render_latex import find_engine

    engine = find_engine()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"career-bot triage UI  ->  http://{HOST}:{PORT}   (ctrl-c to stop)")
    print(f"LaTeX engine: {engine[0] if engine else 'none found — use the Overleaf button'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
