import json
from typing import Any

from axon.activity.models import ActivityEvent


def event_to_jsonl_line(event: ActivityEvent) -> str:
    """Serialize an ActivityEvent to a JSONL string envelope."""
    payload = {
        "schema_version": 1,
        "event": event.model_dump(mode="json")
    }
    return json.dumps(payload)

def jsonl_line_to_event(line: str) -> ActivityEvent:
    """Deserialize a JSONL string envelope to an ActivityEvent."""
    payload: dict[str, Any] = json.loads(line)
    schema_version = payload.get("schema_version")
    if schema_version != 1:
        raise ValueError(f"Unsupported schema_version: {schema_version}")
    
    event_data = payload["event"]
    return ActivityEvent.model_validate(event_data)
