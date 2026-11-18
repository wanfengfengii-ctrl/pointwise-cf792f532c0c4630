from .batch_state import BatchStatus
from .archive_codec import build_snapshot, canonical_snapshot_bytes, publication_fingerprint
from .evidence import bind_row_evidence
from .html_parser import parse_table
from .record_normalizer import normalize_rows
from .row_validation import validate_batch_records
from .reader import load_publication


class PublishError(ValueError):
    pass


class PublicationService:
    def __init__(self, repository, manifest, batch, page_store):
        self.repository = repository
        self.manifest = manifest
        self.batch = batch
        self.page_store = page_store

    def publish(self, batch_id, records, evidence_refs):
        candidate = None
        try:
            if batch_id != self.manifest.batch_id or self.batch.status not in (
                    BatchStatus.PREPARED, BatchStatus.PUBLISHED):
                raise PublishError('BATCH_NOT_PREPARED')
            self.batch.mark_prepared()
            records = validate_batch_records(records)
            refs = tuple(evidence_refs)
            positions = [(r.page_no, r.row_no) for r in refs]
            if len(set(positions)) != len(positions) or set(positions) != {
                    (r.page_no, r.row_no) for r in records}:
                raise PublishError('EVIDENCE_SET')
            pages, parsed = {}, []
            expected_refs = []
            for source in self.manifest.pages:
                if self.batch.accepted[source.page_no] != (source.url, source.sha256):
                    raise PublishError('SOURCE_CONFLICT')
                page = self.page_store.load(batch_id, source.page_no, source.url, source.sha256)
                pages[source.page_no] = page.data
                table = parse_table(page.data, self.manifest.table_selector)
                parsed.append((page.page_no, table.rows))
                expected_refs.extend(bind_row_evidence(page, row) for row in table.rows)
            normalized = normalize_rows(parsed)
            if normalized.errors:
                raise PublishError(str(normalized.errors))
            proposed = build_snapshot(self.manifest, records, refs)
            expected = build_snapshot(self.manifest, normalized.records, expected_refs)
            if proposed != expected:
                raise PublishError('CANDIDATE_REPLAY_MISMATCH')
            identifier = publication_fingerprint(proposed)
            old = self.repository.find_batch(batch_id)
            if old is not None:
                load_publication(self.repository, old)
                if old != identifier:
                    raise PublishError('BATCH_CONFLICT')
                self.batch.status = BatchStatus.PUBLISHED
                return old
            candidate = self.repository.stage(canonical_snapshot_bytes(proposed), pages)
            result = self.repository.commit(candidate)
            self.batch.status = BatchStatus.PUBLISHED
            return result
        except (ValueError, OSError, KeyError, TypeError) as exc:
            if candidate is not None:
                self.repository.discard(candidate)
            error = exc if isinstance(exc, PublishError) else PublishError(str(exc))
            self.batch.remember(error)
            raise error from exc
