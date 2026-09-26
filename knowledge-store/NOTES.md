# NOTES — decisions, limits, and traps we paid for

Some evidence below (similarity measurements, concept titles extracted from real
notes) was recorded while Chiron still worked in Vietnamese. Strings whose exact
characters matter are kept verbatim, with an English gloss.

## Settled decisions (not reopened)
- Postgres `nodes` + `edges`, **not Neo4j**.
- A node is one concept, **not one study session**.
- Duplicate detection with `pg_trgm` full-text, threshold **0.6**. **No embeddings,
  no pgvector** — an upgrade waits for real evidence from Mnemosyne.
- Edges: the LLM suggests → `pending` → a person reviews → `approved`. **No auto-approve.**
- `subject` is free TEXT, **NOT an enum** (Horae has no closed subject taxonomy).
- `rejected` / `discarded` rows are kept forever, never deleted — "never ask again a
  question the user already answered".
- Merging nodes uses `merged_into_id`, **no hard delete**.
- Mnemosyne writes after the whole session, not after every answer.
- The raw transcript is stored FIRST; extraction is a separate, retryable job.
- The LLM client is written separately for KS and imports nothing from Horae, but
  follows the same conventions.

## Two core functions that are opposites on purpose
| Function | Behaviour |
|---|---|
| `save_transcript` | **NEVER raises.** Swallows every error, including a lost DB connection → `SaveResult(ok=False)`. A study session must not break because KS is down; Mnemosyne is the system of record. |
| `ingest_concepts` | **Fail-loud.** `psycopg.Error` propagates. The HTTP wrapper catches it → 503. |

## Known limits — NOT fixed, only locked in by tests
Trigram dedup wrongly merges concepts with near-identical names. Locked in by
`tests/test_dedup_limits.py::test_numbered_variants_are_wrongly_deduped`.

### Three evidence points — traced back, the environment did NOT change
Measured on PostgreSQL 18.6, pg_trgm 1.6, collation `en_US.UTF-8`, provider `libc`.
The strings are Vietnamese physics terms and must stay verbatim — the numbers
depend on the exact characters.

| String A (verbatim) | String B (verbatim) | Gloss | similarity | Merged at 0.6? |
|---|---|---|---|---|
| `Định luật Newton 1` | `Định luật Newton 2` | Newton's first / second law | 0.8095 | **yes** |
| `Định luật khúc xạ ánh sáng` | `Định luật phản xạ ánh sáng` | law of refraction / reflection of light | 0.6774 | **yes** |
| `Định luật Ohm (curl-nodes)` | `Định luật Newton 2 (curl-nodes)` | Ohm's law / Newton's second law | 0.6364 | **yes** |
| `khúc xạ` | `phản xạ` | refraction / reflection | 0.2308 | no |
| `Định luật Ohm` | `Định luật Newton 2` | Ohm's law / Newton's second law | 0.4348 | no |

All three evidence points from the previous build reproduce **exactly** when the
exact strings are measured (0.636 → 0.6364; 0.677 → 0.6774). At first I measured
the bare title pairs (`khúc xạ` vs `phản xạ`, `Định luật Ohm` vs
`Định luật Newton 2`) and wrongly concluded the environment had changed. **There is
no environmental difference.** The collation hypothesis is refuted; no further
investigation needed.

Mechanism: the shared part of two strings makes up most of the trigrams. The same
prefix `"Định luật "` ("law of") plus the same suffix `" ánh sáng"` ("of light") /
`" (curl-nodes)"` pushes similarity from 0.23 to 0.68 even though the differing part
is identical. A shared suffix is the most dangerous thing for trigram dedup — and
real titles from Mnemosyne easily share suffixes (chapter names, subject names,
question-set names).

### RULE: how to record an evidence point
A number recorded without its exact input strings **cannot be reused** — the most
expensive lesson here. Two of the three old numbers were nearly read as "the
environment changed" just because the original strings were missing.

From now on every dedup evidence point MUST record:
1. **Both strings verbatim**, including prefixes/suffixes that look like technical
   noise (`(curl-nodes)` is exactly what produced the number).
2. `SELECT extversion FROM pg_extension WHERE extname = 'pg_trgm';`
3. `SELECT datcollate, datctype, datlocprovider FROM pg_database WHERE datname = current_database();`

Without these three, when the time comes to decide on pgvector nobody will know
what the old numbers meant. `tests/test_dedup_limits.py` locks all three in with
tests, including the environment fingerprint.

## RULE: exchange raw data, not conclusions

Three times while working with Mnemosyne, KS concluded more than the data allowed,
and all three were caught by the other side:

1. "Restarting `chiron-ks-http` triggers Mnemosyne's fallback" — wrong: a restart
   gives `connection refused` → `knowledge_store_error`. The fallback only runs when
   KS is alive but running old code.
2. The truncated test used a body with a `message` field — the real body has only
   `error` and `reason`.
3. "0 rows with 502, so there are no old errors to reinterpret" — correctly: the
   hypothesis had NO CASES TO TEST; it was not refuted.

All three were conclusions drawn where **only one side could see the data**. KS had
no way to know that a restart gives `connection refused` rather than an HTML 404,
because that behaviour lives in Mnemosyne's client.

What exposed them was not either side's care, but **both sides writing specifically
enough that the other could check it against what they held**: KS sent verbatim
payloads rather than descriptions, Mnemosyne sent tables of numbers rather than
conclusions. That is how the mismatches collided instead of slipping past.

In practice: when reporting across a module boundary, send the verbatim
payload/measurements along with the conclusion, never the conclusion alone. Same
root as the evidence-point rule above.

## Recorded technical debt
- `find_candidates` uses `similarity()` rather than the `%` operator, so it **does
  not use the GIN index**. In exchange, the threshold does not depend on the
  session's `pg_trgm.similarity_threshold` GUC. Acceptable at single-user scale.

## deepseek-v4-flash is a REASONING model — reasoning tokens count toward max_tokens

Found in a real run, not guessed. One edge-suggestion call with 2 candidates:

```
completion_tokens: 222
  completion_tokens_details.reasoning_tokens: 158   ← 71% of the budget
prompt_tokens: 474 (cached 384)
```

The amount of reasoning varies per run. With `max_tokens=1000`, reasoning once ate
almost the whole budget and left ~15 tokens for the JSON → a response cut off
mid-way:

```
[
  {
    "candidate": 0,
    "relation_type": "pr        ← out of tokens here
```

Two fixes:
1. `EDGE_SUGGESTION_MAX_TOKENS` / `EXTRACTION_MAX_TOKENS` = **4000** (both since
   raised; see the sections at the end). Do not lower these to match the VISIBLE
   output length (~200 characters) — most of the budget is invisible reasoning. Cost
   only accrues for tokens actually generated; a cut-off ruins the whole batch.
2. `LLMTruncatedError` (a subclass of `LLMTransientError`) catches
   `finish_reason == "length"` on OpenAI-compatible APIs and
   `stop_reason == "max_tokens"` on Anthropic. Before, the truncated JSON reached
   `json.loads` and surfaced as an `LLMParseError` — a completely wrong diagnosis,
   since the response structure was not wrong, just unfinished. As a subclass of
   Transient it is retryable: the same `max_tokens` is sometimes enough and
   sometimes not.

Instrumentation recorded exactly this incident (`edge_suggestion_run.outcome =
'parse_error'`) — evidence that the §7 constraint works in practice.

## card_sync: verification status of each branch

Five cases were run for real against a live Mnemosyne on `127.0.0.1:8081`:

| Case | Verified | Result |
|---|---|---|
| 201 new card | ✅ real | `sent` |
| 409 card already exists | ✅ real | `sent` (Mnemosyne checks BEFORE calling the LLM → no tokens spent) |
| 404 wrong set/node | ✅ real | `skipped`, no retry |
| Mnemosyne unreachable | ✅ real | the whole batch stops, `attempts` unchanged |
| 503 KS not configured | ❌ fake only | `pending` |
| 502 `reason="truncated"` | ⚠️ tried 6 nodes, not reproduced | `failed`, no retry |

### HUNTED: 6 nodes, natural truncation through card_sync could NOT be reproduced

Goal: force a **natural** `reason="truncated"` case (without lowering `max_tokens`)
through the real path `card_sync` → `POST /cards/from_node` → DeepSeek defaults.
Result: **6/6 nodes `sent` (201). No truncation.**

| Node | prompt (chars) | tokens used | Result |
|---|---|---|---|
| Formation and evolution of stars | 2345 | 1925 | sent |
| Gödel's incompleteness theorems | 3023 | **5164** | sent |
| The sorites paradox | 1059 | 1116 | sent |
| Schrödinger's cat | 1092 | **3058** | sent |
| The ship of Theseus | 1034 | 1150 | sent |
| The trolley problem | 1247 | **3811** | sent |

**The initial hypothesis was WRONG, and the measurements refute it.** I assumed a
long summary would push toward the cap. Mnemosyne pointed out the flaw: a long
summary increases **prompt** tokens, while `max_tokens` only caps the **completion**
— two different budgets. The real variable is **reasoning difficulty**.

The numbers confirm they were right: Schrödinger, a 1092-character prompt, burned
3058 tokens, while Theseus, 1034 characters, burned only 1150 — same length, 2.7×
apart. The last node ("The trolley problem", designed to maximise deliberation:
four competing ethical frameworks plus a reversing intuition) burned 3811 tokens
with a prompt of only 1247 characters. Right direction, but still not the cap.

The highest observed was **5164 tokens, still successful** — so DeepSeek's default
budget has room above that.

**Conclusion (valid, not a dead end):** with the current data distribution, natural
truncation through `card_sync` is **rare enough that 6 deliberate attempts did not
hit it**. That STRENGTHENS the decision to keep `failed` / no retry: something this
rare is not worth the risk of blind retries. The branch table keeps the `fake` label
for this row, with the note changed from "not tried" to "tried 6 nodes, not
reproduced".

### ⚠️ KS's 30 s timeout HID a real classification
An unexpected finding during the hunt. `HttpCardClient` had a hard 30-second
timeout. The Gödel node took over 30 s to generate a card, so KS gave up and
recorded `CardClientError: timeout`, **losing** the real classification Mnemosyne
was about to return.

A short timeout is worse than slowness: it overwrites every `reason` (`truncated` /
`provider_error` / `knowledge_store_error`) with a meaningless infrastructure error.
Exactly once it hid the very case we needed to see.

Fixed: `KS_MNEMOSYNE_TIMEOUT`, default **180 seconds**. Re-running the same node
with the wider timeout gave `sent` (201) after 36 seconds.

**Suspicion REFUTED — Actix does NOT cancel the handler when the client disconnects.**
I suspected KS's timeout cancelled the in-flight DeepSeek request on Mnemosyne's
side. Wrong. Mnemosyne proved it by experiment: killing the client after 2 seconds
(`curl --max-time 2`), their handler still ran to completion and **created the card
normally** 3 seconds later.

More striking: the strongest counter-evidence was already in the data I held. Had
the handler been cancelled, the `ai_interactions` row at 15:07:58 **could not
exist** — a dropped future does not run its error branch and cannot INSERT
anything. The row is there, so the handler was alive 31 seconds after KS gave up.
The very time gap I found suspicious was the refutation. Lesson: I had the
counter-evidence and did not use it.

What the EOF error really was: Mnemosyne's message carried an **empty**
`body snippet:`, i.e. DeepSeek returned **HTTP 2xx with an empty body**. A
connection dropped mid-way makes reqwest report `Network`, not `Parse`. This is an
upstream anomaly, unrelated to KS.

### Where the 6 nodes in the production DB came from — kept on purpose, not junk
The six nodes below were created during the truncation hunt, **not because any
student studied them**. The user decided to keep them; neither side cleans them up.

`Sự hình thành và tiến hóa của sao` (formation and evolution of stars) ·
`Định lý bất toàn Gödel` (Gödel's incompleteness theorems) ·
`Nghịch lý Sorites` (the sorites paradox) · `Mèo Schrödinger` (Schrödinger's cat) ·
`Con tàu Theseus` (the ship of Theseus) · `Bài toán xe điên` (the trolley problem)

They are coherent concepts, real tokens were spent generating their cards, and each
has exactly one card in Mnemosyne's "KS review" set (8 cards in total = 2 older
nodes + these 6). Keeping them does no harm, and deleting them takes coordination
on both sides.

**If they are ever cleaned up: clean BOTH sides AT THE SAME TIME.** Deleting the KS
node but keeping the card leaves an orphan card; deleting the card in Mnemosyne but
keeping the node makes `card_sync` recreate it on the next run. Neither side can
clean up alone.

### A timeout creates ORPHANED RESULTS, it does not break anything
Because the handler on the other side runs to completion, KS's timeout cancels
nothing — it creates a state where **the two sides believe different things about
the same node**: Mnemosyne has a card, KS recorded a failure.

The system reconciles itself, but only through a two-step chain where **both steps
are required**:

1. A timeout records `pending`, **not** `failed` → the node can be picked again.
2. The next run gets `409` → records `sent`, because **409 is not an error**.
   Mnemosyne deliberately returns `existing_card_id` with it for exactly this
   reconciliation.

Changing either step leaves the orphan stuck forever. Locked in by the test
`test_timeout_then_409_reconciles_an_orphaned_result`, which runs exactly that chain.

The actual Gödel case was **not** orphaned — that time their handler really failed
too (empty body), so the "KS review" set has exactly 8 cards, none extra. But had
DeepSeek answered normally, there would be a card KS recorded as failed.

### `truncated`: the wire format is confirmed, the KS path has not run for real
Mnemosyne forced truncation through the real API (temporarily patching
`max_tokens=200` in their client, running once, dropping the patch without
committing). All three `reason` values have now been seen on the real wire. The
verbatim body:

```json
{"error": "DeepSeek stopped mid-answer at its token limit (length); nothing was parsed. Retrying, or requesting fewer items, may succeed.",
 "reason": "truncated"}
```

**There is no `message` field** — KS's old test made that field up. The test now
anchors on the verbatim payload above (`TRUNCATED_BODY` in
`tests/test_card_sync.py`), per the evidence-point lesson: anchor on the real wire,
not on imagination.

The scope still has to be stated precisely: **KS's `card_sync` has never RECEIVED a
real truncated response.** Mnemosyne's probe called their endpoint directly, not
through this job. What is confirmed is the *shape of the data*, not the *path*.
`_decide()` has been checked with that exact payload and returns `failed` as designed.

### SETTLED: NO retry for `truncated`. The decision is closed; do not reopen it.
The brief settled "NO retry with the same input — it almost certainly repeats
exactly". Mnemosyne measured 40 real calls (same node, same prompt, 10 per budget):

| max_tokens | truncated | reasoning observed | retry with same input succeeded |
|---|---|---|---|
| 200 | 10/10 | 200 (hit the cap) | 0/10 |
| 350 | 10/10 | 232 – 350 | 0/10 |
| 500 | 8/10 | 78 – 500 | 2/8 |
| 650 | 7/10 | 162 – 650 | 2/7 |

Both earlier claims were half wrong: below the boundary zone retries succeed
**0/20**, inside it **2–3/10**. Retrying pays off, but only in the boundary zone, and
for 1–2 attempts at most.

**WARNING when reading this table — do not carry the 2–3/10 rate over to real
operation.** Mnemosyne **does not send `max_tokens`**; it uses the model's default.
So truncation in operation means reasoning has eaten the ENTIRE default budget —
**outside** the range measured above. Nobody has numbers for that regime and they
cannot be inferred.

**Agent A settled it: keep `failed`, no retry.** The reason stated when settling —
*do not change behaviour based on measurements from a different regime than the
one being decided*. The 40-call table is good data, but measured in a forced
low-`max_tokens` band, while the decision concerns the model's default regime. Good
numbers from the wrong regime are still the wrong basis.

This decision is CLOSED. Reopen it only with measurements in the RIGHT operating
regime (no forced `max_tokens`). If the answer changes then, the place to change is
the `truncated` branch in `ks/card_sync.py::_decide()`, and it should share the
retry budget with `provider_error` rather than retry without limit.

### A truncated reply OFTEN has content — that is the real trap
At `max_tokens=350`, **5/10** truncated calls still returned real content
(half-written JSON, up to 310 characters). Not a rare case.

Without checking `finish_reason`, those go straight to the parser and are reported
as **format** errors, sending whoever reads the log off to inspect the prompt when
the real error is the **token budget**. Exactly KS's original production bug —
before `LLMTruncatedError`, `suggest_edges` reported `parse_error` for this very case.

KS is protected: in `ks/llm.py`, `finish_reason == "length"` is checked BEFORE
`content` is read, so a truncated-but-non-empty response still becomes
`LLMTruncatedError`. Three tests lock it in:
- truncated with half-written JSON → `LLMTruncatedError`, not `LLMParseError`
- **truncated with SYNTACTICALLY VALID JSON** → still `LLMTruncatedError`. This is
  the most dangerous silent case: the model writes the closing `]` and then runs out
  of tokens; the string parses but the content is INCOMPLETE. Accepting it would
  silently lose data. The budget must win over the syntax.
- control: `finish_reason == "stop"` → passes through normally, no false block

Checked the history of `ks.card_sync_log`: **no 502 rows at all**. Read it
correctly — the hypothesis "old parse errors were really truncations" had **no cases
to test**; it was NOT refuted. Clean in the sense of no old debt, not in the sense of
having proven anything.

Reproduction details are in Mnemosyne's `docs/gotchas.md`, item 2.

### Cost variation for one prompt: 4×
KS's measurement (65 → 200) was right and understated. At `max_tokens=650`, the
same request used anywhere from **162 to 650** reasoning tokens with nothing changed
on the caller's side. **Any logic that assumes a prompt has a stable cost is wrong**
— including choosing `max_tokens` from the visible output length.

### ⚠️ Mnemosyne's `tokens_used` records 0 for every failed call
Mnemosyne found it themselves: a truncated call **still burns real tokens** (~200 in
the probe) but `ai_interactions.tokens_used` records `0`, because
`LLMError::Truncated` carries no `usage`. Every failed call is invisible in the cost
ledger — and truncation is the most expensive kind, since the reasoning tokens are
all spent before it fails.

Impact on KS: **none**. `ks stats` has no cost column and should not get one — KS is
not where Mnemosyne's tokens are accounted. Just remember: if anyone later reads
Mnemosyne's cost figures, that column **cannot be trusted for failed calls**. They
reported it to their own coordinator; KS does not touch it.

### Correction: what TRIGGERS Mnemosyne's list-scan fallback
I once told Mnemosyne that `systemctl --user restart chiron-ks-http` would trigger
their fallback branch. **Wrong.** While the service is down the connection is
refused → `KsError::Unreachable` → 502 `knowledge_store_error`, not the fallback path.

The fallback only runs when KS is **alive but running old code**: Werkzeug returns an
HTML 404 for a route that does not exist yet, and from the status code alone that
404 cannot be told apart from "node does not exist". So it is **version skew**, not
downtime. The case is real because the two services were developed side by side in
the same checkout. Mnemosyne keeps the fallback and makes it log a warning when it
runs — the two paths are not equivalent (the list-scan path cannot resolve merges),
so replacing one with the other silently would leave that difference as a mystery to
discover later.

## Mnemosyne had NO systemd unit

`card_sync` depends on Mnemosyne being alive at `127.0.0.1:8081`, but at the time
Mnemosyne was run by hand with `cargo run -p backend` and had no systemd unit
(it has user units in `mnemosyne/deploy/` now). The `chiron-ks-card-sync.timer` runs
hourly regardless — when Mnemosyne is down the job records `pending` and does NOT
burn retry attempts, so the next tick catches up. No data loss, only delay. Nothing
to fix on the KS side.

Mnemosyne also had no auth layer on this route (a deliberate simplification on
their side), so `KS_MNEMOSYNE_TOKEN` may be empty. KS still sends the
`Authorization` header IF the variable has a value, ready for when they add auth.

### ⚠️ The endpoint `card_sync` depends on was NOT on origin (at the time)
When this was written, Mnemosyne had **11 unpushed commits**, and
`POST /cards/from_node` was among them. So all of `card_sync` depended on an
endpoint that **existed only in this machine's local checkout** — not on origin,
not recoverable if the machine failed.

Do not write anywhere that the Mnemosyne side "is safe on origin" without checking.

The concrete consequence to state, and only state: **`card_sync` was confirmed
correct against code that existed only locally on the Mnemosyne side.** Had that
machine failed, the milestone just delivered could NOT have been re-verified.

KS does not push their repository, and **does not remind them to push again** —
even for a good reason. It is information for the Mnemosyne-side user to decide on,
not a request. Pushing to origin is an outward-facing action that belongs to their
user, and one earlier permission is not permanent permission. KS pushed once and was
told it was wrong; Agent A also briefly repeated a similar mistake. Do not repeat it.

For comparison: the brief to rebuild KS exists because last time the code was not
pushed before the machine was reinstalled — everything was lost. This was the same
shape of risk, in a different module.

## DELIBERATE DEPARTURE FROM THE BRIEF: USER-level systemd, not system

Brief §10 and the previous build used system-level units (`/etc/systemd/system/`,
`sudo systemctl ...`). This time it is settled as **user-level**
(`~/.config/systemd/user/`).

Consequence — every operational command in the brief and older documents needs `--user`:

| Old documents | Correct for this build |
|---|---|
| `sudo systemctl status chiron-ks-http` | `systemctl --user status chiron-ks-http` |
| `sudo systemctl restart chiron-ks-http` | `systemctl --user restart chiron-ks-http` |
| `sudo journalctl -u chiron-ks-http` | `journalctl --user -u chiron-ks-http` |
| `sudo systemctl show ... -p MainPID` | `systemctl --user show ... -p MainPID` |

Trade-offs considered:
- **Gained:** no sudo for every unit change; units run as user `zinnn`, the same user
  that owns PGDATA `~/.local/share/chiron-ks-postgres`, so there is no need for
  `User=`/`Group=` or directory-permission worries.
- **Lost:** `sudo loginctl enable-linger zinnn` (once) is needed for the services to
  survive logout and start at boot. **Not run yet** — `Linger=no`. Without linger, KS
  dies at logout and Mnemosyne loses its endpoint.

## Operational traps — do not repeat
1. **`fish` has no `export`.** Pass variables with `env VAR=value command`.
2. **Before killing a `ks serve` process:** confirm with
   `systemctl --user show chiron-ks-http.service -p MainPID`. The production service
   was SIGKILLed by mistake twice, judged "orphaned" from `lsof`/ports alone.
3. **After a code change, systemd keeps running the old code** until `systemctl restart`.
4. Postgres defaults to `unix_socket_directories = '/run/postgresql'` → a regular user
   gets `FATAL: could not create lock file`. Change it to PGDATA.
5. The port is **5432** (the `initdb` default). If you see `55432` anywhere, that is
   the cluster from the previous build — do not reuse it.

## New finding in this build
**`KS_HTTP_TOKEN` must be ASCII.** Measured with real curl: a token containing
Vietnamese characters made EVERY request 403 forever. Cause: WSGI decodes HTTP header
values as latin-1, so the token's UTF-8 bytes reach the application as mojibake and
never match. This is a CONFIGURATION error, not a client error — so
`validate_token_config()` runs when `ks serve` starts and dies at once on a
non-ASCII token instead of failing silently.

Different from the `hmac.compare_digest` bug (§9 of the brief): that one was a
non-ASCII header sent by the CLIENT crashing with 500; it is stopped by comparing
bytes. Two independent bugs, each with its own test.

## Extracting concepts from scanned notes — measured 2026-09-25

**Data.** 3 real handwritten pages from the learner's notebooks (in Vietnamese): 1
Chemistry page (fertilisers), 2 Biology pages (metabolism, Cornell style). Run
through the application's own path: `POST /notes` → `PATCH /notes/{id}` →
`POST /notes/{id}/extract`, with deepseek-v4-flash. Two notes:

- **A — corrected:** the OCR text replaced by a retyped copy exactly as written,
  i.e. what the learner does at the correction step (3051 characters).
- **B — raw OCR:** nothing corrected (CER about 22 %, see `ocr/NOTES.md`).

**Truncation is real, and `EXTRACTION_MAX_TOKENS=4000` was not enough.** With 4000,
both A and B returned `extraction_failed`:
`deepseek: response cut off because max_tokens ran out … completion_tokens=4000, reasoning_tokens=4000`
(the message was in Vietnamese at the time). The model spent the whole budget
reasoning. The error was correctly identified as truncation, not misreported as a
parse error. Re-measured with a 16000 budget (calling the provider directly):

| Input | Chars | completion | reasoning | Time |
|---|---:|---:|---:|---:|
| 3 pages, run 1 | 3050 | 8270 | 6871 | 32 s |
| 3 pages, run 2 | 3050 | 8577 | 6880 | 29 s |
| Chemistry page | 914 | 2382 | 1754 | 9 s |
| Biology page 1 | 922 | 944 | 297 | 3 s |
| Biology page 2 | 1210 | 8723 | 7824 | 30 s |

Splitting by page does not help: a single page needed 8723 tokens. Raised
`EXTRACTION_MAX_TOKENS` to 16000 and the HTTP timeout to the LLM to 150 s
(`ks/llm.py`). After the fix: A took 31 s and gave 20 concepts, B took 52 s and gave 17.

**A — 20 concepts, with assessment** (English translations of the Vietnamese
originals; the verbatim text is in the database)

| # | Concept | Assessment |
|---|---|---|
| 1 | [Chemistry] Fertiliser — A product that supplies nutrients to crops or improves the soil. Without fertiliser, plants grow poorly, get diseases, die. | Correct |
| 2 | [Chemistry] Classification of fertilisers — Classified by the element content in plants: macro-, secondary and micronutrients. Classified by origin: inorganic and organic. | Correct, but **partly overlaps** 3–7 (an "umbrella" concept holding sub-concepts) |
| 3 | [Chemistry] Macro — By element content in plants, a relatively large mass (>1000 mg/kg), including N, P, K. | Content correct; **name too generic**: "Macro" on its own, should be "Macronutrient fertiliser" / "Macronutrients" |
| 4 | [Chemistry] Secondary — … (100-1000 mg/kg), including Ca, Mg, S. | As 3 |
| 5 | [Chemistry] Micro — … (<100 mg/kg), including B, Cu, Fe, Cl, Mn, Na, Zn, Ni, Mo,... | As 3 |
| 6 | [Chemistry] Inorganic fertiliser — Made from inorganic chemical products and produced industrially. | Correct |
| 7 | [Chemistry] Organic fertiliser — Made from organic matter and organic waste through processing, mixing, fermentation and added minerals. | Correct |
| 8 | [Chemistry] Role of fertiliser — Increases soil fertility, supplies nutrients to plants and regulates the nutrient cycle in the soil. | Correct |
| 9 | [Biology] Metabolism and energy conversion in organisms — Sustains life, helps organisms survive and grow; supplies materials and energy to the body. | Correct, but the name is the lesson title while the content is the "Role" section; slightly off |
| 10 | [Biology] Characteristic signs of metabolism — 7 features: intake, transport, transformation, synthesis and storage, breakdown and release, excretion, regulation. | Correct |
| 11 | [Biology] Stages of conversion in the living world — Synthesis (from light), breakdown (storing energy in organic matter) and mobilisation (storing energy in ATP). | **Vague, easy to misread.** The notebook draws each stage with an input (←) and output (→): light → synthesis → energy in organic matter → breakdown → ATP → mobilisation → life activity. The text keeps only one direction, so it reads as if "breakdown = storing energy in organic matter". Cause: the arrow diagram flattened into text, even in the retyped copy |
| 12 | [Biology] The main energy source of the living world — Light energy, converted and stored in organic compounds used by all organisms. | Correct |
| 13 | [Biology] Metabolism in unicellular organisms — The whole metabolic process takes place at the cell level. | Correct |
| 14 | [Biology] Metabolism in multicellular organisms — Takes place at both body and cell level, in 3 stages: external environment ⇄ body, internal environment ⇄ cells, cell ⇄ cell. | Correct, and correctly joins "There are 3 stages" at the end of page 1 with the list at the top of page 2 |
| 15 | [Biology] Modes of metabolism — There are 2 modes: autotrophy and heterotrophy. | Correct, **partly overlaps** 16 and 20 |
| 16 | [Biology] Autotrophy — Self-feeding, self-absorbing, self-sustaining; includes photoautotrophy and chemoautotrophy. | Correct, taken correctly from the Cornell cue column |
| 17 | [Biology] Photoautotrophy — Uses inorganic matter, water, CO2 and light; typically plants. | Correct |
| 18 | [Biology] Chemoautotrophy — Uses a carbon source (CO2) and inorganic matter (H2S, NO2-, ...); typically some bacteria. | Correct |
| 19 | [Biology] Role of autotrophy — Supplies O2, sustains the life of most organisms; provides food, shelter and breeding grounds for animals; regulates climate, giving favourable temperature and humidity. | Correct |
| 20 | [Biology] Heterotrophy — Takes organic matter from autotrophs or other animals; through absorption, digestion and assimilation builds the body and uses energy; typically animals. | Correct; drops the cue-column point ("needs foreign factors") |

Summary of A: no concept invented knowledge beyond the notebook. 1 vague concept
(#11, from the diagram). 3 names too generic (#3–5). 3 partial overlaps (#2 with
3–7, #15 with 16/20, #9 misnamed). The learner still has to review; that is exactly
what the `pending_review` step is for.

**B — 17 concepts from raw OCR: what went wrong** (all 17 remain in
`ks.extracted_concepts` with status `discarded`, note `75a80c37…`):

- **#3–5 invented a unit:** "(>1000 mg/l)", "(100-1000 mg/l)", "(<100 mg/l)". The
  notebook says `mg/kg`; OCR read it as "ông lấy"/"mg lấy" and the model guessed
  `mg/l`. Wrong knowledge that looks entirely plausible.
- **#16 Heterotrophy completely wrong:** "uses a carbon source and inorganic matter
  to absorb and use energy; typical of some bacteria". That is chemoautotrophy's
  content. Cause: OCR joined the Cornell cue line "Heterotrophy: needs factors" onto
  the "Uses a carbon source…" line at the same height.
- **#17 "Nutrition":** OCR read "Dị dưỡng" (heterotrophy, the second entry) as
  "Dinh văng", so the model named it wrongly and created a duplicate of #16.
- **#14, #15 empty:** "Photoautotrophy — A form of autotrophy.", "Chemoautotrophy —
  A form of autotrophy." Useless.
- **#7** took "added minerals" (a step in making organic fertiliser) as a role of
  fertiliser, and lost "regulates the nutrient cycle".
- **#9 duplicates #8** (the same "role" content). **#12** is misnamed ("The
  relationship between metabolism and energy conversion" for the section on
  cell/body level).

**Conclusions.**

1. With corrected text the quality is usable: 16/20 concepts correct and concise, the
   rest vague, too generic or overlapping; no concept is factually wrong.
2. Skipping the correction step is dangerous, not merely worse: raw OCR text produces
   **plausible-looking** errors (`mg/l`, heterotrophy ↔ chemoautotrophy). A reviewer
   who does not check against the notebook will struggle to spot them.
3. The two remaining error sources are not in the LLM: **arrow diagrams** lose their
   direction when flattened into text, and the **Cornell cue column** gets mixed into
   the note lines.


## GET /edges — a separate read route for the concept map (2026-09-26)

- `GET /nodes` still does **not** return edges (a settled decision). Edges have their
  own route.
- Only `approved` edges. `pending` is a suggestion nobody has reviewed, `rejected` is
  the reviewer's "no"; drawing them on the map would misstate what the learner decided.
- Both ends resolve `merged_into_id` **exactly one step**, the same rule as
  `_LIST_SQL`, so every id in an edge is an id `GET /nodes` returns (a test locks this
  in). After resolving, A→A edges are dropped and duplicate `(from, to, relation)`
  edges collapse into one.
- Tests: `tests/test_edges_http.py` (7 tests). Checked in a browser against a separate
  test DB: 12 nodes, 11 approved edges + 1 pending + 1 rejected, with one merged node.
  The map drew exactly 12 nodes and 11 edges (8 prerequisite, 1 related,
  2 contrasts_with); the merged node's edges moved to the target node.
- **At the time the real data had 0 nodes, 0 edges.** 20 concepts from the notebooks
  were awaiting review. There is no evidence yet about the map on real study data.


## The duplicate detector merged wrongly on real data; the review screen asks first (2026-09-26)

**Data.** The learner accepted 20 concepts from the Chemistry + Biology notebooks. The
automatic rule (`similarity >= 0.6`) merged 5 concepts into existing nodes, and **all
5 were wrong**:

| Concept | Merged into | similarity |
|---|---|---:|
| Metabolism in multicellular organisms | … unicellular organisms (the opposite concept) | 0.85 |
| Photoautotrophy | Autotrophy (the parent concept) | 0.64 |
| Chemoautotrophy | Autotrophy | ≥ 0.6 |
| Inorganic fertiliser | Fertiliser | 0.60 |
| Classification of fertilisers | Fertiliser | ≥ 0.6 |

(The titles were Vietnamese, e.g. `Quang tự dưỡng` vs `Tự dưỡng`; the scores are for
those original strings.) Trigrams on titles cannot tell a concept from its parent,
or from an opposite concept with a near-identical name. School notes are full of
such pairs.

**The user's decision:** keep the 0.6 threshold for the automatic path, but the review
screen must ask before writing.

- `GET /extracted/{id}/candidates`: near-duplicate nodes (score ≥ 0.3, at most 5), with
  `suggested_node_id`, the node the threshold would merge into.
- `POST /extracted/{id}/accept` with `{"decision":"create"}` or
  `{"decision":"merge","node_id":…}`. Without a body the automatic rule applies as
  before (CLI `ks accept`, Mnemosyne).
- UI: when a candidate is over the threshold, **nothing is preselected**, and the
  accept button stays locked until the learner chooses. Below the threshold the
  default is to create a new node.
- `ks.ingest_log.chosen_by` (migration 0006): `rule` or `learner`. A row with
  `chosen_by='learner'`, `decision='created'`, `top_score >= threshold` is exactly a
  false positive of the threshold, with its score. From here on it is measurable how
  often the threshold is wrong on real data.

Tests: `tests/test_review_dedup.py` (7 tests, using exactly the name pairs from the
table above). Checked in a browser against a test DB: 3 concepts with a candidate over
the threshold (button locked, showing "the automatic rule would choose"), 1 without
(button open); created 2 new, merged 1 by hand; `ingest_log` recorded all 4 `learner`
rows with the right `top_score`.

**Fixed afterwards:** the 5 wrongly merged concepts on real data were split off with
the new `ks split <concept_id>` command (`ks/confirm.py::split`), which creates a node
from the concept's own title, subject and summary and logs the decision as the
learner's. The 20 concepts now map to 20 nodes.

## Edge suggestion was also cut off at 4000 tokens (2026-09-26)

Running `suggest-edges` for 15 real nodes: 8/15 were cut off, reasoning 3630–4000 —
**every Biology node**. The user chose `EDGE_SUGGESTION_MAX_TOKENS=20000`. Re-running
those 8 nodes: 8/8 `ok`, 6–35 s per node, 30 more `pending` edges; 47 edges awaiting
review in total. `edge_suggestion_run` does not record token counts, so it is not yet
known how much headroom 20000 leaves.

## The English switch (2026-09-26)

Chiron is now English only, for an international hackathon. The extraction and
edge-suggestion prompts ask for English output, and every error message, CLI line,
docstring and comment in KS is English. Concepts and edges already in the database
keep their original Vietnamese text; nothing was machine-translated in place.
