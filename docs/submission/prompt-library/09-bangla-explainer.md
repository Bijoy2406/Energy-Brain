# Issue #24: Bangla questions and answers for the Explainer with the same number-verification rules

- **Used with:** Codex CLI (gpt-6.1-sol, high effort)
- **Outcome:** Built and tested, then removed by product decision (Bangla speech recognition was inaccurate and is not needed for the business model). Kept here for transparency.
- **Written by:** Bijoy (orchestrator role: the lead wrote each prompt, checked the result himself, and approved every commit)
- **Proprietary:** No (suggested; repository is public)

The prompt exactly as given to the coding agent:

---

Implement GitHub issue #24 in the Gridpilot AI repo: the Explainer accepts questions in Bangla and answers in Bangla, with the same grounding rule as English (every number traced to a tool output). Text first; browser voice (speech input and output) second and optional. Do NOT commit, push or touch git. Use the project venv: `.venv-linux/bin/python`.

Read first: src/energy_brain/explainer/agent.py (LANGUAGES, `ask(... language)`, the language refusal, system prompt substitution of {{language}}, retry/ground prompts), explainer/verify.py (the whole module: tokeniser, unit words, facts, match rule), explainer/llm.py, explainer/tools.py (tool outputs; they stay English/ASCII), prompts/explainer_system.md, prompts/explainer_retry.md, prompts/explainer_ground.md, prompts/explainer_demo_questions.yaml, api/models.py (ExplainRequest, ExplainResponse), api/routers/explain.py, api/service.py (`explain`), api/demo_answers.py (stored answers, `record-demo-answers`), tests/test_explainer.py, tests/test_api.py, web/src/components/ExplainerPanel.tsx, web/src/components/AnswerText.tsx, web/index.html, web/src/App.css, web/src/Explainer.test.tsx. Keep every existing test green without weakening it, and keep English behaviour and cached English demo answers byte-identical.

## Design (decided: Bangla questions and answers, numbers still verified)

### 1. Language selection
- `ExplainRequest` gets optional `language: "auto" | "en" | "bn"` (default "auto"). Auto-detect in a small pure function `detect_language(question)`: "bn" when at least 30 % of the letters are in the Bengali Unicode block U+0980-U+09FF, else "en". Latin-script Bangla ("kano generator chalaiso") is treated as English; say so in the docs. Update the field description (no longer "in English").
- `ExplainResponse` gains `language` ("en"|"bn") of the answer. Existing English responses stay the same apart from this field.
- The agent's `LANGUAGES` becomes {"en": "English", "bn": "Bangla (Bengali script)"}; the refusal for an unknown language stays for other codes.
- The question length limit stays in characters.

### 2. Prompts (bump versions in the header comments)
- explainer_system.md: keep all rules. For Bangla add, under rule 11 or as a new rule: write in Bangla script, but copy every number, time, date and unit EXACTLY as the tools return it: ASCII digits (0-9), Latin unit symbols (kW, kWh, BDT, kg, L, %, h), times as HH:MM, dates as YYYY-MM-DD. Never write Bangla digits (০-৯), never spell numbers as words, never convert units or use lakh/crore grouping. Bangla words for ideas ("grid outage" -> গ্রিড বিভ্রাট / লোডশেডিং, "battery" -> ব্যাটারি, "generator" -> জেনারেটর, "tariff" -> ট্যারিফ) are fine.
- explainer_retry.md and explainer_ground.md: make them language-neutral or add the same instruction so a Bangla retry does not fall back to English.
- Refusals (missing data, off topic, change request) and banners must come back in Bangla when the question was Bangla (keep the English text in the existing fields' logic; add Bangla variants for the fixed server-side texts such as the language/unsupported, NOT SAFE and fallback banners only if they are produced by the Explainer module; the API's other banners stay as they are).

### 3. Verification must stay strict but understand Bangla text
- Add defensive handling in `verify.py` so that even if the model slips: (a) Bangla digits ০১২৩৪৫৬৭৮৯ are converted to ASCII before tokenising (so ৭ matches 7); (b) Indian grouping `1,52,703` is accepted as one number equal to 152703 (strict pattern: groups of 2 then a final group of 3); (c) Bangla unit words map to the same units as the Latin ones: কিলোওয়াট / কিলোওয়াট-ঘণ্টা (kW / kWh), টাকা (BDT), কেজি (kg), লিটার (l), শতাংশ (pct), ঘণ্টা (h), মিনিট (min) - include the common spelling variants (ঘন্টা, কিলোওয়াট আওয়ার); (d) Bangla month names (জানুয়ারি ... ডিসেম্বর) in dates, and Bangla "সন্ধ্যা/সকাল/রাত/দুপুর" with a clock time are NOT needed: HH:MM is required by the prompt.
- Digits glued to Bangla letters (e.g. "৭টায়", "19:00টায়", "500kW-এর") must still tokenise correctly: the lookbehind/lookahead and the unit gap logic must not break on Bangla letters, combining marks or the Bengali danda.
- The rule stays: a number not in a tool output makes the answer unverified. Do not loosen the match rule. A Bangla answer with an invented number must fail verification and be refused/flagged exactly like an English one.
- Unit tests for every item above in tests/test_explainer.py (or a new test file), including negative cases (a wrong Bangla-digit number fails; "৭৩২.৯ কিলোওয়াট" does not match 732.9 kWh).

### 4. Demo questions in Bangla
- Extend prompts/explainer_demo_questions.yaml: every question gets `question_bn` (natural Bangla for the same question; keep "7 PM"/"2 PM" as Bangla phrasing "সন্ধ্যা ৭টায়" if you like, the question text is data, numbers in questions never count) and the same key_facts. Bump the file version.
- `record-demo-answers` records both the English and the Bangla question (Bangla entries are separate stored answers keyed by their own question text; no format change is needed because matching is by normalised question text). Tests for the recorder/serving must run offline with a fake LLM client (no network, no key): a scripted Bangla answer built from the real tool outputs that passes verification, a scripted answer with an invented number that fails, and a Bangla refusal.
- `python -m energy_brain.explainer --demo` (explainer/__main__.py) runs the Bangla questions too behind a `--bn` flag.
- Do NOT try to call the real LLM from here; I will record the live Bangla answers myself afterwards. Tell me the exact command to run.

### 5. API
- Pass the detected/explicit language through `service.explain` to `agent.ask(..., language=...)`. Stored/cached lookup unchanged (English ones keep working; Bangla ones are found by their own question text).
- Regenerate docs/openapi.json (`.venv-linux/bin/python -m energy_brain.api export-openapi`) and web/src/api/generated.ts (`cd web && npm run gen:api`); update docs/api.md (Explainer section: language field, auto-detect rule, Latin-script Bangla limitation, numbers are always written with ASCII digits and Latin units so they can be verified).

### 6. Web
- Bengali font: add Noto Sans Bengali (Google Fonts, display=swap, non-blocking like the existing font links) and put it in the font stack used for the chat messages so Bangla renders properly in light and dark themes and does not change English text. Offline fallback: the system Bengali font (fonts must not block first paint or the splash).
- ExplainerPanel: a small language selector in the chat header or composer area: "Auto / English / বাংলা", default Auto; sends `language` only when not Auto. Show suggestion chips in the chosen language: when Bangla is selected use the Bangla demo questions (put the Bangla strings in a small constants file next to the English ones; they must exactly equal the `question_bn` values in the YAML so cached answers match). Placeholder, hint and the fixed UI texts of the chat in Bangla when Bangla is selected (labels like "Verified" may stay English badges, keep it small and consistent). `lang="bn"` on Bangla message elements for screen readers and correct font shaping.
- AnswerText/provenance popovers must still work when numbers sit next to Bangla letters (highlighting of verified numbers).
- Keep the chat widget layout, focus handling, persistence and all existing behaviour; no horizontal overflow with long Bangla words (overflow-wrap).
- Voice (only after text works; behind feature detection, never required): a mic button using the browser's `SpeechRecognition` / `webkitSpeechRecognition` with lang `bn-BD` (or `en-US` when English) that fills the input (never auto-sends), and a "Listen" button on answers using `speechSynthesis` with a `bn-BD`/`bn` voice if the browser has one. If a feature is not available, hide the button (do not show a broken control). Add a short note shown at first use: browser speech services may send audio to the browser vendor's servers. Respect prefers-reduced-motion for any pulsing mic animation. Tests mock the speech APIs.
- Light and dark themes: CSS variables only.

## Honesty rules
- Same as English: the optimiser decides, the Explainer explains; never promise features that are not there (no pre-cooling, no control); no invented numbers; unverified answers are shown as unverified.
- Say plainly in docs and in the UI hint that the Bangla quality depends on the LLM and that the numbers are verified but the Bangla wording is not.

## Tests and finish
Backend tests (verify unit tests, detect_language, agent with a fake client in Bangla: verified answer, invented number, refusal, Bangla retry path, API field handling with the TestClient and fake agent), web tests (language selector, Bangla chips send the exact question_bn strings, lang attribute, voice buttons hidden when unsupported and working with mocks).
Finish by running from the repo root: `.venv-linux/bin/python -m ruff check .`, `.venv-linux/bin/python -m pytest -q`, and in web/: `npm run lint`, `npx tsc -b`, `npx vitest run`, `npm run build`. Report what you changed, the results, anything you could not check (visual rendering of Bangla text, real speech), and the exact command I should run to record the live Bangla demo answers.
