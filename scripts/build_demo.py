"""Build the GitHub Pages demo (docs/index.html).

  python3 scripts/build_demo.py

The page runs migrate/mapper.py in the browser with Pyodide, so a pasted config goes through the
same code as the CLI. The three examples are precomputed here so they show before Python loads.
If out/smoke.json exists (python3 -m migrate smoke), its results are written into the page.
"""

import json
import shutil
import subprocess
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from migrate.mapper import to_phonic  # noqa: E402

DOCS = ROOT / "docs"
EXAMPLES = [("line_cook_screener", "Line cook screener", "the live test agent"),
            ("clinic_front_desk", "Clinic front desk", "HIPAA, SIP transfer"),
            ("store_support_bilingual", "Bilingual store support", "waits for the caller")]


def precompute():
    out = []
    for slug, label, tag in EXAMPLES:
        src = (ROOT / "examples" / f"{slug}.vapi.json").read_text()
        agent, tools, schema, evals, r = to_phonic(json.loads(src))
        out.append({"label": label, "tag": tag, "config": src,
                    "result": {"agent": agent, "tools": tools, "schema": schema, "evals": evals,
                               "report": r.items, "counts": r.counts()}})
    return out


def smoke_section():
    path = ROOT / "out" / "smoke.json"
    if not path.exists():
        return ('<p class="note">Pending. The line cook screener above has been applied to a real Phonic number; '
                'the next step is 20 scripted calls (<code>agent/caller_scripts.md</code>), each graded on the '
                '10 screening criteria by Phonic\'s own eval prompts. Results appear here when they are in.</p>')
    d = json.loads(path.read_text())
    s = d["summary"]
    rows = [("Finished screens", f"{s['completed']} of {s['calls']}"),
            ("Criteria passed", f"{s['criteria_pass']} of {s['criteria_judged']}"),
            ("Critical failures", str(len(s["critical_failures"]))),
            ("Approve or coach call matches the script", f"{s['decision_agree']} of {s['decision_total']}"),
            ("Response latency, p50 / p90", f"{s['latency_p50_ms']:,} / {s['latency_p90_ms']:,} ms"),
            ("Cost per finished screen", f"${s['cost_per_completed_screen']:.2f}")]
    body = "".join(f"<tr><td>{escape(k)}</td><td class='num'>{escape(v)}</td></tr>" for k, v in rows)
    return (f'<p class="note">{escape(d["note"])} Calls since {escape(d["since"])} to agent '
            f'<code>{escape(d["agent"])}</code>, at ${d["price_per_min"]}/min.</p>'
            f'<div class="scroll"><table class="t"><tbody>{body}</tbody></table></div>')


CSS = (ROOT / "scripts" / "demo.css").read_text()
JS = (ROOT / "scripts" / "demo.js").read_text()


def build():
    ex = precompute()
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vapi to Phonic</title>
<meta name="description" content="Paste a Vapi assistant config and get the Phonic agent, its tools, and a field-by-field report of what carries over and what needs a person.">
<style>{CSS}</style>
</head>
<body>
<main>
<div class="head">
  <h1>Vapi to Phonic</h1>
  <p class="lead">A migration kit for moving a voice assistant from Vapi to a Phonic speech-to-speech agent. It reads the Vapi config, writes the Phonic agent and tools, and reports every field: what carries over, what changes, and what a person has to decide before the agent takes calls.</p>
</div>

<section class="desk" id="tool" aria-label="Migrate an assistant">
  <div class="compose">
    <label for="cfg">Vapi assistant config (JSON)</label>
    <textarea id="cfg" spellcheck="false"></textarea>
    <div class="act">
      <button class="go" id="go" type="button">Migrate</button>
      <span class="kbd">or Ctrl / Cmd + Enter</span>
    </div>
    <div class="loadmsg busy" id="loadmsg" role="status"><i aria-hidden="true"></i><span>Loading Python in your browser (about 10 seconds the first time). The examples work now.</span></div>
    <div class="exlab" id="exlab">Examples</div>
    <div class="exlist" id="exlist" role="group" aria-labelledby="exlab"></div>
  </div>

  <article class="doc" id="out" aria-live="polite"></article>

  <div class="rec" id="made">
    <h2>What gets created on Phonic</h2>
    <p>The agent and tool bodies <code>migrate apply</code> sends to Phonic's API.</p>
    <dl class="kv" id="kv"></dl>
    <details class="raw"><summary>Full request bodies (JSON)</summary><pre id="rawjson"></pre></details>
  </div>
</section>

<div class="layout">
<div class="content">

<section id="how">
  <h2>How it works</h2>
  <p class="point">Moving an assistant is mostly bookkeeping with a few real decisions hidden in it. The kit does the bookkeeping and puts the decisions in front of a person: a voice has to be picked by ear, a webhook server has to accept a new request shape, a transfer to a SIP line has to be set up by hand. Nothing is dropped without a line in the report.</p>
  <div class="steps">
    <div class="step"><h3><span>1</span>Read</h3><p><b>The Vapi config as exported.</b> Prompt, greeting, model, voice, transcriber, tools, timeouts, turn-taking, analysis plan, compliance and recording settings.</p></div>
    <div class="step"><h3><span>2</span>Map</h3><p><b>Field by field to Phonic's agent and tools APIs.</b> Function tools become webhooks with a schema Phonic accepts; end-call phrases and closing lines move into the prompt; seconds become named turn-taking levels.</p></div>
    <div class="step"><h3><span>3</span>Report</h3><p><b>Four statuses.</b> Mapped, adjusted, needs a person, not needed. Unknown fields are reported too. <code>apply</code> refuses to run while anything needs a person, until someone has read the list.</p></div>
    <div class="step"><h3><span>4</span>Apply and test</h3><p><b>On a real Phonic account.</b> Creates the tools, the agent and its phone number, the extraction schema and eval prompts, then grades scripted test calls with Phonic's own evals.</p></div>
  </div>
</section>

<section id="smoke">
  <h2>Smoke test on live calls</h2>
  {smoke_section()}
</section>

<section id="limits">
  <h2>Limits</h2>
  <ul class="lim">
    <li>Vapi voices have no Phonic twin, so a person picks the voice by ear. The kit sets a placeholder.</li>
    <li>Vapi and Phonic call webhooks differently. The kit moves the tool and its schema; the server behind it still has to accept Phonic's request and response.</li>
    <li>Vapi's call-event server (status updates, end-of-call reports) and SMS tools have no direct Phonic equivalent and stay on the list for a person.</li>
    <li>Field coverage follows the public docs of both APIs as of November 24, 2026. A field the mapper does not know is reported, never dropped silently.</li>
  </ul>
</section>
</div>

<aside class="side" aria-label="About this build">
  <nav aria-label="On this page"><a href="#tool">Migrate an assistant</a><a href="#how">How it works</a><a href="#smoke">Smoke test</a><a href="#limits">Limits</a></nav>
  <div><h3>Data</h3><p>Field names come from Vapi's public API reference (docs.vapi.ai) and Phonic's (docs.phonic.ai), read November 24, 2026. The three assistants are invented; their phone numbers (555) and URLs (example.com) are placeholders. Nothing you paste leaves your browser.</p></div>
  <div><h3>Run it yourself</h3><p>Python 3.9+, standard library only. <code>apply</code> and <code>smoke</code> need a Phonic API key.</p><pre>python3 -m migrate plan examples/line_cook_screener.vapi.json
python3 -m migrate apply examples/line_cook_screener.vapi.json --accept-manual
python3 -m migrate smoke --since 2026-11-24</pre></div>
  <div><a class="pill" href="https://github.com/pg2342-hub/vapi-to-phonic">Code on GitHub</a></div>
</aside>
</div>
</main>
<script src="https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js"></script>
<script id="examples" type="application/json">{json.dumps(ex).replace("</", "<\\/")}</script>
<script>
{JS}
</script>
</body>
</html>
"""
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(html)
    shutil.copy(ROOT / "migrate" / "mapper.py", DOCS / "mapper.py")
    (DOCS / ".nojekyll").write_text("")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "demo_chrome.py"), str(DOCS / "index.html"),
                    "--repo", "vapi-to-phonic"], check=True)
    print("wrote docs/index.html")


if __name__ == "__main__":
    build()
