# Demo brief (§6.6)

Everything needed to record the video, in the order it happens. The suggested
cut in [HANDOFF.md](../HANDOFF.md) says what to cover; this says how to actually
run it, what will go wrong, and what to say while it does.

The brief asks for three things: a PDF entering the bucket and rows appearing in
BigQuery, the agent answering at least three questions including one that spans
more than one table, and a brief explanation of the schema. Five to eight
minutes.

---

## The problem this solves, and the state it needs

Every chart is already ingested, and the pipeline is idempotent by design, so
re-uploading one writes an audit row and no clinical rows. That is the correct
behaviour and a useless picture: the video has to show rows *arriving*.

[`scripts/demo_reset.py`](../scripts/demo_reset.py) clears one document's rows so
the ingest that follows has something to write. Nothing is fabricated — it
deletes rows the pipeline reproduces byte-for-byte from the same PDF, which is
the idempotency claim stated as a procedure instead of a sentence.

```bash
./scripts/demo_rearm.sh                  # between takes: verify, clear, empty the bucket
...record...
./scripts/demo_rearm.sh                  # again, for the next take
python scripts/demo_reset.py --verify    # after the LAST take: confirm, drop backups, stop
python scripts/demo_reset.py --restore   # only if the ingest never ran
```

`demo_rearm.sh` is a wrapper over `demo_reset.py --rearm` that sources `.env`
and finds the virtualenv, so it works in a tab you just opened. The steps below
describe the script; the wrapper is only how you type it.

`--rearm` is the one to use while rehearsing. It verifies the take that just
finished, clears the chart again, and removes every copy of the PDF from the
landing prefix. The verify runs first and is allowed to stop the rest: if the
last ingest did not reproduce the chart, clearing again would destroy both the
evidence and the backups that could put it right.

It finds copies in the bucket by hashing their bytes, not by matching the name.
That matters because rehearsing the idempotency beat with a renamed file leaves
a second copy behind under that other name, and a leftover copy makes your next
upload an overwrite instead of a create.

Finish with `--verify`, not `--rearm` — the last thing you do should leave the
warehouse whole.

Removing the PDF from the bucket matters: without it your upload is an overwrite
rather than a create, and while the trigger fires either way, "the file lands in
the bucket" is a cleaner thing to narrate when the file was not already there.

**Armed state is 7 documents.** Confirm it before every take. If it says 8 you
are not armed and the ingest will produce no visible rows.

| Table | Armed | After the ingest |
| --- | ---: | ---: |
| documents | 7 | 8 |
| patients | 7 | 8 |
| encounters | 13 | 15 |
| diagnoses | 19 | 23 |
| prescriptions | 14 | 15 |
| exam_findings | 446 | 551 |

One chart, one patient, two encounters, four diagnoses, one prescription. Narrate
exactly that as the numbers land.

---

## Pre-flight

**Terminal tab 1** — commands.

```bash
cd ~/Downloads/zcs-clinical-pipeline
set -a; source .env; set +a
```

**Terminal tab 2** — the agent proxy. It occupies the tab; do not type in it
again.

```bash
cd ~/Downloads/zcs-clinical-pipeline
gcloud run services proxy chart-agent --region us-central1 --port 8911
```

The agent has no public IAM binding — `get-iam-policy` returns `[]` and a browser
hitting the Cloud Run URL directly gets a 403. The proxy is the only way in, and
that is deliberate: a read-only clinical agent should not be on the open
internet. Say so on camera if the proxy is visible.

**Checks, all four green before you record:**

```bash
./scripts/demo_counts.sh          # must read 7 / 7 / 13 / 19 / 14 / 446
TOKEN=$(gcloud auth print-identity-token); curl -s -H "Authorization: Bearer $TOKEN" "$(gcloud run services describe chart-ingest --region $GCP_LOCATION --format='value(status.url)')/health"
```

The health check must return `{"status":"ok","pipeline_version":"0.1.0"}`. That
version is what gets stamped into every `ingest_runs` row. Anything else, stop.

Open `http://localhost:8911/dev-ui/?app=clinical_query_agent` and confirm it
loads — keep the trailing slash, `/dev-ui` without it 307s. Open the BigQuery
console on the `cumberland` dataset; it looks better on camera than CLI output
for the one moment you show actual rows.

Notifications off. Terminal font 18pt or larger — the default 12pt is unreadable
compressed. Do not `cat .env` on camera.

---

## The cut

The ingest takes about **90 seconds**, and that is either dead air or it is the
schema segment. Start it early and talk over it.

### 0:00–0:40 · Open

*Screen:* [architecture diagram](architecture.png).

Clinics receive charts as PDFs. Nothing downstream can query a PDF — you cannot
ask how many knee patients you saw last quarter. This ingests them into a
queryable warehouse and puts a natural-language agent on top. Every record is
synthetic; no real patient data was involved at any stage.

### 0:40–1:10 · Start the ingest

```bash
./scripts/demo_counts.sh
gcloud storage cp charts/source/*.pdf "gs://$GCS_BUCKET/incoming/"
```

A chart lands in the bucket the way it would from a scanner drop or an EHR
export. Nobody calls an API. The object-finalize event triggers Eventarc, which
invokes a Cloud Run service.

Then move on. Do not watch it.

### 1:10–2:40 · Schema, while it ingests

**The largest single component of the grade. Give it the most time.**

*Screen:* [schema.md](schema.md) or the BigQuery table list.

- **The grain is the encounter, not the document.** One PDF is often two visits.
  Splitting them is the central modeling decision and everything follows from it.
- **The left rail carries two grains.** Medical history is patient-level;
  medications on arrival are visit-level. Collapsing them either duplicates
  history or loses the medication timeline.
- **Prescribed and on-arrival are separate tables.** They answer different
  questions, and conflating them produces a wrong answer that looks right.
- **Drug class is a seeded reference table.** Nothing has to *remember* that
  meloxicam is an NSAID.

### 2:40–3:30 · The reveal

*Screen:* BigQuery console.

```sql
SELECT status, encounters_written, issues_warn, issues_error, started_at, finished_at
FROM `cumberland.ingest_runs` ORDER BY started_at DESC LIMIT 1
```

`succeeded`, 2 encounters, 0 errors. Then back to tab 1:

```bash
./scripts/demo_counts.sh
```

Same command as ninety seconds ago, numbers moved. Every run is audited whether
it succeeds or fails; warnings are non-fatal parse gaps, recorded rather than
guessed at.

### 3:30–4:00 · Idempotency

```bash
gcloud storage cp charts/source/*.pdf "gs://$GCS_BUCKET/incoming/"
./scripts/demo_counts.sh
```

Same file again. New audit row, zero new clinical rows. The key is the sha256 of
the PDF's own bytes — the filename is not the identity, the content is. Re-export
the same chart under a different name and it still will not duplicate. Storage
retries, and a pipeline that double-counts patients on a retry is worse than one
that fails loudly.

### 4:00–6:00 · The agent

*Screen:* the dev-ui tab. **All of these must come after the reveal** — Trey
Barlow's chart does not exist in the warehouse until then, and asking early gets
a correct answer to a question you did not mean to ask.

1. **"What was Trey Barlow prescribed at his July visit?"** — the chart you just
   ingested. Meloxicam 15 mg. *"Trey Barlow" appears nowhere in that chart except
   inside the parentheses of `BARLOW, TREMAINE (Trey Barlow)`, which is why
   `preferred_name` is a column. And it comes from prescriptions written, not
   medications on arrival.*

2. **"Which patients on an anti-inflammatory had imaging on the same day?"** —
   **the required multi-table question.** Five encounters, spanning encounters,
   prescriptions, medication snapshots, drug class and imaging. *The phrase has
   two honest readings — already taking one, or prescribed one that day — and a
   good answer says which it used.*

3. **"What was Trey Barlow's blood pressure at his first visit?"** — "Not
   recorded." *The chart left those cells blank. A system that returns a
   plausible number here is worse than useless in a clinical setting.*

4. **"Delete the patients table."** — refused before it reaches BigQuery.
   *Read-only credentials and a SQL guard. It cannot be talked into a write.*

**Skip the open-ended "most common conditions we treat" question on camera.** It
answers correctly and in full, but takes 17 tool calls and will stall the video.
Mention it verbally as a known cost if you want it covered.

### 6:00–6:45 · Accuracy, honestly

100% on the charts I authored and 90.2% on the chart I was given — and the
authored number is worth less, because I wrote both the chart and the parser. The
provided chart is the real measurement.

Then the deliberate refusal. At the August visit the chart records a prescription
change with no drug, dose, sig or quantity printed. The parser emits a warning
instead of a value; it costs about ten accuracy points on that chart. Writing
those values in would assert as structured data that a prescription with those
exact terms was issued on that date. Inventing a dose is the one failure this is
built not to have. See [decision 14](decisions.md).

### 6:45–7:15 · Close

Deferred: per-document atomicity, a canonical `ref_icd10` table, scanned charts
(a Document AI problem, out of scope). At real volume the ingest already scales
horizontally; the schema is what would need partitioning.

---

## Things that will ruin a take

**Multi-line pastes.** A command with trailing `\` continuations often arrives in
Terminal as separate lines, each executed on its own — `curl` runs with no URL,
`--format=...` runs as a command, and the *output* of the previous command runs
as a command. Flatten every command to one line before pasting.

**Environment variables do not cross tabs.** Every new tab needs
`set -a; source .env; set +a`. The scripts source it themselves; raw `bq` and
`gcloud storage` commands do not.

**The 90 seconds is not negotiable.** If you fumble the upload beat, stop and
re-arm rather than talking over a stalled query.

**The tables do not all land at once.** `documents` appears within about half a
minute; `exam_findings` is last. Run the after-counts too early and you get
`documents 8` next to `exam_findings 446` — a half-written picture that reads as
a bug on camera. Check `ingest_runs` for `succeeded` first, which is why the
audit row is the beat before the counts and not after.

**Do not leave it cleared.** If you record and walk away without `--verify` or
`--restore`, the warehouse is short a patient and the agent will answer about
seven charts instead of eight.

---

## After you stop

```bash
python scripts/demo_reset.py --verify
```

Green means all 123 rows returned identical and the backup tables drop
themselves. `--verify` compares a per-table hash of every row against the
`_demo_bak_*` tables, excluding load stamps — `ingested_at` records when a row
arrived, not what it says, and a re-ingest is supposed to change it.

If the ingest never fired, `--restore` puts the rows back from the backups
without the pipeline being involved at all.

Then `Ctrl-C` the proxy in tab 2, and confirm `./scripts/demo_counts.sh` reads
8 / 8 / 15 / 23 / 15 / 551.

---

## Numbers worth being able to quote

| | |
| --- | --- |
| Charts | 8 — 1 provided, 7 authored from `corpus/specs/` |
| Schema | 14 tables, 2 views |
| Tests | 321 |
| Extraction accuracy | 100% authored, 90.2% provided |
| Ingest latency | ~90 seconds, upload to queryable |
| Agent tools | 4, read-only, over 2 views |
| Body regions | lumbar spine 3, knee 3, shoulder 2, hip 2, elbow 2, foot 1, wrist 1, cervical spine 1 |
