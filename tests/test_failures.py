from dataclasses import replace
from hashlib import sha256
from permit_capture.intake import IntakeService
from permit_capture.batch_state import IntakeError, BatchStatus
from permit_capture.schema import ParseError
from permit_capture.row_validation import RowValidationError
from permit_capture.publication import PublicationService, PublishError
from permit_capture.archive_codec import build_snapshot, publication_fingerprint
from permit_capture.review import ReviewService
from permit_capture.url_rules import normalize_source_url, SourceUrlError
from test_capture_flow import WorkspaceCase, manifest, table, row, accept_all


class FailureTests(WorkspaceCase):
    def test_missing_page_or_table(self):
        source = manifest('missing', [table(''), table(''), table('')])
        service = IntakeService(self.root, source)
        page = source.pages[0]
        for number in (0, -1, True, 1.0, 3):
            with self.subTest(number=number):
                with self.assertRaises(IntakeError):
                    service.ingest_page(source.batch_id, page.url, number, page.html)
        service.ingest_page(source.batch_id, page.url, 1, page.html)
        with self.assertRaises(IntakeError) as raised:
            service.prepare_batch(source.batch_id)
        self.assertEqual(raised.exception.context['missing'], [2, 3])
        self.assertEqual(service.get_batch(source.batch_id).status, BatchStatus.PREPARING)
        self.assertIsNone(service.archive.find_batch(source.batch_id))
        broken = manifest('no-table', ['<div>no table</div>'])
        accept_all(service, broken)
        with self.assertRaises(ParseError):
            service.prepare_batch(broken.batch_id)
        self.assertEqual(service.get_batch(broken.batch_id).status, BatchStatus.PREPARING)
        self.assertTrue(service.get_batch(broken.batch_id).errors)

    def test_invalid_fields(self):
        service = IntakeService(self.root)
        valid = ['P-1', '2024-02-29', 'approved', 'NORTH', '']
        errors = [(0, 'bad permit'), (0, 'ß'), (1, '2025-02-29'), (1, '2026-04-31'),
                  (1, '2024-2-29'), (1, '0000-01-01'), (2, 'APPROVED'), (3, 'UNKNOWN'),
                  (2, ''), (3, '')]
        for index, (column, value) in enumerate(errors):
            fields = valid.copy()
            fields[column] = value
            source = manifest('invalid-' + str(index), [table(row(fields))])
            accept_all(service, source)
            with self.assertRaises(RowValidationError) as raised:
                service.prepare_batch(source.batch_id)
            self.assertEqual(raised.exception.issues[0].column_no, column + 1)
            self.assertIsNone(service.archive.find_batch(source.batch_id))
        for i, date in enumerate(('0001-01-01', '9999-12-31', '2024-02-29')):
            fields = valid.copy()
            fields[1] = date
            self.assertTrue(service.run_manifest(manifest('valid-' + str(i), [table(row(fields))])))

    def test_conflict_preserves_old(self):
        old_service = IntakeService(self.root / 'old')
        old_id = old_service.run_manifest(manifest('fixed'))
        original = (old_service.archive.publications / old_id / 'snapshot.json').read_bytes()
        changes = [manifest('fixed', [table(row(['P-1', '2024-01-01', 'revoked', 'NORTH', '']))]),
                   manifest('fixed', [manifest('fixed').pages[0].html.decode() + '<!--new source-->'])]
        for i, source in enumerate(changes):
            candidate = IntakeService(self.root / f'candidate-{i}')
            accept_all(candidate, source)
            records, refs = candidate.prepare_batch(source.batch_id)
            publisher = PublicationService(old_service.archive, source, candidate.get_batch(source.batch_id),
                                           candidate.pages)
            with self.assertRaises(PublishError):
                publisher.publish(source.batch_id, records, refs)
            self.assertEqual((old_service.archive.publications / old_id / 'snapshot.json').read_bytes(), original)
            self.assertTrue(ReviewService(old_service.archive).verify(old_id).ok)
        source = manifest('fixed')
        changed = replace(source, pages=(replace(source.pages[0], url='https://other.example.test/p'),))
        candidate = IntakeService(self.root / 'candidate-url')
        accept_all(candidate, changed)
        records, refs = candidate.prepare_batch(changed.batch_id)
        with self.assertRaises(PublishError):
            PublicationService(old_service.archive, changed, candidate.get_batch(changed.batch_id),
                               candidate.pages).publish(changed.batch_id, records, refs)

    def test_row_key_conflict_and_new_batch_repair(self):
        service = IntakeService(self.root)
        values = ['P-1', '2024-01-01', 'approved', 'NORTH', '']
        duplicate = manifest('duplicate', [table(row(values, 'same') + row(values, 'same'))])
        accept_all(service, duplicate)
        with self.assertRaises(RowValidationError):
            service.prepare_batch(duplicate.batch_id)
        self.assertIsNone(service.archive.find_batch(duplicate.batch_id))
        self.assertTrue(service.get_batch(duplicate.batch_id).errors)
        repaired = manifest('repaired', [table(row(values, 'first') + row(values, 'second'))])
        identifier = service.run_manifest(repaired)
        self.assertEqual(len(ReviewService(service.archive).read(identifier).records), 2)
        self.assertTrue(IntakeService(self.root).get_batch('duplicate').errors)

    def test_atomic_reject_evidence_and_commit_io(self):
        service = IntakeService(self.root)
        old_id = service.run_manifest(manifest('old'))
        source = manifest('candidate')
        accept_all(service, source)
        records, refs = service.prepare_batch(source.batch_id)
        publisher = service.publisher(source.batch_id)
        with self.assertRaises(PublishError):
            publisher.publish(source.batch_id, records[:-1], refs[:-1])
        for invalid in (refs[:-1], (replace(refs[0], fragment_sha256='0' * 64),) + refs[1:],
                        (replace(refs[0], start=refs[0].start + 1),) + refs[1:]):
            with self.assertRaises(PublishError):
                publisher.publish(source.batch_id, records, invalid)
            self.assertIsNone(service.archive.find_batch(source.batch_id))
        expected_id = publication_fingerprint(build_snapshot(source, records, refs))
        obstruction = service.archive.publications / expected_id
        obstruction.write_bytes(b'preexisting obstruction')
        with self.assertRaises(PublishError):
            publisher.publish(source.batch_id, records, refs)
        self.assertEqual(obstruction.read_bytes(), b'preexisting obstruction')
        self.assertEqual(list(service.archive.staging.iterdir()), [])
        self.assertTrue(ReviewService(service.archive).verify(old_id).ok)
        self.assertEqual(service.get_batch(source.batch_id).status, BatchStatus.PREPARED)
        obstruction.unlink()
        self.assertEqual(publisher.publish(source.batch_id, records, refs), expected_id)

    def test_utf8_url_and_page_conflict(self):
        for url in ('ftp://example.test/a', 'https://u:p@example.test/a', 'https://example.test/#x',
                    'https://example.test/a b', 'http:///missing'):
            with self.assertRaises(SourceUrlError):
                normalize_source_url(url)
        self.assertEqual(normalize_source_url('HTTPS://Example.Test/a?b=2&a=1'),
                         'https://example.test/a?b=2&a=1')
        source = manifest()
        service = IntakeService(self.root, source)
        page = source.pages[0]
        with self.assertRaises(IntakeError):
            service.ingest_page(source.batch_id, page.url, 1, b'\xff')
        self.assertEqual(service.get_batch(source.batch_id).accepted, {})
        service.ingest_page(source.batch_id, page.url, 1, page.html)
        with self.assertRaises(IntakeError) as raised:
            service.ingest_page(source.batch_id, page.url, 1, page.html + b' ')
        self.assertEqual(raised.exception.code, 'PAGE_CONFLICT')
        self.assertEqual(service.pages.path(source.batch_id, 1).read_bytes(), page.html)

    def test_structure_precedes_normalization(self):
        first = table(row(['P-1', '2025-02-29', 'approved', 'NORTH', '']))
        source = manifest('structure-first', [first, '<div>missing table</div>'])
        service = IntakeService(self.root)
        accept_all(service, source)
        with self.assertRaises(ParseError):
            service.prepare_batch(source.batch_id)
        self.assertEqual(service.get_batch(source.batch_id).status, BatchStatus.PREPARING)
        self.assertIsNone(service.archive.find_batch(source.batch_id))
