import { app, shell, safeStorage, protocol, BrowserWindow, ipcMain, dialog, net } from "electron";
import { fileURLToPath, pathToFileURL } from "node:url";
import path$3 from "node:path";
import crypto$1 from "node:crypto";
import fs$1 from "node:fs";
import http from "node:http";
import { _ as _coercedNumber, Z as ZodNumber, C as CallToolResultSchema, M as McpError, E as ErrorCode, P as Protocol, A as AjvJsonSchemaValidator, T as ToolListChangedNotificationSchema, a as PromptListChangedNotificationSchema, R as ResourceListChangedNotificationSchema, m as mergeCapabilities, g as getObjectShape, b as getLiteralValue, L as LATEST_PROTOCOL_VERSION, I as InitializeResultSchema, S as SUPPORTED_PROTOCOL_VERSIONS, c as assertToolsCallTaskCapability, d as assertClientRequestTaskCapability, e as EmptyResultSchema, f as CompleteResultSchema, G as GetPromptResultSchema, h as ListPromptsResultSchema, i as ListResourcesResultSchema, j as ListResourceTemplatesResultSchema, k as ReadResourceResultSchema, l as ListToolsResultSchema, n as ListChangedOptionsBaseSchema, s as safeParse, o as ElicitRequestSchema, p as CreateTaskResultSchema, q as ElicitResultSchema, r as CreateMessageRequestSchema, t as CreateMessageResultWithToolsSchema, u as CreateMessageResultSchema, v as url, N as NEVER, w as looseObject, x as boolean, y as array, z as string, B as object, D as literal, F as any, H as number$1, J as JSONRPCMessageSchema, K as commonjsGlobal, O as getDefaultExportFromCjs, Q as ReadBuffer, U as serializeMessage } from "./stdio-BIHAWkvt.js";
import require$$0$1 from "child_process";
import path$4 from "path";
import require$$0 from "fs";
import process$1 from "node:process";
import { PassThrough } from "node:stream";
import { execFile } from "node:child_process";
import os from "node:os";
const API_BASE = !app.isPackaged && process.env["AGENTIDE_API_URL"] || "http://localhost:8000";
const LOGIN_TIMEOUT_MS = 5 * 60 * 1e3;
let refreshToken = null;
let access = null;
let refreshInFlight = null;
let cancelPendingLogin = null;
const sessionFile = () => path$3.join(app.getPath("userData"), "session.bin");
function persist(token) {
  refreshToken = token;
  if (token === null) access = null;
  try {
    if (token === null) {
      fs$1.rmSync(sessionFile(), { force: true });
    } else if (safeStorage.isEncryptionAvailable()) {
      fs$1.writeFileSync(sessionFile(), safeStorage.encryptString(token), { mode: 384 });
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
    if (!fs$1.existsSync(file)) return null;
    refreshToken = safeStorage.decryptString(fs$1.readFileSync(file));
    return refreshToken;
  } catch {
    return null;
  }
}
function adopt(tokens) {
  persist(tokens.refresh_token);
  access = { token: tokens.access_token, expiresAt: Date.now() + tokens.expires_in * 1e3 };
  return { accessToken: tokens.access_token, expiresIn: tokens.expires_in, user: tokens.user };
}
async function errorDetail(res) {
  const body = await res.json().catch(() => null);
  return typeof body?.detail === "string" ? body.detail : `${res.status} ${res.statusText}`;
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
      const url2 = new URL(req.url ?? "/", "http://127.0.0.1");
      const code = url2.searchParams.get("code");
      const error = url2.searchParams.get("error");
      if (!code && !error) {
        res.writeHead(404).end();
        return;
      }
      if (url2.searchParams.get("state") !== state) {
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
  cancelPendingLogin?.();
  const cfgRes = await fetch(`${API_BASE}/auth/config`);
  if (!cfgRes.ok) throw new Error(`Backend unavailable (${cfgRes.status})`);
  const cfg = await cfgRes.json();
  if (!cfg.google_enabled) throw new Error("Google sign-in is not configured on the server");
  const verifier = b64url(crypto$1.randomBytes(32));
  const challenge = b64url(crypto$1.createHash("sha256").update(verifier).digest());
  const state = b64url(crypto$1.randomBytes(16));
  const { code, redirectUri } = await waitForCode(state, (redirectUri2) => {
    const url2 = new URL(cfg.authorization_endpoint);
    url2.search = new URLSearchParams({
      client_id: cfg.google_client_id,
      redirect_uri: redirectUri2,
      response_type: "code",
      scope: cfg.scopes,
      code_challenge: challenge,
      code_challenge_method: "S256",
      state,
      prompt: "select_account"
    }).toString();
    void shell.openExternal(url2.toString());
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
  refreshInFlight ??= (async () => {
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
  });
  return refreshInFlight;
}
async function accessToken(forceRefresh = false) {
  if (!forceRefresh && access && access.expiresAt - Date.now() > 6e4) return access.token;
  const session = await refresh();
  return session?.accessToken ?? null;
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
const ZodIssueCode = {
  custom: "custom"
};
function number(params) {
  return _coercedNumber(ZodNumber, params);
}
class ExperimentalClientTasks {
  constructor(_client) {
    this._client = _client;
  }
  /**
   * Calls a tool and returns an AsyncGenerator that yields response messages.
   * The generator is guaranteed to end with either a 'result' or 'error' message.
   *
   * This method provides streaming access to tool execution, allowing you to
   * observe intermediate task status updates for long-running tool calls.
   * Automatically validates structured output if the tool has an outputSchema.
   *
   * @example
   * ```typescript
   * const stream = client.experimental.tasks.callToolStream({ name: 'myTool', arguments: {} });
   * for await (const message of stream) {
   *   switch (message.type) {
   *     case 'taskCreated':
   *       console.log('Tool execution started:', message.task.taskId);
   *       break;
   *     case 'taskStatus':
   *       console.log('Tool status:', message.task.status);
   *       break;
   *     case 'result':
   *       console.log('Tool result:', message.result);
   *       break;
   *     case 'error':
   *       console.error('Tool error:', message.error);
   *       break;
   *   }
   * }
   * ```
   *
   * @param params - Tool call parameters (name and arguments)
   * @param resultSchema - Zod schema for validating the result (defaults to CallToolResultSchema)
   * @param options - Optional request options (timeout, signal, task creation params, etc.)
   * @returns AsyncGenerator that yields ResponseMessage objects
   *
   * @experimental
   */
  async *callToolStream(params, resultSchema = CallToolResultSchema, options) {
    const clientInternal = this._client;
    const optionsWithTask = {
      ...options,
      // We check if the tool is known to be a task during auto-configuration, but assume
      // the caller knows what they're doing if they pass this explicitly
      task: options?.task ?? (clientInternal.isToolTask(params.name) ? {} : void 0)
    };
    const stream = clientInternal.requestStream({ method: "tools/call", params }, resultSchema, optionsWithTask);
    const validator = clientInternal.getToolOutputValidator(params.name);
    for await (const message2 of stream) {
      if (message2.type === "result" && validator) {
        const result = message2.result;
        if (!result.structuredContent && !result.isError) {
          yield {
            type: "error",
            error: new McpError(ErrorCode.InvalidRequest, `Tool ${params.name} has an output schema but did not return structured content`)
          };
          return;
        }
        if (result.structuredContent) {
          try {
            const validationResult = validator(result.structuredContent);
            if (!validationResult.valid) {
              yield {
                type: "error",
                error: new McpError(ErrorCode.InvalidParams, `Structured content does not match the tool's output schema: ${validationResult.errorMessage}`)
              };
              return;
            }
          } catch (error) {
            if (error instanceof McpError) {
              yield { type: "error", error };
              return;
            }
            yield {
              type: "error",
              error: new McpError(ErrorCode.InvalidParams, `Failed to validate structured content: ${error instanceof Error ? error.message : String(error)}`)
            };
            return;
          }
        }
      }
      yield message2;
    }
  }
  /**
   * Gets the current status of a task.
   *
   * @param taskId - The task identifier
   * @param options - Optional request options
   * @returns The task status
   *
   * @experimental
   */
  async getTask(taskId, options) {
    return this._client.getTask({ taskId }, options);
  }
  /**
   * Retrieves the result of a completed task.
   *
   * @param taskId - The task identifier
   * @param resultSchema - Zod schema for validating the result
   * @param options - Optional request options
   * @returns The task result
   *
   * @experimental
   */
  async getTaskResult(taskId, resultSchema, options) {
    return this._client.getTaskResult({ taskId }, resultSchema, options);
  }
  /**
   * Lists tasks with optional pagination.
   *
   * @param cursor - Optional pagination cursor
   * @param options - Optional request options
   * @returns List of tasks with optional next cursor
   *
   * @experimental
   */
  async listTasks(cursor, options) {
    return this._client.listTasks(cursor ? { cursor } : void 0, options);
  }
  /**
   * Cancels a running task.
   *
   * @param taskId - The task identifier
   * @param options - Optional request options
   *
   * @experimental
   */
  async cancelTask(taskId, options) {
    return this._client.cancelTask({ taskId }, options);
  }
  /**
   * Sends a request and returns an AsyncGenerator that yields response messages.
   * The generator is guaranteed to end with either a 'result' or 'error' message.
   *
   * This method provides streaming access to request processing, allowing you to
   * observe intermediate task status updates for task-augmented requests.
   *
   * @param request - The request to send
   * @param resultSchema - Zod schema for validating the result
   * @param options - Optional request options (timeout, signal, task creation params, etc.)
   * @returns AsyncGenerator that yields ResponseMessage objects
   *
   * @experimental
   */
  requestStream(request, resultSchema, options) {
    return this._client.requestStream(request, resultSchema, options);
  }
}
function applyElicitationDefaults(schema, data) {
  if (!schema || data === null || typeof data !== "object")
    return;
  if (schema.type === "object" && schema.properties && typeof schema.properties === "object") {
    const obj = data;
    const props = schema.properties;
    for (const key of Object.keys(props)) {
      const propSchema = props[key];
      if (obj[key] === void 0 && Object.prototype.hasOwnProperty.call(propSchema, "default")) {
        obj[key] = propSchema.default;
      }
      if (obj[key] !== void 0) {
        applyElicitationDefaults(propSchema, obj[key]);
      }
    }
  }
  if (Array.isArray(schema.anyOf)) {
    for (const sub of schema.anyOf) {
      if (typeof sub !== "boolean") {
        applyElicitationDefaults(sub, data);
      }
    }
  }
  if (Array.isArray(schema.oneOf)) {
    for (const sub of schema.oneOf) {
      if (typeof sub !== "boolean") {
        applyElicitationDefaults(sub, data);
      }
    }
  }
}
function getSupportedElicitationModes(capabilities) {
  if (!capabilities) {
    return { supportsFormMode: false, supportsUrlMode: false };
  }
  const hasFormCapability = capabilities.form !== void 0;
  const hasUrlCapability = capabilities.url !== void 0;
  const supportsFormMode = hasFormCapability || !hasFormCapability && !hasUrlCapability;
  const supportsUrlMode = hasUrlCapability;
  return { supportsFormMode, supportsUrlMode };
}
class Client extends Protocol {
  /**
   * Initializes this client with the given name and version information.
   */
  constructor(_clientInfo, options) {
    super(options);
    this._clientInfo = _clientInfo;
    this._cachedToolOutputValidators = /* @__PURE__ */ new Map();
    this._cachedKnownTaskTools = /* @__PURE__ */ new Set();
    this._cachedRequiredTaskTools = /* @__PURE__ */ new Set();
    this._listChangedDebounceTimers = /* @__PURE__ */ new Map();
    this._capabilities = options?.capabilities ?? {};
    this._jsonSchemaValidator = options?.jsonSchemaValidator ?? new AjvJsonSchemaValidator();
    if (options?.listChanged) {
      this._pendingListChangedConfig = options.listChanged;
    }
  }
  /**
   * Set up handlers for list changed notifications based on config and server capabilities.
   * This should only be called after initialization when server capabilities are known.
   * Handlers are silently skipped if the server doesn't advertise the corresponding listChanged capability.
   * @internal
   */
  _setupListChangedHandlers(config) {
    if (config.tools && this._serverCapabilities?.tools?.listChanged) {
      this._setupListChangedHandler("tools", ToolListChangedNotificationSchema, config.tools, async () => {
        const result = await this.listTools();
        return result.tools;
      });
    }
    if (config.prompts && this._serverCapabilities?.prompts?.listChanged) {
      this._setupListChangedHandler("prompts", PromptListChangedNotificationSchema, config.prompts, async () => {
        const result = await this.listPrompts();
        return result.prompts;
      });
    }
    if (config.resources && this._serverCapabilities?.resources?.listChanged) {
      this._setupListChangedHandler("resources", ResourceListChangedNotificationSchema, config.resources, async () => {
        const result = await this.listResources();
        return result.resources;
      });
    }
  }
  /**
   * Access experimental features.
   *
   * WARNING: These APIs are experimental and may change without notice.
   *
   * @experimental
   */
  get experimental() {
    if (!this._experimental) {
      this._experimental = {
        tasks: new ExperimentalClientTasks(this)
      };
    }
    return this._experimental;
  }
  /**
   * Registers new capabilities. This can only be called before connecting to a transport.
   *
   * The new capabilities will be merged with any existing capabilities previously given (e.g., at initialization).
   */
  registerCapabilities(capabilities) {
    if (this.transport) {
      throw new Error("Cannot register capabilities after connecting to transport");
    }
    this._capabilities = mergeCapabilities(this._capabilities, capabilities);
  }
  /**
   * Override request handler registration to enforce client-side validation for elicitation.
   */
  setRequestHandler(requestSchema, handler) {
    const shape = getObjectShape(requestSchema);
    const methodSchema = shape?.method;
    if (!methodSchema) {
      throw new Error("Schema is missing a method literal");
    }
    const methodValue = getLiteralValue(methodSchema);
    if (typeof methodValue !== "string") {
      throw new Error("Schema method literal must be a string");
    }
    const method = methodValue;
    if (method === "elicitation/create") {
      const wrappedHandler = async (request, extra) => {
        const validatedRequest = safeParse(ElicitRequestSchema, request);
        if (!validatedRequest.success) {
          const errorMessage = validatedRequest.error instanceof Error ? validatedRequest.error.message : String(validatedRequest.error);
          throw new McpError(ErrorCode.InvalidParams, `Invalid elicitation request: ${errorMessage}`);
        }
        const { params } = validatedRequest.data;
        params.mode = params.mode ?? "form";
        const { supportsFormMode, supportsUrlMode } = getSupportedElicitationModes(this._capabilities.elicitation);
        if (params.mode === "form" && !supportsFormMode) {
          throw new McpError(ErrorCode.InvalidParams, "Client does not support form-mode elicitation requests");
        }
        if (params.mode === "url" && !supportsUrlMode) {
          throw new McpError(ErrorCode.InvalidParams, "Client does not support URL-mode elicitation requests");
        }
        const result = await Promise.resolve(handler(request, extra));
        if (params.task) {
          const taskValidationResult = safeParse(CreateTaskResultSchema, result);
          if (!taskValidationResult.success) {
            const errorMessage = taskValidationResult.error instanceof Error ? taskValidationResult.error.message : String(taskValidationResult.error);
            throw new McpError(ErrorCode.InvalidParams, `Invalid task creation result: ${errorMessage}`);
          }
          return taskValidationResult.data;
        }
        const validationResult = safeParse(ElicitResultSchema, result);
        if (!validationResult.success) {
          const errorMessage = validationResult.error instanceof Error ? validationResult.error.message : String(validationResult.error);
          throw new McpError(ErrorCode.InvalidParams, `Invalid elicitation result: ${errorMessage}`);
        }
        const validatedResult = validationResult.data;
        const requestedSchema = params.mode === "form" ? params.requestedSchema : void 0;
        if (params.mode === "form" && validatedResult.action === "accept" && validatedResult.content && requestedSchema) {
          if (this._capabilities.elicitation?.form?.applyDefaults) {
            try {
              applyElicitationDefaults(requestedSchema, validatedResult.content);
            } catch {
            }
          }
        }
        return validatedResult;
      };
      return super.setRequestHandler(requestSchema, wrappedHandler);
    }
    if (method === "sampling/createMessage") {
      const wrappedHandler = async (request, extra) => {
        const validatedRequest = safeParse(CreateMessageRequestSchema, request);
        if (!validatedRequest.success) {
          const errorMessage = validatedRequest.error instanceof Error ? validatedRequest.error.message : String(validatedRequest.error);
          throw new McpError(ErrorCode.InvalidParams, `Invalid sampling request: ${errorMessage}`);
        }
        const { params } = validatedRequest.data;
        const result = await Promise.resolve(handler(request, extra));
        if (params.task) {
          const taskValidationResult = safeParse(CreateTaskResultSchema, result);
          if (!taskValidationResult.success) {
            const errorMessage = taskValidationResult.error instanceof Error ? taskValidationResult.error.message : String(taskValidationResult.error);
            throw new McpError(ErrorCode.InvalidParams, `Invalid task creation result: ${errorMessage}`);
          }
          return taskValidationResult.data;
        }
        const hasTools = params.tools || params.toolChoice;
        const resultSchema = hasTools ? CreateMessageResultWithToolsSchema : CreateMessageResultSchema;
        const validationResult = safeParse(resultSchema, result);
        if (!validationResult.success) {
          const errorMessage = validationResult.error instanceof Error ? validationResult.error.message : String(validationResult.error);
          throw new McpError(ErrorCode.InvalidParams, `Invalid sampling result: ${errorMessage}`);
        }
        return validationResult.data;
      };
      return super.setRequestHandler(requestSchema, wrappedHandler);
    }
    return super.setRequestHandler(requestSchema, handler);
  }
  assertCapability(capability, method) {
    if (!this._serverCapabilities?.[capability]) {
      throw new Error(`Server does not support ${capability} (required for ${method})`);
    }
  }
  async connect(transport, options) {
    await super.connect(transport);
    if (transport.sessionId !== void 0) {
      return;
    }
    try {
      const result = await this.request({
        method: "initialize",
        params: {
          protocolVersion: LATEST_PROTOCOL_VERSION,
          capabilities: this._capabilities,
          clientInfo: this._clientInfo
        }
      }, InitializeResultSchema, options);
      if (result === void 0) {
        throw new Error(`Server sent invalid initialize result: ${result}`);
      }
      if (!SUPPORTED_PROTOCOL_VERSIONS.includes(result.protocolVersion)) {
        throw new Error(`Server's protocol version is not supported: ${result.protocolVersion}`);
      }
      this._serverCapabilities = result.capabilities;
      this._serverVersion = result.serverInfo;
      if (transport.setProtocolVersion) {
        transport.setProtocolVersion(result.protocolVersion);
      }
      this._instructions = result.instructions;
      await this.notification({
        method: "notifications/initialized"
      });
      if (this._pendingListChangedConfig) {
        this._setupListChangedHandlers(this._pendingListChangedConfig);
        this._pendingListChangedConfig = void 0;
      }
    } catch (error) {
      void this.close();
      throw error;
    }
  }
  /**
   * After initialization has completed, this will be populated with the server's reported capabilities.
   */
  getServerCapabilities() {
    return this._serverCapabilities;
  }
  /**
   * After initialization has completed, this will be populated with information about the server's name and version.
   */
  getServerVersion() {
    return this._serverVersion;
  }
  /**
   * After initialization has completed, this may be populated with information about the server's instructions.
   */
  getInstructions() {
    return this._instructions;
  }
  assertCapabilityForMethod(method) {
    switch (method) {
      case "logging/setLevel":
        if (!this._serverCapabilities?.logging) {
          throw new Error(`Server does not support logging (required for ${method})`);
        }
        break;
      case "prompts/get":
      case "prompts/list":
        if (!this._serverCapabilities?.prompts) {
          throw new Error(`Server does not support prompts (required for ${method})`);
        }
        break;
      case "resources/list":
      case "resources/templates/list":
      case "resources/read":
      case "resources/subscribe":
      case "resources/unsubscribe":
        if (!this._serverCapabilities?.resources) {
          throw new Error(`Server does not support resources (required for ${method})`);
        }
        if (method === "resources/subscribe" && !this._serverCapabilities.resources.subscribe) {
          throw new Error(`Server does not support resource subscriptions (required for ${method})`);
        }
        break;
      case "tools/call":
      case "tools/list":
        if (!this._serverCapabilities?.tools) {
          throw new Error(`Server does not support tools (required for ${method})`);
        }
        break;
      case "completion/complete":
        if (!this._serverCapabilities?.completions) {
          throw new Error(`Server does not support completions (required for ${method})`);
        }
        break;
    }
  }
  assertNotificationCapability(method) {
    switch (method) {
      case "notifications/roots/list_changed":
        if (!this._capabilities.roots?.listChanged) {
          throw new Error(`Client does not support roots list changed notifications (required for ${method})`);
        }
        break;
    }
  }
  assertRequestHandlerCapability(method) {
    if (!this._capabilities) {
      return;
    }
    switch (method) {
      case "sampling/createMessage":
        if (!this._capabilities.sampling) {
          throw new Error(`Client does not support sampling capability (required for ${method})`);
        }
        break;
      case "elicitation/create":
        if (!this._capabilities.elicitation) {
          throw new Error(`Client does not support elicitation capability (required for ${method})`);
        }
        break;
      case "roots/list":
        if (!this._capabilities.roots) {
          throw new Error(`Client does not support roots capability (required for ${method})`);
        }
        break;
      case "tasks/get":
      case "tasks/list":
      case "tasks/result":
      case "tasks/cancel":
        if (!this._capabilities.tasks) {
          throw new Error(`Client does not support tasks capability (required for ${method})`);
        }
        break;
    }
  }
  assertTaskCapability(method) {
    assertToolsCallTaskCapability(this._serverCapabilities?.tasks?.requests, method, "Server");
  }
  assertTaskHandlerCapability(method) {
    if (!this._capabilities) {
      return;
    }
    assertClientRequestTaskCapability(this._capabilities.tasks?.requests, method, "Client");
  }
  async ping(options) {
    return this.request({ method: "ping" }, EmptyResultSchema, options);
  }
  async complete(params, options) {
    return this.request({ method: "completion/complete", params }, CompleteResultSchema, options);
  }
  async setLoggingLevel(level, options) {
    return this.request({ method: "logging/setLevel", params: { level } }, EmptyResultSchema, options);
  }
  async getPrompt(params, options) {
    return this.request({ method: "prompts/get", params }, GetPromptResultSchema, options);
  }
  async listPrompts(params, options) {
    return this.request({ method: "prompts/list", params }, ListPromptsResultSchema, options);
  }
  async listResources(params, options) {
    return this.request({ method: "resources/list", params }, ListResourcesResultSchema, options);
  }
  async listResourceTemplates(params, options) {
    return this.request({ method: "resources/templates/list", params }, ListResourceTemplatesResultSchema, options);
  }
  async readResource(params, options) {
    return this.request({ method: "resources/read", params }, ReadResourceResultSchema, options);
  }
  async subscribeResource(params, options) {
    return this.request({ method: "resources/subscribe", params }, EmptyResultSchema, options);
  }
  async unsubscribeResource(params, options) {
    return this.request({ method: "resources/unsubscribe", params }, EmptyResultSchema, options);
  }
  /**
   * Calls a tool and waits for the result. Automatically validates structured output if the tool has an outputSchema.
   *
   * For task-based execution with streaming behavior, use client.experimental.tasks.callToolStream() instead.
   */
  async callTool(params, resultSchema = CallToolResultSchema, options) {
    if (this.isToolTaskRequired(params.name)) {
      throw new McpError(ErrorCode.InvalidRequest, `Tool "${params.name}" requires task-based execution. Use client.experimental.tasks.callToolStream() instead.`);
    }
    const result = await this.request({ method: "tools/call", params }, resultSchema, options);
    const validator = this.getToolOutputValidator(params.name);
    if (validator) {
      if (!result.structuredContent && !result.isError) {
        throw new McpError(ErrorCode.InvalidRequest, `Tool ${params.name} has an output schema but did not return structured content`);
      }
      if (result.structuredContent) {
        try {
          const validationResult = validator(result.structuredContent);
          if (!validationResult.valid) {
            throw new McpError(ErrorCode.InvalidParams, `Structured content does not match the tool's output schema: ${validationResult.errorMessage}`);
          }
        } catch (error) {
          if (error instanceof McpError) {
            throw error;
          }
          throw new McpError(ErrorCode.InvalidParams, `Failed to validate structured content: ${error instanceof Error ? error.message : String(error)}`);
        }
      }
    }
    return result;
  }
  isToolTask(toolName) {
    if (!this._serverCapabilities?.tasks?.requests?.tools?.call) {
      return false;
    }
    return this._cachedKnownTaskTools.has(toolName);
  }
  /**
   * Check if a tool requires task-based execution.
   * Unlike isToolTask which includes 'optional' tools, this only checks for 'required'.
   */
  isToolTaskRequired(toolName) {
    return this._cachedRequiredTaskTools.has(toolName);
  }
  /**
   * Cache validators for tool output schemas.
   * Called after listTools() to pre-compile validators for better performance.
   */
  cacheToolMetadata(tools) {
    this._cachedToolOutputValidators.clear();
    this._cachedKnownTaskTools.clear();
    this._cachedRequiredTaskTools.clear();
    for (const tool of tools) {
      if (tool.outputSchema) {
        const toolValidator = this._jsonSchemaValidator.getValidator(tool.outputSchema);
        this._cachedToolOutputValidators.set(tool.name, toolValidator);
      }
      const taskSupport = tool.execution?.taskSupport;
      if (taskSupport === "required" || taskSupport === "optional") {
        this._cachedKnownTaskTools.add(tool.name);
      }
      if (taskSupport === "required") {
        this._cachedRequiredTaskTools.add(tool.name);
      }
    }
  }
  /**
   * Get cached validator for a tool
   */
  getToolOutputValidator(toolName) {
    return this._cachedToolOutputValidators.get(toolName);
  }
  async listTools(params, options) {
    const result = await this.request({ method: "tools/list", params }, ListToolsResultSchema, options);
    this.cacheToolMetadata(result.tools);
    return result;
  }
  /**
   * Set up a single list changed handler.
   * @internal
   */
  _setupListChangedHandler(listType, notificationSchema, options, fetcher) {
    const parseResult = ListChangedOptionsBaseSchema.safeParse(options);
    if (!parseResult.success) {
      throw new Error(`Invalid ${listType} listChanged options: ${parseResult.error.message}`);
    }
    if (typeof options.onChanged !== "function") {
      throw new Error(`Invalid ${listType} listChanged options: onChanged must be a function`);
    }
    const { autoRefresh, debounceMs } = parseResult.data;
    const { onChanged } = options;
    const refresh2 = async () => {
      if (!autoRefresh) {
        onChanged(null, null);
        return;
      }
      try {
        const items = await fetcher();
        onChanged(null, items);
      } catch (e) {
        const error = e instanceof Error ? e : new Error(String(e));
        onChanged(error, null);
      }
    };
    const handler = () => {
      if (debounceMs) {
        const existingTimer = this._listChangedDebounceTimers.get(listType);
        if (existingTimer) {
          clearTimeout(existingTimer);
        }
        const timer = setTimeout(refresh2, debounceMs);
        this._listChangedDebounceTimers.set(listType, timer);
      } else {
        refresh2();
      }
    };
    this.setNotificationHandler(notificationSchema, handler);
  }
  async sendRootsListChanged() {
    return this.notification({ method: "notifications/roots/list_changed" });
  }
}
class ParseError extends Error {
  constructor(message2, options) {
    super(message2), this.name = "ParseError", this.type = options.type, this.field = options.field, this.value = options.value, this.line = options.line;
  }
}
const LF = 10, CR = 13, SPACE = 32;
function noop(_arg) {
}
function createParser(config) {
  if (typeof config == "function")
    throw new TypeError(
      "`config` must be an object, got a function instead. Did you mean `createParser({onEvent: fn})`?"
    );
  const { onEvent = noop, onError = noop, onRetry = noop, onComment, maxBufferSize } = config, pendingFragments = [];
  let pendingFragmentsLength = 0, isFirstChunk = true, id, data = "", dataLines = 0, eventType, terminated = false;
  function feed(chunk) {
    if (terminated)
      throw new Error(
        "Cannot feed parser: it was terminated after exceeding the configured max buffer size. Call `reset()` to resume parsing."
      );
    if (isFirstChunk && (isFirstChunk = false, chunk.charCodeAt(0) === 239 && chunk.charCodeAt(1) === 187 && chunk.charCodeAt(2) === 191 && (chunk = chunk.slice(3))), pendingFragments.length === 0) {
      const trailing2 = processLines(chunk);
      trailing2 !== "" && (pendingFragments.push(trailing2), pendingFragmentsLength = trailing2.length), checkBufferSize();
      return;
    }
    if (chunk.indexOf(`
`) === -1 && chunk.indexOf("\r") === -1) {
      pendingFragments.push(chunk), pendingFragmentsLength += chunk.length, checkBufferSize();
      return;
    }
    pendingFragments.push(chunk);
    const input = pendingFragments.join("");
    pendingFragments.length = 0, pendingFragmentsLength = 0;
    const trailing = processLines(input);
    trailing !== "" && (pendingFragments.push(trailing), pendingFragmentsLength = trailing.length), checkBufferSize();
  }
  function checkBufferSize() {
    maxBufferSize !== void 0 && (pendingFragmentsLength + data.length <= maxBufferSize || (terminated = true, pendingFragments.length = 0, pendingFragmentsLength = 0, id = void 0, data = "", dataLines = 0, eventType = void 0, onError(
      new ParseError(`Buffered data exceeded max buffer size of ${maxBufferSize} characters`, {
        type: "max-buffer-size-exceeded"
      })
    )));
  }
  function processLines(chunk) {
    let searchIndex = 0;
    if (chunk.indexOf("\r") === -1) {
      let lfIndex = chunk.indexOf(`
`, searchIndex);
      for (; lfIndex !== -1; ) {
        if (searchIndex === lfIndex) {
          dataLines > 0 && onEvent({ id, event: eventType, data }), id = void 0, data = "", dataLines = 0, eventType = void 0, searchIndex = lfIndex + 1, lfIndex = chunk.indexOf(`
`, searchIndex);
          continue;
        }
        const firstCharCode = chunk.charCodeAt(searchIndex);
        if (isDataPrefix(chunk, searchIndex, firstCharCode)) {
          const valueStart = chunk.charCodeAt(searchIndex + 5) === SPACE ? searchIndex + 6 : searchIndex + 5, value = chunk.slice(valueStart, lfIndex);
          if (dataLines === 0 && chunk.charCodeAt(lfIndex + 1) === LF) {
            onEvent({ id, event: eventType, data: value }), id = void 0, data = "", eventType = void 0, searchIndex = lfIndex + 2, lfIndex = chunk.indexOf(`
`, searchIndex);
            continue;
          }
          data = dataLines === 0 ? value : `${data}
${value}`, dataLines++;
        } else isEventPrefix(chunk, searchIndex, firstCharCode) ? eventType = chunk.slice(
          chunk.charCodeAt(searchIndex + 6) === SPACE ? searchIndex + 7 : searchIndex + 6,
          lfIndex
        ) || void 0 : parseLine(chunk, searchIndex, lfIndex);
        searchIndex = lfIndex + 1, lfIndex = chunk.indexOf(`
`, searchIndex);
      }
      return chunk.slice(searchIndex);
    }
    for (; searchIndex < chunk.length; ) {
      const crIndex = chunk.indexOf("\r", searchIndex), lfIndex = chunk.indexOf(`
`, searchIndex);
      let lineEnd = -1;
      if (crIndex !== -1 && lfIndex !== -1 ? lineEnd = crIndex < lfIndex ? crIndex : lfIndex : crIndex !== -1 ? crIndex === chunk.length - 1 ? lineEnd = -1 : lineEnd = crIndex : lfIndex !== -1 && (lineEnd = lfIndex), lineEnd === -1)
        break;
      parseLine(chunk, searchIndex, lineEnd), searchIndex = lineEnd + 1, chunk.charCodeAt(searchIndex - 1) === CR && chunk.charCodeAt(searchIndex) === LF && searchIndex++;
    }
    return chunk.slice(searchIndex);
  }
  function parseLine(chunk, start, end) {
    if (start === end) {
      dispatchEvent();
      return;
    }
    const firstCharCode = chunk.charCodeAt(start);
    if (isDataPrefix(chunk, start, firstCharCode)) {
      const valueStart = chunk.charCodeAt(start + 5) === SPACE ? start + 6 : start + 5, value2 = chunk.slice(valueStart, end);
      data = dataLines === 0 ? value2 : `${data}
${value2}`, dataLines++;
      return;
    }
    if (isEventPrefix(chunk, start, firstCharCode)) {
      eventType = chunk.slice(chunk.charCodeAt(start + 6) === SPACE ? start + 7 : start + 6, end) || void 0;
      return;
    }
    if (firstCharCode === 105 && chunk.charCodeAt(start + 1) === 100 && chunk.charCodeAt(start + 2) === 58) {
      const value2 = chunk.slice(chunk.charCodeAt(start + 3) === SPACE ? start + 4 : start + 3, end);
      value2.includes("\0") || (id = value2);
      return;
    }
    if (firstCharCode === 58) {
      if (onComment) {
        const line2 = chunk.slice(start, end);
        onComment(line2.slice(chunk.charCodeAt(start + 1) === SPACE ? 2 : 1));
      }
      return;
    }
    const line = chunk.slice(start, end), fieldSeparatorIndex = line.indexOf(":");
    if (fieldSeparatorIndex === -1) {
      processField(line, "", line);
      return;
    }
    const field = line.slice(0, fieldSeparatorIndex), offset = line.charCodeAt(fieldSeparatorIndex + 1) === SPACE ? 2 : 1, value = line.slice(fieldSeparatorIndex + offset);
    processField(field, value, line);
  }
  function processField(field, value, line) {
    switch (field) {
      case "event":
        eventType = value || void 0;
        break;
      case "data":
        data = dataLines === 0 ? value : `${data}
${value}`, dataLines++;
        break;
      case "id":
        value.includes("\0") || (id = value);
        break;
      case "retry":
        /^\d+$/.test(value) ? onRetry(parseInt(value, 10)) : onError(
          new ParseError(`Invalid \`retry\` value: "${value}"`, {
            type: "invalid-retry",
            value,
            line
          })
        );
        break;
      default:
        onError(
          new ParseError(
            `Unknown field "${field.length > 20 ? `${field.slice(0, 20)}…` : field}"`,
            { type: "unknown-field", field, value, line }
          )
        );
        break;
    }
  }
  function dispatchEvent() {
    dataLines > 0 && onEvent({
      id,
      event: eventType,
      data
    }), id = void 0, data = "", dataLines = 0, eventType = void 0;
  }
  function reset(options = {}) {
    if (options.consume && pendingFragments.length > 0) {
      const incompleteLine = pendingFragments.join("");
      parseLine(incompleteLine, 0, incompleteLine.length);
    }
    isFirstChunk = true, id = void 0, data = "", dataLines = 0, eventType = void 0, pendingFragments.length = 0, pendingFragmentsLength = 0, terminated = false;
  }
  return { feed, reset };
}
function isDataPrefix(chunk, i, firstCharCode) {
  return firstCharCode === 100 && chunk.charCodeAt(i + 1) === 97 && chunk.charCodeAt(i + 2) === 116 && chunk.charCodeAt(i + 3) === 97 && chunk.charCodeAt(i + 4) === 58;
}
function isEventPrefix(chunk, i, firstCharCode) {
  return firstCharCode === 101 && chunk.charCodeAt(i + 1) === 118 && chunk.charCodeAt(i + 2) === 101 && chunk.charCodeAt(i + 3) === 110 && chunk.charCodeAt(i + 4) === 116 && chunk.charCodeAt(i + 5) === 58;
}
class ErrorEvent extends Event {
  /**
   * Constructs a new `ErrorEvent` instance. This is typically not called directly,
   * but rather emitted by the `EventSource` object when an error occurs.
   *
   * @param type - The type of the event (should be "error")
   * @param errorEventInitDict - Optional properties to include in the error event
   */
  constructor(type, errorEventInitDict) {
    var _a, _b;
    super(type), this.code = (_a = errorEventInitDict == null ? void 0 : errorEventInitDict.code) != null ? _a : void 0, this.message = (_b = errorEventInitDict == null ? void 0 : errorEventInitDict.message) != null ? _b : void 0;
  }
  /**
   * Node.js "hides" the `message` and `code` properties of the `ErrorEvent` instance,
   * when it is `console.log`'ed. This makes it harder to debug errors. To ease debugging,
   * we explicitly include the properties in the `inspect` method.
   *
   * This is automatically called by Node.js when you `console.log` an instance of this class.
   *
   * @param _depth - The current depth
   * @param options - The options passed to `util.inspect`
   * @param inspect - The inspect function to use (prevents having to import it from `util`)
   * @returns A string representation of the error
   */
  [Symbol.for("nodejs.util.inspect.custom")](_depth, options, inspect) {
    return inspect(inspectableError(this), options);
  }
  /**
   * Deno "hides" the `message` and `code` properties of the `ErrorEvent` instance,
   * when it is `console.log`'ed. This makes it harder to debug errors. To ease debugging,
   * we explicitly include the properties in the `inspect` method.
   *
   * This is automatically called by Deno when you `console.log` an instance of this class.
   *
   * @param inspect - The inspect function to use (prevents having to import it from `util`)
   * @param options - The options passed to `Deno.inspect`
   * @returns A string representation of the error
   */
  [Symbol.for("Deno.customInspect")](inspect, options) {
    return inspect(inspectableError(this), options);
  }
}
function syntaxError(message2) {
  const DomException = globalThis.DOMException;
  return typeof DomException == "function" ? new DomException(message2, "SyntaxError") : new SyntaxError(message2);
}
function flattenError(err) {
  return err instanceof Error ? "errors" in err && Array.isArray(err.errors) ? err.errors.map(flattenError).join(", ") : "cause" in err && err.cause instanceof Error ? `${err}: ${flattenError(err.cause)}` : err.message : `${err}`;
}
function inspectableError(err) {
  return {
    type: err.type,
    message: err.message,
    code: err.code,
    defaultPrevented: err.defaultPrevented,
    cancelable: err.cancelable,
    timeStamp: err.timeStamp
  };
}
var __typeError = (msg) => {
  throw TypeError(msg);
}, __accessCheck = (obj, member, msg) => member.has(obj) || __typeError("Cannot " + msg), __privateGet = (obj, member, getter) => (__accessCheck(obj, member, "read from private field"), getter ? getter.call(obj) : member.get(obj)), __privateAdd = (obj, member, value) => member.has(obj) ? __typeError("Cannot add the same private member more than once") : member instanceof WeakSet ? member.add(obj) : member.set(obj, value), __privateSet = (obj, member, value, setter) => (__accessCheck(obj, member, "write to private field"), member.set(obj, value), value), __privateMethod = (obj, member, method) => (__accessCheck(obj, member, "access private method"), method), _readyState, _url, _redirectUrl, _withCredentials, _fetch, _reconnectInterval, _reconnectTimer, _lastEventId, _controller, _parser, _onError, _onMessage, _onOpen, _EventSource_instances, connect_fn, _onFetchResponse, _onFetchError, getRequestOptions_fn, _onEvent, _onRetryChange, failConnection_fn, scheduleReconnect_fn, _reconnect;
class EventSource extends EventTarget {
  constructor(url2, eventSourceInitDict) {
    var _a, _b;
    super(), __privateAdd(this, _EventSource_instances), this.CONNECTING = 0, this.OPEN = 1, this.CLOSED = 2, __privateAdd(this, _readyState), __privateAdd(this, _url), __privateAdd(this, _redirectUrl), __privateAdd(this, _withCredentials), __privateAdd(this, _fetch), __privateAdd(this, _reconnectInterval), __privateAdd(this, _reconnectTimer), __privateAdd(this, _lastEventId, null), __privateAdd(this, _controller), __privateAdd(this, _parser), __privateAdd(this, _onError, null), __privateAdd(this, _onMessage, null), __privateAdd(this, _onOpen, null), __privateAdd(this, _onFetchResponse, async (response) => {
      var _a2;
      __privateGet(this, _parser).reset();
      const { body, redirected, status, headers } = response;
      if (status === 204) {
        __privateMethod(this, _EventSource_instances, failConnection_fn).call(this, "Server sent HTTP 204, not reconnecting", 204), this.close();
        return;
      }
      if (redirected ? __privateSet(this, _redirectUrl, new URL(response.url)) : __privateSet(this, _redirectUrl, void 0), status !== 200) {
        __privateMethod(this, _EventSource_instances, failConnection_fn).call(this, `Non-200 status code (${status})`, status);
        return;
      }
      if (!(headers.get("content-type") || "").startsWith("text/event-stream")) {
        __privateMethod(this, _EventSource_instances, failConnection_fn).call(this, 'Invalid content type, expected "text/event-stream"', status);
        return;
      }
      if (__privateGet(this, _readyState) === this.CLOSED)
        return;
      __privateSet(this, _readyState, this.OPEN);
      const openEvent = new Event("open");
      if ((_a2 = __privateGet(this, _onOpen)) == null || _a2.call(this, openEvent), this.dispatchEvent(openEvent), typeof body != "object" || !body || !("getReader" in body)) {
        __privateMethod(this, _EventSource_instances, failConnection_fn).call(this, "Invalid response body, expected a web ReadableStream", status), this.close();
        return;
      }
      const decoder = new TextDecoder(), reader = body.getReader();
      let open = true;
      do {
        const { done, value } = await reader.read();
        value && __privateGet(this, _parser).feed(decoder.decode(value, { stream: !done })), done && (open = false, __privateGet(this, _parser).reset(), __privateMethod(this, _EventSource_instances, scheduleReconnect_fn).call(this));
      } while (open);
    }), __privateAdd(this, _onFetchError, (err) => {
      __privateSet(this, _controller, void 0), !(err.name === "AbortError" || err.type === "aborted") && __privateMethod(this, _EventSource_instances, scheduleReconnect_fn).call(this, flattenError(err));
    }), __privateAdd(this, _onEvent, (event) => {
      typeof event.id == "string" && __privateSet(this, _lastEventId, event.id);
      const messageEvent = new MessageEvent(event.event || "message", {
        data: event.data,
        origin: __privateGet(this, _redirectUrl) ? __privateGet(this, _redirectUrl).origin : __privateGet(this, _url).origin,
        lastEventId: event.id || ""
      });
      __privateGet(this, _onMessage) && (!event.event || event.event === "message") && __privateGet(this, _onMessage).call(this, messageEvent), this.dispatchEvent(messageEvent);
    }), __privateAdd(this, _onRetryChange, (value) => {
      __privateSet(this, _reconnectInterval, value);
    }), __privateAdd(this, _reconnect, () => {
      __privateSet(this, _reconnectTimer, void 0), __privateGet(this, _readyState) === this.CONNECTING && __privateMethod(this, _EventSource_instances, connect_fn).call(this);
    });
    try {
      if (url2 instanceof URL)
        __privateSet(this, _url, url2);
      else if (typeof url2 == "string")
        __privateSet(this, _url, new URL(url2, getBaseURL()));
      else
        throw new Error("Invalid URL");
    } catch {
      throw syntaxError("An invalid or illegal string was specified");
    }
    __privateSet(this, _parser, createParser({
      onEvent: __privateGet(this, _onEvent),
      onRetry: __privateGet(this, _onRetryChange)
    })), __privateSet(this, _readyState, this.CONNECTING), __privateSet(this, _reconnectInterval, 3e3), __privateSet(this, _fetch, (_a = eventSourceInitDict == null ? void 0 : eventSourceInitDict.fetch) != null ? _a : globalThis.fetch), __privateSet(this, _withCredentials, (_b = eventSourceInitDict == null ? void 0 : eventSourceInitDict.withCredentials) != null ? _b : false), __privateMethod(this, _EventSource_instances, connect_fn).call(this);
  }
  /**
   * Returns the state of this EventSource object's connection. It can have the values described below.
   *
   * [MDN Reference](https://developer.mozilla.org/docs/Web/API/EventSource/readyState)
   *
   * Note: typed as `number` instead of `0 | 1 | 2` for compatibility with the `EventSource` interface,
   * defined in the TypeScript `dom` library.
   *
   * @public
   */
  get readyState() {
    return __privateGet(this, _readyState);
  }
  /**
   * Returns the URL providing the event stream.
   *
   * [MDN Reference](https://developer.mozilla.org/docs/Web/API/EventSource/url)
   *
   * @public
   */
  get url() {
    return __privateGet(this, _url).href;
  }
  /**
   * Returns true if the credentials mode for connection requests to the URL providing the event stream is set to "include", and false otherwise.
   *
   * [MDN Reference](https://developer.mozilla.org/docs/Web/API/EventSource/withCredentials)
   */
  get withCredentials() {
    return __privateGet(this, _withCredentials);
  }
  /** [MDN Reference](https://developer.mozilla.org/docs/Web/API/EventSource/error_event) */
  get onerror() {
    return __privateGet(this, _onError);
  }
  set onerror(value) {
    __privateSet(this, _onError, value);
  }
  /** [MDN Reference](https://developer.mozilla.org/docs/Web/API/EventSource/message_event) */
  get onmessage() {
    return __privateGet(this, _onMessage);
  }
  set onmessage(value) {
    __privateSet(this, _onMessage, value);
  }
  /** [MDN Reference](https://developer.mozilla.org/docs/Web/API/EventSource/open_event) */
  get onopen() {
    return __privateGet(this, _onOpen);
  }
  set onopen(value) {
    __privateSet(this, _onOpen, value);
  }
  addEventListener(type, listener, options) {
    const listen = listener;
    super.addEventListener(type, listen, options);
  }
  removeEventListener(type, listener, options) {
    const listen = listener;
    super.removeEventListener(type, listen, options);
  }
  /**
   * Aborts any instances of the fetch algorithm started for this EventSource object, and sets the readyState attribute to CLOSED.
   *
   * [MDN Reference](https://developer.mozilla.org/docs/Web/API/EventSource/close)
   *
   * @public
   */
  close() {
    __privateGet(this, _reconnectTimer) && clearTimeout(__privateGet(this, _reconnectTimer)), __privateGet(this, _readyState) !== this.CLOSED && (__privateGet(this, _controller) && __privateGet(this, _controller).abort(), __privateSet(this, _readyState, this.CLOSED), __privateSet(this, _controller, void 0));
  }
}
_readyState = /* @__PURE__ */ new WeakMap(), _url = /* @__PURE__ */ new WeakMap(), _redirectUrl = /* @__PURE__ */ new WeakMap(), _withCredentials = /* @__PURE__ */ new WeakMap(), _fetch = /* @__PURE__ */ new WeakMap(), _reconnectInterval = /* @__PURE__ */ new WeakMap(), _reconnectTimer = /* @__PURE__ */ new WeakMap(), _lastEventId = /* @__PURE__ */ new WeakMap(), _controller = /* @__PURE__ */ new WeakMap(), _parser = /* @__PURE__ */ new WeakMap(), _onError = /* @__PURE__ */ new WeakMap(), _onMessage = /* @__PURE__ */ new WeakMap(), _onOpen = /* @__PURE__ */ new WeakMap(), _EventSource_instances = /* @__PURE__ */ new WeakSet(), /**
* Connect to the given URL and start receiving events
*
* @internal
*/
connect_fn = function() {
  __privateSet(this, _readyState, this.CONNECTING), __privateSet(this, _controller, new AbortController()), __privateGet(this, _fetch)(__privateGet(this, _url), __privateMethod(this, _EventSource_instances, getRequestOptions_fn).call(this)).then(__privateGet(this, _onFetchResponse)).catch(__privateGet(this, _onFetchError));
}, _onFetchResponse = /* @__PURE__ */ new WeakMap(), _onFetchError = /* @__PURE__ */ new WeakMap(), /**
* Get request options for the `fetch()` request
*
* @returns The request options
* @internal
*/
getRequestOptions_fn = function() {
  var _a;
  const init = {
    // [spec] Let `corsAttributeState` be `Anonymous`…
    // [spec] …will have their mode set to "cors"…
    mode: "cors",
    redirect: "follow",
    headers: { Accept: "text/event-stream", ...__privateGet(this, _lastEventId) ? { "Last-Event-ID": __privateGet(this, _lastEventId) } : void 0 },
    cache: "no-store",
    signal: (_a = __privateGet(this, _controller)) == null ? void 0 : _a.signal
  };
  return "window" in globalThis && (init.credentials = this.withCredentials ? "include" : "same-origin"), init;
}, _onEvent = /* @__PURE__ */ new WeakMap(), _onRetryChange = /* @__PURE__ */ new WeakMap(), /**
* Handles the process referred to in the EventSource specification as "failing a connection".
*
* @param error - The error causing the connection to fail
* @param code - The HTTP status code, if available
* @internal
*/
failConnection_fn = function(message2, code) {
  var _a;
  __privateGet(this, _readyState) !== this.CLOSED && __privateSet(this, _readyState, this.CLOSED);
  const errorEvent = new ErrorEvent("error", { code, message: message2 });
  (_a = __privateGet(this, _onError)) == null || _a.call(this, errorEvent), this.dispatchEvent(errorEvent);
}, /**
* Schedules a reconnection attempt against the EventSource endpoint.
*
* @param message - The error causing the connection to fail
* @param code - The HTTP status code, if available
* @internal
*/
scheduleReconnect_fn = function(message2, code) {
  var _a;
  if (__privateGet(this, _readyState) === this.CLOSED)
    return;
  __privateSet(this, _readyState, this.CONNECTING);
  const errorEvent = new ErrorEvent("error", { code, message: message2 });
  (_a = __privateGet(this, _onError)) == null || _a.call(this, errorEvent), this.dispatchEvent(errorEvent), __privateSet(this, _reconnectTimer, setTimeout(__privateGet(this, _reconnect), __privateGet(this, _reconnectInterval)));
}, _reconnect = /* @__PURE__ */ new WeakMap(), /**
* ReadyState representing an EventSource currently trying to connect
*
* @public
*/
EventSource.CONNECTING = 0, /**
* ReadyState representing an EventSource connection that is open (eg connected)
*
* @public
*/
EventSource.OPEN = 1, /**
* ReadyState representing an EventSource connection that is closed (eg disconnected)
*
* @public
*/
EventSource.CLOSED = 2;
function getBaseURL() {
  const doc = "document" in globalThis ? globalThis.document : void 0;
  return doc && typeof doc == "object" && "baseURI" in doc && typeof doc.baseURI == "string" ? doc.baseURI : void 0;
}
function normalizeHeaders(headers) {
  if (!headers)
    return {};
  if (headers instanceof Headers) {
    return Object.fromEntries(headers.entries());
  }
  if (Array.isArray(headers)) {
    return Object.fromEntries(headers);
  }
  return { ...headers };
}
function createFetchWithInit(baseFetch = fetch, baseInit) {
  if (!baseInit) {
    return baseFetch;
  }
  return async (url2, init) => {
    const mergedInit = {
      ...baseInit,
      ...init,
      // Headers need special handling - merge instead of replace
      headers: init?.headers ? { ...normalizeHeaders(baseInit.headers), ...normalizeHeaders(init.headers) } : baseInit.headers
    };
    return baseFetch(url2, mergedInit);
  };
}
const MAX_REDIRECTS = 5;
const REDIRECT_STATUSES = [301, 302, 303, 307, 308];
function isWithinOrigin(from, to) {
  if (from.protocol === to.protocol && from.hostname === to.hostname && from.port === to.port) {
    return true;
  }
  return from.hostname === to.hostname && from.protocol === "http:" && from.port === "" && to.protocol === "https:" && to.port === "";
}
function redirectTarget(response, requestUrl) {
  const location = REDIRECT_STATUSES.includes(response.status) ? response.headers.get("location") : null;
  try {
    return location ? new URL(location, requestUrl) : void 0;
  } catch {
    return void 0;
  }
}
function followWithinOrigin(baseFetch, url2, init, response, followed = 0) {
  const target = redirectTarget(response, url2);
  const keepsMethod = response.status === 307 || response.status === 308 || (init?.method ?? "GET").toUpperCase() === "GET";
  if (!target || followed === MAX_REDIRECTS || !keepsMethod) {
    return void 0;
  }
  const from = new URL(url2);
  const addsUserinfo = (target.username || target.password) && (target.username !== from.username || target.password !== from.password);
  if (addsUserinfo || !isWithinOrigin(from, target)) {
    return void 0;
  }
  return Promise.resolve(response.body?.cancel()).then(() => baseFetch(target, { ...init, redirect: "manual" })).then((next) => followWithinOrigin(baseFetch, target, init, next, followed + 1) ?? next);
}
const followingFetches = /* @__PURE__ */ new WeakSet();
function fetchLeavingRedirects(baseFetch) {
  const following = (url2, init) => (baseFetch ?? fetch)(url2, init);
  followingFetches.add(following);
  return following;
}
function fetchWithinOrigin(baseFetch = fetch) {
  if (followingFetches.has(baseFetch)) {
    return baseFetch;
  }
  return (url2, init) => {
    if (init?.redirect === "error" || init?.redirect === "manual") {
      return baseFetch(url2, init);
    }
    return Promise.resolve(baseFetch(url2, { ...init, redirect: "manual" })).then((response) => followWithinOrigin(baseFetch, url2, init, response) ?? response);
  };
}
function unfollowedRedirect(response, requestUrl) {
  if (response.type === "opaqueredirect") {
    return "Redirect not followed: this runtime does not expose the redirect target";
  }
  const from = response.url || requestUrl;
  const target = redirectTarget(response, from);
  if (!target) {
    return void 0;
  }
  target.username = target.password = target.search = target.hash = "";
  if (target.protocol === "http:" && new URL(from).protocol === "https:") {
    target.protocol = "https:";
    return `Redirect to plain http not followed; try ${target.href} instead`;
  }
  return `Redirect to ${target.href} not followed`;
}
let crypto;
crypto = globalThis.crypto?.webcrypto ?? // Node.js [18-16] REPL
globalThis.crypto ?? // Node.js >18
import("node:crypto").then((m) => m.webcrypto);
async function getRandomValues(size) {
  return (await crypto).getRandomValues(new Uint8Array(size));
}
async function random(size) {
  const mask = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~";
  const evenDistCutoff = Math.pow(2, 8) - Math.pow(2, 8) % mask.length;
  let result = "";
  while (result.length < size) {
    const randomBytes = await getRandomValues(size - result.length);
    for (const randomByte of randomBytes) {
      if (randomByte < evenDistCutoff) {
        result += mask[randomByte % mask.length];
      }
    }
  }
  return result;
}
async function generateVerifier(length) {
  return await random(length);
}
async function generateChallenge(code_verifier) {
  const buffer = await (await crypto).subtle.digest("SHA-256", new TextEncoder().encode(code_verifier));
  return btoa(String.fromCharCode(...new Uint8Array(buffer))).replace(/\//g, "_").replace(/\+/g, "-").replace(/=/g, "");
}
async function pkceChallenge(length) {
  if (!length)
    length = 43;
  if (length < 43 || length > 128) {
    throw `Expected a length between 43 and 128. Received ${length}.`;
  }
  const verifier = await generateVerifier(length);
  const challenge = await generateChallenge(verifier);
  return {
    code_verifier: verifier,
    code_challenge: challenge
  };
}
const SafeUrlSchema = url().superRefine((val, ctx) => {
  if (!URL.canParse(val)) {
    ctx.addIssue({
      code: ZodIssueCode.custom,
      message: "URL must be parseable",
      fatal: true
    });
    return NEVER;
  }
}).refine((url2) => {
  const u = new URL(url2);
  return u.protocol !== "javascript:" && u.protocol !== "data:" && u.protocol !== "vbscript:";
}, { message: "URL cannot use javascript:, data:, or vbscript: scheme" });
const OAuthProtectedResourceMetadataSchema = looseObject({
  resource: string().url(),
  authorization_servers: array(SafeUrlSchema).optional(),
  jwks_uri: string().url().optional(),
  scopes_supported: array(string()).optional(),
  bearer_methods_supported: array(string()).optional(),
  resource_signing_alg_values_supported: array(string()).optional(),
  resource_name: string().optional(),
  resource_documentation: string().optional(),
  resource_policy_uri: string().url().optional(),
  resource_tos_uri: string().url().optional(),
  tls_client_certificate_bound_access_tokens: boolean().optional(),
  authorization_details_types_supported: array(string()).optional(),
  dpop_signing_alg_values_supported: array(string()).optional(),
  dpop_bound_access_tokens_required: boolean().optional()
});
const OAuthMetadataSchema = looseObject({
  issuer: string(),
  authorization_endpoint: SafeUrlSchema,
  token_endpoint: SafeUrlSchema,
  registration_endpoint: SafeUrlSchema.optional(),
  scopes_supported: array(string()).optional(),
  response_types_supported: array(string()),
  response_modes_supported: array(string()).optional(),
  grant_types_supported: array(string()).optional(),
  token_endpoint_auth_methods_supported: array(string()).optional(),
  token_endpoint_auth_signing_alg_values_supported: array(string()).optional(),
  service_documentation: SafeUrlSchema.optional(),
  revocation_endpoint: SafeUrlSchema.optional(),
  revocation_endpoint_auth_methods_supported: array(string()).optional(),
  revocation_endpoint_auth_signing_alg_values_supported: array(string()).optional(),
  introspection_endpoint: string().optional(),
  introspection_endpoint_auth_methods_supported: array(string()).optional(),
  introspection_endpoint_auth_signing_alg_values_supported: array(string()).optional(),
  code_challenge_methods_supported: array(string()).optional(),
  client_id_metadata_document_supported: boolean().optional()
});
const OpenIdProviderMetadataSchema = looseObject({
  issuer: string(),
  authorization_endpoint: SafeUrlSchema,
  token_endpoint: SafeUrlSchema,
  userinfo_endpoint: SafeUrlSchema.optional(),
  jwks_uri: SafeUrlSchema,
  registration_endpoint: SafeUrlSchema.optional(),
  scopes_supported: array(string()).optional(),
  response_types_supported: array(string()),
  response_modes_supported: array(string()).optional(),
  grant_types_supported: array(string()).optional(),
  acr_values_supported: array(string()).optional(),
  subject_types_supported: array(string()),
  id_token_signing_alg_values_supported: array(string()),
  id_token_encryption_alg_values_supported: array(string()).optional(),
  id_token_encryption_enc_values_supported: array(string()).optional(),
  userinfo_signing_alg_values_supported: array(string()).optional(),
  userinfo_encryption_alg_values_supported: array(string()).optional(),
  userinfo_encryption_enc_values_supported: array(string()).optional(),
  request_object_signing_alg_values_supported: array(string()).optional(),
  request_object_encryption_alg_values_supported: array(string()).optional(),
  request_object_encryption_enc_values_supported: array(string()).optional(),
  token_endpoint_auth_methods_supported: array(string()).optional(),
  token_endpoint_auth_signing_alg_values_supported: array(string()).optional(),
  display_values_supported: array(string()).optional(),
  claim_types_supported: array(string()).optional(),
  claims_supported: array(string()).optional(),
  service_documentation: string().optional(),
  claims_locales_supported: array(string()).optional(),
  ui_locales_supported: array(string()).optional(),
  claims_parameter_supported: boolean().optional(),
  request_parameter_supported: boolean().optional(),
  request_uri_parameter_supported: boolean().optional(),
  require_request_uri_registration: boolean().optional(),
  op_policy_uri: SafeUrlSchema.optional(),
  op_tos_uri: SafeUrlSchema.optional(),
  client_id_metadata_document_supported: boolean().optional()
});
const OpenIdProviderDiscoveryMetadataSchema = object({
  ...OpenIdProviderMetadataSchema.shape,
  ...OAuthMetadataSchema.pick({
    code_challenge_methods_supported: true
  }).shape
});
const OAuthTokensSchema = object({
  access_token: string(),
  id_token: string().optional(),
  // Optional for OAuth 2.1, but necessary in OpenID Connect
  token_type: string(),
  expires_in: number().optional(),
  scope: string().optional(),
  refresh_token: string().optional(),
  /**
   * Not part of the wire format: the authorization server this value was obtained from, added by
   * the client's `auth()` before it is stored and compared when it is read back.
   */
  issuer: string().optional().catch(void 0)
}).strip();
const OAuthErrorResponseSchema = object({
  error: string(),
  error_description: string().optional(),
  error_uri: string().optional()
});
const OptionalSafeUrlSchema = SafeUrlSchema.optional().or(literal("").transform(() => void 0));
const OAuthClientMetadataSchema = object({
  redirect_uris: array(SafeUrlSchema),
  token_endpoint_auth_method: string().optional(),
  grant_types: array(string()).optional(),
  response_types: array(string()).optional(),
  client_name: string().optional(),
  client_uri: SafeUrlSchema.optional(),
  logo_uri: OptionalSafeUrlSchema,
  scope: string().optional(),
  contacts: array(string()).optional(),
  tos_uri: OptionalSafeUrlSchema,
  policy_uri: string().optional(),
  jwks_uri: SafeUrlSchema.optional(),
  jwks: any().optional(),
  software_id: string().optional(),
  software_version: string().optional(),
  software_statement: string().optional()
}).strip();
const OAuthClientInformationSchema = object({
  client_id: string(),
  client_secret: string().optional(),
  client_id_issued_at: number$1().optional(),
  client_secret_expires_at: number$1().optional(),
  /**
   * Not part of the wire format: the authorization server this value was obtained from, added by
   * the client's `auth()` before it is stored and compared when it is read back.
   */
  issuer: string().optional().catch(void 0)
}).strip();
const OAuthClientInformationFullSchema = OAuthClientMetadataSchema.merge(OAuthClientInformationSchema);
object({
  error: string(),
  error_description: string().optional()
}).strip();
object({
  token: string(),
  token_type_hint: string().optional()
}).strip();
function resourceUrlFromServerUrl(url2) {
  const resourceURL = typeof url2 === "string" ? new URL(url2) : new URL(url2.href);
  resourceURL.hash = "";
  return resourceURL;
}
function checkResourceAllowed({ requestedResource, configuredResource }) {
  const requested = typeof requestedResource === "string" ? new URL(requestedResource) : new URL(requestedResource.href);
  const configured = typeof configuredResource === "string" ? new URL(configuredResource) : new URL(configuredResource.href);
  if (requested.origin !== configured.origin) {
    return false;
  }
  if (requested.pathname.length < configured.pathname.length) {
    return false;
  }
  const requestedPath = requested.pathname.endsWith("/") ? requested.pathname : requested.pathname + "/";
  const configuredPath = configured.pathname.endsWith("/") ? configured.pathname : configured.pathname + "/";
  return requestedPath.startsWith(configuredPath);
}
class OAuthError extends Error {
  constructor(message2, errorUri) {
    super(message2);
    this.errorUri = errorUri;
    this.name = this.constructor.name;
  }
  /**
   * Converts the error to a standard OAuth error response object
   */
  toResponseObject() {
    const response = {
      error: this.errorCode,
      error_description: this.message
    };
    if (this.errorUri) {
      response.error_uri = this.errorUri;
    }
    return response;
  }
  get errorCode() {
    return this.constructor.errorCode;
  }
}
class InvalidRequestError extends OAuthError {
}
InvalidRequestError.errorCode = "invalid_request";
class InvalidClientError extends OAuthError {
}
InvalidClientError.errorCode = "invalid_client";
class InvalidGrantError extends OAuthError {
}
InvalidGrantError.errorCode = "invalid_grant";
class UnauthorizedClientError extends OAuthError {
}
UnauthorizedClientError.errorCode = "unauthorized_client";
class UnsupportedGrantTypeError extends OAuthError {
}
UnsupportedGrantTypeError.errorCode = "unsupported_grant_type";
class InvalidScopeError extends OAuthError {
}
InvalidScopeError.errorCode = "invalid_scope";
class AccessDeniedError extends OAuthError {
}
AccessDeniedError.errorCode = "access_denied";
class ServerError extends OAuthError {
}
ServerError.errorCode = "server_error";
class TemporarilyUnavailableError extends OAuthError {
}
TemporarilyUnavailableError.errorCode = "temporarily_unavailable";
class UnsupportedResponseTypeError extends OAuthError {
}
UnsupportedResponseTypeError.errorCode = "unsupported_response_type";
class UnsupportedTokenTypeError extends OAuthError {
}
UnsupportedTokenTypeError.errorCode = "unsupported_token_type";
class InvalidTokenError extends OAuthError {
}
InvalidTokenError.errorCode = "invalid_token";
class MethodNotAllowedError extends OAuthError {
}
MethodNotAllowedError.errorCode = "method_not_allowed";
class TooManyRequestsError extends OAuthError {
}
TooManyRequestsError.errorCode = "too_many_requests";
class InvalidClientMetadataError extends OAuthError {
}
InvalidClientMetadataError.errorCode = "invalid_client_metadata";
class InsufficientScopeError extends OAuthError {
}
InsufficientScopeError.errorCode = "insufficient_scope";
class InvalidTargetError extends OAuthError {
}
InvalidTargetError.errorCode = "invalid_target";
const OAUTH_ERRORS = {
  [InvalidRequestError.errorCode]: InvalidRequestError,
  [InvalidClientError.errorCode]: InvalidClientError,
  [InvalidGrantError.errorCode]: InvalidGrantError,
  [UnauthorizedClientError.errorCode]: UnauthorizedClientError,
  [UnsupportedGrantTypeError.errorCode]: UnsupportedGrantTypeError,
  [InvalidScopeError.errorCode]: InvalidScopeError,
  [AccessDeniedError.errorCode]: AccessDeniedError,
  [ServerError.errorCode]: ServerError,
  [TemporarilyUnavailableError.errorCode]: TemporarilyUnavailableError,
  [UnsupportedResponseTypeError.errorCode]: UnsupportedResponseTypeError,
  [UnsupportedTokenTypeError.errorCode]: UnsupportedTokenTypeError,
  [InvalidTokenError.errorCode]: InvalidTokenError,
  [MethodNotAllowedError.errorCode]: MethodNotAllowedError,
  [TooManyRequestsError.errorCode]: TooManyRequestsError,
  [InvalidClientMetadataError.errorCode]: InvalidClientMetadataError,
  [InsufficientScopeError.errorCode]: InsufficientScopeError,
  [InvalidTargetError.errorCode]: InvalidTargetError
};
class UnauthorizedError extends Error {
  constructor(message2) {
    super(message2 ?? "Unauthorized");
  }
}
function isClientAuthMethod(method) {
  return ["client_secret_basic", "client_secret_post", "none"].includes(method);
}
const AUTHORIZATION_CODE_RESPONSE_TYPE = "code";
const AUTHORIZATION_CODE_CHALLENGE_METHOD = "S256";
function selectClientAuthMethod(clientInformation, supportedMethods) {
  const hasClientSecret = clientInformation.client_secret !== void 0;
  if ("token_endpoint_auth_method" in clientInformation && clientInformation.token_endpoint_auth_method && isClientAuthMethod(clientInformation.token_endpoint_auth_method) && (supportedMethods.length === 0 || supportedMethods.includes(clientInformation.token_endpoint_auth_method))) {
    return clientInformation.token_endpoint_auth_method;
  }
  if (supportedMethods.length === 0) {
    return hasClientSecret ? "client_secret_basic" : "none";
  }
  if (hasClientSecret && supportedMethods.includes("client_secret_basic")) {
    return "client_secret_basic";
  }
  if (hasClientSecret && supportedMethods.includes("client_secret_post")) {
    return "client_secret_post";
  }
  if (supportedMethods.includes("none")) {
    return "none";
  }
  return hasClientSecret ? "client_secret_post" : "none";
}
function applyClientAuthentication(method, clientInformation, headers, params) {
  const { client_id, client_secret } = clientInformation;
  switch (method) {
    case "client_secret_basic":
      applyBasicAuth(client_id, client_secret, headers);
      return;
    case "client_secret_post":
      applyPostAuth(client_id, client_secret, params);
      return;
    case "none":
      applyPublicAuth(client_id, params);
      return;
    default:
      throw new Error(`Unsupported client authentication method: ${method}`);
  }
}
function applyBasicAuth(clientId, clientSecret, headers) {
  if (!clientSecret) {
    throw new Error("client_secret_basic authentication requires a client_secret");
  }
  const credentials = btoa(`${clientId}:${clientSecret}`);
  headers.set("Authorization", `Basic ${credentials}`);
}
function applyPostAuth(clientId, clientSecret, params) {
  params.set("client_id", clientId);
  if (clientSecret) {
    params.set("client_secret", clientSecret);
  }
}
function applyPublicAuth(clientId, params) {
  params.set("client_id", clientId);
}
async function parseErrorResponse(input) {
  const statusCode = input instanceof Response ? input.status : void 0;
  const body = input instanceof Response ? await input.text() : input;
  try {
    const result = OAuthErrorResponseSchema.parse(JSON.parse(body));
    const { error, error_description, error_uri } = result;
    const errorClass = OAUTH_ERRORS[error] || ServerError;
    return new errorClass(error_description || "", error_uri);
  } catch (error) {
    const redirect = input instanceof Response ? unfollowedRedirect(input, input.url) : void 0;
    const errorMessage = `${statusCode ? `HTTP ${statusCode}: ` : ""}${redirect ?? `Invalid OAuth error response: ${error}. Raw body: ${body}`}`;
    return new ServerError(errorMessage);
  }
}
function issuersMatch(a, b) {
  let [x, y] = [a, b];
  try {
    [x, y] = [new URL(a).href, new URL(b).href];
  } catch {
  }
  return x === y || x.endsWith("/") && x.slice(0, -1) === y || y.endsWith("/") && y.slice(0, -1) === x;
}
function discardIfIssuerMismatch(stored, issuer) {
  if (!stored)
    return void 0;
  if (typeof stored.issuer !== "string")
    return stored.issuer == null ? stored : { ...stored, issuer: void 0 };
  return issuersMatch(stored.issuer, issuer) ? stored : void 0;
}
const TokenResponseSchema = OAuthTokensSchema.omit({ issuer: true });
const RegistrationResponseSchema = OAuthClientInformationFullSchema.omit({ issuer: true });
function boundElsewhereError(stored, issuer) {
  return new Error(`OAuth client information is bound to authorization server ${stored.issuer} and is not presented to ${issuer}. Clear the stored client information, or correct \`expectedIssuer\`, if the authorization server has moved.`);
}
async function auth(provider, options) {
  try {
    return await authInternal(provider, options);
  } catch (error) {
    if (error instanceof InvalidClientError || error instanceof UnauthorizedClientError) {
      await provider.invalidateCredentials?.("all");
      return await authInternal(provider, options);
    } else if (error instanceof InvalidGrantError) {
      await provider.invalidateCredentials?.("tokens");
      return await authInternal(provider, options);
    }
    throw error;
  }
}
async function authInternal(provider, { serverUrl, authorizationCode, scope, resourceMetadataUrl, fetchFn }) {
  const cachedState = await provider.discoveryState?.();
  let resourceMetadata;
  let authorizationServerUrl;
  let metadata;
  let effectiveResourceMetadataUrl = resourceMetadataUrl;
  if (!effectiveResourceMetadataUrl && cachedState?.resourceMetadataUrl) {
    effectiveResourceMetadataUrl = new URL(cachedState.resourceMetadataUrl);
  }
  if (cachedState?.authorizationServerUrl) {
    authorizationServerUrl = cachedState.authorizationServerUrl;
    resourceMetadata = cachedState.resourceMetadata;
    metadata = cachedState.authorizationServerMetadata ?? await discoverAuthorizationServerMetadata(authorizationServerUrl, { fetchFn });
    if (!resourceMetadata) {
      try {
        resourceMetadata = await discoverOAuthProtectedResourceMetadata(serverUrl, { resourceMetadataUrl: effectiveResourceMetadataUrl }, fetchFn);
      } catch {
      }
    }
    if (metadata !== cachedState.authorizationServerMetadata || resourceMetadata !== cachedState.resourceMetadata) {
      await provider.saveDiscoveryState?.({
        authorizationServerUrl: String(authorizationServerUrl),
        resourceMetadataUrl: effectiveResourceMetadataUrl?.toString(),
        resourceMetadata,
        authorizationServerMetadata: metadata
      });
    }
  } else {
    const serverInfo = await discoverOAuthServerInfo(serverUrl, { resourceMetadataUrl: effectiveResourceMetadataUrl, fetchFn });
    authorizationServerUrl = serverInfo.authorizationServerUrl;
    metadata = serverInfo.authorizationServerMetadata;
    resourceMetadata = serverInfo.resourceMetadata;
    await provider.saveDiscoveryState?.({
      authorizationServerUrl: String(authorizationServerUrl),
      resourceMetadataUrl: effectiveResourceMetadataUrl?.toString(),
      resourceMetadata,
      authorizationServerMetadata: metadata
    });
  }
  const issuer = String(authorizationServerUrl);
  const selectedResource = await selectResourceURL(serverUrl, provider, resourceMetadata);
  const resource = selectedResource && resourceMetadata && !provider.validateResourceURL ? resourceMetadata.resource : selectedResource;
  const resolvedScope = scope || resourceMetadata?.scopes_supported?.join(" ") || provider.clientMetadata.scope;
  const storedClientInformation = await Promise.resolve(provider.clientInformation());
  let clientInformation = discardIfIssuerMismatch(storedClientInformation, issuer);
  const canRegisterAgain = provider.saveClientInformation !== void 0 && !!provider.redirectUrl;
  if (storedClientInformation && !clientInformation && !canRegisterAgain) {
    throw boundElsewhereError(storedClientInformation, issuer);
  }
  const unstampedClientInformation = clientInformation?.issuer == null ? clientInformation : void 0;
  const bindClientInformation = async () => {
    try {
      if (unstampedClientInformation)
        await provider.saveClientInformation?.({ ...unstampedClientInformation, issuer });
    } catch {
    }
  };
  if (!clientInformation) {
    if (authorizationCode !== void 0) {
      throw new Error("Existing OAuth client information is required when exchanging an authorization code");
    }
    const supportsUrlBasedClientId = metadata?.client_id_metadata_document_supported === true;
    const clientMetadataUrl = provider.clientMetadataUrl;
    if (clientMetadataUrl && !isHttpsUrl(clientMetadataUrl)) {
      throw new InvalidClientMetadataError(`clientMetadataUrl must be a valid HTTPS URL with a non-root pathname, got: ${clientMetadataUrl}`);
    }
    const shouldUseUrlBasedClientId = supportsUrlBasedClientId && clientMetadataUrl;
    if (shouldUseUrlBasedClientId) {
      clientInformation = { client_id: clientMetadataUrl, issuer };
      await provider.saveClientInformation?.(clientInformation);
    } else {
      if (!provider.saveClientInformation) {
        throw new Error("OAuth client information must be saveable for dynamic registration");
      }
      const fullInformation = await registerClient(authorizationServerUrl, {
        metadata,
        clientMetadata: provider.clientMetadata,
        scope: resolvedScope,
        fetchFn
      });
      clientInformation = { ...fullInformation, issuer };
      await provider.saveClientInformation(clientInformation);
    }
  }
  const nonInteractiveFlow = !provider.redirectUrl;
  if (authorizationCode !== void 0 || nonInteractiveFlow) {
    const tokens2 = await fetchToken(provider, authorizationServerUrl, {
      metadata,
      resource,
      authorizationCode,
      fetchFn
    });
    await bindClientInformation();
    await provider.saveTokens({ ...tokens2, issuer });
    return "AUTHORIZED";
  }
  const tokens = discardIfIssuerMismatch(await provider.tokens(), issuer);
  if (tokens?.refresh_token && tokens.issuer == null) {
    console.warn("[mcp-sdk] stored OAuth tokens have no 'issuer' property (saved by an earlier version, or by a provider that does not keep it) and are used as-is; make sure your OAuthClientProvider stores what saveTokens() and saveClientInformation() receive unchanged.");
  }
  if (tokens?.refresh_token) {
    try {
      const newTokens = await refreshAuthorization(authorizationServerUrl, {
        metadata,
        clientInformation,
        refreshToken: tokens.refresh_token,
        resource,
        addClientAuthentication: provider.addClientAuthentication,
        fetchFn
      });
      await bindClientInformation();
      await provider.saveTokens({ ...newTokens, issuer });
      return "AUTHORIZED";
    } catch (error) {
      if (!(error instanceof OAuthError) || error instanceof ServerError) ;
      else {
        throw error;
      }
    }
  }
  const state = provider.state ? await provider.state() : void 0;
  const { authorizationUrl, codeVerifier } = await startAuthorization(authorizationServerUrl, {
    metadata,
    clientInformation,
    state,
    redirectUrl: provider.redirectUrl,
    scope: resolvedScope,
    resource
  });
  await provider.saveCodeVerifier(codeVerifier);
  await provider.redirectToAuthorization(authorizationUrl);
  return "REDIRECT";
}
function isHttpsUrl(value) {
  if (!value)
    return false;
  try {
    const url2 = new URL(value);
    return url2.protocol === "https:" && url2.pathname !== "/";
  } catch {
    return false;
  }
}
async function selectResourceURL(serverUrl, provider, resourceMetadata) {
  const defaultResource = resourceUrlFromServerUrl(serverUrl);
  if (provider.validateResourceURL) {
    return await provider.validateResourceURL(defaultResource, resourceMetadata?.resource);
  }
  if (!resourceMetadata) {
    return void 0;
  }
  if (!checkResourceAllowed({ requestedResource: defaultResource, configuredResource: resourceMetadata.resource })) {
    throw new Error(`Protected resource ${resourceMetadata.resource} does not match expected ${defaultResource} (or origin)`);
  }
  return new URL(resourceMetadata.resource);
}
function extractWWWAuthenticateParams(res) {
  const authenticateHeader = res.headers.get("WWW-Authenticate");
  if (!authenticateHeader) {
    return {};
  }
  const [type, scheme] = authenticateHeader.split(" ");
  if (type.toLowerCase() !== "bearer" || !scheme) {
    return {};
  }
  const resourceMetadataMatch = extractFieldFromWwwAuth(res, "resource_metadata") || void 0;
  let resourceMetadataUrl;
  if (resourceMetadataMatch) {
    try {
      resourceMetadataUrl = new URL(resourceMetadataMatch);
    } catch {
    }
  }
  const scope = extractFieldFromWwwAuth(res, "scope") || void 0;
  const error = extractFieldFromWwwAuth(res, "error") || void 0;
  return {
    resourceMetadataUrl,
    scope,
    error
  };
}
function extractFieldFromWwwAuth(response, fieldName) {
  const wwwAuthHeader = response.headers.get("WWW-Authenticate");
  if (!wwwAuthHeader) {
    return null;
  }
  const pattern = new RegExp(`${fieldName}=(?:"([^"]+)"|([^\\s,]+))`);
  const match = wwwAuthHeader.match(pattern);
  if (match) {
    return match[1] || match[2];
  }
  return null;
}
async function discoverOAuthProtectedResourceMetadata(serverUrl, opts, fetchFn = fetch) {
  const response = await discoverMetadataWithFallback(serverUrl, "oauth-protected-resource", fetchFn, {
    protocolVersion: opts?.protocolVersion,
    metadataUrl: opts?.resourceMetadataUrl
  });
  if (!response || response.status === 404) {
    await response?.body?.cancel();
    throw new Error(`Resource server does not implement OAuth 2.0 Protected Resource Metadata.`);
  }
  if (!response.ok) {
    await response.body?.cancel();
    throw new Error(`HTTP ${response.status} trying to load well-known OAuth protected resource metadata.`);
  }
  return OAuthProtectedResourceMetadataSchema.parse(await response.json());
}
async function fetchWithCorsRetry(url2, headers, fetchFn = fetch) {
  try {
    return await fetchWithinOrigin(fetchFn)(url2, { headers });
  } catch (error) {
    if (error instanceof TypeError) {
      if (headers) {
        return fetchWithCorsRetry(url2, void 0, fetchFn);
      } else {
        return void 0;
      }
    }
    throw error;
  }
}
function buildWellKnownPath(wellKnownPrefix, pathname = "", options = {}) {
  if (pathname.endsWith("/")) {
    pathname = pathname.slice(0, -1);
  }
  return options.prependPathname ? `${pathname}/.well-known/${wellKnownPrefix}` : `/.well-known/${wellKnownPrefix}${pathname}`;
}
async function tryMetadataDiscovery(url2, protocolVersion, fetchFn = fetch) {
  const headers = {
    "MCP-Protocol-Version": protocolVersion
  };
  return await fetchWithCorsRetry(url2, headers, fetchFn);
}
function shouldAttemptFallback(response, pathname) {
  return !response || !response.ok && response.status < 500 && pathname !== "/";
}
async function discoverMetadataWithFallback(serverUrl, wellKnownType, fetchFn, opts) {
  const issuer = new URL(serverUrl);
  const protocolVersion = opts?.protocolVersion ?? LATEST_PROTOCOL_VERSION;
  let url2;
  if (opts?.metadataUrl) {
    url2 = new URL(opts.metadataUrl);
  } else {
    const wellKnownPath = buildWellKnownPath(wellKnownType, issuer.pathname);
    url2 = new URL(wellKnownPath, opts?.metadataServerUrl ?? issuer);
    url2.search = issuer.search;
  }
  let response = await tryMetadataDiscovery(url2, protocolVersion, fetchFn);
  if (!opts?.metadataUrl && shouldAttemptFallback(response, issuer.pathname)) {
    const rootUrl = new URL(`/.well-known/${wellKnownType}`, issuer);
    response = await tryMetadataDiscovery(rootUrl, protocolVersion, fetchFn);
  }
  return response;
}
function buildDiscoveryUrls(authorizationServerUrl) {
  const url2 = typeof authorizationServerUrl === "string" ? new URL(authorizationServerUrl) : authorizationServerUrl;
  const hasPath = url2.pathname !== "/";
  const urlsToTry = [];
  if (!hasPath) {
    urlsToTry.push({
      url: new URL("/.well-known/oauth-authorization-server", url2.origin),
      type: "oauth"
    });
    urlsToTry.push({
      url: new URL(`/.well-known/openid-configuration`, url2.origin),
      type: "oidc"
    });
    return urlsToTry;
  }
  let pathname = url2.pathname;
  if (pathname.endsWith("/")) {
    pathname = pathname.slice(0, -1);
  }
  urlsToTry.push({
    url: new URL(`/.well-known/oauth-authorization-server${pathname}`, url2.origin),
    type: "oauth"
  });
  urlsToTry.push({
    url: new URL(`/.well-known/openid-configuration${pathname}`, url2.origin),
    type: "oidc"
  });
  urlsToTry.push({
    url: new URL(`${pathname}/.well-known/openid-configuration`, url2.origin),
    type: "oidc"
  });
  return urlsToTry;
}
async function discoverAuthorizationServerMetadata(authorizationServerUrl, { fetchFn = fetch, protocolVersion = LATEST_PROTOCOL_VERSION } = {}) {
  const headers = {
    "MCP-Protocol-Version": protocolVersion,
    Accept: "application/json"
  };
  const urlsToTry = buildDiscoveryUrls(authorizationServerUrl);
  for (const { url: endpointUrl, type } of urlsToTry) {
    const response = await fetchWithCorsRetry(endpointUrl, headers, fetchFn);
    if (!response) {
      continue;
    }
    if (!response.ok) {
      await response.body?.cancel();
      if (response.status < 500) {
        continue;
      }
      throw new Error(`HTTP ${response.status} trying to load ${type === "oauth" ? "OAuth" : "OpenID provider"} metadata from ${endpointUrl}`);
    }
    if (type === "oauth") {
      return OAuthMetadataSchema.parse(await response.json());
    } else {
      return OpenIdProviderDiscoveryMetadataSchema.parse(await response.json());
    }
  }
  return void 0;
}
async function discoverOAuthServerInfo(serverUrl, opts) {
  let resourceMetadata;
  let authorizationServerUrl;
  try {
    resourceMetadata = await discoverOAuthProtectedResourceMetadata(serverUrl, { resourceMetadataUrl: opts?.resourceMetadataUrl }, opts?.fetchFn);
    if (resourceMetadata.authorization_servers && resourceMetadata.authorization_servers.length > 0) {
      authorizationServerUrl = resourceMetadata.authorization_servers[0];
    }
  } catch {
  }
  if (!authorizationServerUrl) {
    authorizationServerUrl = String(new URL("/", serverUrl));
  }
  const authorizationServerMetadata = await discoverAuthorizationServerMetadata(authorizationServerUrl, { fetchFn: opts?.fetchFn });
  return {
    authorizationServerUrl,
    authorizationServerMetadata,
    resourceMetadata
  };
}
function resourceIndicatorToString(resource) {
  return typeof resource === "string" ? resource : resource.href;
}
async function startAuthorization(authorizationServerUrl, { metadata, clientInformation, redirectUrl, scope, state, resource }) {
  let authorizationUrl;
  if (metadata) {
    authorizationUrl = new URL(metadata.authorization_endpoint);
    if (!metadata.response_types_supported.includes(AUTHORIZATION_CODE_RESPONSE_TYPE)) {
      throw new Error(`Incompatible auth server: does not support response type ${AUTHORIZATION_CODE_RESPONSE_TYPE}`);
    }
    if (metadata.code_challenge_methods_supported && !metadata.code_challenge_methods_supported.includes(AUTHORIZATION_CODE_CHALLENGE_METHOD)) {
      throw new Error(`Incompatible auth server: does not support code challenge method ${AUTHORIZATION_CODE_CHALLENGE_METHOD}`);
    }
  } else {
    authorizationUrl = new URL("/authorize", authorizationServerUrl);
  }
  const challenge = await pkceChallenge();
  const codeVerifier = challenge.code_verifier;
  const codeChallenge = challenge.code_challenge;
  authorizationUrl.searchParams.set("response_type", AUTHORIZATION_CODE_RESPONSE_TYPE);
  authorizationUrl.searchParams.set("client_id", clientInformation.client_id);
  authorizationUrl.searchParams.set("code_challenge", codeChallenge);
  authorizationUrl.searchParams.set("code_challenge_method", AUTHORIZATION_CODE_CHALLENGE_METHOD);
  authorizationUrl.searchParams.set("redirect_uri", String(redirectUrl));
  if (state) {
    authorizationUrl.searchParams.set("state", state);
  }
  if (scope) {
    authorizationUrl.searchParams.set("scope", scope);
  }
  if (scope?.includes("offline_access")) {
    authorizationUrl.searchParams.append("prompt", "consent");
  }
  if (resource) {
    authorizationUrl.searchParams.set("resource", resourceIndicatorToString(resource));
  }
  return { authorizationUrl, codeVerifier };
}
function prepareAuthorizationCodeRequest(authorizationCode, codeVerifier, redirectUri) {
  return new URLSearchParams({
    grant_type: "authorization_code",
    code: authorizationCode,
    code_verifier: codeVerifier,
    redirect_uri: String(redirectUri)
  });
}
async function executeTokenRequest(authorizationServerUrl, { metadata, tokenRequestParams, clientInformation, addClientAuthentication, resource, fetchFn }) {
  const tokenUrl = metadata?.token_endpoint ? new URL(metadata.token_endpoint) : new URL("/token", authorizationServerUrl);
  const headers = new Headers({
    "Content-Type": "application/x-www-form-urlencoded",
    Accept: "application/json"
  });
  if (resource) {
    tokenRequestParams.set("resource", resourceIndicatorToString(resource));
  }
  if (addClientAuthentication) {
    await addClientAuthentication(headers, tokenRequestParams, tokenUrl, metadata);
  } else if (clientInformation) {
    const supportedMethods = metadata?.token_endpoint_auth_methods_supported ?? [];
    const authMethod = selectClientAuthMethod(clientInformation, supportedMethods);
    applyClientAuthentication(authMethod, clientInformation, headers, tokenRequestParams);
  }
  const response = await fetchWithinOrigin(fetchFn ?? fetch)(tokenUrl, {
    method: "POST",
    headers,
    body: tokenRequestParams
  });
  if (!response.ok) {
    throw await parseErrorResponse(response);
  }
  return TokenResponseSchema.parse(await response.json());
}
async function refreshAuthorization(authorizationServerUrl, { metadata, clientInformation, refreshToken: refreshToken2, resource, addClientAuthentication, fetchFn }) {
  const tokenRequestParams = new URLSearchParams({
    grant_type: "refresh_token",
    refresh_token: refreshToken2
  });
  const tokens = await executeTokenRequest(authorizationServerUrl, {
    metadata,
    tokenRequestParams,
    clientInformation,
    addClientAuthentication,
    resource,
    fetchFn
  });
  return { refresh_token: refreshToken2, ...tokens };
}
async function fetchToken(provider, authorizationServerUrl, { metadata, resource, authorizationCode, fetchFn } = {}) {
  const readClientInformation = async () => {
    const storedClientInformation = await provider.clientInformation();
    const checked = discardIfIssuerMismatch(storedClientInformation, String(authorizationServerUrl));
    if (storedClientInformation && !checked) {
      throw boundElsewhereError(storedClientInformation, String(authorizationServerUrl));
    }
    return checked;
  };
  let clientInformation = await readClientInformation();
  const scope = provider.clientMetadata.scope;
  let tokenRequestParams;
  if (provider.prepareTokenRequest) {
    tokenRequestParams = await provider.prepareTokenRequest(scope);
  }
  if (!tokenRequestParams) {
    if (!authorizationCode) {
      throw new Error("Either provider.prepareTokenRequest() or authorizationCode is required");
    }
    if (!provider.redirectUrl) {
      throw new Error("redirectUrl is required for authorization_code flow");
    }
    const codeVerifier = await provider.codeVerifier();
    tokenRequestParams = prepareAuthorizationCodeRequest(authorizationCode, codeVerifier, provider.redirectUrl);
  }
  clientInformation ?? (clientInformation = await readClientInformation());
  return executeTokenRequest(authorizationServerUrl, {
    metadata,
    tokenRequestParams,
    clientInformation,
    addClientAuthentication: provider.addClientAuthentication,
    resource,
    fetchFn
  });
}
async function registerClient(authorizationServerUrl, { metadata, clientMetadata, scope, fetchFn }) {
  let registrationUrl;
  if (metadata) {
    if (!metadata.registration_endpoint) {
      throw new Error("Incompatible auth server: does not support dynamic client registration");
    }
    registrationUrl = new URL(metadata.registration_endpoint);
  } else {
    registrationUrl = new URL("/register", authorizationServerUrl);
  }
  const response = await fetchWithinOrigin(fetchFn ?? fetch)(registrationUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      ...clientMetadata,
      ...scope !== void 0 ? { scope } : {}
    })
  });
  if (!response.ok) {
    throw await parseErrorResponse(response);
  }
  return RegistrationResponseSchema.parse(await response.json());
}
class SseError extends Error {
  constructor(code, message2, event) {
    super(`SSE error: ${message2}`);
    this.code = code;
    this.event = event;
  }
}
class SSEClientTransport {
  constructor(url2, opts) {
    this._url = url2;
    this._resourceMetadataUrl = void 0;
    this._scope = void 0;
    this._eventSourceInit = opts?.eventSourceInit;
    this._requestInit = opts?.requestInit;
    this._authProvider = opts?.authProvider;
    this._fetch = opts?.fetch;
    this._fetchWithInit = createFetchWithInit(opts?.fetch, opts?.requestInit);
    this._followRedirects = opts?.redirectPolicy === "follow";
    if (this._followRedirects) {
      this._fetch = fetchLeavingRedirects(this._fetch);
      this._fetchWithInit = fetchLeavingRedirects(this._fetchWithInit);
    }
  }
  /** Error text for a redirect `response` that was not followed, or `undefined` for any other response. */
  _unfollowedRedirect(response, url2) {
    const text = unfollowedRedirect(response, url2);
    return text && !this._followRedirects ? `${text} (redirectPolicy: 'same-origin')` : text;
  }
  async _authThenStart() {
    if (!this._authProvider) {
      throw new UnauthorizedError("No auth provider");
    }
    let result;
    try {
      result = await auth(this._authProvider, {
        serverUrl: this._url,
        resourceMetadataUrl: this._resourceMetadataUrl,
        scope: this._scope,
        fetchFn: this._fetchWithInit
      });
    } catch (error) {
      this.onerror?.(error);
      throw error;
    }
    if (result !== "AUTHORIZED") {
      throw new UnauthorizedError();
    }
    return await this._startOrAuth();
  }
  async _commonHeaders() {
    const headers = {};
    if (this._authProvider) {
      const tokens = await this._authProvider.tokens();
      if (tokens) {
        headers["Authorization"] = `Bearer ${tokens.access_token}`;
      }
    }
    if (this._protocolVersion) {
      headers["mcp-protocol-version"] = this._protocolVersion;
    }
    const extraHeaders = normalizeHeaders(this._requestInit?.headers);
    return new Headers({
      ...headers,
      ...extraHeaders
    });
  }
  _startOrAuth() {
    const baseFetch = this?._eventSourceInit?.fetch ?? this._fetch ?? fetch;
    const fetchImpl = this._followRedirects ? baseFetch : fetchWithinOrigin(baseFetch);
    let redirect;
    return new Promise((resolve, reject) => {
      this._eventSource = new EventSource(this._url.href, {
        ...this._eventSourceInit,
        fetch: async (url2, init) => {
          const headers = await this._commonHeaders();
          headers.set("Accept", "text/event-stream");
          const response = await fetchImpl(url2, {
            ...init,
            headers
          });
          redirect = this._unfollowedRedirect(response, url2);
          if (response.status === 401 && response.headers.has("www-authenticate")) {
            const { resourceMetadataUrl, scope } = extractWWWAuthenticateParams(response);
            this._resourceMetadataUrl = resourceMetadataUrl;
            this._scope = scope;
          }
          return response;
        }
      });
      this._abortController = new AbortController();
      this._eventSource.onerror = (event) => {
        if (event.code === 401 && this._authProvider) {
          this._authThenStart().then(resolve, reject);
          return;
        }
        const error = new SseError(event.code, redirect ?? event.message, event);
        reject(error);
        this.onerror?.(error);
      };
      this._eventSource.onopen = () => {
      };
      this._eventSource.addEventListener("endpoint", (event) => {
        const messageEvent = event;
        try {
          this._endpoint = new URL(messageEvent.data, this._url);
          if (this._endpoint.origin !== this._url.origin) {
            throw new Error(`Endpoint origin does not match connection origin: ${this._endpoint.origin}`);
          }
        } catch (error) {
          reject(error);
          this.onerror?.(error);
          void this.close();
          return;
        }
        resolve();
      });
      this._eventSource.onmessage = (event) => {
        const messageEvent = event;
        let message2;
        try {
          message2 = JSONRPCMessageSchema.parse(JSON.parse(messageEvent.data));
        } catch (error) {
          this.onerror?.(error);
          return;
        }
        this.onmessage?.(message2);
      };
    });
  }
  async start() {
    if (this._eventSource) {
      throw new Error("SSEClientTransport already started! If using Client class, note that connect() calls start() automatically.");
    }
    return await this._startOrAuth();
  }
  /**
   * Call this method after the user has finished authorizing via their user agent and is redirected back to the MCP client application. This will exchange the authorization code for an access token, enabling the next connection attempt to successfully auth.
   */
  async finishAuth(authorizationCode) {
    if (!this._authProvider) {
      throw new UnauthorizedError("No auth provider");
    }
    const result = await auth(this._authProvider, {
      serverUrl: this._url,
      authorizationCode,
      resourceMetadataUrl: this._resourceMetadataUrl,
      scope: this._scope,
      fetchFn: this._fetchWithInit
    });
    if (result !== "AUTHORIZED") {
      throw new UnauthorizedError("Failed to authorize");
    }
  }
  async close() {
    this._abortController?.abort();
    this._eventSource?.close();
    this.onclose?.();
  }
  async send(message2) {
    if (!this._endpoint) {
      throw new Error("Not connected");
    }
    try {
      const headers = await this._commonHeaders();
      headers.set("content-type", "application/json");
      const init = {
        ...this._requestInit,
        method: "POST",
        headers,
        body: JSON.stringify(message2),
        signal: this._abortController?.signal
      };
      const response = await fetchWithinOrigin(this._fetch ?? fetch)(this._endpoint, init);
      if (!response.ok) {
        const text = await response.text().catch(() => null);
        if (response.status === 401 && this._authProvider) {
          const { resourceMetadataUrl, scope } = extractWWWAuthenticateParams(response);
          this._resourceMetadataUrl = resourceMetadataUrl;
          this._scope = scope;
          const result = await auth(this._authProvider, {
            serverUrl: this._url,
            resourceMetadataUrl: this._resourceMetadataUrl,
            scope: this._scope,
            fetchFn: this._fetchWithInit
          });
          if (result !== "AUTHORIZED") {
            throw new UnauthorizedError();
          }
          return this.send(message2);
        }
        throw new Error(`Error POSTing to endpoint (HTTP ${response.status}): ${this._unfollowedRedirect(response, this._endpoint) ?? text}`);
      }
      await response.body?.cancel();
    } catch (error) {
      this.onerror?.(error);
      throw error;
    }
  }
  setProtocolVersion(version) {
    this._protocolVersion = version;
  }
}
var crossSpawn = { exports: {} };
var windows;
var hasRequiredWindows;
function requireWindows() {
  if (hasRequiredWindows) return windows;
  hasRequiredWindows = 1;
  windows = isexe2;
  isexe2.sync = sync2;
  var fs2 = require$$0;
  function checkPathExt(path2, options) {
    var pathext = options.pathExt !== void 0 ? options.pathExt : process.env.PATHEXT;
    if (!pathext) {
      return true;
    }
    pathext = pathext.split(";");
    if (pathext.indexOf("") !== -1) {
      return true;
    }
    for (var i = 0; i < pathext.length; i++) {
      var p = pathext[i].toLowerCase();
      if (p && path2.substr(-p.length).toLowerCase() === p) {
        return true;
      }
    }
    return false;
  }
  function checkStat(stat, path2, options) {
    if (!stat.isSymbolicLink() && !stat.isFile()) {
      return false;
    }
    return checkPathExt(path2, options);
  }
  function isexe2(path2, options, cb) {
    fs2.stat(path2, function(er, stat) {
      cb(er, er ? false : checkStat(stat, path2, options));
    });
  }
  function sync2(path2, options) {
    return checkStat(fs2.statSync(path2), path2, options);
  }
  return windows;
}
var mode;
var hasRequiredMode;
function requireMode() {
  if (hasRequiredMode) return mode;
  hasRequiredMode = 1;
  mode = isexe2;
  isexe2.sync = sync2;
  var fs2 = require$$0;
  function isexe2(path2, options, cb) {
    fs2.stat(path2, function(er, stat) {
      cb(er, er ? false : checkStat(stat, options));
    });
  }
  function sync2(path2, options) {
    return checkStat(fs2.statSync(path2), options);
  }
  function checkStat(stat, options) {
    return stat.isFile() && checkMode(stat, options);
  }
  function checkMode(stat, options) {
    var mod = stat.mode;
    var uid = stat.uid;
    var gid = stat.gid;
    var myUid = options.uid !== void 0 ? options.uid : process.getuid && process.getuid();
    var myGid = options.gid !== void 0 ? options.gid : process.getgid && process.getgid();
    var u = parseInt("100", 8);
    var g = parseInt("010", 8);
    var o = parseInt("001", 8);
    var ug = u | g;
    var ret = mod & o || mod & g && gid === myGid || mod & u && uid === myUid || mod & ug && myUid === 0;
    return ret;
  }
  return mode;
}
var core;
if (process.platform === "win32" || commonjsGlobal.TESTING_WINDOWS) {
  core = requireWindows();
} else {
  core = requireMode();
}
var isexe_1 = isexe$1;
isexe$1.sync = sync;
function isexe$1(path2, options, cb) {
  if (typeof options === "function") {
    cb = options;
    options = {};
  }
  if (!cb) {
    if (typeof Promise !== "function") {
      throw new TypeError("callback not provided");
    }
    return new Promise(function(resolve, reject) {
      isexe$1(path2, options || {}, function(er, is) {
        if (er) {
          reject(er);
        } else {
          resolve(is);
        }
      });
    });
  }
  core(path2, options || {}, function(er, is) {
    if (er) {
      if (er.code === "EACCES" || options && options.ignoreErrors) {
        er = null;
        is = false;
      }
    }
    cb(er, is);
  });
}
function sync(path2, options) {
  try {
    return core.sync(path2, options || {});
  } catch (er) {
    if (options && options.ignoreErrors || er.code === "EACCES") {
      return false;
    } else {
      throw er;
    }
  }
}
const isWindows = process.platform === "win32" || process.env.OSTYPE === "cygwin" || process.env.OSTYPE === "msys";
const path$2 = path$4;
const COLON = isWindows ? ";" : ":";
const isexe = isexe_1;
const getNotFoundError = (cmd) => Object.assign(new Error(`not found: ${cmd}`), { code: "ENOENT" });
const getPathInfo = (cmd, opt) => {
  const colon = opt.colon || COLON;
  const pathEnv = cmd.match(/\//) || isWindows && cmd.match(/\\/) ? [""] : [
    // windows always checks the cwd first
    ...isWindows ? [process.cwd()] : [],
    ...(opt.path || process.env.PATH || /* istanbul ignore next: very unusual */
    "").split(colon)
  ];
  const pathExtExe = isWindows ? opt.pathExt || process.env.PATHEXT || ".EXE;.CMD;.BAT;.COM" : "";
  const pathExt = isWindows ? pathExtExe.split(colon) : [""];
  if (isWindows) {
    if (cmd.indexOf(".") !== -1 && pathExt[0] !== "")
      pathExt.unshift("");
  }
  return {
    pathEnv,
    pathExt,
    pathExtExe
  };
};
const which$1 = (cmd, opt, cb) => {
  if (typeof opt === "function") {
    cb = opt;
    opt = {};
  }
  if (!opt)
    opt = {};
  const { pathEnv, pathExt, pathExtExe } = getPathInfo(cmd, opt);
  const found = [];
  const step = (i) => new Promise((resolve, reject) => {
    if (i === pathEnv.length)
      return opt.all && found.length ? resolve(found) : reject(getNotFoundError(cmd));
    const ppRaw = pathEnv[i];
    const pathPart = /^".*"$/.test(ppRaw) ? ppRaw.slice(1, -1) : ppRaw;
    const pCmd = path$2.join(pathPart, cmd);
    const p = !pathPart && /^\.[\\\/]/.test(cmd) ? cmd.slice(0, 2) + pCmd : pCmd;
    resolve(subStep(p, i, 0));
  });
  const subStep = (p, i, ii) => new Promise((resolve, reject) => {
    if (ii === pathExt.length)
      return resolve(step(i + 1));
    const ext = pathExt[ii];
    isexe(p + ext, { pathExt: pathExtExe }, (er, is) => {
      if (!er && is) {
        if (opt.all)
          found.push(p + ext);
        else
          return resolve(p + ext);
      }
      return resolve(subStep(p, i, ii + 1));
    });
  });
  return cb ? step(0).then((res) => cb(null, res), cb) : step(0);
};
const whichSync = (cmd, opt) => {
  opt = opt || {};
  const { pathEnv, pathExt, pathExtExe } = getPathInfo(cmd, opt);
  const found = [];
  for (let i = 0; i < pathEnv.length; i++) {
    const ppRaw = pathEnv[i];
    const pathPart = /^".*"$/.test(ppRaw) ? ppRaw.slice(1, -1) : ppRaw;
    const pCmd = path$2.join(pathPart, cmd);
    const p = !pathPart && /^\.[\\\/]/.test(cmd) ? cmd.slice(0, 2) + pCmd : pCmd;
    for (let j = 0; j < pathExt.length; j++) {
      const cur = p + pathExt[j];
      try {
        const is = isexe.sync(cur, { pathExt: pathExtExe });
        if (is) {
          if (opt.all)
            found.push(cur);
          else
            return cur;
        }
      } catch (ex) {
      }
    }
  }
  if (opt.all && found.length)
    return found;
  if (opt.nothrow)
    return null;
  throw getNotFoundError(cmd);
};
var which_1 = which$1;
which$1.sync = whichSync;
var pathKey$1 = { exports: {} };
const pathKey = (options = {}) => {
  const environment = options.env || process.env;
  const platform = options.platform || process.platform;
  if (platform !== "win32") {
    return "PATH";
  }
  return Object.keys(environment).reverse().find((key) => key.toUpperCase() === "PATH") || "Path";
};
pathKey$1.exports = pathKey;
pathKey$1.exports.default = pathKey;
var pathKeyExports = pathKey$1.exports;
const path$1 = path$4;
const which = which_1;
const getPathKey = pathKeyExports;
function resolveCommandAttempt(parsed, withoutPathExt) {
  const env = parsed.options.env || process.env;
  const cwd = process.cwd();
  const hasCustomCwd = parsed.options.cwd != null;
  const shouldSwitchCwd = hasCustomCwd && process.chdir !== void 0 && !process.chdir.disabled;
  if (shouldSwitchCwd) {
    try {
      process.chdir(parsed.options.cwd);
    } catch (err) {
    }
  }
  let resolved;
  try {
    resolved = which.sync(parsed.command, {
      path: env[getPathKey({ env })],
      pathExt: withoutPathExt ? path$1.delimiter : void 0
    });
  } catch (e) {
  } finally {
    if (shouldSwitchCwd) {
      process.chdir(cwd);
    }
  }
  if (resolved) {
    resolved = path$1.resolve(hasCustomCwd ? parsed.options.cwd : "", resolved);
  }
  return resolved;
}
function resolveCommand$1(parsed) {
  return resolveCommandAttempt(parsed) || resolveCommandAttempt(parsed, true);
}
var resolveCommand_1 = resolveCommand$1;
var _escape = {};
const metaCharsRegExp = /([()\][%!^"`<>&|;, *?])/g;
function escapeCommand(arg) {
  arg = arg.replace(metaCharsRegExp, "^$1");
  return arg;
}
function escapeArgument(arg, doubleEscapeMetaChars) {
  arg = `${arg}`;
  arg = arg.replace(/(?=(\\+?)?)\1"/g, '$1$1\\"');
  arg = arg.replace(/(?=(\\+?)?)\1$/, "$1$1");
  arg = `"${arg}"`;
  arg = arg.replace(metaCharsRegExp, "^$1");
  if (doubleEscapeMetaChars) {
    arg = arg.replace(metaCharsRegExp, "^$1");
  }
  return arg;
}
_escape.command = escapeCommand;
_escape.argument = escapeArgument;
var shebangRegex$1 = /^#!(.*)/;
const shebangRegex = shebangRegex$1;
var shebangCommand$1 = (string2 = "") => {
  const match = string2.match(shebangRegex);
  if (!match) {
    return null;
  }
  const [path2, argument] = match[0].replace(/#! ?/, "").split(" ");
  const binary = path2.split("/").pop();
  if (binary === "env") {
    return argument;
  }
  return argument ? `${binary} ${argument}` : binary;
};
const fs = require$$0;
const shebangCommand = shebangCommand$1;
function readShebang$1(command) {
  const size = 150;
  const buffer = Buffer.alloc(size);
  let fd;
  try {
    fd = fs.openSync(command, "r");
    fs.readSync(fd, buffer, 0, size, 0);
    fs.closeSync(fd);
  } catch (e) {
  }
  return shebangCommand(buffer.toString());
}
var readShebang_1 = readShebang$1;
const path = path$4;
const resolveCommand = resolveCommand_1;
const escape = _escape;
const readShebang = readShebang_1;
const isWin$1 = process.platform === "win32";
const isExecutableRegExp = /\.(?:com|exe)$/i;
const isCmdShimRegExp = /node_modules[\\/].bin[\\/][^\\/]+\.cmd$/i;
function detectShebang(parsed) {
  parsed.file = resolveCommand(parsed);
  const shebang = parsed.file && readShebang(parsed.file);
  if (shebang) {
    parsed.args.unshift(parsed.file);
    parsed.command = shebang;
    return resolveCommand(parsed);
  }
  return parsed.file;
}
function parseNonShell(parsed) {
  if (!isWin$1) {
    return parsed;
  }
  const commandFile = detectShebang(parsed);
  const needsShell = !isExecutableRegExp.test(commandFile);
  if (parsed.options.forceShell || needsShell) {
    const needsDoubleEscapeMetaChars = isCmdShimRegExp.test(commandFile);
    parsed.command = path.normalize(parsed.command);
    parsed.command = escape.command(parsed.command);
    parsed.args = parsed.args.map((arg) => escape.argument(arg, needsDoubleEscapeMetaChars));
    const shellCommand = [parsed.command].concat(parsed.args).join(" ");
    parsed.args = ["/d", "/s", "/c", `"${shellCommand}"`];
    parsed.command = process.env.comspec || "cmd.exe";
    parsed.options.windowsVerbatimArguments = true;
  }
  return parsed;
}
function parse$1(command, args, options) {
  if (args && !Array.isArray(args)) {
    options = args;
    args = null;
  }
  args = args ? args.slice(0) : [];
  options = Object.assign({}, options);
  const parsed = {
    command,
    args,
    options,
    file: void 0,
    original: {
      command,
      args
    }
  };
  return options.shell ? parsed : parseNonShell(parsed);
}
var parse_1 = parse$1;
const isWin = process.platform === "win32";
function notFoundError(original, syscall) {
  return Object.assign(new Error(`${syscall} ${original.command} ENOENT`), {
    code: "ENOENT",
    errno: "ENOENT",
    syscall: `${syscall} ${original.command}`,
    path: original.command,
    spawnargs: original.args
  });
}
function hookChildProcess(cp2, parsed) {
  if (!isWin) {
    return;
  }
  const originalEmit = cp2.emit;
  cp2.emit = function(name, arg1) {
    if (name === "exit") {
      const err = verifyENOENT(arg1, parsed);
      if (err) {
        return originalEmit.call(cp2, "error", err);
      }
    }
    return originalEmit.apply(cp2, arguments);
  };
}
function verifyENOENT(status, parsed) {
  if (isWin && status === 1 && !parsed.file) {
    return notFoundError(parsed.original, "spawn");
  }
  return null;
}
function verifyENOENTSync(status, parsed) {
  if (isWin && status === 1 && !parsed.file) {
    return notFoundError(parsed.original, "spawnSync");
  }
  return null;
}
var enoent$1 = {
  hookChildProcess,
  verifyENOENT,
  verifyENOENTSync,
  notFoundError
};
const cp = require$$0$1;
const parse = parse_1;
const enoent = enoent$1;
function spawn(command, args, options) {
  const parsed = parse(command, args, options);
  const spawned = cp.spawn(parsed.command, parsed.args, parsed.options);
  enoent.hookChildProcess(spawned, parsed);
  return spawned;
}
function spawnSync(command, args, options) {
  const parsed = parse(command, args, options);
  const result = cp.spawnSync(parsed.command, parsed.args, parsed.options);
  result.error = result.error || enoent.verifyENOENTSync(result.status, parsed);
  return result;
}
crossSpawn.exports = spawn;
crossSpawn.exports.spawn = spawn;
crossSpawn.exports.sync = spawnSync;
crossSpawn.exports._parse = parse;
crossSpawn.exports._enoent = enoent;
var crossSpawnExports = crossSpawn.exports;
const spawn$1 = /* @__PURE__ */ getDefaultExportFromCjs(crossSpawnExports);
const DEFAULT_INHERITED_ENV_VARS = process$1.platform === "win32" ? [
  "APPDATA",
  "HOMEDRIVE",
  "HOMEPATH",
  "LOCALAPPDATA",
  "PATH",
  "PROCESSOR_ARCHITECTURE",
  "SYSTEMDRIVE",
  "SYSTEMROOT",
  "TEMP",
  "USERNAME",
  "USERPROFILE",
  "PROGRAMFILES"
] : (
  /* list inspired by the default env inheritance of sudo */
  ["HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER"]
);
function getDefaultEnvironment() {
  const env = {};
  for (const key of DEFAULT_INHERITED_ENV_VARS) {
    const value = process$1.env[key];
    if (value === void 0) {
      continue;
    }
    if (value.startsWith("()")) {
      continue;
    }
    env[key] = value;
  }
  return env;
}
class StdioClientTransport {
  constructor(server) {
    this._stderrStream = null;
    this._serverParams = server;
    this._readBuffer = new ReadBuffer({ maxBufferSize: server.maxBufferSize });
    if (server.stderr === "pipe" || server.stderr === "overlapped") {
      this._stderrStream = new PassThrough();
    }
  }
  /**
   * Starts the server process and prepares to communicate with it.
   */
  async start() {
    if (this._process) {
      throw new Error("StdioClientTransport already started! If using Client class, note that connect() calls start() automatically.");
    }
    return new Promise((resolve, reject) => {
      this._process = spawn$1(this._serverParams.command, this._serverParams.args ?? [], {
        // merge default env with server env because mcp server needs some env vars
        env: {
          ...getDefaultEnvironment(),
          ...this._serverParams.env
        },
        stdio: ["pipe", "pipe", this._serverParams.stderr ?? "inherit"],
        shell: false,
        windowsHide: process$1.platform === "win32",
        cwd: this._serverParams.cwd
      });
      this._process.on("error", (error) => {
        reject(error);
        this.onerror?.(error);
      });
      this._process.on("spawn", () => {
        resolve();
      });
      this._process.on("close", (_code) => {
        this._process = void 0;
        this.onclose?.();
      });
      this._process.stdin?.on("error", (error) => {
        this.onerror?.(error);
      });
      this._process.stdout?.on("data", (chunk) => {
        try {
          this._readBuffer.append(chunk);
          this.processReadBuffer();
        } catch (error) {
          this.onerror?.(error);
          this.close().catch(() => {
          });
        }
      });
      this._process.stdout?.on("error", (error) => {
        this.onerror?.(error);
      });
      if (this._stderrStream && this._process.stderr) {
        this._process.stderr.pipe(this._stderrStream);
      }
    });
  }
  /**
   * The stderr stream of the child process, if `StdioServerParameters.stderr` was set to "pipe" or "overlapped".
   *
   * If stderr piping was requested, a PassThrough stream is returned _immediately_, allowing callers to
   * attach listeners before the start method is invoked. This prevents loss of any early
   * error output emitted by the child process.
   */
  get stderr() {
    if (this._stderrStream) {
      return this._stderrStream;
    }
    return this._process?.stderr ?? null;
  }
  /**
   * The child process pid spawned by this transport.
   *
   * This is only available after the transport has been started.
   */
  get pid() {
    return this._process?.pid ?? null;
  }
  processReadBuffer() {
    while (true) {
      try {
        const message2 = this._readBuffer.readMessage();
        if (message2 === null) {
          break;
        }
        this.onmessage?.(message2);
      } catch (error) {
        this.onerror?.(error);
      }
    }
  }
  async close() {
    if (this._process) {
      const processToClose = this._process;
      this._process = void 0;
      const closePromise = new Promise((resolve) => {
        processToClose.once("close", () => {
          resolve();
        });
      });
      try {
        processToClose.stdin?.end();
      } catch {
      }
      await Promise.race([closePromise, new Promise((resolve) => setTimeout(resolve, 2e3).unref())]);
      if (processToClose.exitCode === null) {
        try {
          processToClose.kill("SIGTERM");
        } catch {
        }
        await Promise.race([closePromise, new Promise((resolve) => setTimeout(resolve, 2e3).unref())]);
      }
      if (processToClose.exitCode === null) {
        try {
          processToClose.kill("SIGKILL");
        } catch {
        }
      }
    }
    this._readBuffer.clear();
  }
  send(message2) {
    return new Promise((resolve) => {
      if (!this._process?.stdin) {
        throw new Error("Not connected");
      }
      const json = serializeMessage(message2);
      if (this._process.stdin.write(json)) {
        resolve();
      } else {
        this._process.stdin.once("drain", resolve);
      }
    });
  }
}
const BUILTIN_IDS = ["filesystem", "git", "fetch"];
const NO_WORKSPACE = "Pick a workspace folder first.";
const UVX_HINT = "Needs uv: install it from https://docs.astral.sh/uv/, then restart this server.";
const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const defaultExposure = () => ({
  enabled: true,
  allowed_agents: [],
  tool_allowlist: []
});
function childPath() {
  const home = os.homedir();
  const extra = [path$3.join(home, ".local", "bin"), path$3.join(home, ".cargo", "bin")];
  if (process.platform !== "win32") extra.push("/opt/homebrew/bin", "/usr/local/bin");
  const current = (process.env.PATH ?? "").split(path$3.delimiter).filter(Boolean);
  return [...current, ...extra.filter((dir) => !current.includes(dir))].join(path$3.delimiter);
}
function hasUvx() {
  return new Promise((resolve) => {
    execFile(
      "uvx",
      ["--version"],
      { env: { ...process.env, PATH: childPath() }, timeout: 1e4, windowsHide: true },
      (err) => resolve(!err)
    );
  });
}
function workspaceInfo(dir) {
  return { path: dir, name: path$3.basename(dir) || dir };
}
class McpConfigStore {
  constructor(file) {
    this.file = file;
    this.data = load(file);
  }
  data;
  get workspace() {
    return this.data.workspace ? workspaceInfo(this.data.workspace) : null;
  }
  setWorkspace(dir) {
    const resolved = path$3.resolve(dir);
    if (!fs$1.statSync(resolved, { throwIfNoEntry: false })?.isDirectory()) {
      throw new Error(`Not a folder: ${resolved}`);
    }
    this.data.workspace = resolved;
    this.save();
    return workspaceInfo(resolved);
  }
  specs(filesystemScript, uvx2) {
    const ws = this.data.workspace;
    const { filesystem, git, fetch: fetch2 } = this.data.builtins;
    const builtins = [
      {
        ...filesystem,
        id: "filesystem",
        name: "Filesystem",
        description: `Read and write files in ${ws ?? "the workspace folder"}`,
        builtin: true,
        transport: "stdio",
        command: process.execPath,
        args: ws ? [filesystemScript, ws] : [],
        env: { ELECTRON_RUN_AS_NODE: "1" },
        cwd: null,
        url: "",
        launch: "bundled @modelcontextprotocol/server-filesystem",
        unavailable: ws ? null : NO_WORKSPACE
      },
      {
        ...git,
        id: "git",
        name: "Git",
        description: `Git operations on ${ws ?? "the workspace folder"}`,
        builtin: true,
        transport: "stdio",
        command: "uvx",
        args: ws ? ["mcp-server-git", "--repository", ws] : [],
        env: {},
        cwd: null,
        url: "",
        launch: "uvx mcp-server-git",
        unavailable: !ws ? NO_WORKSPACE : uvx2 ? null : UVX_HINT
      },
      {
        ...fetch2,
        id: "fetch",
        name: "Web Fetch",
        description: "Fetch a URL and extract its content as markdown",
        builtin: true,
        transport: "stdio",
        command: "uvx",
        args: ["mcp-server-fetch"],
        env: {},
        cwd: null,
        url: "",
        launch: "uvx mcp-server-fetch",
        unavailable: uvx2 ? null : UVX_HINT
      }
    ];
    const custom = this.data.servers.map(
      (s) => ({
        ...s,
        builtin: false,
        launch: s.transport === "stdio" ? [s.command, ...s.args].join(" ") : s.url,
        unavailable: null
      })
    );
    return [...builtins, ...custom];
  }
  add(input) {
    const server = parseCustom(input);
    if (this.data.servers.some((s) => s.id === server.id)) {
      throw new Error(`A server called "${server.id}" already exists`);
    }
    this.data.servers.push(server);
    this.save();
  }
  // Exposure only (enable, agents, tool allow-list); to change how a custom
  // server launches, remove it and add it again.
  update(id, patch) {
    const changes = isObject(patch) ? patch : {};
    if (isBuiltin(id)) {
      this.data.builtins[id] = parseExposure(changes, this.data.builtins[id]);
    } else {
      const index = this.data.servers.findIndex((s) => s.id === id);
      if (index < 0) throw new Error(`No server called "${id}"`);
      const server = this.data.servers[index];
      this.data.servers[index] = { ...server, ...parseExposure(changes, server) };
    }
    this.save();
  }
  remove(id) {
    if (isBuiltin(id)) throw new Error("Built-in servers can be disabled but not removed");
    this.data.servers = this.data.servers.filter((s) => s.id !== id);
    this.save();
  }
  save() {
    fs$1.mkdirSync(path$3.dirname(this.file), { recursive: true });
    const tmp = `${this.file}.tmp`;
    fs$1.writeFileSync(tmp, JSON.stringify(this.data, null, 2));
    fs$1.renameSync(tmp, this.file);
  }
}
function isObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function isBuiltin(id) {
  return BUILTIN_IDS.includes(id);
}
const str = (value) => typeof value === "string" ? value : "";
const strList = (value) => Array.isArray(value) ? value.filter((v) => typeof v === "string") : [];
function strMap(value) {
  if (!isObject(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter((e) => typeof e[1] === "string")
  );
}
function parseExposure(input, base) {
  return {
    enabled: typeof input.enabled === "boolean" ? input.enabled : base.enabled,
    allowed_agents: Array.isArray(input.allowed_agents) ? strList(input.allowed_agents) : base.allowed_agents,
    tool_allowlist: Array.isArray(input.tool_allowlist) ? strList(input.tool_allowlist) : base.tool_allowlist
  };
}
function parseCustom(input) {
  if (!isObject(input)) throw new Error("Invalid server");
  const id = str(input.id).trim();
  if (!ID_RE.test(id) || id.includes("__")) {
    throw new Error('The id must be 1–64 letters, digits, "-" or "_", without "__"');
  }
  if (isBuiltin(id)) throw new Error(`"${id}" is a built-in server`);
  const transport = input.transport;
  if (transport !== "stdio" && transport !== "sse") {
    throw new Error("The transport must be stdio or sse");
  }
  const server = {
    id,
    transport,
    name: str(input.name).trim() || id,
    description: str(input.description),
    command: str(input.command).trim(),
    args: strList(input.args),
    env: strMap(input.env),
    cwd: str(input.cwd) || null,
    url: str(input.url).trim(),
    ...parseExposure(input, defaultExposure())
  };
  if (server.transport === "stdio" && !server.command) {
    throw new Error("A stdio server needs a command");
  }
  if (server.transport === "sse" && !/^https?:\/\//.test(server.url)) {
    throw new Error("An SSE server needs an http(s) URL");
  }
  return server;
}
function load(file) {
  const config = {
    version: 1,
    workspace: null,
    builtins: { filesystem: defaultExposure(), git: defaultExposure(), fetch: defaultExposure() },
    servers: []
  };
  let raw;
  try {
    raw = JSON.parse(fs$1.readFileSync(file, "utf8"));
  } catch {
    return config;
  }
  if (!isObject(raw)) return config;
  config.workspace = str(raw.workspace) || null;
  const builtins = isObject(raw.builtins) ? raw.builtins : {};
  for (const id of BUILTIN_IDS) {
    const stored = builtins[id];
    if (isObject(stored)) config.builtins[id] = parseExposure(stored, defaultExposure());
  }
  for (const entry of Array.isArray(raw.servers) ? raw.servers : []) {
    try {
      config.servers.push(parseCustom(entry));
    } catch (err) {
      console.warn("[mcp] skipping invalid server in config:", err);
    }
  }
  return config;
}
const BUILTIN_READ_ONLY = /^(read_|list_|get_|search_|directory_tree$|fetch$|git_(status|diff\w*|log|show|branch)$)/;
function needsConfirm(builtin, tool) {
  if (tool.readOnly) return false;
  return !(builtin && BUILTIN_READ_ONLY.test(tool.name));
}
class McpHost {
  constructor(onChange) {
    this.onChange = onChange;
  }
  entries = /* @__PURE__ */ new Map();
  // Bring the running servers in line with ``specs``: start new or changed ones,
  // stop removed or disabled ones. ``restartId`` restarts that server regardless.
  async sync(specs, restartId) {
    const wanted = new Set(specs.map((s) => s.id));
    for (const [id, entry] of this.entries) {
      if (!wanted.has(id)) {
        this.entries.delete(id);
        void this.disconnect(entry);
      }
    }
    const starts = [];
    for (const spec of specs) {
      const key = launchKey(spec);
      let entry = this.entries.get(spec.id);
      if (!entry) {
        entry = { spec, key, client: null, status: "stopped", error: null, tools: [], generation: 0 };
        this.entries.set(spec.id, entry);
      }
      const relaunch = entry.key !== key || spec.id === restartId;
      entry.spec = spec;
      entry.key = key;
      if (!spec.enabled || spec.unavailable) {
        await this.disconnect(entry);
        entry.status = spec.enabled ? "unavailable" : "disabled";
        entry.error = spec.enabled ? spec.unavailable : null;
        continue;
      }
      if (relaunch || ["stopped", "disabled", "unavailable"].includes(entry.status)) {
        starts.push(this.connect(entry));
      }
    }
    this.onChange();
    await Promise.all(starts);
  }
  views() {
    return [...this.entries.values()].map(({ spec, status, error, tools }) => ({
      id: spec.id,
      name: spec.name,
      description: spec.description,
      builtin: spec.builtin,
      transport: spec.transport,
      launch: spec.launch,
      enabled: spec.enabled,
      allowed_agents: spec.allowed_agents,
      tool_allowlist: spec.tool_allowlist,
      status,
      error,
      tools: tools.map((t) => ({
        name: t.name,
        description: t.description,
        confirm: needsConfirm(spec.builtin, t)
      }))
    }));
  }
  inventory() {
    return [...this.entries.values()].filter((e) => e.client !== null && e.status === "connected").map(({ spec, tools }) => ({
      id: spec.id,
      name: spec.name,
      allowed_agents: spec.allowed_agents,
      tool_allowlist: spec.tool_allowlist,
      tools: tools.map((t) => ({
        name: t.name,
        description: t.description,
        input_schema: t.inputSchema
      }))
    }));
  }
  // The tool, if it's running and exposed to ``agentId`` on this machine. The
  // backend filters too; this re-checks against the local config.
  findTool(serverId, toolName, agentId) {
    const entry = this.entries.get(serverId);
    if (!entry?.client || entry.status !== "connected") return null;
    const { spec } = entry;
    if (spec.allowed_agents.length && !spec.allowed_agents.includes(agentId)) return null;
    if (spec.tool_allowlist.length && !spec.tool_allowlist.includes(toolName)) return null;
    const tool = entry.tools.find((t) => t.name === toolName);
    return tool ? { spec, tool } : null;
  }
  async call(serverId, toolName, args) {
    const client = this.entries.get(serverId)?.client;
    if (!client) throw new Error(`The ${serverId} server isn't running`);
    const result = await client.callTool({ name: toolName, arguments: args });
    const text = resultText(result);
    if (result.isError) {
      throw new Error(text || `${toolName} failed`);
    }
    return text;
  }
  async closeAll() {
    await Promise.all([...this.entries.values()].map((e) => this.disconnect(e)));
  }
  // ── internals ───────────────────────────────────────────────────────────────
  async connect(entry) {
    await this.disconnect(entry);
    const generation = entry.generation;
    const { spec } = entry;
    entry.status = "connecting";
    entry.error = null;
    this.onChange();
    let stderr = "";
    const client = new Client({ name: "agentide-desktop", version: app.getVersion() });
    try {
      let transport;
      if (spec.transport === "sse") {
        transport = new SSEClientTransport(new URL(spec.url));
      } else {
        const stdio = new StdioClientTransport({
          command: spec.command,
          args: spec.args,
          env: { ...getDefaultEnvironment(), PATH: childPath(), ...spec.env },
          cwd: spec.cwd ?? void 0,
          stderr: "pipe"
        });
        stdio.stderr?.on("data", (chunk) => {
          stderr = (stderr + chunk.toString()).slice(-2e3);
        });
        transport = stdio;
      }
      await client.connect(transport);
      const tools = await listTools(client);
      if (generation !== entry.generation) {
        await client.close().catch(() => void 0);
        return;
      }
      client.onclose = () => {
        if (entry.client !== client) return;
        entry.client = null;
        entry.tools = [];
        entry.status = "error";
        entry.error = describe(new Error("The server stopped"), spec, stderr);
        this.onChange();
      };
      entry.client = client;
      entry.tools = tools;
      entry.status = "connected";
    } catch (err) {
      await client.close().catch(() => void 0);
      if (generation !== entry.generation) return;
      entry.status = "error";
      entry.error = describe(err, spec, stderr);
    }
    this.onChange();
  }
  async disconnect(entry) {
    entry.generation += 1;
    const client = entry.client;
    entry.client = null;
    entry.tools = [];
    if (entry.status === "connected" || entry.status === "connecting") entry.status = "stopped";
    if (client) await client.close().catch(() => void 0);
  }
}
function launchKey(spec) {
  const { transport, command, args, env, cwd, url: url2 } = spec;
  return JSON.stringify({ transport, command, args, env, cwd, url: url2 });
}
async function listTools(client) {
  const tools = [];
  let cursor;
  do {
    const page = await client.listTools(cursor ? { cursor } : void 0);
    for (const tool of page.tools) {
      tools.push({
        name: tool.name,
        description: tool.description ?? "",
        inputSchema: tool.inputSchema,
        readOnly: tool.annotations?.readOnlyHint === true
      });
    }
    cursor = page.nextCursor;
  } while (cursor);
  return tools;
}
function resultText(result) {
  const r = result;
  const blocks = Array.isArray(r.content) ? r.content : [];
  const parts = blocks.map((block) => {
    const b = block;
    if (b.type === "text" && typeof b.text === "string") return b.text;
    if (b.type === "resource" && b.resource) return b.resource.text ?? b.resource.uri ?? "";
    if (b.type === "resource_link" && b.uri) return b.uri;
    return `[${b.type ?? "unknown"} content${b.mimeType ? ` (${b.mimeType})` : ""} omitted]`;
  });
  if (!parts.length && r.structuredContent !== void 0) {
    return JSON.stringify(r.structuredContent);
  }
  return parts.join("\n");
}
function describe(err, spec, stderr) {
  let message2 = err instanceof Error ? err.message : String(err);
  if (/ENOENT/.test(message2)) message2 = `Command not found: ${spec.command}`;
  const tail = stderr.trim().split(/\r?\n/).slice(-4).join("\n");
  return tail ? `${message2}
${tail}` : message2;
}
const WS_UNAUTHORIZED = 4401;
const WS_REPLACED = 4409;
const PING_MS = 25e3;
const MAX_BACKOFF_MS = 3e4;
const message = (err) => err instanceof Error ? err.message : String(err);
class McpBridge {
  constructor(opts) {
    this.opts = opts;
  }
  ws = null;
  running = false;
  epoch = 0;
  // bumped on every connect/teardown; stale callbacks back off
  status = "signedOut";
  detail = null;
  attempt = 0;
  forceRefresh = false;
  reconnectTimer;
  pushTimer;
  pingTimer;
  lastInventory = "";
  inflight = /* @__PURE__ */ new Map();
  alwaysAllowed = /* @__PURE__ */ new Set();
  confirmQueue = Promise.resolve();
  get state() {
    return { status: this.status, detail: this.detail };
  }
  // Called after every successful sign-in or token refresh; connects once.
  start() {
    if (this.running) return;
    this.running = true;
    this.attempt = 0;
    void this.connect();
  }
  // Sign-out: disconnect, stop retrying, and forget "always allow" approvals.
  stop() {
    this.running = false;
    this.alwaysAllowed.clear();
    this.teardown();
    this.setStatus("signedOut", null);
  }
  // The user asked to use this device's tools (e.g. after another window took over).
  reconnect() {
    if (!this.running) {
      this.start();
      return;
    }
    this.attempt = 0;
    this.teardown();
    void this.connect();
  }
  // The local servers changed; tell the backend (coalesced, and only real changes).
  pushInventory() {
    clearTimeout(this.pushTimer);
    this.pushTimer = setTimeout(() => this.sendInventory(), 150);
  }
  // ── connection ──────────────────────────────────────────────────────────────
  async connect() {
    clearTimeout(this.reconnectTimer);
    const epoch = ++this.epoch;
    this.setStatus("connecting", null);
    let token;
    try {
      token = await this.opts.getToken(this.forceRefresh);
      this.forceRefresh = false;
    } catch (err) {
      if (epoch === this.epoch) this.retry(`Can't reach the backend: ${message(err)}`);
      return;
    }
    if (epoch !== this.epoch || !this.running) return;
    if (!token) {
      this.running = false;
      this.setStatus("signedOut", null);
      return;
    }
    const ws = new WebSocket(`${this.opts.apiBase.replace(/^http/, "ws")}/mcp/bridge/ws`);
    this.ws = ws;
    this.lastInventory = "";
    ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token }));
    ws.onmessage = (event) => this.onMessage(ws, event.data);
    ws.onclose = (event) => this.onClose(ws, event.code, event.reason);
  }
  onMessage(ws, data) {
    if (ws !== this.ws) return;
    let frame;
    try {
      frame = JSON.parse(String(data));
    } catch {
      return;
    }
    switch (frame.type) {
      case "ready":
        this.attempt = 0;
        this.setStatus("connected", null);
        this.sendInventory();
        clearInterval(this.pingTimer);
        this.pingTimer = setInterval(() => this.send(ws, { type: "ping" }), PING_MS);
        break;
      case "call":
        void this.handleCall(ws, frame);
        break;
      case "cancel":
        if (frame.id) this.inflight.get(frame.id)?.abort();
        break;
      case "error":
        console.warn("[mcp-bridge] backend says:", frame.message);
        break;
    }
  }
  onClose(ws, code, reason) {
    if (ws !== this.ws) return;
    this.ws = null;
    clearInterval(this.pingTimer);
    this.abortInflight();
    if (!this.running) return;
    if (code === WS_REPLACED) {
      this.setStatus(
        "replaced",
        "Agents are using the tools of another AgentIDE window or device on this account."
      );
      return;
    }
    if (code === WS_UNAUTHORIZED) this.forceRefresh = true;
    this.retry(reason || "Connection lost");
  }
  retry(detail) {
    if (!this.running) return;
    this.setStatus("offline", detail);
    const delay = Math.min(MAX_BACKOFF_MS, 1e3 * 2 ** this.attempt) * (0.5 + Math.random() / 2);
    this.attempt += 1;
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => void this.connect(), delay);
  }
  teardown() {
    this.epoch += 1;
    clearTimeout(this.reconnectTimer);
    clearTimeout(this.pushTimer);
    clearInterval(this.pingTimer);
    this.abortInflight();
    const ws = this.ws;
    this.ws = null;
    if (ws && ws.readyState <= WebSocket.OPEN) ws.close(1e3);
  }
  abortInflight() {
    for (const controller of this.inflight.values()) controller.abort();
  }
  send(ws, frame) {
    if (ws === this.ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(frame));
  }
  sendInventory() {
    const ws = this.ws;
    if (!ws || this.status !== "connected" || ws.readyState !== WebSocket.OPEN) return;
    const frame = JSON.stringify({ type: "inventory", servers: this.opts.host.inventory() });
    if (frame === this.lastInventory) return;
    this.lastInventory = frame;
    ws.send(frame);
  }
  setStatus(status, detail) {
    if (status === this.status && detail === this.detail) return;
    this.status = status;
    this.detail = detail;
    this.opts.onChange();
  }
  // ── tool calls ──────────────────────────────────────────────────────────────
  async handleCall(ws, frame) {
    const controller = new AbortController();
    this.inflight.set(frame.id, controller);
    let reply;
    try {
      const found = this.opts.host.findTool(frame.server_id, frame.tool, frame.agent_id);
      if (!found) {
        throw new Error(
          `${frame.server_id}/${frame.tool} isn't available to the ${frame.agent_id} agent on this computer.`
        );
      }
      const args = frame.arguments ?? {};
      const key = `${frame.server_id}/${frame.tool}`;
      if (needsConfirm(found.spec.builtin, found.tool) && !this.alwaysAllowed.has(key)) {
        const decision = await this.confirmInTurn(
          { agentId: frame.agent_id, serverName: found.spec.name, tool: frame.tool, args },
          controller.signal
        );
        if (controller.signal.aborted) throw new Error("Cancelled: the agent stopped waiting.");
        if (decision === "deny") throw new Error("The user declined this tool call.");
        if (decision === "always") this.alwaysAllowed.add(key);
      }
      const content = await this.opts.host.call(frame.server_id, frame.tool, args);
      reply = { type: "result", id: frame.id, ok: true, content };
    } catch (err) {
      reply = { type: "result", id: frame.id, ok: false, error: message(err) };
    } finally {
      this.inflight.delete(frame.id);
    }
    this.send(ws, reply);
  }
  // One dialog at a time; a call cancelled while it waits never shows one.
  confirmInTurn(request, signal) {
    const turn = this.confirmQueue.then(
      () => signal.aborted ? "deny" : this.opts.confirm(request, signal)
    );
    this.confirmQueue = turn.catch(() => void 0);
    return turn;
  }
}
const __dirname$1 = path$3.dirname(fileURLToPath(import.meta.url));
process.env.APP_ROOT = path$3.join(__dirname$1, "..");
const VITE_DEV_SERVER_URL = process.env["VITE_DEV_SERVER_URL"];
const MAIN_DIST = path$3.join(process.env.APP_ROOT, "dist-electron");
const RENDERER_DIST = path$3.join(process.env.APP_ROOT, "dist");
process.env.VITE_PUBLIC = VITE_DEV_SERVER_URL ? path$3.join(process.env.APP_ROOT, "public") : RENDERER_DIST;
const APP_SCHEME = "app";
const APP_HOST = "agentide";
protocol.registerSchemesAsPrivileged([
  { scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true } }
]);
function serveRenderer(request) {
  const { host, pathname } = new URL(request.url);
  if (host !== APP_HOST) return new Response("Not found", { status: 404 });
  const decoded = decodeURIComponent(pathname);
  const file = path$3.join(RENDERER_DIST, decoded === "/" ? "index.html" : decoded);
  const inside = path$3.relative(RENDERER_DIST, file);
  if (inside.startsWith("..") || path$3.isAbsolute(inside)) {
    return new Response("Forbidden", { status: 403 });
  }
  return net.fetch(pathToFileURL(file).toString());
}
let win;
function createWindow() {
  win = new BrowserWindow({
    icon: path$3.join(process.env.VITE_PUBLIC, "electron-vite.svg"),
    webPreferences: {
      preload: path$3.join(__dirname$1, "preload.mjs"),
      // The preload exposes this as window.api.baseUrl.
      additionalArguments: [`--agentide-api-url=${API_BASE}`]
    }
  });
  win.webContents.on("did-finish-load", () => {
    win?.webContents.send("main-process-message", (/* @__PURE__ */ new Date()).toLocaleString());
  });
  if (VITE_DEV_SERVER_URL) {
    win.loadURL(VITE_DEV_SERVER_URL);
  } else {
    win.loadURL(`${APP_SCHEME}://${APP_HOST}/`);
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
let mcpConfig = null;
let uvx = false;
const mcpHost = new McpHost(onMcpChange);
const mcpBridge = new McpBridge({
  apiBase: API_BASE,
  getToken: accessToken,
  host: mcpHost,
  confirm: confirmToolCall,
  onChange: onMcpChange
});
function mcpStore() {
  mcpConfig ??= new McpConfigStore(path$3.join(app.getPath("userData"), "mcp-servers.json"));
  return mcpConfig;
}
function mcpState() {
  return {
    workspace: mcpStore().workspace,
    uvx,
    bridge: mcpBridge.state,
    servers: mcpHost.views()
  };
}
function onMcpChange() {
  if (win && !win.isDestroyed()) win.webContents.send("mcp:changed", mcpState());
  mcpBridge.pushInventory();
}
function filesystemServerScript() {
  return path$3.join(MAIN_DIST, "mcp-filesystem.js").replace(`app.asar${path$3.sep}`, `app.asar.unpacked${path$3.sep}`);
}
async function applyMcpConfig(restartId) {
  uvx = await hasUvx();
  await mcpHost.sync(mcpStore().specs(filesystemServerScript(), uvx), restartId);
}
async function confirmToolCall(request, signal) {
  const args = JSON.stringify(request.args, null, 2);
  const options = {
    type: "question",
    buttons: ["Allow", "Deny"],
    defaultId: 1,
    cancelId: 1,
    noLink: true,
    title: "Allow tool call?",
    message: `The ${request.agentId} agent wants to run ${request.tool} (${request.serverName}) on this computer.`,
    detail: args.length > 1500 ? `${args.slice(0, 1500)}
…` : args,
    checkboxLabel: `Always allow ${request.tool} until I sign out`,
    signal
    // the backend gave up waiting: close the dialog (counts as Deny)
  };
  const parent = win && !win.isDestroyed() ? win : null;
  if (parent && !parent.isFocused()) parent.flashFrame(true);
  const { response, checkboxChecked } = parent ? await dialog.showMessageBox(parent, options) : await dialog.showMessageBox(options);
  parent?.flashFrame(false);
  if (response !== 0) return "deny";
  return checkboxChecked ? "always" : "allow";
}
const runMcp = (action) => {
  action();
  void applyMcpConfig();
  return mcpState();
};
ipcMain.handle("mcp:get", () => mcpState());
ipcMain.handle("mcp:add", (_event, server) => runMcp(() => mcpStore().add(server)));
ipcMain.handle(
  "mcp:update",
  (_event, id, patch) => runMcp(() => mcpStore().update(String(id), patch))
);
ipcMain.handle("mcp:remove", (_event, id) => runMcp(() => mcpStore().remove(String(id))));
ipcMain.handle("mcp:restart", (_event, id) => {
  void applyMcpConfig(String(id));
  return mcpState();
});
ipcMain.handle("mcp:reconnect", () => {
  mcpBridge.reconnect();
  return mcpState();
});
ipcMain.handle("workspace:get", () => mcpStore().workspace);
ipcMain.handle("workspace:pick", async () => {
  const options = {
    title: "Select workspace folder",
    properties: ["openDirectory", "createDirectory"]
  };
  const result = win && !win.isDestroyed() ? await dialog.showOpenDialog(win, options) : await dialog.showOpenDialog(options);
  if (result.canceled || result.filePaths.length === 0) return null;
  const workspace = mcpStore().setWorkspace(result.filePaths[0]);
  void applyMcpConfig();
  return workspace;
});
ipcMain.handle("auth:login", async () => {
  const session = await login();
  mcpBridge.start();
  return session;
});
ipcMain.handle("auth:refresh", async () => {
  const session = await refresh();
  if (session) mcpBridge.start();
  else mcpBridge.stop();
  return session;
});
ipcMain.handle("auth:logout", async () => {
  mcpBridge.stop();
  await logout();
});
let quitting = false;
app.on("before-quit", (event) => {
  if (quitting) return;
  quitting = true;
  event.preventDefault();
  mcpBridge.stop();
  const deadline = new Promise((resolve) => setTimeout(resolve, 3e3));
  void Promise.race([mcpHost.closeAll(), deadline]).finally(() => app.quit());
});
app.whenReady().then(() => {
  protocol.handle(APP_SCHEME, serveRenderer);
  void applyMcpConfig();
  createWindow();
});
export {
  MAIN_DIST,
  RENDERER_DIST,
  VITE_DEV_SERVER_URL
};
