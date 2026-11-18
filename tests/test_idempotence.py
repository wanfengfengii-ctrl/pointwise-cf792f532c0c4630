import json
from io import BytesIO
import zipfile
from pathlib import Path
from permit_capture.intake import IntakeService
from permit_capture.batch_state import IntakeError, BatchStatus
from permit_capture.review import ReviewService, ReviewError
from permit_capture.replay import verify_review_bundle
from test_capture_flow import WorkspaceCase, manifest, accept_all, table, row


class IdempotenceTests(WorkspaceCase):
    def test_duplicate_page(self):
        source = manifest()
        service = IntakeService(self.root, source)
        page = source.pages[0]
        first = service.ingest_page(source.batch_id, page.url, 1, page.html)
        second = service.ingest_page(source.batch_id, page.url, 1, page.html)
        self.assertEqual(first['sha256'], second['sha256'])
        self.assertEqual(second['status'], 'duplicate')
        self.assertEqual(len(service.get_batch(source.batch_id).accepted), 1)
        identifier = service.publish_batch(source.batch_id)
        records, refs = service.prepare_batch(source.batch_id)
        self.assertEqual(service.publisher(source.batch_id).publish(source.batch_id, records, refs), identifier)
        self.assertEqual(len(ReviewService(service.archive).read(identifier).records), 2)
        with self.assertRaises(IntakeError) as raised:
            service.ingest_page(source.batch_id, page.url, 1, page.html)
        self.assertEqual(raised.exception.code, 'BATCH_PUBLISHED')
        self.assertEqual(service.get_batch(source.batch_id).status, BatchStatus.PUBLISHED)

    def test_reinitialize_and_retry(self):
        source = manifest()
        service = IntakeService(self.root)
        identifier = service.run_manifest(source)
        folder = service.archive.publications / identifier
        original = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
        reopened = IntakeService(self.root)
        self.assertEqual(reopened.run_manifest(source), identifier)
        self.assertEqual(original, {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()})
        page = folder / 'pages/000001.html'
        page.write_bytes(page.read_bytes() + b' ')
        with self.assertRaises(IntakeError):
            IntakeService(self.root).run_manifest(source)
        self.assertTrue(page.read_bytes().endswith(b' '))

    def test_capture_store_is_not_repaired(self):
        source = manifest()
        service = IntakeService(self.root)
        accept_all(service, source)
        page = service.pages.path(source.batch_id, 1)
        page.write_bytes(b'user changed this file')
        with self.assertRaises(IntakeError):
            IntakeService(self.root, source)
        self.assertEqual(page.read_bytes(), b'user changed this file')

    def test_stable_export_and_pinned_version(self):
        source = manifest('A')
        service = IntakeService(self.root)
        identifier = service.run_manifest(source)
        review = ReviewService(service.archive)
        pinned = review.read(identifier, {'district': 'NORTH'})
        newer = service.run_manifest(manifest('B', [table(row(
            ['P-99', '2024-05-01', 'revoked', 'EAST', 'different publication']))]))
        self.assertNotEqual(identifier, newer)
        one, two = self.root / 'one.zip', self.root / 'two.zip'
        review.export_bundle(identifier, one, {'district': 'NORTH'})
        review.export_bundle(identifier, two, {'district': 'NORTH'})
        self.assertEqual(one.read_bytes(), two.read_bytes())
        self.assertEqual(review.export_bundle(identifier, one, {'district': 'NORTH'}), one)
        report = verify_review_bundle(one)
        self.assertTrue(report.ok, report.errors)
        with zipfile.ZipFile(BytesIO(one.read_bytes())) as bundle:
            selection = json.loads(bundle.read('selection.json'))
            self.assertEqual(selection['publication_id'], identifier)
            self.assertEqual(selection['row_keys'], [r['row_key'] for r in pinned.records])
            self.assertEqual(bundle.namelist(), sorted(bundle.namelist()))
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in bundle.infolist()))
        existing = self.root / 'existing.zip'
        existing.write_bytes(b'user content')
        with self.assertRaises(ReviewError):
            review.export_bundle(identifier, existing)
        self.assertEqual(existing.read_bytes(), b'user content')
        self.assertTrue(review.verify(identifier).ok)
