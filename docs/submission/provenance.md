# Data & AI provenance (issue #35)

Section for the final submission portal, scored under "AI & Data Use". The first block can be pasted as it is; the
rest is the evidence behind it. Dates are Nov 2026. Detailed variable lists and re-creation commands are in
[`docs/data-sources.md`](../data-sources.md).

## 1. Text to paste into the portal

> **Data.** Energy Brain schedules a simulated garment factory in Ashulia/Savar, Dhaka. We use **no personal data and
> no proprietary or licensed data**. Weather (2022-2024) is real, public data from NASA POWER, with Open-Meteo as a
> backup. Everything that depends on the factory itself is **simulated or synthetic and labelled as such in the
> product**: solar output is simulated from the weather with pvlib; the factory load and the grid load-shedding
> calendar are synthetic, built from documented engineering assumptions. Electricity tariffs, the grid and diesel
> emission factors and the diesel price are official public values (BERC, Department of Environment, IPCC, BPC),
> each with its source and as-of date. The savings we show are a historical replay on 2024 days that the
> forecasters never saw during training; they are not measured results from a real site.
>
> **AI.** The optimiser that makes every decision is a deterministic MILP (Pyomo + HiGHS), not an LLM. We trained
> our own LightGBM quantile forecasters on the 2022-2023 data. We call two foundation models, both open-weight
> models served by Groq: **openai/gpt-oss-120b** only to explain a finished plan in words (it reads the plan through
> read-only tools, and every number in its answer is checked against tool outputs, otherwise the answer is
> rejected), and **whisper-large-v3-turbo** only to turn a spoken question into text for the chat box. No model
> decides, changes or controls the schedule. No personal data is sent to either model.

## 2. Datasets

| # | Dataset | Source | Public / licensed / proprietary | Real or simulated | Used for |
| --- | --- | --- | --- | --- | --- |
| 1 | Hourly weather 2022-01-01 to 2024-12-31 (irradiance GHI/DNI/DHI, temperature, humidity, cloud, wind) at 23.90 N, 90.32 E | **NASA POWER** (MERRA-2 / GEOS-FP) | Public, open. NASA's data policy allows public, academic and commercial use. | **Real** (reanalysis, not a site sensor) | Input to PV simulation, load model, forecasters |
| 2 | Same variables, backup and gap filling | **Open-Meteo Historical Weather API** | Public. Data under CC BY 4.0 (attribution required). The free API is for non-commercial use, so check its terms before a commercial deployment. | **Real** | Fills gaps in NASA POWER (`combine_first`) |
| 3 | 15-minute solar AC output, 600 kWp, 105,216 slots | Simulated with **pvlib** (PVWatts DC/inverter, Faiman cell temperature, Hay-Davies transposition) | Our own simulation; pvlib is BSD-3 | **Simulated** (no meter export exists) | Planner input, forecaster training target |
| 4 | 15-minute factory load: base load + HVAC, 105,216 slots | Our synthetic model from `config/site.yaml` (shifts, AR(1) noise with fixed seed 42, Bangladesh weekly holiday and public holidays, HVAC driven by real temperature) | Our own synthetic data (real RMG meter data is commercially confidential) | **Synthetic** | Planner input, forecaster training target |
| 5 | Grid outage (load-shedding) calendar, 409 events, 570.2 h over 2022-2024 | Our seeded synthetic calendar following common patterns in industrial Bangladesh | Our own synthetic data | **Synthetic** | Signal agent, outage scenarios, backtest |
| 6 | Electricity tariff: 8.49 / 9.43 / 11.78 BDT/kWh (off-peak / flat / peak), demand charge 90 BDT/kW/month | **BERC** retail tariff order, category MT-3 (as of Feb 2024) | Public, official | **Real published values** | Cost objective |
| 7 | Grid emission factor 0.62 kg CO2/kWh (flat, no hourly series) | **Department of Environment**, Government of Bangladesh / UNFCCC BUR1 (2022) | Public, official | **Real published value** | CO2 objective |
| 8 | Diesel price 107 BDT/L | **Bangladesh Petroleum Corporation** circular (May 2024) | Public, official | **Real published value** | Generator cost |
| 9 | Diesel emission factor 2.68 kg CO2/L | **IPCC 2006 Guidelines**, Vol. 2 Ch. 3 (and UK DEFRA/DESNZ factors) | Public | **Real published value** | Generator CO2 |
| 10 | Site assets: 1,000 kWh battery, 500 kW charge/discharge, EV chargers, 800 kW generator, HVAC limits | Our assumptions in `config/site.yaml` | Our own assumptions | **Assumed** | Planner constraints |
| 11 | Backtest of 2024 (366 days, 3 weights) and the two baseline rules | Our evaluator, `reports/backtest.json` | Our own output | **Computed on synthetic inputs** | Savings shown to users |

Every number the product shows from items 3-5 and 10-11 carries a "synthetic / simulated, historical replay" label
(top strip, the caveats panel, the report PDF and the chart notes). The caveats are stored with the results in
`reports/backtest.json` and are shown next to every number.

Other facts a reviewer should know:
- The forecast accuracy figures use the *observed* weather as the forecast input ("tier 0"). They are not a true
  day-ahead weather forecast, so real-world error would be larger. This is written in the caveats.
- The grid CO2 factor is flat, so shifting grid use in time cannot reduce CO2; CO2 changes come only from diesel vs
  grid. At weight 0.5 the optimiser's CO2 over the year is 0.3 % *higher* than the solar-first baseline, and the
  report says so.
- HVAC runs at its occupied minimum in every method (there is no thermal model), so HVAC is not a source of savings.

## 3. Personal data

- **None is collected or stored.** There are no accounts, logins, tracking cookies or user profiles. The factory
  data is synthetic and the weather data is not about people. The only things kept in the browser are the chosen
  light/dark theme (`localStorage`) and a flag that the intro screen was already shown in this tab
  (`sessionStorage`); neither is sent anywhere.
- The chat sends the operator's typed question to the Explainer. The server does not log question or answer text.
- **Voice input (optional):** the browser records a few seconds of audio, the server forwards it to Groq (Whisper) to
  get text, and the text is put into the input box. The server does not store or log audio or the transcript (it logs
  only size, duration, language and status). Groq's own data handling applies to what it receives; the product tells
  the user "Voice is sent to Groq to turn it into text and is not stored" before use. Users are told not to speak
  personal data into it.
- No personal data is used for training or evaluation.

## 4. AI models

| Model | Where it runs | Trained by | Used for | Not used for |
| --- | --- | --- | --- | --- |
| **LightGBM quantile regressors** (solar and load; P10, P50, P90) | In our backend | **Us**, from scratch, on the 2022-2023 data (2024 held out for the backtest) | Day-ahead forecast with an uncertainty band for the planner and the Risk agent | - |
| **Pyomo + HiGHS MILP** | In our backend | Not a learned model | Chooses every battery, EV, HVAC and generator action under hard constraints; minimises w x cost + (1 - w) x CO2 | - |
| **openai/gpt-oss-120b** (open-weight, Apache-2.0) | Groq API (`LLM_MODEL`, set in `.env`) | OpenAI (foundation model); we do not fine-tune it | The **Explainer agent**: reads one finished plan through read-only tools and explains it in plain English, max about 120 words | Making, changing or recommending decisions; answering from memory; doing any arithmetic |
| **whisper-large-v3-turbo** (open-weight, MIT) | Groq API (`EB_TRANSCRIBE_MODEL`) | OpenAI (foundation model); not fine-tuned | Speech-to-text for the chat input box (English) | Anything beyond producing the text the person then reviews and sends |

How the Explainer is kept honest (this is the core of our responsible-AI design):
1. **Optimiser decides, LLM explains.** The plan is produced and checked (hard constraints, Risk agent) before the LLM
   sees it. The LLM has no tool that can change anything.
2. **Read-only tools with typed results.** The model calls `get_plan_summary`, `get_slot`, `get_slot_range`,
   `find_slots`, `get_forecast`, `get_signals`, `get_assets`, `get_risk_log`, `get_forecast_note`,
   `get_backtest_totals` or `refuse`. The tools do all arithmetic (sums, energies, percentages), so the model never
   calculates.
3. **Traceability check.** Every number, time and date in an answer must appear in a tool output of the same
   conversation (rounding allowed). If not, the answer is sent back once, then refused as "unverified". Answers show
   a "Verified" badge, and every number in the answer is highlighted and shows, on hover or focus, the tool and field it came from.
4. **Refusals.** It refuses questions about missing data, off-topic questions and any request to change or control
   the plan.
5. **Stored answers.** Five demo questions have recorded, verified answers that are re-checked against fresh tool
   results before being served, and are labelled "stored answer" with the recording date and model.
6. **Prompt injection.** The question and tool results are treated as data; unknown tools are rejected, and all
   tools are read-only.
7. **Settings:** temperature 0, low reasoning effort, at most 6 tool rounds, one retry.

The prompts are in [`prompts/`](../../prompts) and in the [prompt library](prompt-library.md). Free-text
questions that are not among the five stored ones use the live Groq quota and are rate limited.

## 5. AI-assisted development

Code, tests and documentation were written with AI coding assistants under human control: the team lead wrote the
prompts, reviewed and tested the output, and approved every commit and merge. Tools and how they were used are
listed in [`disclosure.md`](disclosure.md); the prompts are in [`prompt-library.md`](prompt-library.md). AI-generated
code is held to the same checks as human code: 511 backend and 253 web automated tests, lint and type checks run in
CI on every pull request.

## 6. Third-party components (for attribution)

| Component | Licence (as published by the project) | Notes |
| --- | --- | --- |
| pvlib, Pyomo, HiGHS (highspy), FastAPI, pandas, NumPy, scikit-learn, LightGBM, reportlab | BSD / MIT / Apache style | Python backend |
| React, Vite, Recharts, motion, lucide-react, Tailwind CSS | MIT / ISC | Web app |
| **Tweenly AI Recorder** (voice recorder UI), by Manjunath Irukulla | MIT | Copied into `web/src/components/ui/`, licence header kept; three small edits are listed in its header |
| **React Bits** components (Aurora, ShinyText, CountUp) | Check the licence on reactbits.dev before reuse | Copied from its public registry into `web/src/fx/` with a credit line; **team to confirm the licence terms** |
| Fonts: Plus Jakarta Sans, JetBrains Mono, Playfair Display | SIL Open Font License | Loaded from Google Fonts |
| Logos and brand images in `web/public/` | **Team to confirm who made them** | Not AI-generated by this repo's tooling as far as the commit history shows |

## 7. Open items to confirm before submitting

- [ ] Licence of React Bits and who made the logo files (see section 6).
- [ ] The repository has no `LICENSE` file; decide the licence for our own code.
- [ ] `docs/data-sources.md` section 5 says the system "pre-cools the factory via the flexible HVAC". The product does
      not model pre-cooling and says so; reword that sentence so the two documents agree.
- [ ] Re-check the as-of dates of the tariff, emission and diesel values if a newer official value exists.
