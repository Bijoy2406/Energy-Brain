"""Ask the Explainer about a real day from the command line (#19). Needs LLM_API_KEY (.env) for live answers.

    python -m energy_brain.explainer --demo [--date 2024-07-02] [--weight 0.5] [--pause 20]
    python -m energy_brain.explainer -q "Why did you run the generator at 7 PM?"

Plans the day with the real default agents, then asks one question at a time with ``--pause`` seconds between
questions (the Groq free tier has tight per-minute token limits) and stops with a report if a 429 persists.
Prints the question, the answer, the tools called and the verified flag. The API key is never printed.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

DEMO_FILE = Path(__file__).resolve().parents[3] / "prompts" / "explainer_demo_questions.yaml"


def main(argv: list[str] | None = None) -> int:
    from energy_brain.defaults import build_default_agents
    from energy_brain.explainer import ExplainerAgent, GroqClient, PlanContext, load_backtest

    ap = argparse.ArgumentParser(prog="python -m energy_brain.explainer", description=__doc__.splitlines()[0])
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: the demo day)")
    ap.add_argument("--weight", type=float, default=None)
    ap.add_argument("-q", "--question", action="append", default=[])
    ap.add_argument(
        "--demo", action="store_true", help="ask every question in prompts/explainer_demo_questions.yaml"
    )
    ap.add_argument("--pause", type=float, default=20.0, help="seconds between questions")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.ERROR)

    demo = yaml.safe_load(DEMO_FILE.read_text(encoding="utf-8"))
    questions = [(q["id"], q["question"]) for q in demo["questions"]] if args.demo else []
    questions += [(f"q{i + 1}", q) for i, q in enumerate(args.question)]
    if not questions:
        ap.error("give --demo or at least one -q")
    day = datetime.fromisoformat(args.date or demo["day"]).replace(tzinfo=ZoneInfo("Asia/Dhaka"))
    weight = args.weight if args.weight is not None else demo["weight"]

    ctx = PlanContext.from_agents(day, weight, backtest=load_backtest(), **build_default_agents())
    agent = ExplainerAgent(GroqClient())
    print(f"Day {day.date()} w={weight}: status={ctx.plan.status}, model={agent.client.model}\n")
    for i, (qid, question) in enumerate(questions):
        if i:
            time.sleep(args.pause)
        ans = agent.ask(question, ctx)
        tools = ", ".join(
            f"{c.name}({','.join(f'{k}={v}' for k, v in c.arguments.items())})" for c in ans.tool_calls
        )
        print(f"[{qid}] Q: {question}")
        print(f"A: {ans.answer}")
        print(f"tools: {tools or '-'}")
        print(f"verified={ans.verified} refusal={ans.refusal_reason} error={ans.error} rounds={ans.rounds}")
        if ans.unverified_numbers:
            print(f"unverified numbers: {ans.unverified_numbers}")
        print()
        if ans.error in ("rate_limit", "blocked_model", "no_key", "bad_config"):
            print(f"Stopped after {i + 1} of {len(questions)} questions: {ans.error}.")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
