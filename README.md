# Municipal permit capture

Python 3.11 standard-library tools for the fictional Chengjiang municipal records centre. Clerks submit saved UTF-8 pages; researchers receive fixed permit records, source evidence and reproducible offline review bundles. No network access is performed.

A working day:
1. Load a batch declaration containing its id, target table id and ordered pages.
2. Accept pages consecutively from page 1, preserving exact bytes and source addresses.
3. Check every declared page and target table before normalizing any records.
4. Normalize the five fields and reject invalid dates, values or duplicate row identities.
5. Recheck the complete candidate against its pages and publish one immutable directory.
6. Select a fixed publication by permit number, district and inclusive issue-date bounds.
7. Replay its archived evidence, independently of the original input files.
8. Export the same version and selection as a deterministic ZIP for a colleague.

## Run

From this directory, with Python 3.11 available:

```sh
python3.11 -B -m unittest discover -s tests -v
python3.11 -B -m permit_capture.intake --manifest examples/normal_day.json --case normal_day --demo
python3.11 -B -m permit_capture.intake --manifest examples/normal_day.json --case normal_day --workdir .permit-work
```

The last command prints the computed publication id. Use that id with:
`python3.11 -B -m permit_capture.review read --workdir .permit-work --publication ID`.
The review actions are `read`, `verify`, `export` (requires `--output`) and
`offline --bundle PATH`. Filters are a JSON object supplied with `--filters`.
Use `--limit` and `--cursor` for stable pagination. Business failures exit 2.

The demo uses a temporary directory, actually captures three permits, selects the
two NORTH records on the inclusive date range, builds a bundle and replays it.
It asserts results before emitting a POINTWISE_SCENARIO JSON line.

## Public API

- `source_manifest.load_manifest(path, case=None)` reads inline HTML or relative page files.
- `IntakeService(workdir, manifest=None)`, `register(manifest)`,
  `ingest_page(batch_id, page_url, page_no, html)`,
  `prepare_batch(batch_id)` and `publish_batch(batch_id)` support manual intake.
- `IntakeService.run_manifest(manifest)` runs a complete declaration, or checks and
  returns its identical existing publication.
- `html_parser.parse_table(html, '#permits')` returns complete page-local rows.
- `PublicationService(repository, manifest, batch, page_store).publish(batch_id,
  records, evidence_refs)` independently checks publication eligibility.
- `ReviewService(repository).read(publication_id, filters=None, limit=100,
  cursor=None)`, `verify(publication_id)` and
  `export_bundle(publication_id, destination, filters=None)` work on fixed versions.
- `replay.verify_review_bundle(path_or_bytes)` checks a bundle without a work directory.

See V1-CONTRACT.md for types, field rules, supported HTML and failure semantics.
RUNBOOK.json and .pointwise-runtime.json contain executable argument arrays.

## Storage and responsibilities

`batch_state`, `source_manifest`, `page_store`, `url_rules` and `intake`
own declarations, retries and captured inputs. `table_locator`, `schema`,
`footnotes`, `grid` and `html_parser` produce byte-positioned page-local rows.
`field_rules`, `identity`, `row_validation` and `record_normalizer` produce
validated permits. `evidence`, `archive_codec`, `archive`, `publication`
and `reader` own bound immutable publications. `filter_rules`, `query`,
`review`, `replay` and `export` perform fixed-version research and delivery.

Initialization creates only missing directories. Accepted inputs and published
batches cannot be overwritten. Input corrections use a new batch id. Reopening
checks stored material and never repairs user modifications silently. Published
pages are separate copies, so removal of external input files is harmless.

This is a local, single-writer V1. It accepts fixed columns and complete rows on
each page. Spans other than 1, nested tables and tbody headers are explicit errors.
Evidence is at whole-row granularity. The format provides integrity checks, not
authentication. It does not provide a browser, network fetching or field inference.
