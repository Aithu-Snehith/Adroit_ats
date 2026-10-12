"""Resume Optimizer with Claude as the MAIN AI (ANTHROPIC_API_KEY set).

Claude answers both steps (Gemini isn't touched); the request uses CLAUDE_MODEL, the effort setting,
a cached system prompt, room for thinking, and Anthropic's refusal fallback on models that take it.
If Claude fails or declines, Gemini answers instead; with no backup the result says why in
Claude's terms (never "Google rejected the key"), and the key never appears in the reason.
No network and no paid calls: the Anthropic and Gemini clients are stubs. Labelled test fixtures.
Run: python test_claude_main_ai.py
"""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.GEMINI_API_KEY = ""
config.ANTHROPIC_API_KEY = "sk-ant-test-not-real"
config.OPENROUTER_API_KEY = ""
config.XAI_API_KEY = ""
import resume_bot as rb  # noqa: E402

failures, claude_calls, gemini_calls = [], [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


ANALYSIS = {"initial_match_percentage": 72, "match_breakdown": {"mandatory_skills": 29, "recent_project_relevance": 18, "domain_experience": 12,
            "tools_frameworks_cloud": 6, "certifications_education": 5, "location_work_authorization": 2}, "domain_detected": "Cloud",
            "strong_match_skills": ["Terraform"], "partial_match_skills": [], "mandatory_missing_skills": [], "preferred_missing_skills": [],
            "risky_skills_avoided": [], "skills_to_add": {"summary": [], "technical_skills": ["Ansible"], "projects": [], "environment": []},
            "ats_optimization_notes": [], "target_match_percentage": 80}
REWRITE = "[3] Tools: Terraform, Ansible, Jenkins"
RESUME = "RAVI CLAUDETEST\nSUMMARY\nDevOps engineer with 8 years.\nTools: Terraform, Jenkins\n"
JD = "DevOps Engineer: Terraform, Ansible and Jenkins required."
claude_mode = {"fail": None, "refuse": False}


def _answer(params):
    want_json = "Return ONLY the JSON object" in params["messages"][0]["content"]
    return json.dumps(ANALYSIS) if want_json else REWRITE


class FakeMessages:
    def __init__(self, beta):
        self.beta = beta

    def create(self, **params):
        claude_calls.append(dict(params, _beta=self.beta))
        if claude_mode["fail"]:
            raise claude_mode["fail"]
        if claude_mode["refuse"]:
            return SimpleNamespace(stop_reason="refusal", content=[], model=params["model"])
        return SimpleNamespace(stop_reason="end_turn", model=params["model"],
                               content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=_answer(params))])


class FakeClaude:
    messages = FakeMessages(False)
    beta = SimpleNamespace(messages=FakeMessages(True))


class FakeGeminiModels:
    def generate_content(self, model, contents, config):
        gemini_calls.append(model)
        want_json = getattr(config, "response_mime_type", None) == "application/json"
        return SimpleNamespace(text=json.dumps(ANALYSIS) if want_json else REWRITE, candidates=[SimpleNamespace(finish_reason="STOP")])


class FakeGemini:
    models = FakeGeminiModels()


rb._claude_client = lambda: FakeClaude()
rb._gemini_client = lambda: (FakeGemini(), __import__("google.genai", fromlist=["types"]).types)


def run():
    rb._ANALYSIS_CACHE.clear()
    claude_calls.clear()
    gemini_calls.clear()
    return rb.optimize_resume_for_jd(RESUME, JD)


# ---- Claude only: both steps on Claude, the request is shaped right
res = run()
check(res.get("ai_powered") and res.get("optimized"), f"optimized by Claude: {res.get('not_optimized_reason')} / {res.get('ai_unavailable_reason')}")
check(len(claude_calls) == 2 and not gemini_calls, f"both steps on Claude, Gemini untouched: {len(claude_calls)} / {gemini_calls}")
check(res.get("ai_model") == "claude-opus-5-5", f"result names Claude: {res.get('ai_model')}")
p = claude_calls[0] if claude_calls else {}
check(p.get("model") == "claude-opus-5-5" and p.get("output_config") == {"effort": "medium"}, f"default model + effort: {p.get('model')} {p.get('output_config')}")
check(p.get("system", [{}])[0].get("cache_control") == {"type": "ephemeral"} and "MASTER" in p["system"][0]["text"].upper(), "master prompt sent as a cached system block")
check(p.get("_beta") and p.get("fallbacks") == "default" and p.get("betas") == ["server-side-fallback-2026-07-01"], "refusal fallback on Opus 5.5")
check("thinking" not in p and "temperature" not in p, "no disabled thinking / temperature (both rejected on Opus 5.5)")
check(all(8000 < c["max_tokens"] <= 21000 for c in claude_calls), f"room for thinking, capped: {[c['max_tokens'] for c in claude_calls]}")
check("RAVI CLAUDETEST" in p.get("messages", [{}])[0].get("content", ""), "resume in the user message")

# ---- Claude fails -> Gemini answers; Claude declines -> Gemini answers
config.GEMINI_API_KEY = "gemini-test-not-real"
claude_mode["fail"] = RuntimeError("Error code: 529 - overloaded_error")
res = run()
check(res.get("optimized") and gemini_calls and "claude" not in res.get("ai_model", ""), f"Gemini took over after a Claude failure: {res.get('ai_model')}")
claude_mode["fail"], claude_mode["refuse"] = None, True
res = run()
check(res.get("optimized") and gemini_calls, f"Gemini took over after a refusal: {res.get('ai_model')} {res.get('not_optimized_reason')}")
claude_mode["refuse"] = False

# ---- Claude only, bad key, no backups -> keyword scan, Claude's reason, key redacted
config.GEMINI_API_KEY = ""
claude_mode["fail"] = RuntimeError("Error code: 401 - authentication_error: invalid x-api-key sk-ant-test-not-real")
res = run()
why = res.get("ai_unavailable_reason", "")
check(not res.get("optimized") and "ANTHROPIC_API_KEY" in why and "Google" not in why, f"reason names the Anthropic key: {why}")
check("sk-ant-test-not-real" not in json.dumps(res), "API key never in the result")
claude_mode["fail"] = None

# ---- a model without server-side fallback (e.g. Haiku) uses the plain endpoint
rb.CLAUDE_MODEL = "claude-haiku-5-5"
run()
check(claude_calls and not claude_calls[0]["_beta"] and "fallbacks" not in claude_calls[0], "Haiku: plain messages.create, no fallbacks")
rb.CLAUDE_MODEL = "claude-opus-5-5"

# ---- no Claude key -> unchanged Gemini behaviour
config.ANTHROPIC_API_KEY = ""
config.GEMINI_API_KEY = "gemini-test-not-real"
res = run()
check(not claude_calls and gemini_calls and res.get("optimized"), "without ANTHROPIC_API_KEY Gemini runs as before")

tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "dashboard.html"), encoding="utf-8").read()
check("{% if has_claude %}" in tpl and "AI: Claude ({{ claude_model }})" in tpl, "Resume Bot tab says Claude is the main AI")
check("anthropic" in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")).read(), "anthropic in requirements")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: Claude is the main AI - both steps, cached master prompt, effort, refusal fallback; Gemini takes over on failure/refusal; Claude-specific reason, key redacted.")
