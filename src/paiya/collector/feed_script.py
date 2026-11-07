"""Local JSON feed-script adapter. This module has no network or browser path."""

import json
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from paiya.collector.adapter import FeedCard, FeedRunData
from paiya.collector.errors import AdapterError


class FeedScriptAdapter:
    """Replay one labelled run and its local envelope files from a feed script."""

    def __init__(self, feed_path: Path, schema_path: Path | None = None) -> None:
        self.feed_path = feed_path.resolve()
        self.schema_path = schema_path or (
            Path(__file__).resolve().parents[3] / "fixtures/feeds/feed.schema.json"
        )

    def read_run(self, group_key: str, run_label: str, cards_per_step: int) -> FeedRunData:
        if cards_per_step != 5:
            raise ValueError("The A3 feed contract requires five cards per scroll step")
        try:
            document = json.loads(self.feed_path.read_text(encoding="utf-8"))
            schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AdapterError("adapter_error", "Could not read the local feed script") from error
        validator = Draft202012Validator(
            schema,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )
        failures = list(validator.iter_errors(document))
        if failures:
            raise AdapterError("adapter_error", f"Invalid feed script: {failures[0].message}")
        if document["group_key"] != group_key:
            raise AdapterError("adapter_error", "Feed script group_key does not match the job")
        run = next((item for item in document["runs"] if item["label"] == run_label), None)
        if run is None:
            raise AdapterError("adapter_error", "Feed script does not contain the requested run")

        raw_cards = run["cards"]
        if run["load_limit"] is not None:
            raw_cards = raw_cards[: run["load_limit"]]
        cards: list[FeedCard] = []
        for raw in raw_cards:
            envelope = None
            if raw["parse"] == "ok":
                envelope = self._read_envelope(cast(str, raw["envelope"]))
                try:
                    envelope_key = envelope["payload"]["source_post_key"]
                except (KeyError, TypeError) as error:
                    raise AdapterError(
                        "adapter_error", "Envelope is missing payload.source_post_key"
                    ) from error
                if envelope_key != raw["source_post_key"]:
                    raise AdapterError(
                        "adapter_error", "Feed card and envelope stable keys do not match"
                    )
            cards.append(
                FeedCard(
                    source_post_key=raw["source_post_key"],
                    pinned=raw["pinned"],
                    suggested=raw["suggested"],
                    parse=raw["parse"],
                    envelope=envelope,
                )
            )

        batches = tuple(
            tuple(cards[index : index + cards_per_step])
            for index in range(0, len(cards), cards_per_step)
        )
        return FeedRunData(
            label=run["label"],
            started_at=_parse_datetime(run["started_at"]),
            session=run["session"],
            sort=run["sort"],
            access=run["access"],
            batches=batches,
        )

    def _read_envelope(self, name: str) -> dict[str, Any]:
        relative = PurePosixPath(name)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise AdapterError("adapter_error", "Envelope path must stay inside its feed scenario")
        root = self.feed_path.parent.resolve()
        envelope_path = (root / Path(*relative.parts)).resolve()
        if not envelope_path.is_relative_to(root):
            raise AdapterError("adapter_error", "Envelope path must stay inside its feed scenario")
        try:
            envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AdapterError("adapter_error", "Could not read a local envelope") from error
        if not isinstance(envelope, dict):
            raise AdapterError("adapter_error", "Envelope must be a JSON object")
        return envelope


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdapterError("adapter_error", "Feed run started_at must include a timezone")
    return parsed
