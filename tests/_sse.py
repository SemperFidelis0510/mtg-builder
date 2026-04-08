from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class SseEvent:
    event: str
    data_raw: str

    def data_json(self) -> object:
        return json.loads(self.data_raw)


def parse_sse_event(chunk: str) -> SseEvent:
    """
    Parse a single SSE event chunk of the form:
      event: name
      data: <payload>

    Returns the event name and the raw data line content (without the `data:` prefix).
    """
    if not isinstance(chunk, str) or not chunk.strip():
        raise ValueError("parse_sse_event: chunk must be a non-empty string")

    event_name: str | None = None
    data_line: str | None = None
    for line in chunk.splitlines():
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_line = line[len("data:") :].strip()

    if not event_name:
        raise ValueError(f"parse_sse_event: missing event line in chunk: {chunk!r}")
    if data_line is None:
        raise ValueError(f"parse_sse_event: missing data line in chunk: {chunk!r}")

    return SseEvent(event=event_name, data_raw=data_line)

