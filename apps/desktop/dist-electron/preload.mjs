"use strict";
var _a;
const electron = require("electron");
electron.contextBridge.exposeInMainWorld("ipcRenderer", {
  on(...args) {
    const [channel, listener] = args;
    return electron.ipcRenderer.on(channel, (event, ...args2) => listener(event, ...args2));
  },
  off(...args) {
    const [channel, ...omit] = args;
    return electron.ipcRenderer.off(channel, ...omit);
  },
  send(...args) {
    const [channel, ...omit] = args;
    return electron.ipcRenderer.send(channel, ...omit);
  },
  invoke(...args) {
    const [channel, ...omit] = args;
    return electron.ipcRenderer.invoke(channel, ...omit);
  }
  // You can expose other APTs you need here.
  // ...
});
const API_URL_ARG = "--agentide-api-url=";
electron.contextBridge.exposeInMainWorld("api", {
  baseUrl: ((_a = process.argv.find((arg) => arg.startsWith(API_URL_ARG))) == null ? void 0 : _a.slice(API_URL_ARG.length)) ?? "http://localhost:8000"
});
electron.contextBridge.exposeInMainWorld("auth", {
  login: () => electron.ipcRenderer.invoke("auth:login"),
  refresh: () => electron.ipcRenderer.invoke("auth:refresh"),
  logout: () => electron.ipcRenderer.invoke("auth:logout")
});
electron.contextBridge.exposeInMainWorld("mcp", {
  get: () => electron.ipcRenderer.invoke("mcp:get"),
  add: (server) => electron.ipcRenderer.invoke("mcp:add", server),
  update: (id, patch) => electron.ipcRenderer.invoke("mcp:update", id, patch),
  remove: (id) => electron.ipcRenderer.invoke("mcp:remove", id),
  restart: (id) => electron.ipcRenderer.invoke("mcp:restart", id),
  reconnect: () => electron.ipcRenderer.invoke("mcp:reconnect"),
  // Pushed whenever a server or the backend link changes; returns an unsubscribe.
  onChange: (listener) => {
    const handler = (_event, state) => listener(state);
    electron.ipcRenderer.on("mcp:changed", handler);
    return () => {
      electron.ipcRenderer.off("mcp:changed", handler);
    };
  }
});
electron.contextBridge.exposeInMainWorld("workspace", {
  get: () => electron.ipcRenderer.invoke("workspace:get"),
  // Native folder picker; resolves to the new workspace, or null if cancelled.
  pick: () => electron.ipcRenderer.invoke("workspace:pick")
});
