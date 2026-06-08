# Desktop Client V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Mac/Windows desktop WebUI client that connects to an already-running xbot gateway.

**Architecture:** Keep gateway as the external backend. The desktop app packages the existing React WebUI, stores a configurable gateway URL, and sends HTTP/WebSocket traffic to that gateway.

**Tech Stack:** FastAPI gateway, React/Vite WebUI, Zustand, Axios, Tauri v2 scaffold.

---

### Task 1: Gateway Desktop Connectivity

**Files:**
- Modify: `xbot/interfaces/gateway/app.py`
- Test: `tests/test_webui_adapter.py`

- [ ] Add a lightweight `/api/desktop/ping` endpoint returning app name/version.
- [ ] Add CORS support for desktop origins and localhost Vite development.
- [ ] Add pytest coverage for ping and CORS preflight behavior.
- [ ] Run targeted gateway adapter tests.

### Task 2: Frontend Gateway URL Configuration

**Files:**
- Create: `xbot/interfaces/webui/frontend/src/stores/gateway-store.ts`
- Modify: `xbot/interfaces/webui/frontend/src/lib/api.ts`
- Modify: `xbot/interfaces/webui/frontend/src/lib/ws.ts`
- Create: `xbot/interfaces/webui/frontend/src/pages/connection.tsx`
- Modify: `xbot/interfaces/webui/frontend/src/App.tsx`

- [ ] Add a persisted gateway URL store with default `http://127.0.0.1:18780`.
- [ ] Change Axios base URL to derive from the gateway store.
- [ ] Change WebSocket URL to derive from the gateway store.
- [ ] Add a connection settings page with URL validation and ping test.
- [ ] Add a route to the connection page.
- [ ] Run frontend build.

### Task 3: Desktop Shell Scaffold

**Files:**
- Create: `desktop/package.json`
- Create: `desktop/src-tauri/Cargo.toml`
- Create: `desktop/src-tauri/tauri.conf.json`
- Create: `desktop/src-tauri/src/main.rs`

- [ ] Create a minimal Tauri v2 project that loads the built WebUI.
- [ ] Configure dev mode to use the existing Vite dev server.
- [ ] Configure production mode to use `xbot/interfaces/webui/frontend/dist`.
- [ ] Document build/run commands in the final handoff.
