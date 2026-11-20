# Gridpilot AI — PRD (hackathon challenge: Energy Brain)

Nov 16, 2026 · @Siuuuuuuuu

## 1. Overview and problem

Gridpilot AI is a multi-agent AI controller that schedules a commercial site's flexible energy loads to cut electricity cost and CO2, with every decision explained and backtested. It is our entry for the **ClimateSphere AI → Gridpilot AI** challenge ("Smart energy optimization and demand response") at the International AI Builders Congress 2026.

**The problem.** Bangladeshi commercial and industrial sites (garment factories, hospitals, cold storage, office towers) pay peak tariffs, lose production to load-shedding, and burn diesel in backup generators. Many are adding rooftop solar and batteries, but run them on fixed rules such as "charge when the sun is up" or "start the generator when the grid drops". Those rules ignore tomorrow's solar forecast, tariff windows and grid carbon, so money and emissions are left on the table.

**Our answer.** A Virtual Power Plant (VPP) controller for one site. It forecasts solar output and site load with uncertainty bands, plans when to charge or discharge the battery, shift flexible loads (HVAC pre-cooling, EV or e-rickshaw charging, pumps) and run the generator, then lets an operator trade cost against carbon with a single slider. An explainer agent answers "why" questions using only numbers from the other agents.

## 2. Goals, non-goals and success metrics

The MVP succeeds if a backtest on real weather data shows lower cost and lower CO2 than a rule-based baseline, and a judge can watch the agents re-plan live.

**Goals**

- Cut a site's daily energy cost and CO2 against two baselines: "charge on arrival / solar-first" and a simple time-of-use rule.
- Plan under uncertainty: schedules stay feasible when solar comes in at the pessimistic (P10) forecast.
- Explain every schedule in plain English and Bangla, citing tool outputs only.
- Ship a live demo and a 3-minute pitch for Congress Day (Dec 26, 2026).

**Non-goals for the MVP**

- Controlling real hardware (inverters, BMS, generators). The MVP outputs a schedule; integration is roadmap.
- Multi-site fleets, grid-operator demand-response markets, reinforcement learning.
- Building our own weather model.

**Success metrics**

| Metric | How measured | MVP target |
| --- | --- | --- |
| Cost reduction | Backtest vs baseline, same days, same assets | Report the measured %; aim for a clear double-digit gain (to verify) |
| CO2 reduction | Grid kWh × emission factor + diesel litres × diesel factor | Report the measured % with factors cited |
| Solar forecast error | MAE / RMSE vs a persistence forecast | Beats persistence |
| Plan robustness | Share of backtest days feasible at P10 solar | 100% feasible after Risk agent re-plan |
| Explanation grounding | Spot-check: every number in an answer traces to a tool output | 0 untraceable numbers in demo set |
| Plan latency | Time from new signal to new schedule | Under 10 s for a 24 h, 15-min plan |

## 3. Users and use cases

The primary buyer is a factory or facility owner who wants a lower energy bill; the daily user is the site's energy or maintenance engineer.

| Persona | Role | Need | What Gridpilot AI gives them |
| --- | --- | --- | --- |
| Facility engineer (primary user) | Runs the solar, battery, generator and HVAC day to day | A plan for tomorrow they can trust and override | 24 h schedule at 15-min steps, "why" answers, alerts when the forecast drifts |
| Owner / CFO (buyer) | Pays the bill, reports to export buyers | Proof of savings and CO2 cuts | Monthly savings and CO2 report against a stated baseline |
| Sustainability lead | Answers brand and buyer carbon questions | Credible, auditable CO2 numbers | Emissions log with the factors used, exportable |
| Solar / battery installer (channel) | Sells and maintains systems | A software upsell that proves ROI | White-label dashboard per client site |

**Core use cases**

1. **Day-ahead plan.** Each evening the engineer gets tomorrow's schedule for battery, flexible loads and generator, with expected cost and CO2.
2. **Cost vs carbon slider.** The owner moves a slider and sees the schedule and the cost/CO2 trade-off update.
3. **Forecast miss.** Clouds roll in; the Risk agent detects actual solar below plan and re-plans within seconds.
4. **Load-shedding window.** A known outage block is entered; the planner pre-charges the battery and pre-cools so the generator runs less.
5. **Ask why.** "Why didn't we charge at 2 PM?" gets an answer quoting the forecast, tariff and battery state behind the decision.
6. **Prove it.** The Evaluator replays past weeks and reports savings against the baseline for the monthly report.

## 4. MVP scope

The MVP models one site in Dhaka with rooftop solar, one battery, one flexible HVAC load, one EV or e-rickshaw charging bank and one diesel generator, planned day-ahead at 15-minute steps on historical replay data.

| Area | In MVP (by Nov 20 prelim) | Added by Congress Day (Dec 26) | Out of scope |
| --- | --- | --- | --- |
| Site | 1 simulated Dhaka site, documented asset specs | 2–3 site templates (factory, hospital, office) | Real multi-site fleet |
| Forecasting | Solar + load, gradient boosting, P10/P50/P90 | LSTM or transformer comparison | Own weather model |
| Signals | Time-of-use tariff, grid emission factor, outage blocks | Hourly carbon proxy from generation mix (if data allows) | Live grid-operator API |
| Planner | MILP over battery, HVAC, EV, generator | Pareto frontier view, faster re-plan | Reinforcement learning |
| Risk | Re-check plan at P10 solar, add safety margin | Live "cloud event" re-plan in demo | Probabilistic stochastic programming |
| Explainer | English answers grounded in tool outputs | Bangla answers and voice | Free-form chat on any topic |
| Evaluator | Backtest vs 2 baselines, cost/CO2/forecast error | Monthly report export (PDF) | Third-party M&V certification |
| UI | Single-page dashboard: schedule, slider, chat | Polished demo flow, mobile view | Hardware control |

## 5. Solution: multi-agent architecture

Seven agents split the work so that a deterministic optimiser makes every decision and the LLM only explains it.

&#91;embedded content: Gridpilot AI agents · 7 agents, 1 dashboard\]

Forecast, Signal and Asset agents run first and feed the Planner; the Risk agent checks each plan against the pessimistic solar forecast and sends it back for re-planning if it fails. The Explainer calls the other agents as tools to answer questions, and the Evaluator replays past days through the Planner to measure savings.

**Tech stack (proposed)**

| Layer | Choice |
| --- | --- |
| Forecasting | Python, LightGBM quantile regression (LSTM/transformer as P1 comparison), pvlib for PV output |
| Optimisation | Pyomo or PuLP with the open-source HiGHS or CBC solver |
| Agents and orchestration | Python services with typed JSON tool interfaces; LLM with function calling for the Explainer |
| Backend | FastAPI, SQLite or Postgres for plans and backtest logs |
| Frontend | React or Streamlit dashboard with schedule, SoC and cost/CO2 charts |
| Deployment | Docker on one cloud VM; demo also runs offline from cached data |

## 6. Data sources and data plan

The MVP runs on historical replay of free, global weather data for Dhaka plus clearly labelled synthetic site data; nothing depends on a paid or rate-limited live API.

| Data | Source (candidate) | Used for | Status |
| --- | --- | --- | --- |
| Solar irradiance (GHI), temperature, cloud cover, hourly history | NASA POWER and Open-Meteo historical APIs, Dhaka coordinates | Solar forecast training and backtest | Free, global; confirm access and terms |
| Weather forecast for demo days | Open-Meteo forecast API | Day-ahead forecast inputs | Free tier; confirm limits |
| PV output | Derived from irradiance with a PV model (e.g. pvlib) for a stated kWp | Forecast target | Simulated, labelled as such |
| Site load profile | Synthetic factory/office profile, shaped by temperature and shift times | Load forecast target | Synthetic, labelled; swap in a real meter export if a site partner agrees |
| Tariff | Bangladesh commercial/industrial time-of-use rates (BERC schedule) | Cost signal | Look up and cite current rates |
| Grid CO2 | Published Bangladesh grid emission factor (tCO2/MWh) | Carbon signal | Look up and cite; flat factor unless hourly mix data is found |
| Diesel CO2 and cost | Standard diesel emission factor and local diesel price | Generator cost and CO2 | Cite factor and price used |
| Load-shedding blocks | Synthetic outage calendar modelled on typical rotation | Outage scenario | Synthetic, labelled |

**Data rules**

- Train/test split by date: no future data leaks into the forecaster.
- Every synthetic series and every factor is labelled in the UI and the pitch.
- All numbers in the pitch come from the Evaluator's backtest log, with the baseline named.

## 7. Functional requirements

P0 items must work for the Nov 20 preliminary submission; P1 items are for Congress Day.

| ID | Requirement | Agent / module | Priority |
| --- | --- | --- | --- |
| FR-01 | Produce P10/P50/P90 solar and load forecasts for the next 24 h at 15-min resolution | Forecast agent | P0 |
| FR-02 | Build one time-aligned signal table: tariff, grid CO2 factor, diesel cost/CO2, outage blocks | Signal agent | P0 |
| FR-03 | Each asset publishes its constraints: battery capacity, power, efficiency, SoC limits; HVAC comfort band; EV departure times and target charge; generator min/max load | Asset agents | P0 |
| FR-04 | Solve a MILP schedule that minimises w · cost + (1 − w) · CO2 subject to all asset constraints | Planner agent | P0 |
| FR-05 | Operator sets w with a cost↔carbon slider; schedule and totals update | UI + Planner | P0 |
| FR-06 | Re-check the plan at P10 solar; if infeasible or comfort/charge targets fail, add margin and re-plan | Risk agent | P0 |
| FR-07 | Answer operator questions about a schedule using only tool outputs; refuse when data is missing | Explainer agent (LLM) | P0 |
| FR-08 | Replay historical days and report cost saved, CO2 avoided and forecast MAE vs both baselines | Evaluator agent | P0 |
| FR-09 | Dashboard: schedule chart (stacked sources vs load), battery SoC line, totals, chat panel | UI | P0 |
| FR-10 | Trigger a simulated "cloud event" mid-day and show the re-plan | Risk agent + UI | P1 |
| FR-11 | Show the cost vs CO2 Pareto frontier for a chosen day | Planner + UI | P1 |
| FR-12 | Bangla text and voice for the Explainer | Explainer | P1 |
| FR-13 | Export a monthly savings and CO2 report (PDF) | Evaluator | P1 |
| FR-14 | Operator override of any slot, with the planner re-solving around it | Planner + UI | P1 |

## 8. Non-functional, responsible AI and safety

The optimiser decides and the LLM only explains, so a hallucination can never change a schedule.

**Non-functional**

- **Performance:** a 24 h, 96-slot plan solves in under 10 s on a laptop; re-plan under 10 s.
- **Reliability:** if the forecaster or a data source fails, fall back to persistence forecast and the last valid plan, and show a warning.
- **Modularity:** each agent is a separate service or module with a typed JSON interface, so assets and sites can be added without touching the planner core.
- **Deployability:** containerised (Docker), runs on one cloud VM or a local machine; no GPU needed for inference.
- **Reproducibility:** fixed seeds, versioned data snapshots, one command to rerun the backtest.

**Responsible AI**

- **Grounded explanations:** the Explainer may only quote values returned by tool calls; answers show which tool each number came from. Missing data → "I don't have that".
- **Human in control:** the system recommends; the operator approves or overrides. No automatic hardware actuation in the MVP.
- **Comfort and safety constraints are hard:** HVAC comfort bands, EV departure charge and battery SoC limits are never traded for savings.
- **Transparency:** baselines, emission factors, synthetic data and forecast error are stated on screen and in the pitch.
- **Data privacy:** site meter data stays with the site owner; only aggregated metrics leave the site in the product version.
- **Fairness of claims:** savings are reported per scenario with the baseline named, never as a single headline without context.

## 9. Business model and go-to-market

We sell Gridpilot AI as SaaS per site, sold through solar and battery installers, with a savings-share option so the customer pays only from money saved.

| Element | Plan |
| --- | --- |
| Customer | Bangladeshi C&I sites with rooftop solar, battery or diesel backup: RMG factories first, then hospitals, cold storage, commercial towers |
| Value proposition | Lower monthly energy bill and less diesel, plus auditable CO2 numbers that export buyers increasingly ask for |
| Pricing | Option A: monthly subscription per site, tiered by kW. Option B: share of verified savings. Prices to set after first pilot |
| Channel | Partner with solar EPC and battery installers; they bundle Gridpilot AI as a software upsell that proves ROI |
| Expansion | Same software for South and Southeast Asian markets with time-of-use tariffs and unreliable grids; later, aggregated sites join grid demand-response programmes |
| NRB / global collaboration | Recruit NRB energy engineers and data scientists as advisors and pilot introducers abroad; open plugin interface for regional tariff and carbon data |
| Validation plan | Before Dec 26: interview 3–5 facility engineers or installers, get one letter of intent or data-sharing agreement |
| Moat | Site-specific forecasting accuracy that improves with data, the explainable agent layer, and installer distribution |

Payback for the customer is shown in the demo from the backtest savings against a stated subscription price; no market-size figure is quoted until sourced.

## 10. Judging alignment and demo script

Each of the Congress's six judging criteria maps to a feature we can show on stage in the 3-minute pitch slot.

| Criterion (weight) | What judges look for | How Gridpilot AI answers |
| --- | --- | --- |
| Innovation (20%) | Non-obvious, systems-level AI | Uncertainty-aware multi-agent VPP with a grounded explainer; dual cost/carbon objective |
| Technical execution (20%) | Clear input → processing → output, model choice, explainability | Quantile forecaster + MILP planner + Risk re-plan; architecture diagram; LLM never decides |
| Business model + global readiness (20%) | Monetisation, adoption path, cross-border fit | SaaS per site via installers, savings-share option, South/Southeast Asia expansion, installer/engineer interviews |
| Real-world impact + ethical AI (20%) | Measurable KPIs, safeguards | Backtested cost and CO2 cut vs named baseline; hard comfort/safety constraints; human approval |
| Scalability + NRB collaboration (10%) | Cloud-ready, modular, global builders | Containerised agents, plugin tariffs/carbon data, NRB advisor network |
| Presentation (10%) | Clear story, live demo, AI reasoning explained | Scripted demo below, with "ask why" live |

**3-minute demo script**

1. **0:00–0:30 Problem.** One Dhaka factory: peak tariff, load-shedding, diesel generator, solar on the roof run by fixed rules.
2. **0:30–1:15 Side by side.** Same historical day: baseline schedule vs Gridpilot AI. Totals show cost and CO2 saved.
3. **1:15–1:45 Slider.** Move cost↔carbon; the schedule and totals change live.
4. **1:45–2:15 Cloud event.** Trigger a solar drop; the Risk agent re-plans in seconds and keeps the EV and comfort targets.
5. **2:15–2:35 Ask why.** "Why did you run the generator at 7 PM?" The answer cites forecast, tariff and battery state.
6. **2:35–3:00 Proof and business.** Backtest result over N weeks, pricing, installer channel, next pilot.

A recorded backup video of the same flow is ready in case the venue internet fails.

## 11. Timeline

The preliminary submission ships Nov 20 after a four-day sprint; Nov 21 to Dec 25 adds P1 features and customer validation for Congress Day.

&#91;embedded content: build plan · 4-day sprint, 2 gates\]

Each day ends with a working build committed. If a day slips, the Risk agent stays a fixed P10 margin and Bangla voice moves to December.

## 12. Risks, assumptions and open questions

The biggest risk is time: four days to the preliminary submission, so scope cuts are decided now, not on Nov 19.

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Not enough time for all 7 agents | Weak or broken prelim | P0 = Forecast, Signal, Planner, Explainer, Evaluator; Risk agent starts as a fixed P10 margin |
| Free weather APIs rate-limited or down | No data for training or demo | Download and cache a full year of Dhaka history on day 1; demo runs offline from cache |
| Synthetic load looks fake to judges | Lower impact score | Label it clearly; seek one real meter export from a factory or installer before Dec 26 |
| Savings look too good | Credibility loss | Report against two baselines, show forecast error, publish assumptions |
| MILP too slow | Laggy demo | 15-min slots, 24 h horizon, open-source solver (HiGHS/CBC); pre-solve demo days |
| LLM invents numbers | Ethics and trust hit | Tool-only answers, numeric check against tool outputs, refuse on missing data |
| Energy is less visible to judges than health or agri | Lower interest | Lead with diesel, load-shedding and garment export pressure, which every Bangladeshi judge knows |

**Open questions**

- [ ] Exact preliminary submission format on the Congress portal (video, deck, repo, live link?) and the Nov 20 cut-off time.
- [ ] Current BERC time-of-use rates for the chosen customer class.
- [ ] Which Bangladesh grid emission factor and source to cite.
- [ ] Team roles: who owns forecasting, optimisation, agents/LLM, frontend, pitch.
- [ ] Which LLM and hosting to use within budget.
- [ ] Can we get one real site partner (factory, hospital or installer) for data or a letter of intent?
