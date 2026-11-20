# Restyle the plan explainer as a chatbot card (superseded by the floating widget)

- **Used with:** Codex CLI (gpt-6.1-sol, medium effort)
- **Outcome:** Built, then replaced by entry 04 after the requirement became a floating launcher plus popup.
- **Written by:** Bijoy (orchestrator role: the lead wrote each prompt, checked the result himself, and approved every commit)
- **Proprietary:** No (suggested; repository is public)

The prompt exactly as given to the coding agent:

---

Restyle the PLAN EXPLAINER (web/src/components/ExplainerPanel.tsx + its CSS in web/src/App.css) as a modern chatbot widget, matching the attached reference screenshot (a "Cipher" portfolio assistant chat card). Do NOT commit, push or touch git. Keep the Navy + solar palette (navy #0B2545, blue #2F6FDB, amber #F5A623, teal #14A38B). Another Codex run just finished the animation work in the same branch; keep those effects and do not undo them.

Look to match from the reference (adapt to our palette, light page with a navy chat header; a dark-navy chat card is also fine if it reads well):
- Rounded card with header: round bot avatar icon (use an inline SVG lightning/brain bot, not an emoji), title "Energy Brain Assistant" (keep the existing "PLAN EXPLAINER" eyebrow text somewhere accessible so tests/labels still work), a small green "online" dot with subtitle "Plan explainer", and the day / weight context chip (e.g. "Day <date> · w = 0.50") in a pill row under the header like the model selector in the reference.
- Conversation as chat bubbles: user questions right-aligned blue bubbles; assistant answers left-aligned with the round bot avatar and a rounded bubble; a first assistant welcome message ("Hi, I'm the Energy Brain explainer. Ask why the plan charges, discharges or runs the generator. I can explain the plan but not change it.") shown before any exchange. Typing indicator (three bouncing dots) while pending instead of / in addition to the spinner text (keep role="status" and accessible text "Asking the explainer...").
- Suggested questions as rounded outlined teal chips above the composer (as in the reference).
- Composer: rounded input row with a focused teal/blue ring and a round send button with a paper-plane icon at the right (keep it a real submit button with accessible name "Send"); keep Enter-to-send / Shift+Enter, 500-char limit, counter, hint text, cooldown message.
- Smooth auto-scroll, subtle message pop-in animation (disabled under prefers-reduced-motion), chat area has a fixed max height with internal scroll and works on mobile.

Must keep ALL existing behaviour and accessibility: the Verified / Stored answer / Not answered badges, banners, provenance Sources details, Retry button, unverified-numbers warning, 429 cooldown, abort handling, aria-live region, labels used by web/src/Explainer.test.tsx. Do not change API calls or answer text rendering. Update tests only where a visible label legitimately changed; do not weaken assertions. Check desktop, tablet and phone widths for overflow.
Finish by running in web/: npm run lint, npx tsc -b, npx vitest run, npm run build. Report changes and results.
