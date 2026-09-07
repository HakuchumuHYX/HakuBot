import json
from json_repair import repair_json


def parse_json_output(text: str, *, model=None, repair=False):
    raw = text.strip()
    if not raw:
        raise ValueError("Empty model response")
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    data = json.loads(repair_json(raw) if repair else raw)
    if not isinstance(data, (dict, list)):
        raise ValueError("Model response must be a JSON object or array")
    return model.model_validate(data) if model else data
