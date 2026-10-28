"""Reconcile deterministic editorial units across artifact versions."""
from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip().casefold()


def _position_changed(first: dict, second: dict) -> bool:
    return (
        first.get("page") != second.get("page")
        or first.get("reading_order") != second.get("reading_order")
        or first.get("bbox") != second.get("bbox")
    )


def _record(status: str, old: list[dict], new: list[dict], confidence: float) -> dict:
    return {
        "status": status,
        "old_editorial_ids": [item["editorial_id"] for item in old],
        "new_editorial_ids": [item["editorial_id"] for item in new],
        "confidence": round(confidence, 3),
    }


def reconcile_editorial_structures(previous: dict, current: dict) -> dict:
    """Classify unit relationships as same, moved, modified, split, merged, new or removed."""
    old_units = list(previous.get("editorial_units", []))
    new_units = list(current.get("editorial_units", []))
    old_by_id = {item["editorial_id"]: item for item in old_units}
    new_by_id = {item["editorial_id"]: item for item in new_units}
    used_old: set[str] = set()
    used_new: set[str] = set()
    changes = []

    for identifier in sorted(set(old_by_id) & set(new_by_id)):
        old, new = old_by_id[identifier], new_by_id[identifier]
        status = "moved" if _position_changed(old, new) else "same"
        changes.append(_record(status, [old], [new], 1.0))
        used_old.add(identifier)
        used_new.add(identifier)

    unmatched_old = [item for item in old_units if item["editorial_id"] not in used_old]
    unmatched_new = [item for item in new_units if item["editorial_id"] not in used_new]

    for old in list(unmatched_old):
        old_text = _normalize(old.get("text", ""))
        matches = [item for item in unmatched_new if _normalize(item.get("text", "")) == old_text]
        if matches:
            new = min(matches, key=lambda item: (item.get("page", 0), item.get("reading_order", 0)))
            changes.append(_record("moved", [old], [new], 0.98))
            unmatched_old.remove(old)
            unmatched_new.remove(new)

    for old in list(unmatched_old):
        old_text = _normalize(old.get("text", ""))
        candidates = []
        for count in range(2, min(5, len(unmatched_new)) + 1):
            for start in range(len(unmatched_new) - count + 1):
                group = unmatched_new[start:start + count]
                combined = _normalize(" ".join(item.get("text", "") for item in group))
                ratio = SequenceMatcher(None, old_text, combined).ratio()
                if ratio >= 0.94:
                    candidates.append((ratio, group))
        if candidates:
            ratio, group = max(candidates, key=lambda item: item[0])
            changes.append(_record("split", [old], group, ratio))
            unmatched_old.remove(old)
            for item in group:
                unmatched_new.remove(item)

    for new in list(unmatched_new):
        new_text = _normalize(new.get("text", ""))
        candidates = []
        for count in range(2, min(5, len(unmatched_old)) + 1):
            for start in range(len(unmatched_old) - count + 1):
                group = unmatched_old[start:start + count]
                combined = _normalize(" ".join(item.get("text", "") for item in group))
                ratio = SequenceMatcher(None, combined, new_text).ratio()
                if ratio >= 0.94:
                    candidates.append((ratio, group))
        if candidates:
            ratio, group = max(candidates, key=lambda item: item[0])
            changes.append(_record("merged", group, [new], ratio))
            unmatched_new.remove(new)
            for item in group:
                unmatched_old.remove(item)

    for old in list(unmatched_old):
        candidates = []
        for new in unmatched_new:
            if old.get("editorial_type") != new.get("editorial_type"):
                continue
            ratio = SequenceMatcher(
                None, _normalize(old.get("text", "")), _normalize(new.get("text", ""))
            ).ratio()
            if ratio >= 0.62:
                candidates.append((ratio, new))
        if candidates:
            ratio, new = max(candidates, key=lambda item: item[0])
            changes.append(_record("modified", [old], [new], ratio))
            unmatched_old.remove(old)
            unmatched_new.remove(new)

    changes.extend(_record("removed", [item], [], 1.0) for item in unmatched_old)
    changes.extend(_record("new", [], [item], 1.0) for item in unmatched_new)
    order = {"same": 0, "moved": 1, "modified": 2, "split": 3, "merged": 4, "new": 5, "removed": 6}
    changes.sort(key=lambda item: (
        order[item["status"]], item["old_editorial_ids"], item["new_editorial_ids"]
    ))
    return {
        "schema_version": "0.1.0",
        "previous_document_sha256": previous.get("source_document", {}).get("sha256"),
        "current_document_sha256": current.get("source_document", {}).get("sha256"),
        "changes": changes,
        "summary": dict(sorted(
            ((status, sum(item["status"] == status for item in changes))
             for status in order),
        )),
    }
