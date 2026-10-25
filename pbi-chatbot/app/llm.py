import os, json
from dataclasses import dataclass, field

# Neutral formats
# tool:     {"name", "description", "parameters": <json schema>}
# messages: {"role":"user","content":str}
#           {"role":"assistant","text":str,"tool_calls":[{"id","name","args"}]}
#           {"role":"tool","tool_call_id":str,"content":str}

@dataclass
class LLMResult:
    text: str = ""
    tool_calls: list = field(default_factory=list)   # [{"id","name","args"}]


class OpenAIProvider:
    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.model = os.environ["OPENAI_MODEL"]

    def _msgs(self, system, messages):
        out = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msg = {"role": "assistant", "content": m.get("text") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{
                        "id": t["id"], "type": "function",
                        "function": {"name": t["name"], "arguments": json.dumps(t["args"])},
                    } for t in m["tool_calls"]]
                out.append(msg)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        return out

    def chat(self, system, messages, tools, max_tokens=1500) -> LLMResult:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=self._msgs(system, messages),
            tools=[{"type": "function", "function": t} for t in tools] or None,
            max_completion_tokens=max_tokens,
        )
        msg = resp.choices[0].message
        calls = [{"id": c.id, "name": c.function.name, "args": json.loads(c.function.arguments or "{}")}
                 for c in (msg.tool_calls or [])]
        return LLMResult(text=msg.content or "", tool_calls=calls)


class AnthropicProvider:
    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")

    def _msgs(self, messages):
        out, pending = [], []          # pending = tool_result blocks to group into one user msg
        def flush():
            nonlocal pending
            if pending:
                out.append({"role": "user", "content": pending})
                pending = []
        for m in messages:
            if m["role"] == "tool":
                pending.append({"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]})
                continue
            flush()
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            else:
                blocks = []
                if m.get("text"):
                    blocks.append({"type": "text", "text": m["text"]})
                for t in m.get("tool_calls", []):
                    blocks.append({"type": "tool_use", "id": t["id"], "name": t["name"], "input": t["args"]})
                out.append({"role": "assistant", "content": blocks})
        flush()
        return out

    def chat(self, system, messages, tools, max_tokens=1500) -> LLMResult:
        resp = self.client.messages.create(
            model=self.model, max_tokens=max_tokens, system=system,
            tools=[{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools],
            messages=self._msgs(messages),
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "args": b.input} for b in resp.content if b.type == "tool_use"]
        return LLMResult(text=text, tool_calls=calls)


def get_provider():
    p = os.environ.get("LLM_PROVIDER", "openai").lower()
    return AnthropicProvider() if p == "anthropic" else OpenAIProvider()


def run_loop(provider, system, messages, tools, tool_fns, max_steps=6):
    """Generic tool loop. Mutates `messages`. Returns final text."""
    for _ in range(max_steps):
        r = provider.chat(system, messages, tools)
        messages.append({"role": "assistant", "text": r.text, "tool_calls": r.tool_calls})
        if not r.tool_calls:
            return r.text
        for c in r.tool_calls:
            try:
                out = tool_fns[c["name"]](**c["args"])
            except Exception as e:
                out = {"error": str(e)}
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(out)[:30000]})
    return "Too many steps. Try a narrower question."