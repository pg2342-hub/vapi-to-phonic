import json
import unittest
from pathlib import Path

from migrate.cli import apply, plan, report_md
from migrate.mapper import STATUSES, clean_schema, extraction_schema, Report, to_phonic, turn_taking

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = json.loads((ROOT / "examples" / "line_cook_screener.vapi.json").read_text())


def statuses(report):
    return {i["vapi"]: i["status"] for i in report.items}


class Example(unittest.TestCase):
    def setUp(self):
        self.agent, self.tools, self.schema, self.evals, self.r = to_phonic(EXAMPLE)

    def test_core_fields(self):
        a = self.agent
        self.assertEqual(a["slug"], "line-cook-screener")
        self.assertEqual(a["name"], a["slug"])
        self.assertEqual(a["model"], "phonic-1")
        self.assertEqual(a["welcome_message"], EXAMPLE["firstMessage"])
        self.assertFalse(a["generate_welcome_message"])
        self.assertEqual(a["default_language"], "en")
        self.assertEqual(a["max_conversation_duration_sec"], 1200)
        self.assertEqual(a["no_input_end_conversation_sec"], 30)
        self.assertEqual(a["turn_taking"], "relaxed")
        self.assertEqual(a["min_words_to_interrupt"], 2)
        self.assertIn("saute", a["boosted_keywords"])

    def test_prompt_keeps_original_and_adds_cues(self):
        p = self.agent["system_prompt"]
        self.assertTrue(p.startswith(EXAMPLE["model"]["messages"][0]["content"].rstrip()))
        self.assertIn('"goodbye"', p)
        self.assertIn(EXAMPLE["endCallMessage"], p)

    def test_variables_declared(self):
        self.assertEqual(set(self.agent["template_variables"]), {"worker_first_name", "preferred_language"})

    def test_tools(self):
        by_type = {t["type"]: t for t in self.tools}
        self.assertEqual(by_type["built_in_transfer_to_phone_number"]["phone_number"], "+15555550134")
        hook = by_type["custom_webhook"]
        self.assertEqual(hook["name"], "line_cook_screener_save_screen_result")
        self.assertFalse(hook["parameters"]["additionalProperties"])
        self.assertEqual(hook["endpoint_url"], "https://screening.example.com/vapi/save-screen")
        self.assertEqual(set(self.agent["tools"]), {"natural_conversation_ending", *[t["name"] for t in self.tools]})

    def test_every_top_level_field_is_reported(self):
        reported = " ".join(i["vapi"] for i in self.r.items)
        for k in EXAMPLE:
            self.assertIn(k, reported, k)

    def test_gaps_flagged(self):
        s = statuses(self.r)
        self.assertEqual(s["voice"], "manual")
        self.assertEqual(s["model.tools[3] (sms)"], "manual")
        self.assertEqual(s["server"], "manual")
        self.assertEqual(s["model.provider / model.model"], "dropped")
        self.assertTrue(all(i["status"] in STATUSES for i in self.r.items))

    def test_analysis(self):
        names = {f["name"]: f["type"] for f in self.schema["fields"]}
        self.assertEqual(names["stations"], "string[]")
        self.assertEqual(names["screen_completed"], "bool")
        self.assertEqual(len(self.evals), 1)

    def test_skip_tools(self):
        agent, tools, *_ , r = to_phonic(EXAMPLE, skip_tools=["custom_webhook", "built_in_transfer_to_phone_number"])
        self.assertEqual(tools, [])
        self.assertEqual(agent["tools"], ["natural_conversation_ending"])
        self.assertNotIn("Before transferring", agent["system_prompt"])


class EdgeCases(unittest.TestCase):
    def test_waits_for_user_is_manual(self):
        agent, *_, r = to_phonic({"name": "A", "firstMessageMode": "assistant-waits-for-user"})
        self.assertEqual(statuses(r)["firstMessageMode"], "manual")
        self.assertIsNone(agent["welcome_message"])

    def test_model_generated_greeting(self):
        agent, *_ = to_phonic({"firstMessageMode": "assistant-speaks-first-with-model-generated-message"})
        self.assertTrue(agent["generate_welcome_message"])

    def test_audio_greeting_is_manual(self):
        *_, r = to_phonic({"firstMessage": "https://cdn.example.com/hello.mp3"})
        self.assertEqual(statuses(r)["firstMessage"], "manual")

    def test_multi_language(self):
        agent, *_ = to_phonic({"transcriber": {"provider": "deepgram", "language": "multi"}})
        self.assertEqual(agent["multilingual_mode"], "auto")

    def test_locale_and_unknown_language(self):
        agent, *_ = to_phonic({"transcriber": {"language": "es-MX"}})
        self.assertEqual(agent["default_language"], "es")
        *_, r = to_phonic({"transcriber": {"language": "xx"}})
        self.assertEqual(statuses(r)["transcriber.language"], "manual")

    def test_deepgram_intensifiers_stripped(self):
        agent, *_ = to_phonic({"transcriber": {"keywords": ["saute:2", "expo"]}})
        self.assertEqual(agent["boosted_keywords"], ["saute", "expo"])

    def test_unknown_field_kept_in_report(self):
        *_, r = to_phonic({"someNewVapiSetting": True})
        self.assertEqual(statuses(r)["someNewVapiSetting"], "manual")

    def test_hipaa_and_recording(self):
        agent, *_, r = to_phonic({"compliancePlan": {"hipaaEnabled": True}, "artifactPlan": {"recordingEnabled": False}})
        self.assertTrue(agent["enable_redaction"])
        self.assertEqual(agent["data_retention_policy"]["audio_recordings"]["delete_after_hours"], 0)
        self.assertEqual(statuses(r)["compliancePlan.hipaaEnabled"], "manual")

    def test_tool_ids_are_manual(self):
        *_, r = to_phonic({"model": {"toolIds": ["abc"]}})
        self.assertEqual(statuses(r)["model.toolIds"], "manual")

    def test_reserved_param_renamed(self):
        cfg = {"name": "x", "model": {"tools": [{"type": "function", "server": {"url": "https://a.example.com"},
               "function": {"name": "f", "parameters": {"type": "object", "properties": {"conversation_id": {"type": "string"}},
                                                         "required": ["conversation_id"]}}}]}}
        _, tools, *_ = to_phonic(cfg)
        self.assertEqual(list(tools[0]["parameters"]["properties"]), ["conversation_id_value"])
        self.assertEqual(tools[0]["parameters"]["required"], ["conversation_id_value"])

    def test_schema_cleanup(self):
        r = Report()
        out = clean_schema({"type": "object", "properties": {"a": {"type": ["string", "null"], "format": "date"},
                                                              "b": {"type": "integer", "enum": [1, 2]}},
                            "required": ["a", "missing"], "additionalProperties": True}, "p", r)
        self.assertEqual(out["properties"]["a"]["anyOf"], [{"type": "string"}, {"type": "null"}])
        self.assertNotIn("format", out["properties"]["a"])
        self.assertEqual(out["properties"]["b"]["enum"], ["1", "2"])
        self.assertEqual(out["required"], ["a"])
        self.assertFalse(out["additionalProperties"])

    def test_multiple_transfer_destinations(self):
        cfg = {"name": "Desk", "model": {"tools": [{"type": "transferCall", "destinations": [
            {"type": "number", "number": "+15555550001"}, {"type": "sip", "sipUri": "sip:a@b.example.com"}]}]}}
        _, tools, *_, r = to_phonic(cfg)
        self.assertEqual([t["name"] for t in tools], ["desk_transfer_1"])
        self.assertEqual(statuses(r)["model.tools[0].destinations[1]"], "manual")

    def test_turn_taking_levels(self):
        self.assertEqual([turn_taking(x) for x in (0.1, 0.4, 0.8, 1.0, 2.0)],
                         ["eager", "standard", "relaxed", "patient", "very_patient"])

    def test_extraction_types(self):
        schema, notes = extraction_schema({"properties": {"n": {"type": "integer"}, "o": {"type": "object"}}})
        self.assertEqual([f["type"] for f in schema["fields"]], ["int", "string"])
        self.assertEqual(len(notes), 1)


class Cli(unittest.TestCase):
    def test_plan_and_report(self):
        p = plan(ROOT / "examples" / "line_cook_screener.vapi.json")
        md = report_md(p)
        self.assertIn("need a person", md)
        self.assertLess(md.index("| manual |"), md.index("| mapped |"))

    def test_apply_refuses_with_manual_items(self):
        p = plan(ROOT / "examples" / "line_cook_screener.vapi.json")
        with self.assertRaises(SystemExit):
            apply(p)




class Scorecard(unittest.TestCase):
    def test_summary_on_live_style_calls(self):
        from screening.criteria import CRITERIA
        from screening.scorecard import summarize
        ok = {c.id: "pass" for c in CRITERIA}
        bad = dict(ok, promises="fail")
        calls = [{"call_id": "S01", "outcome": "completed", "duration_s": 600, "latencies_ms": [500, 700],
                  "decision": "approve", "expected_decision": "approve"},
                 {"call_id": "S02", "outcome": "abandoned", "duration_s": 60, "latencies_ms": [900],
                  "decision": None, "expected_decision": None}]
        s = summarize(calls, 0.065, {"S01": bad, "S02": ok})
        self.assertEqual(s["completed"], 1)
        self.assertEqual(len(s["critical_failures"]), 1)
        self.assertAlmostEqual(s["cost_per_completed_screen"], round(11 * 0.065, 3), places=2)
