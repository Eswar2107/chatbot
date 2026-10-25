from dotenv import load_dotenv
load_dotenv()
import json
from app.pbi import dev_token, exec_dax, get_schema

t = dev_token()
print("token ok, length:", len(t))

s = get_schema(t)
for k, v in s.items():
    print(k, "->", (len(v) if isinstance(v, list) else v))
    if isinstance(v, list) and v:
        print("  sample:", json.dumps(v[0])[:300])

# replace with a real table/column from your model:
print(exec_dax('EVALUATE ROW("test", 1)', t))