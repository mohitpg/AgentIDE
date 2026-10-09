import { shell, safeStorage, app, BrowserWindow, ipcMain, dialog } from "electron";
import { fileURLToPath } from "node:url";
import path from "node:path";
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
const API_BASE = process.env["AGENTIDE_API_URL"] ?? "http://localhost:8000";
const LOGIN_TIMEOUT_MS = 5 * 60 * 1e3;
let refreshToken = null;
let refreshInFlight = null;
let cancelPendingLogin = null;
const sessionFile = () => path.join(app.getPath("userData"), "session.bin");
function persist(token) {
  refreshToken = token;
  try {
    if (token === null) {
      fs.rmSync(sessionFile(), { force: true });
    } else if (safeStorage.isEncryptionAvailable()) {
      fs.writeFileSync(sessionFile(), safeStorage.encryptString(token), { mode: 384 });
    }
  } catch (err) {
    console.warn("[auth] could not persist session:", err);
  }
}
function loadPersisted() {
  if (refreshToken) return refreshToken;
  try {
    if (!safeStorage.isEncryptionAvailable()) return null;
    const file = sessionFile();
    if (!fs.existsSync(file)) return null;
    refreshToken = safeStorage.decryptString(fs.readFileSync(file));
    return refreshToken;
  } catch {
    return null;
  }
}
function adopt(tokens) {
  persist(tokens.refresh_token);
  return { accessToken: tokens.access_token, expiresIn: tokens.expires_in, user: tokens.user };
}
async function errorDetail(res) {
  const body = await res.json().catch(() => null);
  return typeof (body == null ? void 0 : body.detail) === "string" ? body.detail : `${res.status} ${res.statusText}`;
}
const b64url = (buf) => buf.toString("base64url");
const DONE_PAGE = (ok, msg) => `<!doctype html><meta charset="utf-8"><title>AgentIDE</title>
<body style="font-family:system-ui;background:#0f0f0f;color:#e5e5e5;display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center"><h2>${ok ? "Signed in to AgentIDE" : "Sign-in failed"}</h2>
<p style="color:#999">${msg}</p></div></body>`;
function waitForCode(state, open) {
  return new Promise((resolve, reject) => {
    let redirectUri = "";
    const server = http.createServer((req, res) => {
      const url = new URL(req.url ?? "/", "http://127.0.0.1");
      const code = url.searchParams.get("code");
      const error = url.searchParams.get("error");
      if (!code && !error) {
        res.writeHead(404).end();
        return;
      }
      if (url.searchParams.get("state") !== state) {
        res.writeHead(400, { "Content-Type": "text/html" }).end(DONE_PAGE(false, "State mismatch."));
        return;
      }
      if (error || !code) {
        res.writeHead(200, { "Content-Type": "text/html" }).end(DONE_PAGE(false, "You can close this tab."));
        finish(new Error(error === "access_denied" ? "Sign-in was cancelled" : `Google returned: ${error}`));
        return;
      }
      res.writeHead(200, { "Content-Type": "text/html" }).end(
        DONE_PAGE(true, "You can close this tab and return to the app.")
      );
      finish(null, code);
    });
    const timer = setTimeout(() => finish(new Error("Sign-in timed out")), LOGIN_TIMEOUT_MS);
    let settled = false;
    function finish(err, code) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      cancelPendingLogin = null;
      server.close();
      if (err) reject(err);
      else resolve({ code, redirectUri });
    }
    cancelPendingLogin = () => finish(new Error("Sign-in was restarted"));
    server.on("error", (err) => finish(err));
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      redirectUri = `http://127.0.0.1:${port}/`;
      open(redirectUri);
    });
  });
}
async function login() {
  cancelPendingLogin == null ? void 0 : cancelPendingLogin();
  const cfgRes = await fetch(`${API_BASE}/auth/config`);
  if (!cfgRes.ok) throw new Error(`Backend unavailable (${cfgRes.status})`);
  const cfg = await cfgRes.json();
  if (!cfg.google_enabled) throw new Error("Google sign-in is not configured on the server");
  const verifier = b64url(crypto.randomBytes(32));
  const challenge = b64url(crypto.createHash("sha256").update(verifier).digest());
  const state = b64url(crypto.randomBytes(16));
  const { code, redirectUri } = await waitForCode(state, (redirectUri2) => {
    const url = new URL(cfg.authorization_endpoint);
    url.search = new URLSearchParams({
      client_id: cfg.google_client_id,
      redirect_uri: redirectUri2,
      response_type: "code",
      scope: cfg.scopes,
      code_challenge: challenge,
      code_challenge_method: "S256",
      state,
      prompt: "select_account"
    }).toString();
    void shell.openExternal(url.toString());
  });
  const res = await fetch(`${API_BASE}/auth/google`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, code_verifier: verifier, redirect_uri: redirectUri })
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  return adopt(await res.json());
}
function refresh() {
  refreshInFlight ?? (refreshInFlight = (async () => {
    const token = loadPersisted();
    if (!token) return null;
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: token })
    });
    if (res.status === 401) {
      persist(null);
      return null;
    }
    if (!res.ok) throw new Error(await errorDetail(res));
    return adopt(await res.json());
  })().finally(() => {
    refreshInFlight = null;
  }));
  return refreshInFlight;
}
async function logout() {
  const token = loadPersisted();
  persist(null);
  if (!token) return;
  try {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: token })
    });
  } catch {
  }
}
const __dirname$1 = path.dirname(fileURLToPath(import.meta.url));
process.env.APP_ROOT = path.join(__dirname$1, "..");
const VITE_DEV_SERVER_URL = process.env["VITE_DEV_SERVER_URL"];
const MAIN_DIST = path.join(process.env.APP_ROOT, "dist-electron");
const RENDERER_DIST = path.join(process.env.APP_ROOT, "dist");
process.env.VITE_PUBLIC = VITE_DEV_SERVER_URL ? path.join(process.env.APP_ROOT, "public") : RENDERER_DIST;
let win;
function createWindow() {
  win = new BrowserWindow({
    icon: path.join(process.env.VITE_PUBLIC, "electron-vite.svg"),
    webPreferences: {
      preload: path.join(__dirname$1, "preload.mjs")
    }
  });
  win.webContents.on("did-finish-load", () => {
    win == null ? void 0 : win.webContents.send("main-process-message", (/* @__PURE__ */ new Date()).toLocaleString());
  });
  if (VITE_DEV_SERVER_URL) {
    win.loadURL(VITE_DEV_SERVER_URL);
  } else {
    win.loadFile(path.join(RENDERER_DIST, "index.html"));
  }
}
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
    win = null;
  }
});
app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
ipcMain.handle("dialog:selectFolder", async () => {
  const result = await dialog.showOpenDialog({
    title: "Select workspace folder",
    properties: ["openDirectory", "createDirectory"]
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
});
ipcMain.handle("auth:login", () => login());
ipcMain.handle("auth:refresh", () => refresh());
ipcMain.handle("auth:logout", () => logout());
app.whenReady().then(createWindow);
export {
  MAIN_DIST,
  RENDERER_DIST,
  VITE_DEV_SERVER_URL
};
