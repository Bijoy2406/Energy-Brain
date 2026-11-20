# Remove the Bangla feature safely and keep everything else

- **Used with:** Antigravity CLI (agy, Gemini 3.8 Flash, high effort)
- **Outcome:** Merged in PR #71. Output was re-verified by the lead (full test suite and a leftover-text search).
- **Written by:** Bijoy (orchestrator role: the lead wrote each prompt, checked the result himself, and approved every commit)
- **Proprietary:** No (suggested; repository is public)

The prompt exactly as given to the coding agent:

---

Task: remove ALL Bangla (Bengali-language) functionality from the Gridpilot AI Explainer/chat/voice feature. The product decision: the Explainer is English only (Bangla speech recognition was wrong and Bangla is not needed for the business model). Do NOT run `git commit`, `git add`, `git push`, `git stash`, `git reset` or change branches. Work only inside this repo folder (and /tmp for scratch files). Python venv: `.venv-linux/bin/python`.

IMPORTANT scope rules
- Remove only the Bangla language feature (issue #24). KEEP everything else that is uncommitted in the working tree: operator overrides and cloud event (#22/#25 are already committed), the cloud-event/override/demo-mode screens, PDF report (#26), dark theme, splash/skeleton, chat widget, English voice input (mic icon + Tweenly recorder + POST /transcribe + Groq Whisper), Tailwind setup under web/src/components/ui. Voice stays, but in English only.
- Do NOT touch references to Bangladesh (tariffs, holidays, timezone, "Bengali New Year" comment in data/synthetic_load.py), nor these files at all: config/*, reports/backtest.json, docs/data-sources.md, docs/site-spec.md, docs/submission/*, src/energy_brain/assets/*, src/energy_brain/data/*, src/energy_brain/forecast/*, tests/test_assets.py, tests/test_load_forecast.py, src/energy_brain.egg-info/*.

## Step 1: exact restore of files that ONLY the Bangla job changed
Run these from the repo root:
```
git checkout HEAD -- prompts/explainer_system.md prompts/explainer_ground.md prompts/explainer_retry.md prompts/explainer_demo_questions.yaml src/energy_brain/explainer/agent.py src/energy_brain/explainer/verify.py src/energy_brain/explainer/__main__.py docs/interfaces.md tests/test_explainer.py reports/explainer_demo_answers.json web/src/components/AnswerText.tsx
rm -f src/energy_brain/explainer/language.py web/src/components/explainerQuestions.ts
```
Before running it, run `git diff HEAD --stat` for those files and confirm none contains a non-Bangla change you would lose (if one does, keep that hunk by hand instead of restoring the whole file, and tell me).

## Step 2: surgery on files that mix Bangla with other work (edit by hand; use `git diff HEAD -- <file>` to see what changed; remove only the Bangla/language parts)
Backend
- src/energy_brain/api/models.py: `ExplainRequest` loses its `language` field again and the question description says English; `ExplainResponse` goes back to what HEAD has for `language`. Keep TranscribeResponse, HealthResponse.transcription_available, what-if/report models.
- src/energy_brain/api/service.py, src/energy_brain/api/routers/explain.py, src/energy_brain/api/demo_answers.py, src/energy_brain/api/__main__.py: remove language detection/selection and the Bangla demo recording (record-demo-answers records only the English questions again, exactly as at HEAD). Keep everything not related to Bangla (report cache, transcribe limiter, etc.).
- src/energy_brain/api/transcribe.py and src/energy_brain/api/routers/transcribe.py: the `language` query parameter becomes `auto|en` only (no `bn`), Bangla/"bengali" mappings removed, docstrings updated. Keep the silence-hallucination filter and everything else.
- tests/test_api.py, tests/test_transcribe.py: delete the Bangla tests and Bangla parametrisations; keep the rest.
- docs/api.md: remove every Bangla/language mention in the POST /explain section and the /transcribe section (the `language` field of /explain, the auto-detect rule, the Latin-script Bangla note, "Bangla accuracy" bullet). The `/transcribe` section says English; `?language=auto|en`.
- Regenerate: `.venv-linux/bin/python -m energy_brain.api export-openapi`, then `cd web && npm run gen:api`.
Web (all under web/)
- index.html: remove Noto+Sans+Bengali from the three Google Fonts URLs.
- src/App.css: remove the Bengali font stack (`--font-chat` with Noto Sans Bengali / Nirmala UI / Vrinda: restore the font stack that was used before) and the `.chat-language*` rules (the language selector is removed).
- src/components/ExplainerPanel.tsx: remove the language state, the "Answer language" select and its CSS wrapper, the `t(en, bn)` bilingual helper (use the English strings directly), `questionLanguage`, `lang="bn"` attributes and every Bangla string. Welcome message stays exactly: "Hi! Ask me why the plan charges, discharges or runs the generator." Suggested questions are the 5 English ones, defined in this file as before (explainerQuestions.ts was deleted). Keep: the mic icon button, mic messages, speech-note logic (server voice note and browser-speech note), unread dot, collapsible suggestions, everything else.
- src/components/BrowserSpeech.tsx and src/components/voiceSupport.ts: English only. Remove the Bangla messages and the `language`/`bn` props; the browser SpeechRecognition uses `en-US`; "Listen" uses an English voice only; the server recorder always calls `api.transcribe(audio, 'en', signal)`; `LanguageHint` type becomes `'auto' | 'en'` or is removed. Keep the Tweenly recorder integration, silence/too-short checks and error messages (English).
- src/api/client.ts: `transcribe(audio, language: 'auto' | 'en', signal)`.
- src/Explainer.test.tsx: delete the Bangla tests (language selector, Bangla chips/voice, bn messages); keep and adapt the English voice tests (the recorder tests should call without a language selector). Keep all other tests.
- Check src/Backtest.test.tsx, src/test/paretoFixture.ts and web/package.json: they were touched by the Bangla job only for regenerated types or the `--default-non-nullable false` flag in `gen:api`. Keep the flag (it is not Bangla); revert only genuine Bangla hunks, if any.

## Verification (run all, fix what fails, do not weaken or delete non-Bangla tests)
- `.venv-linux/bin/python -m ruff check .` and `.venv-linux/bin/python -m pytest -q` (stored English demo answers must still serve: tests for them must pass)
- `.venv-linux/bin/python -m energy_brain.api export-openapi --check`
- in web/: `npm run lint`, `npx tsc -b`, `npx vitest run`, `npm run build`
- Proof nothing Bangla is left: run `grep -rInE "Bangla|Bengali|বাংলা|[অ-হ]|question_bn|\\bbn\\b|bn-BD" src tests web/src web/index.html docs/api.md docs/interfaces.md prompts reports/explainer_demo_answers.json` and explain every remaining hit (the only acceptable ones are Bangladesh references and the "Bengali New Year" comment in data/synthetic_load.py).

Report: the list of files you changed or deleted, every command's result, and anything you could not finish. Do not commit.
