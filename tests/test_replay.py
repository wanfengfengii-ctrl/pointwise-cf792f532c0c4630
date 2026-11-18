from io import BytesIO
import json
import zipfile
from permit_capture.intake import IntakeService
from permit_capture.review import ReviewService, ReviewError
from permit_capture.replay import verify_review_bundle
from permit_capture.archive_codec import canonical_snapshot_bytes
from permit_capture.export import build_review_bundle
from permit_capture.reader import load_publication
from test_capture_flow import WorkspaceCase, manifest


class ReplayTests(WorkspaceCase):
    def test_corrupt_evidence(self):
        for mode in ('page-delete', 'page-byte', 'offset', 'fragment-digest'):
            with self.subTest(mode=mode):
                service = IntakeService(self.root / mode)
                identifier = service.run_manifest(manifest(mode))
                folder = service.archive.publications / identifier
                page = folder / 'pages/000001.html'
                if mode == 'page-delete':
                    page.unlink()
                elif mode == 'page-byte':
                    page.write_bytes(page.read_bytes().replace(b'approved', b'ApproVed'))
                else:
                    path = folder / 'snapshot.json'
                    obj = json.loads(path.read_bytes())
                    ref = obj['records'][0]['evidence']
                    if mode == 'offset':
                        ref['start'] += 1
                    else:
                        ref['fragment_sha256'] = '0' * 64
                    path.write_bytes(canonical_snapshot_bytes(obj))
                review = ReviewService(service.archive)
                report = review.verify(identifier)
                self.assertFalse(report.ok)
                self.assertTrue(report.errors)
                with self.assertRaises(ReviewError):
                    review.read(identifier)
                output = self.root / (mode + '.zip')
                with self.assertRaises(ReviewError):
                    review.export_bundle(identifier, output)
                self.assertFalse(output.exists())

    def test_bundle_index_and_material_tampering(self):
        service = IntakeService(self.root)
        identifier = service.run_manifest(manifest())
        raw = build_review_bundle(load_publication(service.archive, identifier), {'district': 'NORTH'})
        self.assertTrue(verify_review_bundle(raw).ok)
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            original = {name: archive.read(name) for name in archive.namelist()}
        for mode in ('index', 'page', 'missing'):
            members = dict(original)
            if mode == 'index':
                selection = json.loads(members['selection.json'])
                selection['row_keys'] = ['P-02']
                members['selection.json'] = canonical_snapshot_bytes(selection)
            elif mode == 'page':
                members['pages/000001.html'] += b' '
            else:
                del members['pages/000001.html']
            output = BytesIO()
            with zipfile.ZipFile(output, 'w') as archive:
                for name, data in members.items():
                    archive.writestr(name, data)
            self.assertFalse(verify_review_bundle(output.getvalue()).ok)
        self.assertTrue(ReviewService(service.archive).verify(identifier).ok)
