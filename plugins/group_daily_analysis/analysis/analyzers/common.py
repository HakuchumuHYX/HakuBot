import json
from typing import TypeVar

from json_repair import repair_json
from pydantic import BaseModel


PayloadT = TypeVar("PayloadT", bound=BaseModel)


def parse_payload_items(
    content: str,
    payload_model: type[PayloadT],
    *,
    module_name: str,
    require_non_empty: bool = True,
) -> list:
    raw = (content or "").strip()
    if not raw:
        raise ValueError(f"{module_name}返回空 content")

    repaired = repair_json(raw, return_objects=False)
    data = json.loads(repaired)
    if isinstance(data, list):
        data = {"items": data}
    if not isinstance(data, dict):
        raise ValueError(f"{module_name}返回 JSON 不是 object")

    payload = payload_model.model_validate(data)
    items = list(getattr(payload, "items", []) or [])
    if require_non_empty and not items:
        raise ValueError(f"{module_name}返回空 items")
    return items
