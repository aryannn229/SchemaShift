# AI advisory layer

The LLM only gives a **second opinion** on embed vs reference. The optimizer's decision is final; both are stored.

- `LLMAdvisor` protocol (`ai/base.py`): `suggest_placement(rel, schema_excerpt) -> AISuggestion{decision, confidence, justification}`.
- `AnthropicAdvisor`: Anthropic SDK, model from `LLM_MODEL` (default `claude-opus-5-5`, `effort: low`), 15 s timeout, strict JSON
  validated with pydantic, **one retry** on invalid JSON, then the opinion is marked `unavailable`.
- `MockAdvisor`: deterministic rule of thumb, used in tests and whenever `ANTHROPIC_API_KEY` is empty. Its opinion has
  `available=false`, the UI shows "AI advisor disabled" and it never counts toward the agreement rate. The whole app works without a key.
- `attach_opinions(plan, features, schema, advisor, cache)`: bounded concurrency (max 5 threads), per-call timeout, failures and
  advisor bugs become `AIOpinion(source="unavailable")` and never break a compile. `agree` compares EMBED vs not-EMBED.
- Cache key = sha256(prompt template version, model, prompt). The in-memory cache is the default; the app database
  (`ai_cache`) implements the same protocol in Phase 10. Failed calls are not cached.
- **Privacy:** the prompt contains table structure and numeric features only. Seed data and row values are never sent (tested).
  The UI warns users not to paste confidential schemas.
- Self-references are not asked about (they are always REFERENCE).
- Tests use fake clients / `MockAdvisor`; one `@pytest.mark.live` test (skipped by default) calls the real API when `ANTHROPIC_API_KEY` is set.
