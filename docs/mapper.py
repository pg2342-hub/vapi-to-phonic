"""Map a Vapi assistant config to a Phonic agent, Phonic tools, and a gap report.

Pure functions, no network. migrate.cli applies the result through Phonic's API.

Every Vapi field the mapper reads ends up in the report with one of four statuses:
  mapped    same behavior on Phonic
  adjusted  carried over with a change a person should glance at
  manual    needs a person before go-live (no safe automatic mapping)
  dropped   not needed on Phonic (for example the separate speech-to-text model)
Fields the mapper does not know are reported as "manual" so nothing is lost silently.
"""

import re

STATUSES = ("mapped", "adjusted", "manual", "dropped")

PHONIC_LANGS = {"ar", "az", "bg", "bn", "cs", "da", "de", "el", "en", "es", "fa", "fi", "fil", "fr", "gu",
                "he", "hi", "hu", "id", "it", "ja", "ka", "km", "kn", "ko", "lt", "lv", "ml", "mr", "ms",
                "ne", "nl", "no", "pa", "pl", "pt", "ro", "ru", "si", "sk", "sq", "sv", "sw", "ta", "te",
                "th", "tr", "uk", "ur", "vi", "yue", "zh"}
PHONIC_NOISE = {"office": "office"}
RESERVED_PARAMS = {"call_info", "conversation_id", "from_phone_number", "pre_tool_speech", "pre_tool_text",
                   "to_phone_number", "twilio_call_sid"}
SCHEMA_KEYS = {"type", "description", "enum", "items", "properties", "required", "anyOf"}
VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "migrated-agent").lower()).strip("-")
    return s or "migrated-agent"


def snake(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def turn_taking(wait_seconds):
    """Vapi's startSpeakingPlan.waitSeconds (default 0.4) to Phonic's named turn-taking levels."""
    for limit, level in ((0.2, "eager"), (0.5, "standard"), (0.8, "relaxed"), (1.2, "patient")):
        if wait_seconds <= limit:
            return level
    return "very_patient"


class Report:
    def __init__(self):
        self.items = []

    def add(self, field, status, note, phonic=None):
        assert status in STATUSES
        self.items.append({"vapi": field, "status": status, "phonic": phonic, "note": note})

    def counts(self):
        return {s: sum(1 for i in self.items if i["status"] == s) for s in STATUSES}


def clean_schema(schema, path, report, depth=0):
    """Make a JSON Schema fit Phonic's subset: no type lists, no $ref, strings-only enums,
    additionalProperties false, at most 5 levels."""
    if depth > 5:
        report.add(path, "manual", "Nested deeper than the 5 levels Phonic accepts; flatten it.")
        return {"type": "string", "description": schema.get("description", "")}
    out = {}
    for k, v in schema.items():
        if k == "type" and isinstance(v, list):
            types = [t for t in v if t != "null"]
            out["anyOf"] = [{"type": t} for t in types] + ([{"type": "null"}] if "null" in v else [])
            report.add(f"{path}.type", "adjusted", "Type list rewritten as anyOf, which is the form Phonic accepts.")
        elif k == "enum":
            out["enum"] = [str(x) for x in v]
        elif k == "items":
            out["items"] = clean_schema(v, path + ".items", report, depth + 1)
        elif k == "properties":
            out["properties"] = {n: clean_schema(p, f"{path}.{n}", report, depth + 1) for n, p in v.items()}
        elif k in SCHEMA_KEYS:
            out[k] = v
        elif k == "additionalProperties":
            continue
        else:
            report.add(f"{path}.{k}", "dropped", f"Phonic's tool schemas do not support '{k}'.")
    if out.get("type") == "object":
        out["additionalProperties"] = False
        out.setdefault("properties", {})
        out["required"] = [r for r in out.get("required", []) if r in out["properties"]]
    return out


def map_function_tool(t, report, i, prefix):
    fn = t.get("function") or {}
    name = f"{prefix}_{snake(fn.get('name') or f'tool_{i}')}"[:64]
    where = f"model.tools[{i}] (function {name})"
    params = clean_schema(fn.get("parameters") or {"type": "object", "properties": {}}, where + ".parameters", report)
    clash = RESERVED_PARAMS & set(params.get("properties", {}))
    for p in sorted(clash):
        params["properties"][f"{p}_value"] = params["properties"].pop(p)
        params["required"] = [f"{p}_value" if r == p else r for r in params["required"]]
        report.add(f"{where}.{p}", "adjusted", f"'{p}' is reserved on Phonic; renamed to '{p}_value'. Update the server.")
    url = (t.get("server") or {}).get("url")
    tool = {"name": name, "description": fn.get("description") or name, "type": "custom_webhook",
            "execution_mode": "async" if t.get("async") else "sync", "parameters": params,
            "require_speech_before_tool_call": True,
            "endpoint_method": "POST", "endpoint_url": url,
            "parameter_locations": {p: "request_body" for p in params.get("properties", {})}}
    if not url:
        report.add(where, "manual", "No server URL on the tool; set endpoint_url before applying.", name)
    report.add(where, "adjusted", "Becomes a Phonic custom_webhook tool posting the same arguments. Vapi wraps "
               "calls in a message envelope and expects {\"results\": [...]} back; check the server handles "
               "Phonic's request and response shape.", name)
    return tool


def map_tools(tools, report, prefix):
    phonic_tools, builtins, prompt_lines = [], [], []
    for i, t in enumerate(tools or []):
        kind = t.get("type")
        if kind == "endCall":
            builtins.append("natural_conversation_ending")
            report.add(f"model.tools[{i}] (endCall)", "mapped", "Phonic's built-in natural conversation ending.",
                       "natural_conversation_ending")
        elif kind == "transferCall":
            dests = t.get("destinations") or [{}]
            for j, d in enumerate(dests):
                num = d.get("number")
                name = f"{prefix}_transfer" + (f"_{j + 1}" if len(dests) > 1 else "")
                tool = {"name": name, "description": d.get("description") or f"Transfer the call to {num}",
                        "type": "built_in_transfer_to_phone_number", "execution_mode": "sync",
                        "phone_number": num}
                if d.get("message") and d.get("type", "number") == "number" and num:
                    prompt_lines.append(f"Before transferring with {name}, say: \"{d['message']}\"")
                if d.get("type", "number") == "number" and num:
                    phonic_tools.append(tool)
                    report.add(f"model.tools[{i}].destinations[{j}]", "mapped",
                               "Phonic built-in transfer to the same number.", name)
                else:
                    report.add(f"model.tools[{i}].destinations[{j}]", "manual",
                               "Only phone-number destinations carry over; set up SIP and assistant transfers by hand.")
        elif kind == "function":
            phonic_tools.append(map_function_tool(t, report, i, prefix))
        elif kind == "dtmf":
            report.add(f"model.tools[{i}] (dtmf)", "manual",
                       "Vapi sends keypad tones; Phonic's keypad_input reads tones from the caller. Use a transfer "
                       "tool with dtmf digits if the goal is to navigate a phone menu.")
        else:
            report.add(f"model.tools[{i}] ({kind})", "manual", f"No Phonic equivalent for Vapi's '{kind}' tool.")
    return phonic_tools, builtins, prompt_lines


def system_prompt(model):
    msgs = [m for m in model.get("messages") or [] if m.get("role") == "system"]
    return "\n\n".join(m.get("content", "") for m in msgs)


def to_phonic(vapi, phone="assign-automatically", skip_tools=()):
    """Returns (agent_body, tools, extraction_schema, eval_prompts, report)."""
    r = Report()
    seen = set()

    def take(key):
        seen.add(key)
        return vapi.get(key)

    agent = {"slug": slugify(take("name")), "phone_number": phone, "model": "phonic-1"}
    agent["name"] = agent["slug"]
    r.add("name", "adjusted", f"Phonic agent names are lowercase slugs: '{agent['slug']}'.", "slug")

    model = take("model") or {}
    prompt = system_prompt(model)
    extra = []
    if prompt:
        r.add("model.messages[system]", "mapped", "System prompt carried over as is.", "system_prompt")
    else:
        r.add("model.messages[system]", "manual", "No system prompt found.", "system_prompt")
    r.add("model.provider / model.model", "dropped",
          f"{model.get('provider', '?')} {model.get('model', '?')} is replaced by Phonic-1, one speech-to-speech model.", "model")
    for k in ("temperature", "maxTokens", "emotionRecognitionEnabled"):
        if k in model:
            r.add(f"model.{k}", "dropped", "Phonic-1 does not expose this setting.")
    if model.get("toolIds"):
        r.add("model.toolIds", "manual", "Tools stored separately on Vapi; export each one into model.tools and re-run.")

    tools, builtins, lines = map_tools(model.get("tools"), r, snake(agent["slug"]))
    extra += lines

    variables = sorted(set(VAR.findall(prompt)) | set(VAR.findall(vapi.get("firstMessage") or "")))
    if variables:
        agent["template_variables"] = {snake(v): {"default_value": ""} for v in variables}
        r.add("{{variables}}", "adjusted", "Same {{name}} syntax on Phonic. Declared with empty defaults; pass "
              "real values per call (Vapi: assistantOverrides.variableValues).", "template_variables")

    first = take("firstMessage")
    mode = take("firstMessageMode") or "assistant-speaks-first"
    if mode == "assistant-speaks-first-with-model-generated-message":
        agent["generate_welcome_message"] = True
        r.add("firstMessageMode", "mapped", "Phonic generates the greeting.", "generate_welcome_message")
    elif mode == "assistant-waits-for-user":
        agent["generate_welcome_message"] = False
        agent["welcome_message"] = None
        r.add("firstMessageMode", "manual", "Phonic agents greet first; check the flow works with a greeting, "
              "or keep the greeting very short.", "welcome_message")
    elif first and re.match(r"https?://", first):
        r.add("firstMessage", "manual", "Greeting is an audio file; Phonic needs the greeting as text.", "welcome_message")
    elif first:
        agent["welcome_message"] = first
        agent["generate_welcome_message"] = False
        r.add("firstMessage / firstMessageMode", "mapped", "Same greeting, spoken first.", "welcome_message")
    if "firstMessageInterruptionsEnabled" in vapi:
        agent["is_welcome_message_interruptible"] = bool(take("firstMessageInterruptionsEnabled"))
        r.add("firstMessageInterruptionsEnabled", "mapped", "Same setting.", "is_welcome_message_interruptible")

    voice = take("voice") or {}
    r.add("voice", "manual", f"{voice.get('provider', '?')} voice {voice.get('voiceId', '?')} has no Phonic twin. "
          "Pick a Phonic voice by ear (default here: sabrina).", "voice_id")
    agent["voice_id"] = "sabrina"
    if "speed" in voice:
        r.add("voice.speed", "manual", "Speed scales differ (Phonic's default is 1.2); left at Phonic's default. "
              "Adjust by ear.", "audio_speed")

    tr = take("transcriber") or {}
    lang = (tr.get("language") or "en").split("-")[0].lower()
    if lang == "multi":
        agent["default_language"] = "en"
        agent["multilingual_mode"] = "auto"
        r.add("transcriber.language", "adjusted", "'multi' becomes English by default with automatic language "
              "detection. Add the expected languages to additional_languages.", "multilingual_mode")
    elif lang in PHONIC_LANGS:
        agent["default_language"] = lang
        r.add("transcriber.language", "mapped", f"Default language '{lang}'.", "default_language")
    else:
        r.add("transcriber.language", "manual", f"'{lang}' is not in Phonic's language list.", "default_language")
    kw = []
    for k in ("keyterm", "keytermsPrompt", "keywords", "wordBoost"):
        for w in tr.get(k) or []:
            w = re.sub(r":\d+(\.\d+)?$", "", str(w))  # Deepgram "word:2" intensifiers
            if w not in kw:
                kw.append(w)
    if kw:
        agent["boosted_keywords"] = kw
        r.add("transcriber keywords", "mapped", f"{len(kw)} keywords boosted.", "boosted_keywords")
    r.add("transcriber.provider / model", "dropped",
          f"{tr.get('provider', '?')} transcription is replaced by Phonic-1 hearing audio directly.")

    if take("endCallPhrases"):
        builtins.append("natural_conversation_ending")
        extra.append("End the call when the caller says: " + ", ".join(f'"{p}"' for p in vapi["endCallPhrases"]) + ".")
        r.add("endCallPhrases", "adjusted", "Phonic ends calls with a built-in tool; the phrases are added to the "
              "prompt as the cue.", "natural_conversation_ending + system_prompt")
    if take("endCallMessage"):
        builtins.append("natural_conversation_ending")
        extra.append(f"Right before you end the call, say: \"{vapi['endCallMessage']}\"")
        r.add("endCallMessage", "adjusted", "No closing-line field on Phonic; added to the prompt.", "system_prompt")
    if take("voicemailMessage"):
        extra.append(f"If you reach voicemail, leave this message and end the call: \"{vapi['voicemailMessage']}\"")
        r.add("voicemailMessage", "adjusted", "Added to the prompt; Phonic detects the voicemail beep on outbound calls.",
              "system_prompt")
    vm = take("voicemailDetection")
    if vm is not None:
        agent["detect_voicemail_tone"] = vm != "off"
        r.add("voicemailDetection", "mapped", "Phonic listens for the beep on outbound calls only.",
              "detect_voicemail_tone")

    if take("maxDurationSeconds"):
        agent["max_conversation_duration_sec"] = int(vapi["maxDurationSeconds"])
        r.add("maxDurationSeconds", "mapped", "Same cap.", "max_conversation_duration_sec")
    if take("silenceTimeoutSeconds"):
        agent["no_input_end_conversation_sec"] = int(vapi["silenceTimeoutSeconds"])
        r.add("silenceTimeoutSeconds", "mapped", "Same silence limit.", "no_input_end_conversation_sec")
    mp = take("messagePlan") or {}
    if mp.get("idleMessages"):
        agent["no_input_poke_text"] = mp["idleMessages"][0]
        agent["no_input_poke_sec"] = int(mp.get("idleTimeoutSeconds") or 10)
        r.add("messagePlan.idleMessages", "adjusted" if len(mp["idleMessages"]) > 1 or mp.get("idleMessageMaxSpokenCount") else "mapped",
              "Phonic has one silence prompt; the first idle message is used." +
              (" Vapi's limit on how many times it is spoken does not carry over." if mp.get("idleMessageMaxSpokenCount") else ""),
              "no_input_poke_text, no_input_poke_sec")

    bg = take("backgroundSound")
    if bg is not None:
        if bg in PHONIC_NOISE:
            agent["background_noise"] = PHONIC_NOISE[bg]
            agent["background_noise_level"] = 0.1
            r.add("backgroundSound", "adjusted", "Office noise at a low level (0.1); tune by ear.", "background_noise")
        elif bg == "off":
            r.add("backgroundSound", "mapped", "No background noise.", "background_noise")
        else:
            r.add("backgroundSound", "manual", f"'{bg}' is not one of Phonic's noises (office, call-center, coffee-shop).",
                  "background_noise")

    ssp = take("startSpeakingPlan") or {}
    if "waitSeconds" in ssp:
        agent["turn_taking"] = turn_taking(float(ssp["waitSeconds"]))
        r.add("startSpeakingPlan.waitSeconds", "adjusted", f"{ssp['waitSeconds']} s wait becomes Phonic's "
              f"'{agent['turn_taking']}' turn-taking. Phonic uses named levels, not seconds.", "turn_taking")
    for k in ("smartEndpointingPlan", "customEndpointingRules", "transcriptionEndpointingPlan"):
        if ssp.get(k):
            r.add(f"startSpeakingPlan.{k}", "dropped", "Phonic-1 decides turn ends from audio; use turn_taking.")
    stp = take("stopSpeakingPlan") or {}
    if "numWords" in stp:
        agent["min_words_to_interrupt"] = max(1, int(stp["numWords"]))
        r.add("stopSpeakingPlan.numWords", "mapped", "Words before the caller can interrupt.", "min_words_to_interrupt")
    for k in ("voiceSeconds", "backoffSeconds", "acknowledgementPhrases", "interruptionPhrases"):
        if k in stp:
            r.add(f"stopSpeakingPlan.{k}", "dropped", "No Phonic setting; Phonic-1 handles barge-in from audio.")

    kp = take("keypadInputPlan") or {}
    if kp.get("enabled"):
        builtins.append("keypad_input")
        r.add("keypadInputPlan", "mapped", "Built-in keypad input.", "keypad_input")

    art = take("artifactPlan") or {}
    if art.get("recordingEnabled") is False:
        agent["data_retention_policy"] = {"zero_data_retention": False, "transcripts": {"delete_after_hours": None},
                                          "audio_recordings": {"delete_after_hours": 0}}
        r.add("artifactPlan.recordingEnabled", "adjusted", "Phonic always records; audio is deleted right after "
              "the call instead.", "data_retention_policy")
    elif art:
        r.add("artifactPlan.recordingEnabled", "mapped", "Phonic records calls by default.")
    comp = take("compliancePlan") or {}
    if comp.get("hipaaEnabled") or vapi.get("hipaaEnabled"):
        agent["enable_redaction"] = True
        r.add("compliancePlan.hipaaEnabled", "manual", "Redaction is turned on; a HIPAA agreement and retention "
              "policy have to be set up with Phonic before go-live.", "enable_redaction")
    elif comp:
        r.add("compliancePlan", "mapped", "No HIPAA mode needed.")
    seen.add("hipaaEnabled")

    schema, evals = None, []
    ap = take("analysisPlan") or {}
    sd = (ap.get("structuredDataPlan") or {}).get("schema")
    if sd:
        schema, notes = extraction_schema(sd)
        r.add("analysisPlan.structuredDataPlan", "adjusted" if notes else "mapped",
              "Becomes a Phonic extraction schema." + (" " + " ".join(notes) if notes else ""), "extraction_schemas")
    se = ap.get("successEvaluationPlan") or {}
    if se.get("enabled") or se.get("messages"):
        text = " ".join(m.get("content", "") for m in se.get("messages") or [] if m.get("role") == "system")
        if text:
            evals.append({"name": "vapi-success-evaluation", "prompt": text})
            r.add("analysisPlan.successEvaluationPlan", "mapped", "Becomes a Phonic conversation eval prompt.",
                  "conversation_eval_prompts")
        else:
            r.add("analysisPlan.successEvaluationPlan", "manual", "Uses Vapi's default rubric text; write the "
                  "pass rule out as an eval prompt.", "conversation_eval_prompts")
    if ap.get("summaryPlan"):
        r.add("analysisPlan.summaryPlan", "manual", "Write the summary as an extraction field if it is needed.")

    if take("server") or vapi.get("serverUrl"):
        r.add("server", "manual", "Vapi posts call events (status, end-of-call report) to this URL. Point that "
              "integration at Phonic's conversation API or webhooks instead.")
    seen.add("serverUrl")
    if take("metadata"):
        r.add("metadata", "dropped", "Keep it in your own system; Phonic agents have no metadata field.")
    for k in ("clientMessages", "serverMessages", "credentials", "credentialIds", "hooks", "monitorPlan",
              "observabilityPlan", "transportConfigurations", "backgroundSpeechDenoisingPlan",
              "backgroundDenoisingEnabled", "modelOutputInMessagesEnabled"):
        if take(k) is not None:
            r.add(k, "manual" if k in ("hooks", "credentials", "credentialIds", "transportConfigurations") else "dropped",
                  "Vapi platform setting with no Phonic agent field.")
    for k in sorted(set(vapi) - seen):
        r.add(k, "manual", "Not recognised by the mapper; review by hand.")

    if skip_tools:
        for t in [t for t in tools if t["name"] in skip_tools or t["type"] in skip_tools]:
            tools.remove(t)
            extra = [x for x in extra if t["name"] not in x]
            r.add(f"tool {t['name']}", "manual", "Left out of this run on request (--skip-tool).", t["name"])
    builtins = sorted(set(builtins))
    agent["tools"] = builtins + [t["name"] for t in tools]
    if extra:
        prompt = prompt.rstrip() + "\n\n" + "\n".join(f"- {x}" for x in extra)
    agent["system_prompt"] = prompt
    return agent, tools, schema, evals, r


def extraction_schema(js):
    """Vapi structured-data JSON Schema to Phonic extraction fields."""
    types = {"boolean": "bool", "number": "float", "integer": "int", "string": "string"}
    fields, notes = [], []
    for name, p in (js.get("properties") or {}).items():
        t = p.get("type")
        if t == "array" and (p.get("items") or {}).get("type") in types:
            ft = types[p["items"]["type"]] + "[]"
        elif t in types:
            ft = types[t]
        else:
            ft = "string"
            notes.append(f"'{name}' ({t}) is extracted as text.")
        fields.append({"name": snake(name), "type": ft, "description": p.get("description") or name})
    return {"name": "migrated-structured-data", "prompt": "Extract these fields from the conversation.",
            "fields": fields}, notes
