import argparse
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from .batch_state import CaptureBatch, BatchStatus, IntakeError
from .source_manifest import SourceManifest, validate_manifest, compare_manifest, load_manifest
from .page_store import PageStore, html_bytes, batch_token
from .url_rules import normalize_source_url
from .html_parser import parse_table
from .record_normalizer import normalize_rows
from .row_validation import RowValidationError
from .evidence import bind_row_evidence
from .archive import ArchiveRepository
from .archive_codec import canonical_snapshot_bytes
from .publication import PublicationService


class IntakeService:
    def __init__(self, workdir, manifest=None):
        self.root = Path(workdir)
        self.registry = self.root / 'intake'
        self.registry.mkdir(parents=True, exist_ok=True)
        self.pages = PageStore(self.root / 'captured-pages')
        self.archive = ArchiveRepository(self.root / 'archive')
        self.manifests, self.batches = {}, {}
        if manifest is not None:
            self.register(manifest)

    def state_path(self, batch_id):
        return self.registry / (batch_token(batch_id) + '.json')

    def save(self, batch_id):
        batch = self.batches[batch_id]
        obj = {'manifest': self.manifests[batch_id].descriptor(),
               'accepted': [{'page_no': no, 'url': url, 'sha256': digest}
                            for no, (url, digest) in sorted(batch.accepted.items())],
               'errors': batch.errors}
        descriptor, name = tempfile.mkstemp(prefix='.state-', dir=self.registry)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(canonical_snapshot_bytes(obj))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.state_path(batch_id))
        finally:
            Path(name).unlink(missing_ok=True)

    def get_batch(self, batch_id):
        if batch_id not in self.batches:
            try:
                obj = json.loads(self.state_path(batch_id).read_text(encoding='utf-8'))
                manifest = SourceManifest.from_descriptor(obj['manifest'])
                if manifest.batch_id != batch_id:
                    raise IntakeError('STATE', 'batch identity mismatch')
                batch = CaptureBatch(batch_id, len(manifest.pages))
                for item in obj['accepted']:
                    no = item['page_no']
                    source = manifest.pages[no - 1]
                    if (no, item['url'], item['sha256']) != (
                            source.page_no, source.url, source.sha256):
                        raise IntakeError('STATE', 'source declaration differs')
                    page = self.pages.load(batch_id, no, source.url, source.sha256)
                    batch.accept_page(page)
                batch.errors = list(obj['errors'])
                self.manifests[batch_id], self.batches[batch_id] = manifest, batch
            except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
                raise IntakeError('STATE', str(exc)) from exc
        batch = self.batches[batch_id]
        if self.archive.find_batch(batch_id) is not None:
            batch.status = BatchStatus.PUBLISHED
        return batch

    def register(self, manifest):
        validate_manifest(manifest)
        batch_id = manifest.batch_id
        if self.state_path(batch_id).exists() or batch_id in self.batches:
            self.get_batch(batch_id)
            compare_manifest(self.manifests[batch_id], manifest)
            return self.batches[batch_id]
        batch = CaptureBatch(batch_id, len(manifest.pages))
        self.manifests[batch_id], self.batches[batch_id] = manifest, batch
        self.save(batch_id)
        return batch

    def ingest_page(self, batch_id, page_url, page_no, html):
        batch = self.get_batch(batch_id)
        try:
            if batch.status == BatchStatus.PUBLISHED:
                raise IntakeError('BATCH_PUBLISHED', batch_id)
            raw = html_bytes(html)
            url = normalize_source_url(page_url)
            digest = sha256(raw).hexdigest()
            duplicate = batch.check_page(page_no, url, digest)
            source = self.manifests[batch_id].pages[page_no - 1]
            if (url, digest) != (source.url, source.sha256):
                raise IntakeError('PAGE_CONFLICT', 'page differs from declaration', page_no=page_no)
            page = self.pages.put(batch_id, page_no, url, raw)
            batch.accept_page(page)
            self.save(batch_id)
            return {'status': 'duplicate' if duplicate else 'accepted',
                    'page_no': page_no, 'sha256': digest}
        except (ValueError, OSError) as exc:
            error = exc if isinstance(exc, IntakeError) else IntakeError('INTAKE', str(exc))
            batch.remember(error)
            self.save(batch_id)
            raise error from exc

    def prepare_batch(self, batch_id):
        batch = self.get_batch(batch_id)
        try:
            missing = sorted(set(range(1, batch.expected_pages + 1)) - set(batch.accepted))
            if missing:
                raise IntakeError('MISSING_PAGES', 'declared pages are missing', missing=missing)
            parsed, refs = [], []
            manifest = self.manifests[batch_id]
            for source in manifest.pages:
                page = self.pages.load(batch_id, source.page_no, source.url, source.sha256)
                table = parse_table(page.data, manifest.table_selector)
                parsed.append((page.page_no, table.rows))
                refs.extend(bind_row_evidence(page, row) for row in table.rows)
            batch.mark_prepared()
            result = normalize_rows(parsed)
            if result.errors:
                raise RowValidationError(result.errors)
            self.save(batch_id)
            return result.records, tuple(refs)
        except (ValueError, OSError) as exc:
            batch.remember(exc)
            self.save(batch_id)
            raise

    def publisher(self, batch_id):
        return PublicationService(self.archive, self.manifests[batch_id],
                                  self.get_batch(batch_id), self.pages)

    def publish_batch(self, batch_id):
        records, refs = self.prepare_batch(batch_id)
        try:
            return self.publisher(batch_id).publish(batch_id, records, refs)
        finally:
            self.save(batch_id)

    def run_manifest(self, manifest):
        self.register(manifest)
        old = self.archive.find_batch(manifest.batch_id)
        if old is not None:
            from .replay import verify_publication
            report = verify_publication(self.archive, old)
            if not report.ok:
                raise IntakeError('ARCHIVE_DAMAGED', '; '.join(report.errors))
            return old
        for page in manifest.pages:
            if page.html is None:
                raise IntakeError('MANIFEST', 'run needs original HTML materials')
            self.ingest_page(manifest.batch_id, page.url, page.page_no, page.html)
        return self.publish_batch(manifest.batch_id)


def demonstrate(manifest):
    from .review import ReviewService
    from .replay import verify_review_bundle
    with tempfile.TemporaryDirectory(prefix='permit-demo-') as directory:
        service = IntakeService(directory)
        identifier = service.run_manifest(manifest)
        review = ReviewService(service.archive)
        result = review.read(identifier)
        actual = [r['fields'][0] for r in result.records]
        expected = ['CJ-001', 'CJ-002', 'CJ-003']
        assert actual == expected
        filters = {'district': 'NORTH', 'issued_from': '2024-02-29', 'issued_to': '2024-03-01'}
        selected = review.read(identifier, filters)
        keys = [r['row_key'] for r in selected.records]
        assert keys == ['permit-1', 'CJ-003']
        bundle = review.export_bundle(identifier, Path(directory) / 'review.zip', filters)
        report = verify_review_bundle(bundle)
        assert report.ok and report.checked_records == 3
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'V4.stable_export', 'input': manifest.descriptor(),
            'expected': expected, 'actual': actual,
            'downstream_expected': {'selected': ['permit-1', 'CJ-003'], 'offline_records': 3},
            'downstream_actual': {'selected': keys, 'offline_records': report.checked_records},
            'code_paths': ['permit_capture/intake.py', 'permit_capture/html_parser.py',
                           'permit_capture/publication.py', 'permit_capture/query.py',
                           'permit_capture/export.py', 'permit_capture/replay.py'],
            'oracle': 'manually enumerate three sample permits and two inclusive NORTH matches'
        }, ensure_ascii=False))
        print(json.dumps({'publication_id': identifier, 'records': result.records,
                          'offline_review': asdict(report)}, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Capture offline municipal permit tables')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--case')
    parser.add_argument('--workdir')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest, args.case)
        if args.demo:
            demonstrate(manifest)
        else:
            if not args.workdir:
                parser.error('--workdir required unless --demo')
            identifier = IntakeService(args.workdir).run_manifest(manifest)
            print(json.dumps({'publication_id': identifier}))
        return 0
    except (ValueError, OSError) as exc:
        detail = exc.as_dict() if isinstance(exc, IntakeError) else {
            'code': type(exc).__name__, 'detail': str(exc)}
        print(json.dumps({'error': detail}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
