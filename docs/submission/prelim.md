# Preliminary Submission draft (issue #4)

**Deadline:** 20 Nov 2026, 23:59 BST (UTC+6). No grace period; a Draft at the deadline is locked as Incomplete
and the team cannot enter final judging.
**Where:** Congress event page → Team dashboard → Submission. Fields autosave; only the **team leader** can
Submit. We can revert to Draft and resubmit until the deadline, so **submit by 19 Nov** and update after.
**Video:** optional at preliminary (mandatory at final).

Each section below is ready to paste. Square-bracket items still need filling.

## Checklist

- [x] Team has at least 3 members (Bijoy2406, AsifAvaas, ovishkh); confirm all three are on the portal
- [x] Basics filled (text below)
- [x] Problem statement filled (text below)
- [x] Solution filled (text below, with measured results)
- [x] Target user filled (text below)
- [ ] Prototype link / repo added and link check shows **PASS** (repo must be public, or add a Judge Access Note)
- [ ] Team roles pasted from `team-and-mentor.md` (leader and third member's name still to fill)
- [x] Mentor engagement notes: not required for this team (team decision); leave blank or write N/A if the portal asks
- [ ] Team reviewed the text together
- [ ] Leader clicked Submit (target: 19 Nov); portal shows **Submitted**
- [ ] Update before 20 Nov 23:59 BST if the MVP improves

## 1. Basics

**Project name:** Energy Brain

**One-line pitch:** AI agents that plan a factory's solar, battery, cooling and generator every day to cut
electricity cost and CO2, and explain every decision.

**Domain:** ClimateSphere AI

**Challenge:** Energy Brain (Smart energy optimization and demand response)

**Public summary:**
> Energy Brain is a multi-agent AI controller for commercial and industrial sites in Bangladesh, starting with
> garment factories. It forecasts tomorrow's solar output and site load with uncertainty bands, then an optimiser
> schedules the battery, flexible loads (cooling, EV charging) and diesel generator around tariff peaks,
> load-shedding and grid carbon. An operator moves one slider between cheapest and cleanest, and can ask "why"
> about any decision; an AI explainer answers using only numbers from the system. On a 2024 out-of-sample
> backtest (366 days, real Dhaka weather, simulated site) it cut daily cost 4.7 % vs a solar-first rule and
> 2.2 % vs a time-of-use rule, and cut diesel use by 66 % vs the solar-first rule. Savings come mostly from
> load-shedding days.

**Primary team language:** English

## 2. Problem statement

**Who the user is:** The facility or energy engineer at a Bangladeshi garment factory (and similar sites such as
hospitals and cold storage) who runs rooftop solar, batteries, cooling and a diesel backup generator; and the
owner who pays the energy bill and answers export buyers' carbon questions.

**The problem today:** These sites pay higher prices at peak hours, lose production to load-shedding, and burn
diesel when the grid drops. Many are adding solar and batteries but run them on fixed rules such as "charge when
the sun is up" or "start the generator when the grid fails".

**Why existing solutions fail:** Fixed rules ignore tomorrow's solar forecast, tariff windows, planned outages
and grid carbon, so batteries are empty when they are needed most and generators run more than necessary.
Commercial energy-management software is built for other markets' grids and tariffs, rarely plans under
forecast uncertainty, and does not explain its decisions to the engineer who must trust it.

**Measurable outcome we change:** Daily energy cost (BDT) and CO2 (kg) for the site, plus diesel generator hours,
measured against two rule-based baselines on the same days and assets. We report the measured percentages with
the baselines and emission factors stated (results in section 3).

## 3. Proposed solution

**Product:** A day-ahead energy planner with a web dashboard. Each evening it produces a 24-hour, 15-minute
schedule for the battery, cooling, EV charging and generator, with expected cost and CO2.

**AI system architecture:** Seven cooperating agents.

1. **Forecast agent:** solar and load forecasts with P10/P50/P90 uncertainty bands (gradient-boosted quantile
   models trained on Dhaka weather history).
2. **Signal agent:** tariff windows, grid CO2 factor, diesel cost and CO2, and outage blocks per slot.
3. **Asset agents:** battery, cooling, EV fleet and generator each publish their limits.
4. **Planner agent:** a mixed-integer linear program that minimises a weighted mix of cost and CO2 while meeting
   every constraint.
5. **Risk agent:** re-checks the plan against the pessimistic (P10) solar forecast and re-plans with a safety
   margin if it would fail.
6. **Explainer agent:** an LLM that answers operator questions by calling the other agents as tools; it may only
   quote numbers they return.
7. **Evaluator agent:** replays past days and measures cost saved, CO2 avoided and forecast error against
   baselines.

The optimiser makes every decision; the LLM only explains, so a hallucination cannot change a schedule.
Comfort, EV departure charge and battery limits are hard constraints, and the operator approves or overrides.

**Key features:** cost↔carbon slider; uncertainty-aware planning; load-shedding preparation (battery
pre-charge and generator planning); grounded "why" answers whose numbers are checked against tool outputs;
backtested savings report.

**User journey:** Evening: the engineer opens tomorrow's plan and checks expected cost and CO2 → moves the slider
if the owner wants lower carbon → asks "why does the generator run at 7 PM?" and gets an answer citing the
forecast, tariff and battery state → approves. Every plan is first checked by the Risk agent at pessimistic (P10) solar and re-planned with a
battery reserve if it would fail; a live "cloud event" re-plan is planned for the demo. Monthly, the owner gets
savings and CO2 against the baseline (report export planned).

**Mapping to the challenge:** Specific ask "cost and emissions savings" → dual cost/CO2 objective with measured
savings. Details "forecasting + optimization + agents" → quantile forecasting, MILP optimisation, and a
multi-agent system with a grounded explainer.

**Results so far (2024 backtest, out-of-sample, `reports/backtest.json`, reproducible with
`python -m energy_brain.evaluator`):** 366 days at cost/CO2 weight 0.5, same assets and constraints for all
methods, settled against actual solar and load.

| | vs solar-first rule | vs time-of-use rule |
| --- | --- | --- |
| Cost | 4.7 % lower (2.08 M BDT) | 2.2 % lower (0.96 M BDT) |
| Cost on load-shedding days (138) / other days (228) | 8.2 % / 1.9 % lower | 4.8 % / 0.2 % lower |
| CO2 | 0.3 % higher | 0.2 % lower |
| Generator hours / diesel | 134 h vs 190 h; 10.9 kL vs 31.7 kL (66 % less) | 134 h vs 189 h; 10.9 kL vs 25.3 kL (57 % less) |

Honest limits: the load, PV and load-shedding calendar are synthetic or simulated; forecast error (solar MAE
1.9 kW, load MAE 2.2 kW) is measured with observed weather as the forecast input, so real day-ahead error would be
larger; the grid emission factor is a flat 0.62 kg/kWh, so shifting grid use barely changes CO2 and the CO2 gain
is the diesel cut; the saving versus the time-of-use rule is small on days without load-shedding. We do not claim
more than these figures.

**Data (honest scope):** Real historical weather for Dhaka (NASA POWER / Open-Meteo); solar output simulated from
it; a simulated factory with documented assumptions and a synthetic load profile; published Bangladesh tariff and
emission factors. All simulated or synthetic data is labelled.

## 4. Target user

Primary user: facility/energy engineers at Bangladeshi garment factories with rooftop solar, batteries or diesel
backup. Buyer: factory owners/CFOs. Channel partners: solar and battery installers who bundle Energy Brain with
their systems. Later: hospitals, cold storage and commercial buildings, then other South and Southeast Asian
markets with time-of-use tariffs and unreliable grids.

## 5. Prototype link or repo

https://github.com/Bijoy2406/Energy-Brain

Backend (FastAPI), seven agents, backtest and an Explainer with traceable numbers are in the repo; run
instructions are in `README.md` and `docs/api.md`. [Add the live demo link here if deployed before the deadline.]

## 6. Team roles

[Paste from `team-and-mentor.md`.]

## 7. Initial mentor engagement notes

Not required for this team (team decision). If the portal insists on text: "No mentor session held for the
preliminary; we will seek mentor feedback before the final submission."
