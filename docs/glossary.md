# Shared toku glossary

[`glossary.json`](../glossary.json) is the repository's shared terminology cache.
JSON keeps spellings, evidence and changes reviewable without a database. One
file serves Kamen Rider, Super Sentai and Ultraman, with optional franchise and
product scope on each entry.

## Automatic additions

Japanese-to-English production runs research terminology before freezing their
translation glossary. No human approval step is required:

1. Bounded deterministic discovery finds isolated/repeated katakana candidates
   while excluding ordinary operating vocabulary and known terms.
2. Cached Wikidata lookups require matching bilingual canonical labels and
   explicit franchise context. Search hits alone do not establish a spelling.
3. An optional OpenAI web-search agent proposes additional source-backed names.
   Independent fetching verifies source occurrence, bilingual quotations, scope,
   consulted URLs and conflicts before accepting a mapping.
4. Verified terms merge into the shared JSON under a filesystem lock and atomic
   replacement. The run freezes its own exact glossary and research artifacts
   before translation.

Official product spellings take priority. Accepted evidence can come from
allowlisted official pages, official Bandai PDFs or contextual community sources
such as the Kamen Rider Wiki and the Super Sentai pages on RangerWiki. Power
Rangers terminology is not automatically interchangeable with Super Sentai.
Web snippets, a model's suggested spelling and an inaccessible wiki page do not
count as independent bilingual proof.

Official PDF evidence is bounded: downloads cap at 10 MB; extraction reads up to
four text pages, with Document AI fallback for at most the first two scanned
pages. Both names must occur in one independently read page. Cached evidence
retains PDF hash, page and extraction method. OCR fallback adds API usage.

`GLOSSARY_MODEL` selects the research model, falling back to `OPENAI_MODEL` and
then `gpt-5-mini`. It uses the existing `OPENAI_API_KEY`; no separate search
credential is needed. Network/model failures and uncertain evidence do not pause
the manual translation.

## Scope and status

Each entry records Japanese, preferred English, optional English aliases,
franchise/product scope, status and source evidence with URL, title, access date
and quote. Stable franchise identifiers are `kamen-rider`, `sentai` and `ultraman`.
Product scope normally uses the manual directory name.

| Status         | Behavior                                                        |
| -------------- | --------------------------------------------------------------- |
| `accepted`     | Requires evidence; applied only when context matches            |
| `needs_review` | Advisory uncertainty; never applied as a translation constraint |
| `ignored`      | Explicitly dismissed candidate; not a preferred spelling        |

The status name `needs_review` is a maintenance label, not a workflow gate.
Product-specific accepted entries override franchise entries, which override
unscoped entries. Equally specific conflicting spellings are withheld. Unknown
context does not activate scoped terms. Longer phrase matching avoids replacing
a short name inside an unrelated longer name. English aliases are reference data,
not Japanese lookup keys.

Google receives protected names in escaped HTML `translate="no"` spans. Transport
markup is removed from returned English. Ollama receives accepted names in the
source input; conformance is checked afterward. The original OCR text and boxes
remain unchanged in saved data.

## Inspect and maintain

These commands validate and inspect the cache without model calls:

```bash
uv run python -m src.quality glossary
uv run python -m src.quality glossary --pending
uv run python -m src.quality glossary --franchise sentai --product GoKaiKeys
```

For an intentional manual correction, add the independently verified preferred
spelling and its evidence before marking the entry `accepted`; mark a non-name
`ignored`. Keep ambiguity pending. Do not invent evidence to clear a status.
Existing accepted mappings are not silently refreshed or overwritten. Concurrent
contradictory accepted spellings produce a pending conflict.

No-match lookups can leave pending shared candidates. Temporary API failures are
run-only so another run can retry. To intentionally retry a cached no-match,
remove the relevant pending shared entry and clear its lookup-cache record before
a new run; retain frozen run evidence. Native Mac/WSL file locking is supported;
native Windows behavior is untested.

A resumed benchmark reuses its frozen glossary offline. Shared-cache edits apply
to new runs, not old results or published manuals. Use a new run ID when applying
new terminology. Production advisory notes and snapshots live under
`benchmark-runs/pipeline/`; deterministic/web caches and durable agent artifacts
also remain under ignored `benchmark-runs/` directories.
