# Deploy Energy Brain to Hugging Face Spaces

This publishes one Docker container serving the built dashboard and FastAPI API on port 7860.
You create the Space and push its separate Git repository; nothing here deploys automatically from GitHub.
Synthetic load, simulated PV, synthetic outages. Historical replay.

Docker is not installed on the preparation machine. **The Docker image could not be tested locally.**
The application, frontend, packaging script and workflow YAML can be checked without Docker; the Space build
is still required to validate the image, dependency installation, weather download and model training together.

## 1. Create the Space and a write token

1. Create or sign in to a [Hugging Face account](https://huggingface.co/join).
2. Open [Create a Space](https://huggingface.co/new-space). Choose your owner and a name such as `energy-brain`.
   Choose **Docker** SDK, **Blank** template, **Public** visibility and **CPU Basic** hardware. No GPU is needed.
   Hugging Face currently requires a paid plan to create Docker Spaces; CPU Basic itself has no hourly charge.
   Check the current [Space requirements](https://huggingface.co/docs/hub/spaces-overview#creating-a-new-space)
   before subscribing. A public Space makes the exported source and app visible to everyone.
3. Open [Settings → Access Tokens](https://huggingface.co/settings/tokens) and create a token with **write**
   permission for this Space. A fine-grained token scoped to the Space is sufficient. Keep it in your password
   manager. Git asks for your Hugging Face username and the token as its password; do not embed it in a URL,
   shell command or file. See [Hugging Face token instructions](https://huggingface.co/docs/hub/security-tokens).

The Hugging Face token is for uploading code. The Groq key below is a different credential used only by the app.

## 2. Export the working tree

You need Git and Bash (Linux/macOS, WSL or Git Bash on Windows). From the Energy Brain repository root:

```bash
bash scripts/make_hf_space.sh
```

The script creates `build/hf-space/` and prints its size and upload commands. It copies current working-tree
contents, including these uncommitted deployment changes. It does not stage, commit or push anything.
The destination must not already exist; use a new path as the first argument if needed.

The export includes only Docker inputs: `Dockerfile`, `.dockerignore`, `pyproject.toml`, `src/`, `config/`,
`prompts/`, the three runtime JSON reports, `web/` source, and `LICENSE` if present. It replaces the project's
README with [the Space card](../deploy/hf-space/README.md), whose YAML front matter selects Docker and port 7860.
That README is also required by Python package metadata. Symlinks and forbidden input paths are rejected;
local environment files, processed data and trained models are never exported.

## 3. Configure runtime settings

In the Space's **Settings → Variables and secrets**, add:

| Type | Name | Value |
| --- | --- | --- |
| Secret | `LLM_API_KEY` | Your Groq key, entered only in this secret field |
| Variable | `LLM_PROVIDER` | `groq` |
| Variable | `LLM_MODEL` | `openai/gpt-oss-120b` |
| Variable | `EB_EXPLAIN_CACHE` | `prefer` |

These public variable values also have Docker defaults. The image sets `EB_STATIC_DIR=/app/web-dist` and
`EB_WARMUP=1`. Leave `PORT` unset for 7860. No `VITE_API_BASE_URL` or CORS setting is needed for this deployment:
the browser uses relative requests to the same HTTPS origin as the site. `VITE_*` settings are public and baked
into the JavaScript at build time; never put credentials in them.

The key is injected at runtime. It is never copied into the export or supplied as a Docker build argument.
Hugging Face documents [Docker variables, secrets and uid 1000](https://huggingface.co/docs/hub/spaces-sdks-docker).

## 4. Push the separate Space repository

Run the commands printed by the script. Replace `<user>` and `<space>` below with the owner and Space name
before running them (do not type the angle brackets):

```bash
cd build/hf-space
git init -b main
git remote add origin https://huggingface.co/spaces/<user>/<space>
git fetch origin main
git reset --mixed origin/main
git add --all
git commit -m "Deploy Energy Brain"
git push -u origin main
```

The fetch/reset uses the initial README commit that Hugging Face created as the parent of your upload,
while keeping the exported working files. This avoids a force push or unrelated histories.
Run these commands inside the exported folder, which is its own Git repository, not at the Energy Brain root.
At the credential prompt, use the write token as the password. If the Space was created without a `main`
branch, skip the fetch/reset pair on the first upload.

## 5. Watch the build and verify the public link

Open the Space's **App → Logs** and select the build log. Each push rebuilds the app. The image first runs
`npm ci` and the production frontend build, then installs the Python package without dev dependencies.
The build runs these existing pipelines in order, with numbered log messages:

```bash
python -m energy_brain.data.download_weather
python -m energy_brain.data.simulate_pv
python -m energy_brain.data.synthetic_load
python -m energy_brain.forecast.solar --train
python -m energy_brain.forecast.load --train
```

The first three commands are documented in [data sources](data-sources.md); the forecast CLIs require `--train`.
They create 2022–2024 weather/PV/load data, samples, and six quantile models inside the image. Weather download
needs public network access to Open-Meteo and NASA POWER. Raw downloads are removed after training. The API
generates outages in memory, so the outage CLI is intentionally skipped. The copied backtest and stored demo
answer reports remain the recorded results; the training CLIs update forecast metrics inside the image only.
No generated data or models are committed to either repository. `docs/openapi.json` is unnecessary at runtime:
FastAPI generates the schema, and frontend types are already committed.

Wait for **Running** and let warmup finish. Open the public Space link in an incognito/private window, without
signing in. Use its **Open in new tab** action to get the direct `https://<space-subdomain>.hf.space` URL; copy
the actual URL shown by Hugging Face. Verify:

- Days load and selecting **2024-07-02**, weight **0.5**, shows a plan chart and honest data labels.
- Moving the cost/CO2 slider requests a new plan. Return it to **0.5** before trying the demo questions.
- Each of the five suggested demo questions answers quickly and displays **Stored answer**. Use the exact
  suggested wording. Limits are five questions per minute per client IP; wait before repeating all five.
- The backtest tab loads its comparison and caveats.
- `/docs` opens the interactive API docs; `/openapi.json` returns JSON.
- `/health` returns `status: "ok"`, `processed_data: true`, `models: true`, `backtest: true`,
  `demo_answers: 5`, `explain_cache: "prefer"` and eventually `warmup.state: "done"`.
  `llm_key_configured` is only a boolean; it does not prove the provider accepted the key.

Stored answers are accepted only when their recorded tool results match the current plan exactly. A new
weather download, dependency version or model training result can change the plan. If the five questions
are answered live instead, inspect the logs and data/model versions; do not label live answers as stored.
Re-recording answers is a separate task requiring a verified plan and live LLM access (see [API guide](api.md)).

After these checks, use the verified public URL for the portal and the demo-link placeholder in Section 5 of
`docs/submission/prelim.md`. That submission file is not modified by this preparation.

## 6. Wake the Space before a link check

CPU Basic Spaces may sleep after inactivity. Before a reviewer or portal checks the link, open the Space's
Hugging Face page, wait for it to wake and show **Running**, then open the dashboard. If it is paused, use the
owner's restart control. Check `/health`, wait for warmup to finish, and run one plan before sharing the link.
A sleeping Space does not provide an always-on service; see [Space lifecycle](https://huggingface.co/docs/hub/spaces-overview#lifecycle).

## 7. Redeploy after changes

Return to the Energy Brain root and export into a fresh directory:

```bash
bash scripts/make_hf_space.sh ./build/hf-space-update
```

Use the commands printed for that folder. Fetch/reset attaches the new working-tree export to the current
Space `main`, so a normal push updates the existing Space. Use another unused folder name next time; the
script refuses to overwrite an existing folder, including its Git history. Recheck the build log and the
incognito checklist after each deployment. Variables/secrets stay in the Space settings across code pushes.

## 8. Rotate the Groq key

Create a replacement key in [Groq's console](https://console.groq.com/keys), replace the Space's `LLM_API_KEY`
secret, then restart the Space if it has not restarted automatically. Test one custom question that is not a
stored demo question to check live access. Revoke the old key in Groq after the replacement works. No code
change, frontend rebuild or key in a Git commit is needed.

## Troubleshooting

| Symptom | Check or action |
| --- | --- |
| Weather download fails during build | Read the `[1/5]` error for the failing provider. Check Open-Meteo/NASA availability and retry with the Space's Factory reboot/rebuild control after a transient outage. Both providers must work in the current downloader; no key is required. |
| Out of memory | Find whether npm, training, or runtime failed in the logs. Try `EB_WARMUP=0` for runtime startup pressure. If necessary choose hardware with more RAM, checking its price first. Do not remove model training and present fallback output as the full demo. |
| Groq 429 or API rate limit | Wait for the indicated retry interval and stop repeated requests. Keep `EB_EXPLAIN_CACHE=prefer`, day 2024-07-02 and weight 0.5 for the stored questions. Custom questions still use the live quota. |
| Blank page or API unreachable | Open the direct app URL; check `/health`, runtime logs and browser network errors. A failed `/assets/*.js` request suggests an incomplete frontend build or wrong static directory. Confirm `EB_STATIC_DIR=/app/web-dist`, port 7860, and that no build-time `VITE_API_BASE_URL` points at localhost. After redeploy, refresh the page to load the new asset names. |
| No Stored answer badge | Check the exact day, weight and suggested question, and the current plan against the recorded demo results. Cached answers are deliberately rejected if their numbers no longer match. |

When Docker is available elsewhere, an optional image check from the exported folder is:

```bash
docker build -t energy-brain-demo .
docker run --rm -p 7860:7860 energy-brain-demo
```

This command runs without a key; matching stored answers work, while other questions report missing live access.
It does not validate real Groq authentication. The public Space checklist is still required.
