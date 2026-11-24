# Vapi to Phonic

[![CI](https://github.com/pg2342-hub/vapi-to-phonic/actions/workflows/ci.yml/badge.svg)](https://github.com/pg2342-hub/vapi-to-phonic/actions/workflows/ci.yml)

Demo page: https://pranavgudise.dev/vapi-to-phonic/

A migration kit for moving a voice assistant from [Vapi](https://vapi.ai) to a [Phonic](https://phonic.ai) speech-to-speech agent. It reads a Vapi assistant config, writes the Phonic agent and tools, and reports every field with one of four statuses:

| Status | Meaning |
|---|---|
| mapped | Same behavior on Phonic |
| adjusted | Carried over with a change a person should glance at |
| manual | Needs a person before go-live |
| dropped | Not needed on Phonic (for example the separate speech-to-text model) |

A field the mapper does not recognise is reported as manual, never dropped silently. `apply` refuses to run while anything is manual, until someone passes `--accept-manual` after reading the list.

## Run it

Python 3.10+, standard library only.

```
python3 -m migrate plan examples/line_cook_screener.vapi.json
```

writes `out/plan.json` (the Phonic request bodies) and `out/report.md` (the gap report), and prints the report. No network.

```
python3 -m migrate apply examples/line_cook_screener.vapi.json --accept-manual
```

creates the tools, the agent and its phone number, the extraction schema (from Vapi's structured-data plan) and the eval prompts (from Vapi's success evaluation) on Phonic. It needs `PHONIC_API_KEY` in the environment or in `~/.config/phonic/.env`. The key never goes in this repo. `--skip-tool <name or type>` leaves a tool out, for example the placeholder webhook in the example config.

```
python3 -m migrate smoke --since 2026-11-24
```

pulls the migrated agent's conversations in call order, grades each on the 10 screening criteria in `screening/criteria.py` with Phonic's own eval prompts, reads the outcome with the migrated extraction schema, and writes `out/smoke.json` with finish rate, criteria passed, critical failures, approve or coach-call agreement, latency and cost per finished screen. The calls follow the 20 scripts in `agent/caller_scripts.md`.

```
python3 -m unittest
python3 scripts/build_demo.py
```

## What maps where

| Vapi | Phonic | Notes |
|---|---|---|
| `name` | `slug` | Lowercase slug |
| `model.messages` (system) | `system_prompt` | Same `{{variable}}` syntax; variables declared in `template_variables` |
| `model.provider`, `model.model`, `transcriber.provider` | `model: phonic-1` | One speech-to-speech model replaces speech-to-text, the language model and text-to-speech |
| `firstMessage`, `firstMessageMode` | `welcome_message`, `generate_welcome_message` | "Waits for user" is flagged: Phonic agents greet first |
| `voice` | `voice_id` | No twin; a person picks a Phonic voice by ear |
| `transcriber.language`, keyword lists | `default_language`, `multilingual_mode`, `boosted_keywords` | `multi` becomes automatic language detection |
| `model.tools` function | Tools API `custom_webhook` | Schema cleaned to Phonic's subset; reserved parameter names renamed |
| `model.tools` transferCall (number) | Tools API `built_in_transfer_to_phone_number` | SIP and assistant destinations are flagged |
| `model.tools` endCall, `endCallPhrases`, `endCallMessage` | `natural_conversation_ending` plus prompt lines | |
| `silenceTimeoutSeconds`, `messagePlan.idleMessages` | `no_input_end_conversation_sec`, `no_input_poke_text`, `no_input_poke_sec` | |
| `startSpeakingPlan.waitSeconds` | `turn_taking` | Seconds become named levels |
| `stopSpeakingPlan.numWords` | `min_words_to_interrupt` | |
| `maxDurationSeconds` | `max_conversation_duration_sec` | |
| `compliancePlan.hipaaEnabled`, `artifactPlan.recordingEnabled: false` | `enable_redaction`, `data_retention_policy` | HIPAA is flagged for a person |
| `analysisPlan.structuredDataPlan`, `successEvaluationPlan` | Extraction schema, conversation eval prompt | |
| `server`, `sms`, `dtmf`, `toolIds`, `hooks` | | Flagged for a person |

Field names follow the public API references at docs.vapi.ai and docs.phonic.ai as of November 24, 2026.

## Examples

- `examples/line_cook_screener.vapi.json`: an AI screening call for line cook shifts, the agent used for the live smoke test. Its prompt is `agent/screener_prompt_v1.md`.
- `examples/clinic_front_desk.vapi.json`: HIPAA, recording off, a SIP transfer, a tool stored by id, a legacy field.
- `examples/store_support_bilingual.vapi.json`: English and Spanish, waits for the caller, an async webhook with nested parameters, an audio background.

All three are invented. Phone numbers (555) and URLs (example.com) are placeholders.

## Demo page

`scripts/build_demo.py` writes `docs/index.html`. The page runs `migrate/mapper.py` in the browser with Pyodide, so a pasted config goes through the same code as the CLI, and nothing leaves the browser. The `gh-pages` branch is a copy of `docs/`.
