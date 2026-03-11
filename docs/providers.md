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
