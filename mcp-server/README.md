# SLO MCP Server

This is the MCP (Model Context Protocol) server for SLO.  
It allows MCP-compatible clients such as Claude Desktop to remotely create and edit SLO documents.

---

## 📁 Directory

The MCP server is included when you clone the SLO repository.

```bash
git clone https://github.com/IkJun1/SLO.git
```

```
SLO/
└── mcp-server/
    └── mcp_server.py
```

---

## ⚙️ Environment Variables

The MCP server requires the following two environment variables.

| Variable | Description |
|---|---|
| `SLO_API_BASE` | The base URL of your SLO server. If you are running SLO on an external domain or IP (not `localhost:8000`), enter that address here. (e.g. `https://example.com`) |
| `MCP_API_KEY` | The MCP authentication key. Use the same value as the `MCP_API_KEY` set in the `.env` file of the main SLO project. |

---

## 🚀 Usage

Add the following configuration to your MCP-compatible client (e.g. Claude Desktop).

- **`command`** — Absolute path to the Python executable with dependencies installed  
  e.g. `SLO/.venv/bin/python3`
- **`args`** — Absolute path to the MCP server script  
  e.g. `SLO/mcp-server/mcp_server.py`

### Configuration Example

```jsonc
{
    "mcpServers": {
        "SLO": {
            "command": "/absolute/path/to/SLO/.venv/bin/python3",
            "args": [
                "/absolute/path/to/SLO/mcp-server/mcp_server.py"
            ],
            "env": {
                "MCP_API_KEY": "your-mcp-api-key",
                "SLO_API_BASE": "https://example.com"
            }
        }
    }
}
```

> **💡 Running locally?**  
> If SLO is running on `localhost:8000`, you can omit `SLO_API_BASE` or set it to `http://localhost:8000`.

---

## 🔗 Related Links

- [SLO Main Repository](https://github.com/IkJun1/SLO)