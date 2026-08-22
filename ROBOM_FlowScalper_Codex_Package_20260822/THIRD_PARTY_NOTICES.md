# Third-Party Notices

Generated from the locked local environment on 2026-08-22. The authoritative complete dependency graphs are `uv.lock` and `frontend/pnpm-lock.yaml`. Each package remains subject to its own license text and repository terms.

## Python direct dependencies

| Package | Locked version | License | Project |
|---|---:|---|---|
| DuckDB | 1.5.5 | MIT | https://duckdb.org/ |
| FastAPI | 0.116.2 | MIT | https://github.com/fastapi/fastapi |
| HTTPX | 0.28.1 | BSD-3-Clause | https://www.python-httpx.org/ |
| Pydantic | 2.13.4 | MIT | https://github.com/pydantic/pydantic |
| PyArrow | 21.0.0 | Apache-2.0 | https://arrow.apache.org/ |
| PyYAML | 6.0.3 | MIT | https://pyyaml.org/ |
| Uvicorn | 0.35.0 | BSD-3-Clause | https://www.uvicorn.org/ |
| websockets | 15.0.1 | BSD-3-Clause | https://websockets.readthedocs.io/ |

## Python development dependencies

| Package | Locked version | License |
|---|---:|---|
| Hypothesis | 6.165.10 | MPL-2.0 |
| mypy | 1.20.2 | MIT |
| pytest | 8.4.2 | MIT |
| pytest-asyncio | 1.4.0 | Apache-2.0 |
| Ruff | 0.12.12 | MIT |

## Frontend direct runtime and build dependencies

| Package | Locked version | License |
|---|---:|---|
| React | 19.2.8 | MIT |
| React DOM | 19.2.8 | MIT |
| Lightweight Charts | 5.2.1 | Apache-2.0 |
| fancy-canvas | 2.1.0 | MIT |
| Vite | 8.2.2 | MIT |
| @vitejs/plugin-react | 6.1.0 | MIT |

## Frontend direct test and analysis dependencies

| Package | Locked version | License |
|---|---:|---|
| @eslint/js | 10.0.1 | MIT |
| @playwright/test | 1.62.1 | Apache-2.0 |
| @testing-library/jest-dom | 7.0.1 | MIT |
| @testing-library/react | 16.3.2 | MIT |
| @types/node | 26.2.0 | MIT |
| @types/react | 19.2.18 | MIT |
| @types/react-dom | 19.2.4 | MIT |
| ESLint | 10.8.1 | MIT |
| eslint-plugin-react-hooks | 7.1.1 | MIT |
| eslint-plugin-react-refresh | 0.5.4 | MIT |
| globals | 17.11.0 | MIT |
| jsdom | 30.0.1 | MIT |
| TypeScript | 6.0.2 | Apache-2.0 |
| typescript-eslint | 8.67.0 | MIT |
| Vitest | 4.1.11 | MIT |

The application does not bundle an exchange SDK or a remote telemetry SDK. The production browser bundle contains React, React DOM, scheduler, Lightweight Charts, fancy-canvas and application code; testing tools are not required by the normal Python runtime. Redistributed runtime license texts are in `THIRD_PARTY_LICENSES/`.
