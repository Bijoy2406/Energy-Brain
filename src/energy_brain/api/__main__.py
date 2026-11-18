"""API utilities (#21).

    python -m energy_brain.api export-openapi [--out docs/openapi.json] [--check]
    python -m energy_brain.api record-demo-answers [--pause 20] [--passes 3] [--out FILE]

``export-openapi`` writes the OpenAPI schema of the app (the frontend contract); ``--check`` exits 1 if the
file is stale. ``record-demo-answers`` asks the five answerable demo questions of
``prompts/explainer_demo_questions.yaml`` live (needs data/processed, the models and ``LLM_API_KEY``), paced
``--pause`` seconds apart, retrying failed questions in later passes. The file is written only if **every**
answer is verified and passes the same re-check the API applies before serving it; otherwise nothing is
written. The API key is never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from energy_brain.api.settings import ROOT_DIR

DEFAULT_OPENAPI = ROOT_DIR / "docs" / "openapi.json"
DEMO_FILE = ROOT_DIR / "prompts" / "explainer_demo_questions.yaml"
STOP_ERRORS = ("no_key", "bad_config", "blocked_model")


def openapi_text() -> str:
    from energy_brain.api.app import create_app
    from energy_brain.api.settings import ApiSettings

    schema = create_app(settings=ApiSettings(warmup=False)).openapi()
    return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def export_openapi(out: Path, check: bool) -> int:
    text = openapi_text()
    if check:
        current = out.read_text(encoding="utf-8") if out.exists() else ""
        if json.loads(current or "{}") != json.loads(text):
            print(f"{out} is stale; run: python -m energy_brain.api export-openapi")
            return 1
        print(f"{out} is up to date")
        return 0
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return 0


def record_demo_answers(out: Path, pause: float, passes: int) -> int:
    import yaml

    from energy_brain.api.demo_answers import StoredAnswer, build_entry, serve_stored, write_demo_answers
    from energy_brain.api.models import weight_key
    from energy_brain.api.service import PlanService
    from energy_brain.api.settings import ApiSettings
    from energy_brain.explainer import ExplainerAgent, GroqClient
    from energy_brain.timeaxis import site_tz

    demo = yaml.safe_load(DEMO_FILE.read_text(encoding="utf-8"))
    day = datetime.fromisoformat(demo["day"]).date()
    weight = weight_key(demo["weight"])
    questions = [(q["id"], q["question"]) for q in demo["questions"] if q["expect"] == "answer"]

    service = PlanService(ApiSettings(warmup=False, demo_answers_path=out))
    entry, _ = service.get_plan(day, weight)
    ctx = entry.context
    if ctx.forecast.fallback_used or ctx.signal_note:
        print("Refusing to record: the demo day uses a fallback forecast or signal (needs data and models).")
        return 2
    client = GroqClient()
    agent = ExplainerAgent(client)
    print(f"Recording {len(questions)} answers for {day} w={weight} with model {client.model}")

    done: dict[str, dict] = {}
    asked = 0
    for p in range(passes):
        pending = [(i, q) for i, q in questions if i not in done]
        if not pending:
            break
        if p:
            print(f"pass {p + 1}: {len(pending)} question(s) left; waiting {pause * 3:g} s")
            time.sleep(pause * 3)
        for qid, question in pending:
            if asked:
                time.sleep(pause)
            asked += 1
            ans = agent.ask(question, ctx)
            if ans.error in STOP_ERRORS:
                print(f"[{qid}] stopped: {ans.error}. Nothing written.")
                return 2
            if not ans.verified or ans.error or ans.refusal_reason:
                why = f"verified={ans.verified}, error={ans.error}, refusal={ans.refusal_reason}"
                print(f"[{qid}] not usable ({why})")
                continue
            item = build_entry(qid, ans)
            # Same re-check as the API applies before serving a stored answer.
            candidate = StoredAnswer(qid, question, day, weight, item["body"], ans, "check", ans.model)
            if serve_stored(candidate, ctx) is None:
                print(f"[{qid}] failed the serve re-check; not stored")
                continue
            done[qid] = item
            print(f"[{qid}] ok ({ans.rounds} rounds): {item['body'][:100]!r}...")

    missing = [i for i, _ in questions if i not in done]
    if missing:
        print(f"Not all answers recorded (missing: {', '.join(missing)}). Nothing written.")
        return 2
    write_demo_answers(
        out,
        day=day,
        weight=weight,
        recorded_at=datetime.now(site_tz()).isoformat(timespec="seconds"),
        model=client.model,
        entries=[done[i] for i, _ in questions],
    )
    print(f"wrote {out} ({len(done)} verified answers)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m energy_brain.api", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export-openapi", help="write docs/openapi.json")
    ex.add_argument("--out", type=Path, default=DEFAULT_OPENAPI)
    ex.add_argument("--check", action="store_true", help="exit 1 if the file is stale")
    rec = sub.add_parser("record-demo-answers", help="record verified demo answers live (needs LLM_API_KEY)")
    rec.add_argument("--out", type=Path, default=ROOT_DIR / "reports" / "explainer_demo_answers.json")
    rec.add_argument("--pause", type=float, default=20.0, help="seconds between questions")
    rec.add_argument("--passes", type=int, default=3, help="passes over the questions not yet recorded")
    args = ap.parse_args(argv)
    if args.cmd == "export-openapi":
        return export_openapi(args.out, args.check)
    return record_demo_answers(args.out, args.pause, args.passes)


if __name__ == "__main__":
    sys.exit(main())
