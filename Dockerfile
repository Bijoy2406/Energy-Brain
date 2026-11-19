# The browser build contains only public configuration; use relative API requests.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    HOME=/home/user \
    EB_STATIC_DIR=/app/web-dist \
    EB_WARMUP=1 \
    EB_EXPLAIN_CACHE=prefer \
    LLM_PROVIDER=groq \
    LLM_MODEL=openai/gpt-oss-120b

# LightGBM's wheel needs OpenMP; no compiler or development dependencies are installed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 user \
    && mkdir /app && chown user:user /app
WORKDIR /app
COPY --chown=user:user pyproject.toml README.md ./
COPY --chown=user:user src/ ./src/
RUN pip install --no-cache-dir . && rm -rf build src/*.egg-info

# PYTHONPATH deliberately uses /app/src: existing modules resolve config/data/models
# relative to their source tree. A site-packages import would look in the wrong root.
COPY --chown=user:user config/ ./config/
COPY --chown=user:user prompts/ ./prompts/
COPY --chown=user:user reports/backtest.json reports/explainer_demo_answers.json reports/forecast_metrics.json ./reports/
COPY --from=web --chown=user:user /web/dist/ ./web-dist/
USER user

# Network is needed here (Open-Meteo and NASA POWER). Fail the build if a step fails.
# These are the existing CLI pipelines; outages are generated in memory by the API.
RUN printf '\n[1/5] Downloading and cleaning 2022-2024 weather\n' \
    && python -m energy_brain.data.download_weather \
    && printf '\n[2/5] Simulating PV\n' \
    && python -m energy_brain.data.simulate_pv \
    && printf '\n[3/5] Generating synthetic load\n' \
    && python -m energy_brain.data.synthetic_load \
    && printf '\n[4/5] Training solar quantile forecasters\n' \
    && python -m energy_brain.forecast.solar --train \
    && printf '\n[5/5] Training load quantile forecasters\n' \
    && python -m energy_brain.forecast.load --train \
    && rm -rf data/raw

EXPOSE 7860
CMD ["sh", "-c", "exec uvicorn energy_brain.api.app:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers --forwarded-allow-ips='*'"]
