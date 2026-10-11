"""Reply tasks (sorting replies, reply drafts, reading requirement emails) use Gemini first.

Default REPLY_AI_PROVIDER=gemini: the Gemini call answers and the Claude chain isn't touched; when
Gemini fails, the normal chain (Claude, then backups) answers instead; REPLY_AI_PROVIDER=claude goes
straight to the normal chain; no Gemini key -> normal chain. All AI calls are stubs - no network.
Run: python test_reply_ai_provider.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reply_inbox  # noqa: E402
import resume_bot  # noqa: E402

failures, calls = [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


ANSWER = json.dumps({"category": "interview", "reason": "asks for a call"})
gemini_fail = {"on": False}


def fake_gemini(client, types, system, prompt, want_json, max_tokens):
    calls.append("gemini")
    if gemini_fail["on"]:
        raise RuntimeError("503 UNAVAILABLE overloaded")
    return ANSWER, False, "gemini-3.5-flash-lite"


def fake_chain(client, types, system, prompt, want_json, max_tokens):
    calls.append("chain")
    return ANSWER, False, "claude-haiku-5-5"


resume_bot._generate_gemini = fake_gemini
resume_bot._generate = fake_chain
resume_bot._gemini_client = lambda: (object(), None)
resume_bot.gemini_configured = lambda: True
resume_bot.ai_configured = lambda: True
os.environ.pop("REPLY_AI_PROVIDER", None)

c = reply_inbox.classify({"subject": "Interview", "from_email": "ta@x.example", "body": "Can we talk tomorrow?"})
check(calls == ["gemini"] and c["category"] == "interview" and c["model"] == "gemini-3.5-flash-lite", f"default: Gemini only: {calls} {c}")

calls.clear()
gemini_fail["on"] = True
c = reply_inbox.classify({"subject": "Interview", "from_email": "ta@x.example", "body": "Can we talk tomorrow?"})
check(calls == ["gemini", "chain"] and c["model"] == "claude-haiku-5-5", f"Gemini down -> backup chain: {calls}")
gemini_fail["on"] = False

calls.clear()
os.environ["REPLY_AI_PROVIDER"] = "claude"
reply_inbox.classify({"subject": "x", "from_email": "ta@x.example", "body": "x"})
check(calls == ["chain"], f"REPLY_AI_PROVIDER=claude -> normal chain: {calls}")
os.environ.pop("REPLY_AI_PROVIDER", None)

calls.clear()
resume_bot.gemini_configured = lambda: False
reply_inbox.classify({"subject": "x", "from_email": "ta@x.example", "body": "x"})
check(calls == ["chain"], f"no Gemini key -> normal chain: {calls}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: reply tasks use Gemini first, fall back to the normal chain (Claude) on failure, REPLY_AI_PROVIDER=claude / no Gemini key -> normal chain.")
