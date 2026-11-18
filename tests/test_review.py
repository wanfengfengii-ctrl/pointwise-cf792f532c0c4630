from pathlib import Path
from permit_capture.intake import IntakeService
from permit_capture.review import ReviewService, ReviewError
from permit_capture.query import select_records
from permit_capture.reader import load_publication
from test_capture_flow import WorkspaceCase, manifest, row, table


class ReviewTests(WorkspaceCase):
    def publish_rows(self, batch='review'):
        rows = [
            ['P-01', '2024-02-29', 'approved', 'NORTH', 'A'],
            ['P-02', '2024-03-01', 'pending', 'NORTH', ''],
            ['P-03', '2024-03-02', 'approved', 'NORTH', 'C'],
            ['P-04', '2024-03-01', 'approved', 'EAST', 'D']]
        service = IntakeService(self.root)
        identifier = service.run_manifest(manifest(batch, [table(''.join(row(v) for v in rows))]))
        return service, identifier

    def test_read_and_verify(self):
        service, identifier = self.publish_rows()
        review = ReviewService(service.archive)
        selected = review.read(identifier, {'permit_no': 'p-02', 'district': 'NORTH',
                                           'issued_from': '2024-02-29', 'issued_to': '2024-03-01'})
        self.assertEqual([r['row_key'] for r in selected.records], ['P-02'])
        self.assertTrue(review.verify(identifier).ok)
        self.assertEqual(review.verify(identifier).checked_records, 4)

    def test_filtered_snapshot_and_inclusive_dates(self):
        service, identifier = self.publish_rows()
        review = ReviewService(service.archive)
        result = review.read(identifier, {'district': 'NORTH', 'issued_from': '2024-02-29',
                                          'issued_to': '2024-03-01'})
        self.assertEqual([r['row_key'] for r in result.records], ['P-01', 'P-02'])
        self.assertEqual(review.read(identifier, {'permit_no': 'ABSENT'}).records, [])
        newer = service.run_manifest(manifest('newer'))
        self.assertNotEqual(identifier, newer)
        self.assertEqual(len(review.read(identifier).records), 4)

    def test_cursor_binding(self):
        service, identifier = self.publish_rows()
        review = ReviewService(service.archive)
        filters = {'district': 'NORTH'}
        first = review.read(identifier, filters, limit=1)
        second = review.read(identifier, filters, limit=1, cursor=first.next_cursor)
        third = review.read(identifier, filters, limit=1, cursor=second.next_cursor)
        self.assertEqual([r['row_key'] for p in (first, second, third) for r in p.records],
                         ['P-01', 'P-02', 'P-03'])
        self.assertIsNone(third.next_cursor)
        other = service.run_manifest(manifest('other'))
        for publication, conditions, cursor in [
                (identifier, {'district': 'EAST'}, first.next_cursor),
                (other, filters, first.next_cursor), (identifier, filters, '%%%')]:
            with self.subTest(publication=publication, filters=conditions):
                with self.assertRaises(ReviewError):
                    review.read(publication, conditions, cursor=cursor)
        for conditions in [{'unknown': 'x'}, {'issued_from': '2025-02-29'},
                           {'issued_from': '2024-03-02', 'issued_to': '2024-03-01'},
                           {'district': 'MISSING'}]:
            with self.assertRaises(ReviewError):
                review.read(identifier, conditions)
        for size in (0, True, 1001):
            with self.assertRaises(ReviewError):
                review.read(identifier, limit=size)

    def test_detached_read(self):
        service, identifier = self.publish_rows()
        review = ReviewService(service.archive)
        result = review.read(identifier)
        result.records[0]['fields'][0] = 'FORGED'
        result.records[0]['evidence']['start'] = 0
        self.assertEqual(review.read(identifier).records[0]['fields'][0], 'P-01')
        self.assertTrue(review.verify(identifier).ok)
        with self.assertRaises(ReviewError):
            review.read('0' * 64)

    def test_replay_after_external_inputs_removed(self):
        from permit_capture.source_manifest import load_manifest
        import json
        page_path = self.root / 'original.html'
        page_path.write_text(table(row(['P-9', '2024-01-01', 'approved', 'WEST', ''])), encoding='utf-8')
        manifest_path = self.root / 'source.json'
        manifest_path.write_text(json.dumps({'batch_id': 'external', 'table_selector': '#permits',
            'pages': [{'page_no': 1, 'url': 'https://example.test/external', 'path': 'original.html'}]}))
        source = load_manifest(manifest_path)
        service = IntakeService(self.root / 'work')
        identifier = service.run_manifest(source)
        page_path.unlink()
        manifest_path.unlink()
        self.assertTrue(ReviewService(service.archive).verify(identifier).ok)
        self.assertEqual(ReviewService(service.archive).read(identifier).records[0]['row_key'], 'P-9')
