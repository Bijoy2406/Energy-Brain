"""Stored, verified Explainer answers for the demo questions (#21): an honest offline fallback.

``reports/explainer_demo_answers.json`` is recorded live with ``python -m energy_brain.api
record-demo-answers`` (needs the real data, the models and ``LLM_API_KEY``). A stored answer is served only
when **all** of these hold:

1. same date, same weight (rounded to 0.01) and the same question after trimming and lower-casing;
2. every tool call the answer was built from, re-run now against the current plan's tools
   (``ExplainerTools``), returns exactly the stored result, so the facts behind it have not changed;
3. the stored answer text passes ``explainer.verify_answer`` against those fresh results.

Banners are recomputed from the current plan (never taken from the file) and the response says
``cached: true`` with the recording time and the model. Anything else goes to the live Explainer. Nothing is
ever made up.

File format (``schema_version`` 1)::

    {"schema_version": 1, "recorded_at": "<ISO time>", "model": "<LLM>", "day": "YYYY-MM-DD",
     "weight": 0.5, "answers": [{"id": "...", "question": "...", "body": "<answer without banners>",
                                 "answer": <ExplainerAnswer>}]}
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from energy_brain.explainer import ExplainerAnswer, ExplainerTools, PlanContext, verify_answer
from energy_brain.explainer.agent import banners

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def normalise_question(question: str) -> str:
    """The match key of a question: trimmed and lower-cased (nothing else)."""
    return question.strip().lower()


@dataclass(frozen=True)
class StoredAnswer:
    id: str
    question: str
    day: date
    weight: float
    body: str
    answer: ExplainerAnswer
    recorded_at: str
    model: str | None


def _json_norm(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True))


def load_demo_answers(path: str | Path) -> list[StoredAnswer]:
    """Stored answers from ``path``; an empty list if the file is missing or malformed (logged)."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"schema_version {raw.get('schema_version')!r}, expected {SCHEMA_VERSION}")
        day = date.fromisoformat(raw["day"])
        weight = float(raw["weight"])
        out = []
        for item in raw["answers"]:
            ans = ExplainerAnswer.model_validate(item["answer"])
            if not ans.verified or ans.error or ans.refusal_reason:
                raise ValueError(f"answer {item.get('id')!r} is not a verified answer")
            out.append(
                StoredAnswer(
                    id=str(item["id"]),
                    question=str(item["question"]),
                    day=day,
                    weight=weight,
                    body=str(item["body"]),
                    answer=ans,
                    recorded_at=str(raw["recorded_at"]),
                    model=ans.model or raw.get("model"),
                )
            )
        return out
    except Exception as exc:
        logger.warning("ignoring stored demo answers %s: %s", p, exc)
        return []


def find_stored(answers: list[StoredAnswer], day: date, weight: float, question: str) -> StoredAnswer | None:
    key = normalise_question(question)
    for a in answers:
        if a.day == day and abs(a.weight - weight) < 1e-9 and normalise_question(a.question) == key:
            return a
    return None


def serve_stored(stored: StoredAnswer, context: PlanContext) -> ExplainerAnswer | None:
    """The stored answer re-checked against ``context``, or None if any fact no longer holds."""
    tools = ExplainerTools(context)
    calls: list[tuple[str, Any]] = []
    for rec in stored.answer.tool_calls:
        try:
            fresh = tools.call(rec.name, rec.arguments)
        except Exception:
            return None
        if _json_norm(fresh) != _json_norm(rec.result):
            return None
        calls.append((rec.name, fresh))
    if not calls:
        return None
    check = verify_answer(stored.body, calls)
    if not check.ok:
        return None
    current = banners(context)
    return stored.answer.model_copy(
        update={
            "answer": "\n\n".join([*current, stored.body]),
            "banners": current,
            "provenance": check.sources,
            "verified": True,
            "unverified_numbers": [],
        }
    )


def answer_body(answer: ExplainerAnswer) -> str:
    """The answer text without the banners the agent put first."""
    prefix = "\n\n".join(answer.banners)
    text = answer.answer
    if prefix:
        if not text.startswith(prefix + "\n\n"):
            raise ValueError("answer text does not start with its banners")
        text = text[len(prefix) + 2 :]
    return text


def build_entry(qid: str, answer: ExplainerAnswer) -> dict[str, Any]:
    return {
        "id": qid,
        "question": answer.question,
        "body": answer_body(answer),
        "answer": answer.model_dump(mode="json"),
    }


def write_demo_answers(
    path: str | Path, *, day: date, weight: float, recorded_at: str, model: str | None, entries: list[dict]
) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "note": "Verified Explainer answers recorded live for the demo questions (#21). Served only when "
        "the question matches exactly and the facts still match the current plan "
        "(see energy_brain.api.demo_answers).",
        "recorded_at": recorded_at,
        "model": model,
        "day": day.isoformat(),
        "weight": weight,
        "answers": entries,
    }
    Path(path).write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
