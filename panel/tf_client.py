"""AsyncOpenAI wrapper: chat_json(model, system, user, schema, temperature,
seed, max_tokens) with retries and JSON enforcement. See section 5.1.

Owned by Agent A, stage 5. Verified empirically against the live Token
Factory catalog on 2026-10-27 (see MEMORY) before this gets implemented:

- All three models (Nano, Super, Ultra) accept response_format as a
  json_schema and return it cleanly in message.content, no code-fence
  stripping needed for the happy path.
- Ultra is a reasoning model: it spends completion tokens on hidden
  chain-of-thought in a separate message.reasoning_content field before
  content. A max_tokens budget sized only for the final JSON object will
  truncate Ultra mid-reasoning (finish_reason == "length") before it ever
  emits content. Budget headroom for reasoning on top of the ~2000 tokens
  the synthesis object itself needs, and treat "length" truncation as a
  distinct retry case from "content didn't validate" — it means "ran out
  of budget," not "model got the shape wrong."
"""
