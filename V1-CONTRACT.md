# V1 data and failure contract

## Sources and intake

A declaration has a nonempty Unicode batch_id, a single id selector and one or
more ordered pages numbered 1..N. Each input page has page_no, an absolute
http/https url, and exactly one of html (text) or path (relative to the declaration).
Paths cannot escape the declaration directory. Named declarations may be held in
one JSON object and selected with --case. SourceManifest stores normalized source
URLs, original UTF-8 bytes and SHA-256 digests. URL scheme and host are lowercased;
paths and query order are preserved. Credentials, fragments, whitespace and
backslashes are forbidden. There is no network I/O.

IntakeService.register fixes this declaration. ingest_page returns
{status: accepted|duplicate, page_no, sha256}; errors raise IntakeError with
code/detail/context and as_dict(). Same page/url/bytes before publication is an
idempotent retry. A new page must follow its predecessor. Changed pages,
invalid UTF-8, invalid page numbers or changed declarations are rejected.
I1 always raises BATCH_PUBLISHED after publication, including exact retries.
Batch reruns use run_manifest and publication equality instead.

CaptureBatch is PREPARING until every declared page and every page structure
passes. Only then does it become PREPARED and normalization begin. Field errors
retain PREPARED candidates and diagnostic history but prevent publication.
PUBLISHED derives from the committed immutable publication; there is no separate
early success flag. Errors are retained in intake state across reinitialization.
Original captured page modifications are detected, never overwritten.

## HTML and records

The target is exactly one table with the selected id. Its direct structural
children are one thead followed by one tbody. The thead contains one row whose
cell text, after trimming, is exactly:

permit_no, issued_on, status, district, applicant

Rows use td or th and must contain exactly five cells. Data rows have unit spans:
omitted attributes, 1 and positive integer spellings equal to 1 are accepted.
Zero, signed, nondecimal or empty spans are invalid; values greater than 1 are
unsupported ParseError results. Repeated column-name rows in tbody, incorrect
widths, duplicate target ids, nesting and mismatched/unclosed tags are rejected.
Ordinary inline markup is accepted within cells. Standard HTML void tags are
supported; br contributes a newline. Comments remain in the original evidence
but do not contribute text. No browser HTML error recovery is performed.

Non-cell elements whose class tokens include footnote are excluded as subtrees
from data structure and cell text. A containing td/th remains a column even when
its visible text becomes empty. Entity decoding happens exactly once. The
original bytes, including footnotes and comments, remain unchanged.

All fields use Unicode NFC and outer whitespace trimming. Empty applicant is
null; empty other fields fail. Permit numbers must be ASCII, are uppercased and
match [A-Z0-9][A-Z0-9/-]{0,39}. Dates are exact YYYY-MM-DD real calendar days in
years 1..9999. Status is approved/pending/revoked; district is
NORTH/EAST/SOUTH/WEST. No unknown-value defaults are applied.

An explicit data-row-key is NFC/trimmed, nonempty and case-sensitive. Otherwise
the normalized permit number is the key. Every actual data row must have a unique
key within its batch, even if its values equal another row. Different batches may
reuse permits. C1 removes retries before rows exist. Coordinates count only
target data rows and columns, starting at 1. Source ranges are half-open offsets
in the full UTF-8 page bytes. Field errors include code, key candidate, page,
row and column. NormalizationResult retains errors; preparation raises
RowValidationError if any exist.

PermitRecord is immutable and contains row_key, a five-value fields tuple,
page_no, row_no, start and end. EvidenceRef contains page_no, row_no, start, end,
page_sha256 and fragment_sha256. It describes the complete source tr, including
its opening/closing tags; it is not a per-field source map.

## Publication

PublicationService.publish accepts the batch id, normalized records and row refs.
Its context contains the prepared batch, declaration, captured pages and archive.
It checks fields and unique keys, complete source and evidence sets, actual page
and row hashes, and reparses every source to reject incomplete or fabricated
candidates. Any failure raises PublishError and leaves prior publications intact.

A canonical snapshot includes format/rule version, batch id, selector, fixed
columns, ordered page URLs/hashes/paths and every sorted record with row evidence.
Records sort by (page_no,row_no,row_key). JSON is UTF-8, sorted keys, compact
separators, explicit null and no nonfinite values. The SHA-256 of these bytes is
publication_id. Clocks, directory enumeration and absolute paths are excluded.

The repository writes and checks a complete invisible candidate, then renames
that directory into publications on the same filesystem. Readers ignore staging.
Equal candidates reuse the existing id; any changed field, URL, original byte or
reference for the same batch conflicts. This is a single-writer local API;
concurrent multi-process mutation is outside the contract. On successful return
the snapshot and pages have been flushed. No user directories or old publications
are reset during initialization or failed staging.

## Research and bundles

ReviewService.read returns QueryResult(publication_id, records, next_cursor).
Records are detached dictionaries with fields and EvidenceRef dictionaries.
The service checks integrity and semantic replay before returning successful data.
Unknown or damaged versions and bad query contexts raise ReviewError.
verify returns VerificationReport(ok, publication_id, checked_records, errors)
and reports damage without modifying storage.

Filters are permit_no, district, issued_from and issued_to; supplied conditions
intersect and date bounds are inclusive. Unknown filters and reversed bounds fail.
Records sort by (permit_no,row_key). Limit is an integer 1..1000. A cursor binds
the explicit publication, canonical filters and an existing last ordering key.
It guarantees context consistency, not access control.

Export always consumes the pinned version. ZIP members are sorted with fixed
1980 timestamps, fixed attributes and stored compression. snapshot.json, every
bound page and selection.json (publication_id, canonical filters, ordered
row_keys) are included. Repeated export is byte-identical. Existing different
destination bytes are not overwritten. Temporary output is removed on failure.
Offline verification recalculates snapshot identity, actual page/fragment hashes,
normalization, keys, coordinates and the selection index. No external input or
network is used, and no damaged evidence is repaired.

## Regression map

The original scenario anchors are TestCase methods, retained in the six files:
test_capture_flow.py::CaptureFlowTests.test_single_page_publish (V1);
test_parser.py::ParserTests.test_footnote_and_empty (V2);
test_review.py::ReviewTests.test_read_and_verify (V3);
test_idempotence.py::IdempotenceTests.test_duplicate_page (V4);
test_failures.py::FailureTests.test_missing_page_or_table (V5),
test_invalid_fields (V6), test_conflict_preserves_old (V7);
test_replay.py::ReplayTests.test_corrupt_evidence (V8).

The complete unittest discovery command is authoritative and includes all
additional ordering, identity, calendar, cursor, corruption, retry, I/O rejection
and deterministic export cases. Focused file discovery commands in RUNBOOK
retain this full-suite command as the first verification entry.
