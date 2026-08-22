# 14. Build and Release

## 14.1 Development stack

Recommended:

### Backend

- Python 3.12;
- asyncio;
- FastAPI;
- Pydantic;
- httpx;
- websockets;
- NumPy/Polars or Pandas where justified;
- SQLAlchemy or a lightweight explicit SQLite layer;
- DuckDB and PyArrow;
- pytest, pytest-asyncio, Hypothesis;
- Ruff and mypy.

### Frontend

- React;
- TypeScript;
- Vite;
- Tailwind CSS;
- Lightweight Charts;
- Vitest;
- Playwright.

Codex should select current stable compatible versions and commit lockfiles. Do not hardcode a stale version merely because it appears in this document.

## 14.2 Runtime packaging

Build the frontend into static files served by FastAPI. Normal end-user runtime should not require a separate Node process.

Required scripts:

### Windows

- `setup_windows.ps1`
- `run_windows.bat`

### macOS

- `setup_macos.sh`
- `run_macos.command`

The run script should:

1. verify/create a virtual environment;
2. install pinned Python dependencies if needed;
3. run migrations;
4. start the local server;
5. open the dashboard browser;
6. print the local URL and stop command.

## 14.3 Optional desktop wrapper

A Tauri/equivalent desktop wrapper may be added after the local web app is complete. It must not delay core completion or introduce remote authentication.

## 14.4 Make targets

Provide equivalent commands:

```text
make setup
make dev
make run
make test
make lint
make typecheck
make build
make e2e
make fixture-demo
make network-smoke
make clean-data-safe
```

`clean-data-safe` must never delete prior Runs without explicit confirmation/export.

## 14.5 README

README must cover:

- what the product is and is not;
- no-login/no-real-order statement;
- system requirements;
- first run;
- live public-data versus fixture mode;
- UI tour;
- configuration/new Run;
- troubleshooting;
- data storage and removal;
- test commands;
- known limitations.

## 14.6 Versioning

Version:

```text
0.2.0-paper
```

Record app version, strategy version, config hash and Git commit in every Run.

## 14.7 Release artifact

Produce a release directory or archive containing:

- backend/application source or packaged runtime;
- built frontend;
- scripts;
- default configs;
- README;
- licenses/notices;
- fixture demo data;
- migration tools;
- checksums.

Do not include developer caches, secrets, raw huge market data, or private paths.
