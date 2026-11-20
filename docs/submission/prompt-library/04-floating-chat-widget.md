# Turn the explainer into a floating launcher and popup chat (phone full-screen sheet, focus handling, unread dot)

- **Used with:** Codex CLI (gpt-6.1-sol, medium effort)
- **Outcome:** Merged in PR #67.
- **Written by:** Bijoy (orchestrator role: the lead wrote each prompt, checked the result himself, and approved every commit)
- **Proprietary:** No (suggested; repository is public)

The prompt exactly as given to the coding agent:

---

Turn the Plan Explainer into a floating chatbot widget (launcher icon + popup), like the chat assistant on a portfolio site. Work in web/ only. Do NOT commit, push or touch git. Attached: chat_ref.png = the target popup look; current_chat.png = how it looks now (an inline card in the right column — that is what the user does NOT want).

Required behaviour:
1. Remove the explainer from the page layout. In web/src/App.tsx the `.workspace` grid currently has the schedule panel + ExplainerPanel side by side; make the schedule panel full width (adjust App.css so the plan tab still looks good on desktop/tablet/phone).
2. Floating launcher button fixed at bottom-right (24px from edges, respects safe-area insets): a 60px round navy (#0B2545) button with the existing SVG bot/lightning icon in amber, soft shadow, a gentle amber pulse ring (only before the first open, and none under prefers-reduced-motion), a small tooltip/label bubble "Ask about your plan" that appears briefly on first load and on hover. Accessible: <button aria-label="Open plan explainer" aria-expanded aria-controls>. When open the launcher icon morphs to an X ("Close plan explainer").
3. Clicking it opens the chat as a popup panel anchored above the launcher: about 380px wide x min(620px, 100vh - 120px) tall, rounded 16px, shadow, scale/fade-in from bottom-right (motion lib already installed, or CSS; disabled under reduced motion). Layout like the reference: header (avatar, "Gridpilot AI Assistant", green online dot + "Plan explainer", and a close X button in the header), day/weight chip row, scrolling messages, suggestion chips, composer at the bottom. Make the suggestion chips compact: horizontal wrap of small chips, and hide them after the first question (show a small "Suggestions" toggle to bring them back) so the conversation has room.
4. Phone (< 640px): popup becomes a full-screen sheet (100dvh) with the close button; body scroll locked while open.
5. Esc closes; focus moves into the composer on open and returns to the launcher on close; role="dialog" aria-modal="false" aria-labelledby the title. The conversation state must persist when the popup is closed and reopened (keep the component mounted, just hidden, or lift state) — don't lose messages or abort a pending request on close.
6. Show the widget on all three tabs (plan, trade-off, backtest); it uses the currently selected date/weight like today. If no date is selected yet, the composer is disabled as now.
7. Unread indicator: if an answer arrives while the popup is closed, show a small amber dot on the launcher.
8. Keep all existing explainer behaviour/accessibility (badges, banners, sources, retry, 429 cooldown, abort, aria-live, Enter/Shift+Enter, counter). Keep palette (navy #0B2545, blue #2F6FDB, amber #F5A623, teal #14A38B) and the existing splash/skeleton/animation work untouched. Must not cover the page footer content permanently (launcher only), and must not overlap the browser's horizontal scroll (no overflow-x).
9. Tests: update web/src/Explainer.test.tsx and any App tests to open the launcher first where needed; add tests for open/close, Esc, focus return, persistence across close/reopen, unread dot. Don't weaken existing assertions.
Finish by running in web/: npm run lint, npx tsc -b, npx vitest run, npm run build. Report changes and results.
