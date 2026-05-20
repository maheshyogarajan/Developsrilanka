"""fiesta.triage.validators — answer-shape validation for S1.

Pure functions, no Flask imports. Tests exercise these directly.

Spec:
  * Single-select questions: value is one option id (string).
  * Multi-select questions: value is a list of option ids (non-empty).
  * All option ids must come from the question's option set.
  * Empty / unknown / wrong-shape -> TriageValidationError with a message.
"""

from __future__ import annotations

from typing import Any, Dict, List, Union

from .questions import QUESTIONS_BY_ID, is_multi, valid_option_ids


class TriageValidationError(ValueError):
    """Raised when a submitted triage answer fails shape / value checks."""


def validate_answer(qid: str, raw: Union[str, List[str], Any]) -> Union[str, List[str]]:
    """Validate one answer for one question.

    Returns the normalised value (string for single, list[str] for multi) or
    raises TriageValidationError.
    """
    if qid not in QUESTIONS_BY_ID:
        raise TriageValidationError(f"unknown question id: {qid}")

    valid = set(valid_option_ids(qid))

    if is_multi(qid):
        # Accept list, tuple, or single string (treated as one-element list).
        if isinstance(raw, str):
            picked = [raw.strip()] if raw.strip() else []
        elif isinstance(raw, (list, tuple)):
            picked = [str(x).strip() for x in raw if str(x).strip()]
        else:
            raise TriageValidationError(
                f"{qid}: expected a list of option ids, got {type(raw).__name__}"
            )

        if not picked:
            raise TriageValidationError(f"{qid}: pick at least one option")

        # Deduplicate while preserving order
        seen = set()
        deduped: List[str] = []
        for p in picked:
            if p in seen:
                continue
            seen.add(p)
            deduped.append(p)

        unknown = [p for p in deduped if p not in valid]
        if unknown:
            raise TriageValidationError(
                f"{qid}: unknown option(s): {', '.join(unknown)}"
            )
        return deduped

    # Single-select
    if isinstance(raw, (list, tuple)):
        # Browsers sometimes wrap a single radio value in a list. Unwrap.
        if len(raw) == 1:
            raw = raw[0]
        else:
            raise TriageValidationError(
                f"{qid}: expected one option id, got {len(raw)} values"
            )

    if not isinstance(raw, str):
        raise TriageValidationError(
            f"{qid}: expected an option id (string), got {type(raw).__name__}"
        )
    raw = raw.strip()
    if not raw:
        raise TriageValidationError(f"{qid}: pick one option")
    if raw not in valid:
        raise TriageValidationError(f"{qid}: unknown option: {raw}")
    return raw


def validate_full_answers(answers: Dict[str, Any]) -> Dict[str, Union[str, List[str]]]:
    """Validate a complete set of triage answers (all 3 questions answered).

    Returns the cleaned dict (only the 3 known qids, normalised values).
    Raises TriageValidationError on the first failure.
    """
    if not isinstance(answers, dict):
        raise TriageValidationError("answers must be an object")

    cleaned: Dict[str, Union[str, List[str]]] = {}
    for qid in QUESTIONS_BY_ID.keys():
        if qid not in answers:
            raise TriageValidationError(f"missing answer for {qid}")
        cleaned[qid] = validate_answer(qid, answers[qid])
    return cleaned


__all__ = ["TriageValidationError", "validate_answer", "validate_full_answers"]
