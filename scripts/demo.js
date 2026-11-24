// The examples are precomputed by scripts/build_demo.py with migrate/mapper.py; a pasted config
// runs through the same mapper.py in the browser (Pyodide).
const EXAMPLES = JSON.parse(document.getElementById("examples").textContent);
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const LABEL = {manual: "Needs a person", adjusted: "Adjusted on the way", mapped: "Mapped as is", dropped: "Not needed on Phonic"};
const SHORT = {manual: "need a person", adjusted: "adjusted", mapped: "mapped", dropped: "not needed"};
const ORDER = ["manual", "adjusted", "mapped", "dropped"];
let py = null, pending = false, failed = false;

const exlist = $("#exlist");
EXAMPLES.forEach(ex => {
  const b = document.createElement("button");
  b.type = "button"; b.className = "ex"; b.setAttribute("aria-pressed", "false");
  b.innerHTML = esc(ex.label) + "<small>" + esc(ex.tag) + "</small>";
  b.addEventListener("click", () => { $("#cfg").value = ex.config; run(); });
  exlist.appendChild(b);
});
function markChip(){
  const v = $("#cfg").value.trim();
  [...exlist.children].forEach((b, i) => b.setAttribute("aria-pressed", String(EXAMPLES[i].config.trim() === v)));
}

function render(res, cfg, live){
  const c = res.counts, total = ORDER.reduce((s, k) => s + c[k], 0);
  const review = c.manual > 0;
  let h = `<header class="dhead"><div><h2>Phonic agent ${esc(res.agent.slug)}</h2>` +
    `<div class="dsub">from Vapi assistant "${esc(cfg.name || "unnamed")}" · ${total} fields reported · ${res.tools.length} tool${res.tools.length === 1 ? "" : "s"} to create</div></div>` +
    `<span class="status ${review ? "s-review" : "s-ready"}">${review ? c.manual + " TO REVIEW" : "READY TO APPLY"}</span></header>`;
  h += `<div class="fields"><div class="fbar" role="img" aria-label="${ORDER.map(k => c[k] + " " + SHORT[k]).join(", ")}">` +
    ORDER.filter(k => c[k]).map(k => `<span class="st-${k}" style="flex:${c[k]} 0 0"></span>`).join("") + `</div>` +
    `<ul class="fleg">` + ORDER.map(k => `<li><i class="st-${k}"></i><b>${c[k]}</b> ${SHORT[k]}</li>`).join("") + `</ul></div>`;
  h += `<div class="dpad">`;
  for (const k of ORDER){
    const items = res.report.filter(i => i.status === k);
    if (!items.length) continue;
    const list = `<ul>` + items.map(i => `<li><code>${esc(i.vapi)}</code>${i.phonic ? `<span class="to">to ${esc(i.phonic)}</span>` : ""}<p>${esc(i.note)}</p></li>`).join("") + `</ul>`;
    const head = `<h3><i class="st-${k}"></i>${LABEL[k]} (${items.length})</h3>`;
    h += (k === "manual" || k === "adjusted") ? `<div class="part">${head}${list}</div>`
      : `<details class="part"><summary>${head}</summary>${list}</details>`;
  }
  h += `</div><p class="prov${live ? " live" : ""}"><i></i>${live ? "Mapped just now in your browser by migrate/mapper.py." : "Precomputed by migrate/mapper.py, the same code the CLI runs."}</p>`;
  $("#out").classList.remove("stale");
  $("#out").innerHTML = h;
  const a = res.agent;
  const kv = [["name", a.slug], ["phone_number", a.phone_number], ["voice_id", a.voice_id], ["default_language", a.default_language || "en"],
    ["turn_taking", a.turn_taking || "standard"], ["welcome_message", a.generate_welcome_message ? "(generated)" : a.welcome_message],
    ["tools", (a.tools || []).join(", ") || "none"], ["template_variables", Object.keys(a.template_variables || {}).join(", ") || "none"],
    ["extraction fields", res.schema ? res.schema.fields.map(f => f.name).join(", ") : "none"], ["eval prompts", String(res.evals.length)]];
  $("#kv").innerHTML = kv.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v == null ? "null" : v)}</dd>`).join("");
  $("#rawjson").textContent = JSON.stringify({agent: a, tools: res.tools, extraction_schema: res.schema, eval_prompts: res.evals}, null, 2);
  markChip();
}

function setMsg(cls, text){ const m = $("#loadmsg"); m.className = "loadmsg " + cls; m.querySelector("span").textContent = text; }
function fail(title, msg){ $("#out").classList.remove("stale"); $("#out").innerHTML = `<div class="empty"><b>${esc(title)}</b>${esc(msg)}</div>`; markChip(); }

function run(){
  const text = $("#cfg").value.trim();
  if (!text) return;
  let cfg;
  try { cfg = JSON.parse(text); } catch (e) { return fail("That is not valid JSON", e.message); }
  if (!cfg || typeof cfg !== "object" || Array.isArray(cfg)) return fail("Expected one assistant object", "Paste the JSON of a single Vapi assistant.");
  const ex = EXAMPLES.find(e => e.config.trim() === text);
  if (!py){
    if (ex){ pending = false; return render(ex.result, cfg, false); }
    if (failed){ markChip(); return setMsg("err", "Python did not load, so only the examples can be shown. Reload, or run python3 -m migrate plan on your config."); }
    pending = true; $("#out").classList.add("stale"); markChip();
    return setMsg("busy", "Python is still loading. Your config will be mapped as soon as it is ready.");
  }
  try { render(JSON.parse(py.globals.get("migrate_json")(text)), cfg, true); }
  catch (e) { fail("Something went wrong mapping that config", e.message.split("\n").slice(-2).join(" ")); }
}

async function boot(){
  try {
    const pyo = await loadPyodide();
    const r = await fetch("mapper.py", {cache: "no-cache"});
    if (!r.ok) throw new Error("could not load mapper.py");
    pyo.FS.writeFile("/home/pyodide/mapper.py", await r.text());
    pyo.runPython(`
import json, sys
sys.path.insert(0, "/home/pyodide")
from mapper import to_phonic
def migrate_json(text):
    agent, tools, schema, evals, r = to_phonic(json.loads(text))
    return json.dumps({"agent": agent, "tools": tools, "schema": schema, "evals": evals, "report": r.items, "counts": r.counts()})
`);
    py = pyo;
    setMsg("ok", "Ready. Runs entirely in your browser; nothing is sent anywhere.");
    if (pending) run();
  } catch (e) {
    failed = true;
    setMsg("err", "Python failed to load (" + e.message + "). The examples still show their precomputed results.");
  }
}

$("#go").addEventListener("click", run);
$("#cfg").addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); run(); } });
$("#cfg").addEventListener("input", markChip);
$("#cfg").value = EXAMPLES[0].config;
run();
if (typeof loadPyodide === "function") boot();
else { failed = true; setMsg("err", "Python could not be loaded from the CDN. The examples still show their precomputed results."); }
