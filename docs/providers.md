# Provider Abstraction Layer

Phase 6A introduces a unified model provider abstraction.

## Architecture

```
Settings UI  -->  /api/settings/providers  -->  SQLite (provider_settings)
                                                     |
                                                     v
Test / Use   -->  /api/provider-test       -->  ProviderRegistry
                  /api/providers                     |
                  /api/models               +--------+--------+
                                            |        |        |
                                       Anthropic  OpenAI*  Gemini
                                                  (+ DeepSeek, Kimi, MiniMax)
```

## Provider Interface

All providers implement `BaseProvider`:

- `complete(request) -> response` — Send a completion request
- `list_models() -> list[ModelInfo]` — Available models
- `healthcheck() -> HealthCheckResult` — Test connectivity + key validity

## Supported Providers

| Provider | Name | SDK | Notes |
|----------|------|-----|-------|
| Anthropic | `anthropic` | `anthropic` | Claude models |
| OpenAI | `openai` | `openai` | GPT models |
| DeepSeek | `deepseek` | `openai` | OpenAI-compatible |
| Kimi | `kimi` | `openai` | Moonshot API |
| MiniMax | `minimax` | `openai` | ABAB models |
| Gemini | `gemini` | `google-genai` | Google models |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/providers` | List all registered providers |
| GET | `/api/models` | List all models (optional `?provider=` filter) |
| POST | `/api/provider-test` | Test connectivity for a provider |
| GET | `/api/settings/providers` | Read settings (API keys masked) |
| PATCH | `/api/settings/providers` | Save settings (partial update) |

## Security

- API keys stored in local SQLite only, never in git-tracked files
- GET responses mask keys: `sk-****1234`
- Logs never contain full API keys
- `mask_api_key()` in `providers/base.py` handles masking

## Settings Persistence

Table `provider_settings` (schema V4):

```sql
provider_name TEXT PRIMARY KEY,
api_key       TEXT NOT NULL DEFAULT '',
base_url      TEXT NOT NULL DEFAULT '',
enabled       INTEGER NOT NULL DEFAULT 0,
config_json   TEXT NOT NULL DEFAULT '{}',
created_at    TEXT,
updated_at    TEXT
```

## Configuration Flow

1. User enters API key in Settings panel
2. Frontend calls `PATCH /api/settings/providers`
3. Backend saves to SQLite, re-applies to live ProviderRegistry
4. User clicks "Test Connection"
5. Frontend calls `POST /api/provider-test`
6. Backend runs `provider.healthcheck()` (minimal API call)
7. Result displayed in UI

## Adding a New Provider

1. Create `providers/new_provider.py` implementing `BaseProvider`
2. Register in `providers/registry.py` `_build_default_providers()`
3. Provider will automatically appear in API and Settings UI

## Agent Integration (Phase 6B)

Providers are used by the `ModelAgentExecutor` to make real LLM calls during orchestration:

```
Orchestrator → _get_executor(PLANNER) → ModelAgentExecutor
  → ProviderRegistry.get("anthropic") → provider.complete(request)
  → Parse JSON → PlannerOutputSchema.validate() → ExecutionResult
```

### Manual Completion Endpoint

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/completion` | Send arbitrary prompt to a configured provider |

Request body:
```json
{
  "provider": "anthropic",
  "model": "claude-3-5-haiku-20241022",
  "prompt": "Your prompt here",
  "system_prompt": "Optional system prompt",
  "max_tokens": 1024,
  "temperature": 0.7
}
```

Errors: 404 (unknown provider), 400 (not configured), 502 (provider error).

## Role-Level Model Routing (Phase 6C)

Phase 6C introduces config-driven per-role model routing. Each agent role independently selects its provider and model via the `role_model_settings` database table.

### Architecture

```
Settings UI (Role Model Config)
    |
    v
PATCH /api/settings/role-models  -->  SQLite (role_model_settings)
                                              |
GET /api/settings/role-models   <-------------+
                                              |
Orchestrator                                  v
  └─ executor selection  <──  role_model_settings.enabled?
       |                           |
       ├── enabled=true  ──> ModelAgentExecutor
       |                       └── _resolve_provider() reads from DB
       |                       └── ProviderRegistry.get(cfg.provider)
       |                       └── provider.complete(model=cfg.model)
       └── enabled=false ──> MockAgentExecutor
```

### role_model_settings Table (Schema V5)

```sql
role       TEXT PRIMARY KEY,  -- planner, builder, qa, reviewer
provider   TEXT NOT NULL DEFAULT 'mock',
model      TEXT NOT NULL DEFAULT '',
enabled    INTEGER NOT NULL DEFAULT 0,
created_at TEXT,
updated_at TEXT
```

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/settings/role-models` | Read all role-model mappings |
| PATCH | `/api/settings/role-models` | Update role-model mappings (partial) |

### PATCH Validation Rules

- `role` must be a valid AgentRole (planner, builder, qa, reviewer)
- When `enabled=true`: `provider` and `model` must be non-empty
- `provider` must be a registered provider name
- **Phase 6D**: `planner`, `builder`, and `reviewer` can be enabled for real models
- **QA**: Attempts to enable real models are rejected with 400
- **Builder**: Enabled in plan-only mode (no tool execution)

### Dual-Model Support

Same provider, different models per role:

```json
{
  "role_models": [
    {"role": "planner",  "provider": "anthropic", "model": "claude-sonnet-4-20250514"},
    {"role": "builder",  "provider": "anthropic", "model": "claude-3-5-haiku-20241022"},
    {"role": "reviewer", "provider": "anthropic", "model": "claude-3-5-haiku-20241022"}
  ]
}
```

### Truth Source

`role_model_settings` is the **sole runtime truth source** for model routing.
`definitions.py` only provides system prompts, role metadata, and seed defaults
for the V5 migration. It is NOT consulted at runtime for provider/model selection.

### Builder Plan-Only Mode (Phase 6D)

Builder can be enabled for real model calls but operates in **plan-only mode**:
- Outputs structured change plan (proposed_files, change_steps, reasoning, validation)
- Does NOT execute file modifications, shell commands, or git operations
- Does NOT call the Tool Layer or trigger ApprovalRequests
- `action: "delete"` in proposed_files is a proposal only, not executable
