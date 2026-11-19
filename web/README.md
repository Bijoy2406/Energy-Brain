# Energy Brain frontend

Vite + React + TypeScript dashboard for the FastAPI backend. The scaffold loads labelled planning days and
requests a plan at `w=0.5`, selecting `2024-07-02` when available. It shows the API's status, safe flag, warnings
and totals. Schedule charts (#28) and chat (#30) are placeholders. Recharts is pinned for later use.

## Run frontend and backend together

Requires Node.js 22.12+ (22 or a newer LTS release), npm 9.5+, and the Python environment described in the
[root README](../README.md#setup).

In a terminal at the repository root, activate that Python environment and run:

```bash
pip install -r requirements.txt
uvicorn energy_brain.api.app:app --reload --port 8000
```

The backend is at http://localhost:8000; its interactive API docs are at http://localhost:8000/docs.
Without processed data or trained models it uses the sample-data fallback and returns warnings.

In another terminal, from the repository root:

```bash
cd web
npm install
npm run gen:api
npm run dev
```

Open http://localhost:5173. Vite uses port 5173 and fails if it is occupied, keeping the backend's default CORS
origins consistent. Both `localhost:5173` and `127.0.0.1:5173` are allowed by the API.

`VITE_API_BASE_URL` defaults to `http://localhost:8000`. To use another backend, set it before starting Vite:

```bash
VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

For a persistent local setting, copy `.env.example` to `.env.local` and edit `VITE_API_BASE_URL`. Restart Vite
after changing it. For production, set the variable before `npm run build`; Vite embeds it at build time.
`VITE_*` variables are public browser configuration: never put secrets in them. A remote backend must allow
the frontend's origin through its `EB_CORS_ORIGINS` setting.

Run the frontend checks from `web/`:

```bash
npm run lint
npm run test -- --run
npm run build
```

`npm run test` starts Vitest in watch mode. Tests use React Testing Library and mocked `fetch`; they do not
require the backend. `npm run build` checks TypeScript and creates `dist/`. `npm run preview` serves that
production build locally. `node_modules/`, `dist/` and local environment files are ignored by Git.

## API contract and client

[The API guide](../docs/api.md) describes the backend. `npm run gen:api` reads `../docs/openapi.json` and writes
`src/api/generated.ts`. Include that generated file with frontend changes; regenerate it whenever the
contract changes. Do not edit the generated types by hand.

`src/api/client.ts` exposes `days`, `plan`, `backtest`, `backtestDays`, `explain` and `health`. Request bodies,
query parameters and response types come from the generated contract. Each call returns an `ApiResult<T>`:

```ts
import { api } from './src/api/client'

const result = await api.plan({ date: '2024-07-02', w: 0.5 })
if (result.ok) {
  console.log(result.data.safe)
} else {
  console.error(result.error.kind, result.error.message)
}
```

Errors distinguish FastAPI validation lists (`validation`, preserving field locations), structured backend
errors (`api`, preserving `code`, HTTP status and `retry_after_s`, including `/explain` HTTP 429), network
failures (`network`), unrecognised HTTP errors (`http`) and invalid JSON on successful responses (`response`).
The client does not automatically retry rate-limited requests. Network failures show the backend startup command.

A successful HTTP response can contain an unsafe plan. Always check `safe` and show its `banners`,
`forecast_fallback` and `signal_fallback`. Unsafe plans are diagnostic only and must not be presented as
executable. Totals are displayed directly from the API without rounding or frontend accounting.
