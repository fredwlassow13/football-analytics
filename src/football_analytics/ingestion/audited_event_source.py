import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AuditedEventSource:
    match_id: int
    body: bytes
    endpoint: str
    sha256: str


def load_audited_event_source(
    audit_dir: Path,
    *,
    match_id: int,
) -> AuditedEventSource:
    if type(match_id) is not int or match_id <= 0:
        raise ValueError("Match ID must be a positive integer")

    manifest = json.loads((audit_dir / "downloads.json").read_text(encoding="utf-8"))

    relative_path = f"events/{match_id}.json"
    entry = manifest.get("files", {}).get(relative_path)

    if not isinstance(entry, dict) or entry.get("status") != 200:
        raise ValueError("Audited event source is unavailable")

    endpoint = entry.get("url")
    if not isinstance(endpoint, str) or not endpoint.endswith(f"/events/{match_id}.json"):
        raise ValueError("Audited endpoint differs from requested match")

    body = (audit_dir / "sources" / relative_path).read_bytes()
    sha256 = hashlib.sha256(body).hexdigest()

    if sha256 != entry.get("sha256"):
        raise ValueError("Audited event source hash differs")

    return AuditedEventSource(
        match_id=match_id,
        body=body,
        endpoint=endpoint,
        sha256=sha256,
    )
