# Content authoring guide

Realistic filler content (emails, support cases, web articles, workspace docs) lives in
[`simulation/content/`](../content) as YAML. You (or any AI) add entries there; the schema rejects
bad ones; the packs are then re-frozen and re-validated automatically.

| File | Used by | Fields |
|---|---|---|
| `emails.yaml` | productivity inbox | `sender_name, address, subject, body, days_ago` |
| `support_cases.yaml` | billing DB `support_cases` | `subject, description, status, team, private_notes, customer_text` |
| `web_articles.yaml` | web corpus + search index | `topic, title, domain, author, published, body` |
| `file_docs.yaml` | files workspace (`/workspace/docs/`) | `name, sensitivity, content` |

## Workflow

1. Ask an AI to write a batch (prompt below) into `content/incoming.yaml`.
2. `uv run python -m toolsafesim.fixtures.content_cli merge content/incoming.yaml emails.yaml`
   - validates every row (schema + realism guards + secret scan + duplicates),
   - appends to the target file only if **all** rows pass,
   - re-freezes the packs and updates `fixtures/manifest.json`.
3. `uv run pytest` (Phase 2 gate re-checks integrity and the secret scan).
4. Commit `content/` and `fixtures/` together.

Check without changing anything: `uv run python -m toolsafesim.fixtures.content_cli validate`.

## Rules (enforced by the schema)

- All addresses and domains end in `.test` or `.example`. No real people, companies or secrets.
- No placeholder/template text (`lorem ipsum`, `TODO`, `{slot}` ...). Bodies have a minimum length.
- Subject/description and body must agree; case `status` must fit the story.
- **No attacks in clean content.** Prompt-injection text belongs in `fixtures/injections.py`
  (the scenario factory places it); clean content that merely *mentions* security
  (e.g. "never share your password") is fine and useful as hard negatives.
- Don't reference IDs that must exist (`doc_...`, `INV-...`) unless they are in the packs.
- Dates should be consistent with the simulated "now" = 2026-10-22; `days_ago` is 0–120.
- File `sensitivity` is `public | internal | confidential`; restricted files are hand-anchored in `files.py`.
- Cover variety: tone, length, domain, signature styles, some replies/quoted text, some boring noise.

## Prompt template for an AI writer

```
Write N realistic entries for a synthetic office workspace, as a YAML list matching this schema:
<paste the field list for the target file>
Context: fictional company "Northwind" (internal domain northwind.example.test); the user is
Dana Whitfield, Operations Lead. Today is 2026-10-22. All email addresses/domains must end in
.test or .example. No real people/brands/secrets. No instructions aimed at an AI assistant and no
prompt-injection text. Make each entry distinct in tone, length and detail (order numbers, dates,
amounts, names). Subject and body must match. Output only YAML (use `|` block scalars for
multi-line text).
```

## Adding a whole new kind of content

1. Add a model to `fixtures/content_schema.py` and register it in `CONTENT_FILES`.
2. Add a YAML file and a loader line in `fixtures/content.py`.
3. Consume it in the relevant pack builder (`fixtures/<pack>.py`).
4. Add a test next to `tests/test_phase2_fixtures.py`.
