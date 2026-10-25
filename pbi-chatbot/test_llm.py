from dotenv import load_dotenv
load_dotenv()
from app.llm import get_provider, run_loop

tools = [{
    "name": "get_sales",
    "description": "Get total sales for a region.",
    "parameters": {"type": "object", "properties": {"region": {"type": "string"}}, "required": ["region"]},
}]
fns = {"get_sales": lambda region: {"region": region, "total": 12345}}

msgs = [{"role": "user", "content": "What are the sales in Asia, and double that number?"}]
print(run_loop(get_provider(), "Use tools for numbers.", msgs, tools, fns))