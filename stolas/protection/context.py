"""Deterministic contextual analysis for editorial text preservation."""
from __future__ import annotations

from collections import Counter
import hashlib
import re
import unicodedata


ENGLISH_FUNCTION_WORDS = frozenset({
    "a", "about", "after", "all", "also", "an", "and", "are", "as", "at",
    "be", "because", "been", "before", "but", "by", "can", "could", "do",
    "does", "each", "for", "from", "had", "has", "have", "he", "her", "his",
    "how", "i", "if", "in", "into", "is", "it", "its", "may", "more", "most",
    "not", "of", "on", "one", "only", "or", "other", "our", "should", "so",
    "some", "such", "than", "that", "the", "their", "them", "then", "there",
    "these", "they", "this", "those", "through", "to", "was", "we", "were",
    "what", "when", "where", "which", "who", "will", "with", "would", "you",
    "your",
})

# General language evidence, not a list of book-specific formulas or names.
LATIN_CORE_WORDS = frozenset({
    "ad", "aeternum", "amen", "cum", "de", "dei", "deo", "deus", "domine",
    "dominus", "ego", "est", "et", "ex", "filii", "gloria", "in", "jus",
    "lux", "meum", "mundi", "nomine", "non", "noster", "nostrum", "pater",
    "per", "sancti", "sanctus", "sed", "spiritus", "sum", "terra", "verbum",
})
LATIN_SUFFIXES = ("ibus", "orum", "arum", "ium", "que", "us", "um", "ae", "is")
CONTEXT_CUES = (
    "say", "pronounce", "pronounced", "pronunciation", "repeat", "chant",
    "vibrate", "the words", "the word", "the name", "the following",
    "phrase", "sounds like", "sound like", "syllable",
)
DIRECTIVE_CUES = ("say", "repeat", "chant", "vibrate", "pronounce")
SEPARATE_LITERAL_INSTRUCTION_RE = re.compile(
    r"(?i)(?:^|[.!?]\s+)"
    r"(?:say|repeat|chant|vibrate|pronounce)"
    r"(?:\s+(?:the\s+)?(?:following\s+)?(?:words?|phrase|name))?"
    r"(?:\s+(?:once|twice|three\s+times|\d+\s+times))?\s*[:.!]?\s*$"
)
PRONUNCIATION_LINK_RE = re.compile(r"(?i)\bis\s+pronounced(?:\s+as)?\b")
PHONETIC_CONTEXT_RE = re.compile(
    r"(?i)\b(?:pronounc\w*|syllable|sounds?|sound\s+like|is\s+like|"
    r"is\s+the\s+word|end\s+with|beginning|becomes?)\b"
)
MAX_CONTEXT_VERTICAL_GAP = 42.0
GENERIC_LITERAL_PREFIXES = (
    "the following", "the latin word", "the latin words", "the words",
    "the word", "the phrase", "the name", "words", "word", "slowly", "softly",
    "aloud", "again", "carefully", "clearly", "this", "it",
)
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
PHONETIC_CHAIN_RE = re.compile(r"^[A-Za-z]+(?:-[A-Za-z]+){2,}$")
FORMATTED_PHONETIC_FORMULA_RE = re.compile(
    r"^(?=.*-)[A-Z]+(?:-[A-Z]+)*(?:\s+[A-Z]+(?:-[A-Z]+)*)*$"
)
PARENTHETICAL_PHONETIC_RE = re.compile(
    r"^\s*[^()]{1,60}\s*\([A-Z]+(?:-[A-Z]+){1,}\)\s*[.!?]?\s*$"
)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def _memory_key(value: str) -> str:
    normalized = normalize_text(value)
    if len(normalized) <= 3:
        return normalized
    return re.sub(r"[\s\-'’�]+", "", normalized.casefold())


def collect_text_records(manifest: dict) -> list[dict]:
    """Flatten spans with stable source locators and local page context."""
    records = []
    image_boxes_by_page = {}
    page_text_by_page = {
        page["number"]: " ".join(
            block.get("text", "") for block in page.get("blocks", [])
            if block.get("kind") == "text"
        )
        for page in manifest["pages"]
    }
    for page in manifest["pages"]:
        image_boxes_by_page[page["number"]] = [
            block["bbox"] for block in page.get("blocks", [])
            if block.get("kind") == "image"
        ]
        for block_index, block in enumerate(page.get("blocks", [])):
            if block.get("kind") != "text":
                continue
            for line_index, line in enumerate(block.get("lines", [])):
                line_spans = line.get("spans", [])
                line_text = "".join(span.get("text", "") for span in line_spans)
                for span_index, span in enumerate(line_spans):
                    records.append({
                        "page": page["number"],
                        "source_id": block["id"],
                        "block_index": block_index,
                        "line_index": line_index,
                        "span_index": span_index,
                        "text": span.get("text", ""),
                        "bbox": span.get("bbox"),
                        "font": span.get("font"),
                        "size": span.get("size"),
                        "flags": span.get("flags") or 0,
                        "line_isolated": normalize_text(line_text) == normalize_text(
                            span.get("text", "")
                        ),
                        "line_text": line_text,
                        "line_has_bold_italic_example": any(
                            (other.get("flags") or 0) & 16
                            and (other.get("flags") or 0) & 2
                            for other_index, other in enumerate(line_spans)
                            if other_index != span_index
                        ),
                        "image_boxes": image_boxes_by_page[page["number"]],
                        "page_text": page_text_by_page[page["number"]],
                    })
    records.sort(key=lambda item: (
        item["page"], item["block_index"], item["line_index"], item["span_index"],
        item["bbox"][1], item["bbox"][0],
    ))
    for index, record in enumerate(records):
        preceding = []
        for candidate in reversed(records[max(0, index - 12):index]):
            if candidate["page"] != record["page"]:
                break
            vertical_gap = max(record["bbox"][1] - candidate["bbox"][3], 0)
            if candidate["block_index"] != record["block_index"] \
                    and vertical_gap > MAX_CONTEXT_VERTICAL_GAP:
                break
            preceding.append(candidate)
        preceding.reverse()

        following = []
        for candidate in records[index + 1:index + 13]:
            if candidate["page"] != record["page"]:
                break
            vertical_gap = max(candidate["bbox"][1] - record["bbox"][3], 0)
            if candidate["block_index"] != record["block_index"] \
                    and vertical_gap > MAX_CONTEXT_VERTICAL_GAP:
                break
            following.append(candidate)

        same_line_before = [item for item in preceding
                            if item["block_index"] == record["block_index"]
                            and item["line_index"] == record["line_index"]]
        same_line_after = [item for item in following
                           if item["block_index"] == record["block_index"]
                           and item["line_index"] == record["line_index"]]
        record["preceding_text"] = " ".join(item["text"] for item in preceding)
        record["following_text"] = " ".join(item["text"] for item in following)
        record["same_line_before_text"] = "".join(
            item["text"] for item in same_line_before
        )
        record["same_line_after_text"] = "".join(
            item["text"] for item in same_line_after
        )
        record["same_line_before_context"] = " ".join(
            item["text"] for item in same_line_before
        )
        record["same_line_after_context"] = " ".join(
            item["text"] for item in same_line_after
        )
        record["immediate_previous_text"] = (
            preceding[-1]["text"] if preceding else ""
        )
        record["immediate_following_text"] = (
            following[0]["text"] if following else ""
        )
        record["context_text"] = record["preceding_text"]
    return records


def detect_document_language(records: list[dict]) -> dict:
    words = [word.casefold() for record in records for word in WORD_RE.findall(record["text"])]
    english_hits = sum(word in ENGLISH_FUNCTION_WORDS for word in words)
    ratio = english_hits / max(len(words), 1)
    if english_hits >= 12 and ratio >= 0.06:
        confidence = min(0.99, round(0.75 + ratio, 3))
        return {"language": "en", "confidence": confidence,
                "method": "document_function_word_profile"}
    return {"language": "und", "confidence": 0.5,
            "method": "insufficient_document_language_evidence"}


def _script_language(text: str) -> tuple[str | None, float]:
    scripts = Counter()
    for character in text:
        if not character.isalpha():
            continue
        name = unicodedata.name(character, "")
        for script, language in (
            ("HEBREW", "he"), ("GREEK", "el"), ("ARABIC", "ar"),
            ("CYRILLIC", "und-Cyrl"),
        ):
            if script in name:
                scripts[language] += 1
                break
        else:
            if "LATIN" in name or ord(character) < 128:
                scripts["Latn"] += 1
    if not scripts:
        return None, 0.0
    language, count = scripts.most_common(1)[0]
    confidence = count / sum(scripts.values())
    if language != "Latn" and confidence >= 0.6:
        return language, round(confidence, 3)
    return None, 0.0


def detect_local_language(text: str) -> tuple[str, float]:
    script_language, script_confidence = _script_language(text)
    if script_language:
        return script_language, script_confidence
    words = [word.casefold() for word in WORD_RE.findall(text)]
    if not words:
        return "und", 0.0
    english_hits = sum(word in ENGLISH_FUNCTION_WORDS for word in words)
    latin_hits = sum(
        word in LATIN_CORE_WORDS or (len(word) >= 5 and word.endswith(LATIN_SUFFIXES))
        for word in words
    )
    if latin_hits >= 2 and latin_hits > english_hits:
        return "la", min(0.98, round(0.62 + 0.1 * latin_hits, 3))
    if english_hits >= 1 and english_hits >= latin_hits:
        return "en", min(0.95, round(0.62 + 0.04 * english_hits, 3))
    return "und", 0.35


def _bbox_distance(first: list[float], second: list[float]) -> float:
    horizontal = max(second[0] - first[2], first[0] - second[2], 0)
    vertical = max(second[1] - first[3], first[1] - second[3], 0)
    return (horizontal * horizontal + vertical * vertical) ** 0.5


def _context_evidence(record: dict, document_language: str,
                      local_language: str) -> list[str]:
    evidence = [f"document_language={document_language}",
                f"local_language={local_language}"]
    context = record["preceding_text"].casefold()
    cue_positions = [(context.rfind(cue), cue) for cue in CONTEXT_CUES
                     if cue in context]
    cue = max(cue_positions)[1] if cue_positions else None
    if cue:
        evidence.append(f"nearby_cue={cue.replace(' ', '_')}")
        evidence.append("context_direction=preceding")
    if PRONUNCIATION_LINK_RE.search(record["same_line_after_context"]):
        evidence.append("following_cue=is_pronounced")
        evidence.append("context_direction=following_same_line")
    local_phonetic_context = " ".join((
        record["same_line_before_context"], record["same_line_after_context"],
        record["immediate_previous_text"],
    ))
    if PHONETIC_CONTEXT_RE.search(local_phonetic_context):
        evidence.append("local_phonetic_context=true")
    if _has_direct_preceding_instruction(record):
        evidence.append("preceding_instruction=true")
    if record["line_isolated"]:
        evidence.append("isolated_line=true")
    if record["flags"] & 16:
        evidence.append("format=bold")
    if any(_bbox_distance(record["bbox"], box) <= 36 for box in record["image_boxes"]):
        evidence.append("near_protected_image=true")
    return evidence


def _target(start: int, end: int, reason: str, role: str, confidence: float,
            evidence: list[str], *, classification: str = "protected",
            review_required: bool = False) -> dict:
    return {
        "start": start, "end": end, "classification": classification,
        "reason": reason, "linguistic_role": role, "confidence": confidence,
        "review_required": review_required, "evidence": sorted(set(evidence)),
    }


def _has_direct_preceding_instruction(record: dict) -> bool:
    immediate = normalize_text(record.get("immediate_previous_text", ""))
    before = normalize_text(record.get("same_line_before_context", ""))
    return bool(
        SEPARATE_LITERAL_INSTRUCTION_RE.search(immediate)
        or SEPARATE_LITERAL_INSTRUCTION_RE.search(before)
        or re.search(r"(?i)\bwords?\b[^.!?]{0,80}\b(?:are|is)\s*:?[ ]*$", before)
        or PRONUNCIATION_LINK_RE.search(before)
    )


def _looks_like_deliberate_literal(text: str) -> bool:
    normalized = normalize_text(text).rstrip(".,;:!?")
    words = WORD_RE.findall(normalized)
    if not words or len(words) > 12:
        return False
    language, confidence = detect_local_language(normalized)
    uppercase = all(word == word.upper() for word in words)
    titlecase = (len(words) <= 8
                 and all(word[:1].isupper() for word in words)
                 and not any(word.casefold() in ENGLISH_FUNCTION_WORDS
                             for word in words))
    phonetic = bool(PHONETIC_CHAIN_RE.fullmatch(normalized))
    return bool(
        (language not in {"en", "und"} and confidence >= 0.6)
        or uppercase or titlecase or phonetic
    )


def _is_phonetic_formula_surface(text: str) -> bool:
    compact = normalize_text(text).rstrip(".,;:!?")
    return bool(FORMATTED_PHONETIC_FORMULA_RE.fullmatch(compact))


def _trim_range(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and (text[end - 1].isspace() or text[end - 1] in ".,;:!?"):
        end -= 1
    return start, end


def _instruction_targets(record: dict, document_language: str) -> list[dict]:
    text = record["text"]
    targets = []
    base_evidence = _context_evidence(record, document_language, "und")

    quoted = re.finditer(
        r"(?i)\b(latin|greek|hebrew)\s+(?:phrase|words?|name)\s*[\"“']([^\"”']+)[\"”']",
        text,
    )
    for match in quoted:
        language = {"latin": "la", "greek": "el", "hebrew": "he"}[
            match.group(1).casefold()
        ]
        evidence = base_evidence + [f"explicit_language_label={language}",
                                    "quoted_literal=true"]
        targets.append(_target(match.start(2), match.end(2),
                               "foreign_language_preserved",
                               "foreign_language_preserved", 0.98, evidence))

    for match in re.finditer(r"(?i)\b(?:the\s+)?words?\s+(?:are|is)\s+([^.!?]+)", text):
        start, end = _trim_range(text, match.start(1), match.end(1))
        literal = text[start:end]
        if (start < end
                and not literal.casefold().startswith(GENERIC_LITERAL_PREFIXES)
                and _looks_like_deliberate_literal(literal)):
            targets.append(_target(start, end, "ritual_formula", "ritual_formula", 0.94,
                                   base_evidence + ["cue=words_are"]))

    pronounced = re.search(r"(?i)^\s*(.*?)\s+is\s+pronounced\s+as\s+([^.!?]+)", text)
    if pronounced:
        left_start, left_end = _trim_range(text, pronounced.start(1), pronounced.end(1))
        right_start, right_end = _trim_range(text, pronounced.start(2), pronounced.end(2))
        left = text[left_start:left_end].casefold()
        if left and left not in {"the name", "name", "it", "this"}:
            targets.append(_target(left_start, left_end, "pronunciation_literal",
                                   "proper_name", 0.94,
                                   base_evidence + ["cue=is_pronounced_as", "side=subject"]))
        if right_start < right_end:
            targets.append(_target(right_start, right_end, "pronunciation_literal",
                                   "phonetic_formula", 0.96,
                                   base_evidence + ["cue=is_pronounced_as", "side=phonetic"]))

    instruction = re.search(r"(?i)\b(say|repeat|chant|vibrate)\b\s*:?[ \t]+(.+)$", text)
    if instruction:
        remainder = instruction.group(2)
        if not remainder.casefold().startswith(GENERIC_LITERAL_PREFIXES):
            remainder = re.sub(
                r"(?i)\s+(?:once|twice|three\s+times|\d+\s+times)\s*[.!?]?\s*$",
                "", remainder,
            )
            start = instruction.start(2)
            end = start + len(remainder)
            start, end = _trim_range(text, start, end)
            if start < end:
                literal = text[start:end]
                language, _ = detect_local_language(literal)
                words = WORD_RE.findall(literal)
                uppercase_literal = bool(words) and all(word == word.upper()
                                                          for word in words)
                phonetic_literal = literal.count("-") >= 1
                titlecase_literal = len(words) == 1 and literal[:1].isupper()
                eligible = (language != "en" and (
                    language not in {"en", "und"} or uppercase_literal
                    or phonetic_literal or titlecase_literal
                ))
                if eligible:
                    reason = ("foreign_language_preserved"
                              if language not in {"en", "und"} else "ritual_formula")
                    role = ("foreign_language_preserved"
                            if reason == "foreign_language_preserved" else "ritual_formula")
                    targets.append(_target(
                        start, end, reason, role, 0.96,
                        base_evidence + [f"cue={instruction.group(1).casefold()}",
                                         f"local_language={language}"],
                    ))
    return targets


def _script_targets(record: dict, document_language: str) -> list[dict]:
    text = record["text"]
    targets = []
    start = None
    language = None
    for index in range(len(text) + 1):
        current = None
        if index < len(text) and text[index].isalpha():
            name = unicodedata.name(text[index], "")
            current = next((code for script, code in (
                ("HEBREW", "he"), ("GREEK", "el"), ("ARABIC", "ar"),
                ("CYRILLIC", "und-Cyrl"),
            ) if script in name), None)
        if current != language:
            if language is not None and start is not None:
                evidence = _context_evidence(record, document_language, language)
                targets.append(_target(start, index, "foreign_language_preserved",
                                       "foreign_language_preserved", 0.98, evidence))
            start = index if current is not None else None
            language = current
    return targets


def _whole_span_target(record: dict, document_language: str) -> dict | None:
    text = normalize_text(record["text"])
    if not text:
        return None
    local_language, language_confidence = detect_local_language(text)
    evidence = _context_evidence(record, document_language, local_language)
    context_has_cue = any(item.startswith("nearby_cue=") for item in evidence)
    direct_instruction = "preceding_instruction=true" in evidence
    following_pronunciation = "following_cue=is_pronounced" in evidence
    emphasized = "format=bold" in evidence
    isolated = "isolated_line=true" in evidence

    if PARENTHETICAL_PHONETIC_RE.fullmatch(record["text"]):
        return _target(0, len(record["text"]), "phonetic_formula", "phonetic_formula",
                       0.98, evidence + ["pattern=name_with_phonetic_literal"])
    compact = text.rstrip(".,;:!?")
    formula_surface = _is_phonetic_formula_surface(compact)
    neighboring_formula = (
        _is_phonetic_formula_surface(record["immediate_previous_text"])
        or _is_phonetic_formula_surface(record["immediate_following_text"])
    )
    if (isolated and formula_surface
            and (emphasized or context_has_cue or compact.count("-") >= 2
                 or neighboring_formula)):
        start = record["text"].find(compact)
        return _target(start, start + len(compact), "phonetic_formula",
                       "phonetic_formula", 0.96,
                       evidence + ["pattern=formatted_phonetic_formula",
                                   f"neighboring_formula={str(neighboring_formula).lower()}"])
    if (PHONETIC_CHAIN_RE.fullmatch(compact)
            and (context_has_cue or (isolated and emphasized
                                     and compact == compact.upper()))):
        end = record["text"].find(compact) + len(compact)
        start = record["text"].find(compact)
        return _target(start, end, "phonetic_formula", "phonetic_formula", 0.96,
                       evidence + ["pattern=hyphenated_phonetic_chain"])
    subject_words = WORD_RE.findall(text)
    subject_form = bool(subject_words) and text[:1].isupper() \
        and not any(word.casefold() in ENGLISH_FUNCTION_WORDS for word in subject_words)
    if (following_pronunciation and subject_form
            and len(subject_words) <= 4 and len(text) <= 80):
        return _target(0, len(record["text"]), "pronunciation_literal",
                       "proper_name", 0.96,
                       evidence + ["pattern=pronounced_subject"])
    if (document_language != "und" and local_language not in {"und", document_language}
            and language_confidence >= 0.6 and isolated):
        return _target(0, len(record["text"]), "foreign_language_preserved",
                       "foreign_language_preserved", language_confidence, evidence)

    words = WORD_RE.findall(text)
    uppercase_literal = bool(words) and all(word == word.upper() for word in words)
    if ("local_phonetic_context=true" in evidence and uppercase_literal
            and emphasized and len(words) <= 6 and len(text) <= 80):
        return _target(0, len(record["text"]), "phonetic_representation",
                       "phonetic_formula", 0.94,
                       evidence + ["pattern=formatted_phonetic_unit"])
    if (record["line_has_bold_italic_example"] and uppercase_literal
            and emphasized and len(words) <= 4 and len(text) <= 40):
        return _target(0, len(record["text"]), "phonetic_representation",
                       "phonetic_formula", 0.92,
                       evidence + ["pattern=formatted_phonetic_example_line"])
    pronunciation_context = isolated and any(
        item in {
            "nearby_cue=pronounce", "nearby_cue=pronounced",
            "nearby_cue=pronunciation", "nearby_cue=sounds_like",
            "nearby_cue=sound_like", "nearby_cue=syllable",
        }
        for item in evidence
    )
    if (pronunciation_context and uppercase_literal and text.count("-") >= 2
            and len(text) <= 80):
        return _target(0, len(record["text"]), "pronunciation_literal",
                       "pronunciation_literal", 0.94,
                       evidence + ["pattern=contextual_hyphenated_literal"])
    if (pronunciation_context and emphasized and uppercase_literal
            and len(words) <= 4 and len(text) <= 40):
        return _target(0, len(record["text"]), "pronunciation_literal",
                       "pronunciation_literal", 0.9,
                       evidence + ["pattern=contextual_uppercase_literal"])
    literal_segment = isolated or bool(record["same_line_before_text"])
    if (direct_instruction and literal_segment
            and 1 <= len(words) <= 8 and len(text) <= 100
            and _looks_like_deliberate_literal(text)):
        directive = next((cue for cue in DIRECTIVE_CUES
                          if cue in record["preceding_text"].casefold()), "instruction")
        role = "phonetic_formula" if "pronounc" in directive else "ritual_formula"
        return _target(0, len(record["text"]), "contextual_literal", role, 0.94,
                       evidence + [f"cue={directive}", "pattern=separate_literal"])
    if (context_has_cue and isolated and uppercase_literal and emphasized
            and 1 <= len(words) <= 6 and len(text) <= 80):
        return _target(
            0, len(record["text"]), "ambiguous_contextual_literal",
            "unknown_special_text", 0.65,
            evidence + ["policy=translatable_pending_review"],
            classification="translatable", review_required=True,
        )
    if (isolated and emphasized and len(words) >= 3 and len(words) <= 8
            and not any(word.casefold() in ENGLISH_FUNCTION_WORDS for word in words)
            and re.search(r"[A-Za-z][-'’�][A-Za-z]", text)):
        return _target(
            0, len(record["text"]), "ambiguous_formula_like_text",
            "unknown_special_text", 0.7,
            evidence + ["policy=translatable_pending_review",
                        "pattern=formatted_formula_like_text"],
            classification="translatable", review_required=True,
        )
    return None


def strong_targets(record: dict, document_language: str) -> list[dict]:
    targets = _instruction_targets(record, document_language)
    targets.extend(_script_targets(record, document_language))
    whole = _whole_span_target(record, document_language)
    if whole:
        targets.append(whole)
    return _resolve_overlaps(targets)


def _resolve_overlaps(targets: list[dict]) -> list[dict]:
    chosen = []
    for target in sorted(targets, key=lambda item: (
            item["start"], -(item["end"] - item["start"]), -item["confidence"],
            item["reason"])):
        overlaps = [item for item in chosen
                    if min(item["end"], target["end"]) > max(item["start"], target["start"])]
        if not overlaps:
            chosen.append(target)
            continue
        existing = max(overlaps, key=lambda item: (item["confidence"],
                                                   item["end"] - item["start"]))
        if (target["confidence"], target["end"] - target["start"]) > (
                existing["confidence"], existing["end"] - existing["start"]):
            chosen.remove(existing)
            chosen.append(target)
    return sorted(chosen, key=lambda item: (item["start"], item["end"]))


def build_term_memory(records: list[dict], first_pass: list[list[dict]]) -> dict[str, dict]:
    memory = {}
    for record, targets in zip(records, first_pass):
        for target in targets:
            if (target["classification"] != "protected"
                    or target["confidence"] < 0.9):
                continue
            term = normalize_text(record["text"][target["start"]:target["end"]])
            if len(term) < 2:
                continue
            key = _memory_key(term)
            item = memory.setdefault(key, {
                "term_hash": hashlib.sha256(term.encode("utf-8")).hexdigest(),
                "confidence": target["confidence"],
                "seed_count": 0,
                "role_counts": Counter(),
                "reason_counts": Counter(),
                "seed_evidence_hashes": set(),
            })
            item["seed_count"] += 1
            item["confidence"] = max(item["confidence"], target["confidence"])
            item["role_counts"][target["linguistic_role"]] += 1
            item["reason_counts"][target["reason"]] += 1
            evidence_key = "\n".join(sorted(target.get("evidence", [])))
            item["seed_evidence_hashes"].add(
                hashlib.sha256(evidence_key.encode("utf-8")).hexdigest()
            )
    for key, item in memory.items():
        item["linguistic_role"] = sorted(
            item["role_counts"],
            key=lambda role: (-item["role_counts"][role], role),
        )[0]
        item["reason"] = sorted(
            item["reason_counts"],
            key=lambda reason: (-item["reason_counts"][reason], reason),
        )[0]
        item["role_conflict"] = len(item["role_counts"]) > 1
        if len(key) <= 3:
            item["occurrence_count"] = sum(
                _memory_key(record["text"]) == key for record in records
            )
        else:
            item["occurrence_count"] = sum(
                _memory_key(record["text"]).count(key)
                for record in records
            )
    return memory


def consistency_target(record: dict, memory: dict[str, dict]) -> dict | None:
    term = normalize_text(record["text"])
    key = _memory_key(term)
    item = memory.get(key)
    if (item is None or item["occurrence_count"] < 2
            or item["role_conflict"]):
        return None
    script_language, _ = _script_language(term)
    words = WORD_RE.findall(term)
    stable_form = bool(
        script_language
        or PHONETIC_CHAIN_RE.fullmatch(term.rstrip(".,;:!?"))
        or (len(words) == 1 and term == term.upper() and len(term) <= 16)
        or (item["linguistic_role"] == "ritual_formula"
            and 3 <= len(words) <= 8
            and not any(word.casefold() in ENGLISH_FUNCTION_WORDS for word in words)
            and re.search(r"[A-Za-z][-'’�][A-Za-z]", term))
    )
    compatible_context = _has_direct_preceding_instruction(record)
    if not stable_form and not compatible_context:
        return None
    return _target(
        0, len(record["text"]), "consistent_preserved_term", item["linguistic_role"],
        min(0.99, item["confidence"]),
        [f"term_memory={item['term_hash'][:16]}",
         f"global_occurrences={item['occurrence_count']}",
         f"seed_role={item['linguistic_role']}",
         f"context_compatible={str(compatible_context).lower()}",
         f"stable_form={str(stable_form).lower()}"],
    )


def base_segment_rule(text: str, document_language: str) -> dict:
    normalized = normalize_text(text)
    if not normalized:
        return {
            "classification": "ignored", "reason": "empty_or_whitespace_text",
            "linguistic_role": "non_content", "confidence": None,
            "review_required": False, "evidence": [],
        }
    characters = [character for character in normalized if not character.isspace()]
    if any(unicodedata.category(character) == "Co" for character in characters):
        return {
            "classification": "review_required",
            "reason": "private_use_character_requires_editorial_review",
            "linguistic_role": "unknown_special_text", "confidence": 0.98,
            "review_required": True,
            "evidence": [f"document_language={document_language}",
                         "unicode_category=private_use"],
        }
    symbols = [character for character in characters
               if unicodedata.category(character).startswith("S")]
    if symbols and (len(characters) == 1 or len(symbols) / len(characters) >= 0.4):
        return {
            "classification": "review_required",
            "reason": "symbol_sequence_requires_editorial_review",
            "linguistic_role": "unknown_special_text", "confidence": 0.9,
            "review_required": True,
            "evidence": [f"document_language={document_language}",
                         "unicode_density=symbols"],
        }
    local_language, local_confidence = detect_local_language(normalized)
    return {
        "classification": "translatable", "reason": "editorial_translation",
        "linguistic_role": "editorial_translation", "confidence": 0.8,
        "review_required": False,
        "evidence": [f"document_language={document_language}",
                     f"local_language={local_language}",
                     f"local_language_confidence={local_confidence}"],
    }


def segment_record(record: dict, targets: list[dict], document_language: str) -> list[dict]:
    text = record["text"]
    segments = []
    cursor = 0
    for target in _resolve_overlaps(targets):
        if target["start"] > cursor:
            base = base_segment_rule(text[cursor:target["start"]], document_language)
            base.update({"start": cursor, "end": target["start"]})
            segments.append(base)
        segments.append(target)
        cursor = target["end"]
    if cursor < len(text) or not segments:
        base = base_segment_rule(text[cursor:], document_language)
        base.update({"start": cursor, "end": len(text)})
        segments.append(base)
    return [segment for segment in segments if segment["end"] > segment["start"]]


def analyze_text(manifest: dict) -> tuple[dict, list[tuple[dict, list[dict]]], list[dict]]:
    records = collect_text_records(manifest)
    document_language = detect_document_language(records)
    first_pass = [strong_targets(record, document_language["language"]) for record in records]
    memory = build_term_memory(records, first_pass)
    analyzed = []
    for record, targets in zip(records, first_pass):
        consistent = consistency_target(record, memory)
        if consistent and not any(
                target["classification"] == "protected" for target in targets):
            targets = targets + [consistent]
        analyzed.append((record, segment_record(
            record, targets, document_language["language"]
        )))
    public_memory = sorted(({
        "term_hash": item["term_hash"],
        "linguistic_role": item["linguistic_role"],
        "seed_count": item["seed_count"],
        "occurrence_count": item["occurrence_count"],
        "role_conflict": item["role_conflict"],
        "evidence_count": len(item["seed_evidence_hashes"]),
    } for item in memory.values()), key=lambda item: item["term_hash"])
    return document_language, analyzed, public_memory
