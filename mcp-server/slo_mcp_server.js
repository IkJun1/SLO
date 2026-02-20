const MCP_INSTRUCTIONS = `
SLO MCP Tool Usage Policy
1. Documents must always be created with .md extension.
2. For document read, update, delete, move, and create requests, call tree first to confirm path structure before executing other tools.
3. If the user does not provide an exact target path for document creation, infer a reasonable path based on the request and create there.
4. When needed for document creation, it is allowed to create missing parent folders and then create the document at the requested path.
5. Do not modify paths outside the user-requested scope.
6. After any document read, update, delete, move, or create operation, explicitly report the exact path used.
7. Run delete_doc or move_doc with overwrite=true only when user intent is explicit.
`.trim()

const API_BASE = (process.env.SLO_API_BASE || "http://127.0.0.1:8000").replace(/\/+$/, "")
let API_PREFIX = (process.env.SLO_MCP_API_PREFIX || "/api/v1/mcp").trim()
const REQUEST_TIMEOUT_MS = Number(process.env.SLO_MCP_TIMEOUT_MS || "30000")

if (!API_PREFIX.startsWith("/")) {
  API_PREFIX = `/${API_PREFIX}`
}
API_PREFIX = API_PREFIX.replace(/\/+$/, "")

function loadSdk() {
  try {
    return require("@modelcontextprotocol/server")
  } catch (_err) {
    const { McpServer } = require("@modelcontextprotocol/sdk/server/mcp.js")
    const { StdioServerTransport } = require("@modelcontextprotocol/sdk/server/stdio.js")
    return { McpServer, StdioServerTransport }
  }
}

const { McpServer, StdioServerTransport } = loadSdk()
const z = require("zod")

const server = new McpServer({
  name: "slo-mcp",
  version: "0.1.0",
  instructions: MCP_INSTRUCTIONS
})

function getMcpApiKey() {
  const key = (process.env.MCP_API_KEY || "").trim()
  if (key === "") {
    throw new Error("MCP_API_KEY is required")
  }
  return key
}

function buildUrl(path, params = null) {
  const normalizedPath = `/${String(path || "").replace(/^\/+/, "")}`
  const url = new URL(`${API_BASE}${API_PREFIX}${normalizedPath}`)
  if (params && typeof params === "object") {
    for (const [key, value] of Object.entries(params)) {
      if (value === null || value === undefined) {
        continue
      }
      if (typeof value === "boolean") {
        url.searchParams.set(key, value ? "true" : "false")
        continue
      }
      url.searchParams.set(key, String(value))
    }
  }
  return url.toString()
}

async function request(method, path, params = null, payload = null) {
  const url = buildUrl(path, params)
  const headers = {
    Authorization: `Bearer ${getMcpApiKey()}`
  }
  let body
  if (payload !== null && payload !== undefined) {
    headers["Content-Type"] = "application/json"
    body = JSON.stringify(payload)
  }

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch(url, {
      method,
      headers,
      body,
      signal: controller.signal
    })
    const raw = await response.text()
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${raw}`)
    }
    if (raw.trim() === "") {
      return {}
    }
    try {
      return JSON.parse(raw)
    } catch (_err) {
      return { text: raw }
    }
  } finally {
    clearTimeout(timer)
  }
}

function normalizeToolResult(result) {
  if (result !== null && typeof result === "object") {
    return {
      content: [{ type: "text", text: JSON.stringify(result) }],
      structuredContent: result
    }
  }
  return {
    content: [{ type: "text", text: String(result) }]
  }
}

function registerTool(name, description, inputShape, handler) {
  if (typeof server.registerTool === "function") {
    server.registerTool(
      name,
      {
        description,
        inputSchema: z.object(inputShape)
      },
      async (args) => normalizeToolResult(await handler(args))
    )
    return
  }

  server.tool(
    name,
    description,
    inputShape,
    async (args) => normalizeToolResult(await handler(args))
  )
}

registerTool(
  "tree",
  "Return the vault tree view as text, optionally limited by depth and path prefix.",
  {
    depth: z.number().int().min(0).optional(),
    path_prefix: z.string().default("")
  },
  async ({ depth, path_prefix }) => request("GET", "/tree", { depth, path_prefix })
)

registerTool(
  "list_docs",
  "List active documents with metadata, optionally filtered to a path subtree.",
  {
    path_prefix: z.string().optional()
  },
  async ({ path_prefix }) => request("GET", "/docs", { path_prefix })
)

registerTool(
  "read_doc",
  "Read a single markdown document by vault-relative path and return full content.",
  {
    path: z.string().min(1)
  },
  async ({ path }) => request("GET", "/docs/by-path", { path })
)

registerTool(
  "create_doc",
  "Create a markdown document at path with optional title/content and parent folder creation.",
  {
    path: z.string().min(1),
    content: z.string().default(""),
    title: z.string().nullable().optional(),
    create_parents: z.boolean().default(false),
    overwrite: z.boolean().default(false)
  },
  async ({ path, content, title, create_parents, overwrite }) =>
    request("POST", "/docs", null, { path, content, title, create_parents, overwrite })
)

registerTool(
  "update_doc",
  "Update document content by path, with optional optimistic hash check and change reason.",
  {
    path: z.string().min(1),
    content: z.string(),
    title: z.string().nullable().optional(),
    expected_hash: z.string().nullable().optional(),
    reason: z.string().nullable().optional()
  },
  async ({ path, content, title, expected_hash, reason }) =>
    request("PUT", "/docs/by-path", { path }, { content, title, expected_hash, reason })
)

registerTool(
  "delete_doc",
  "Delete a document by path and move it to trash with an optional deletion reason.",
  {
    path: z.string().min(1),
    reason: z.string().nullable().optional()
  },
  async ({ path, reason }) => request("DELETE", "/docs/by-path", { path }, reason == null ? null : { reason })
)

registerTool(
  "move_doc",
  "Move or rename a document from one path to another, optionally overwriting target.",
  {
    from_path: z.string().min(1),
    to_path: z.string().min(1),
    overwrite: z.boolean().default(false)
  },
  async ({ from_path, to_path, overwrite }) => {
    const source = await request("GET", "/docs/by-path", { path: from_path })
    const docId = String(source.id || "").trim()
    if (docId === "") {
      throw new Error("failed to resolve doc_id from from_path")
    }
    return request("POST", "/docs/move", null, {
      doc_id: docId,
      to_path,
      overwrite
    })
  }
)

registerTool(
  "search",
  "Search document chunks by query with keyword/vector mode and optional subtree filter.",
  {
    q: z.string().min(1),
    mode: z.string().default("keyword"),
    top_k: z.number().int().min(1).max(100).default(20),
    chunk_size: z.number().int().min(100).max(8000).default(800),
    chunk_overlap: z.number().int().min(0).max(4000).default(120),
    path_prefix: z.string().optional()
  },
  async ({ q, mode, top_k, chunk_size, chunk_overlap, path_prefix }) =>
    request("GET", "/search", { q, mode, top_k, chunk_size, chunk_overlap, path_prefix })
)

async function main() {
  const transport = new StdioServerTransport()
  await server.connect(transport)
}

main().catch((err) => {
  process.stderr.write(`${String(err && err.stack ? err.stack : err)}\n`)
  process.exit(1)
})
