# Preliminary Submission draft (issue #4)

**Deadline:** 20 Nov 2026, 23:59 BST (UTC+6). No grace period; a Draft at the deadline is locked as Incomplete
and the team cannot enter final judging.
**Where:** Congress event page → Team dashboard → Submission. Fields autosave; only the **team leader** can
Submit. We can revert to Draft and resubmit until the deadline, so **submit by 19 Nov** and update after.
**Video:** optional at preliminary (mandatory at final).

Each section below is ready to paste. Square-bracket items still need filling.

## Checklist

- [ ] Team has at least 3 members on the portal
- [ ] Basics filled
- [ ] Problem statement filled
- [ ] Solution filled
- [ ] Target user filled
- [ ] Prototype link / repo added and link check shows **PASS** (repo must be public, or add a Judge Access Note)
- [ ] Team roles pasted from `team-and-mentor.md`
- [ ] Mentor engagement notes pasted from `team-and-mentor.md` (after a real mentor session)
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
> about any decision; an AI explainer answers using only numbers from the system. Savings are measured by
> backtesting against rule-based baselines on real Dhaka weather data.

**Primary team language:** [English / Bangla — choose]

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
the baselines and emission factors stated; no number is claimed before the backtest produces it.

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

**Key features:** cost↔carbon slider; uncertainty-aware planning; load-shedding preparation (pre-charge,
pre-cool); grounded "why" answers; backtested savings report.

**User journey:** Evening: the engineer opens tomorrow's plan and checks expected cost and CO2 → moves the slider
if the owner wants lower carbon → asks "why does the generator run at 7 PM?" and gets an answer citing the
forecast, tariff and battery state → approves. During the day, if clouds cut solar, the Risk agent re-plans.
Monthly, the owner gets savings and CO2 against the baseline.

**Mapping to the challenge:** Specific ask "cost and emissions savings" → dual cost/CO2 objective with measured
savings. Details "forecasting + optimization + agents" → quantile forecasting, MILP optimisation, and a
multi-agent system with a grounded explainer.

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

[Add the live demo link here if deployed before the deadline.]

## 6. Team roles

[Paste from `team-and-mentor.md`.]

## 7. Initial mentor engagement notes

[Paste from `team-and-mentor.md` after the mentor session.]
