"use strict";
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
electron.contextBridge.exposeInMainWorld("api", {
  baseUrl: process.env["AGENTIDE_API_URL"] ?? "http://localhost:8000"
});
electron.contextBridge.exposeInMainWorld("auth", {
  login: () => electron.ipcRenderer.invoke("auth:login"),
  refresh: () => electron.ipcRenderer.invoke("auth:refresh"),
  logout: () => electron.ipcRenderer.invoke("auth:logout")
});
electron.contextBridge.exposeInMainWorld("workspace", {
  selectFolder: () => electron.ipcRenderer.invoke("dialog:selectFolder")
});
