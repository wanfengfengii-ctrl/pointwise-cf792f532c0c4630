"""Cross-page permit capture entry point (V2).

Accept the declared pages, expand rowspan/colspan across page boundaries and
publish one immutable publication into the existing archive.  V1 behaviour is
untouched; this module only adds the ``Capture`` entry point required by
CROSSPAGE.md.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

from permit_capture.archive import ArchiveRepository
from permit_capture.archive_codec import canonical_snapshot_bytes
from permit_capture.page_store import PageStore, batch_token, html_bytes
from permit_capture.reader import PublicationSnapshot
from permit_capture.replay import VerificationReport
from permit_capture.source_manifest import SourceManifest, compare_manifest, validate_manifest
from permit_capture.span_capture import (SpanError, derive_span_content,
                                        validate_span_materials, verify_span_snapshot)
from permit_capture.url_rules import normalize_source_url


class CaptureError(ValueError):
    """Raised for any cross-page intake, publication or replay failure."""


STATE_VERSION = 1


class Capture:
    """Collect ordered pages for one declaration and publish their merged grid.

    With a manifest the instance is bound to that batch for writing.  Without a
    manifest it only reads and verifies already published batches, using the
    bound original pages stored in the archive.
    """

    def __init__(self, workdir, manifest=None):
        self.root = Path(workdir)
        self.registry = self.root / 'crosspage'
        self.registry.mkdir(parents=True, exist_ok=True)
        self.pages = PageStore(self.root / 'crosspage-pages')
        self.archive = ArchiveRepository(self.root / 'archive')
        self.manifest = None
        if manifest is not None:
            self.register(manifest)

    # -- declaration state -------------------------------------------------

    def state_path(self, batch_id):
        return self.registry / (batch_token(batch_id) + '.json')

    def load_state(self, batch_id):
        path = self.state_path(batch_id)
        try:
            obj = json.loads(path.read_text(encoding='utf-8'))
            if set(obj) != {'version', 'manifest', 'accepted'} or obj['version'] != STATE_VERSION:
                raise CaptureError('stored capture state differs')
            try:
                manifest = SourceManifest.from_descriptor(obj['manifest'])
            except ValueError as exc:
                raise CaptureError(f'invalid stored declaration: {exc}') from exc
            if manifest.batch_id != batch_id:
                raise CaptureError('stored batch identity differs')
            accepted = []
            for item in obj['accepted']:
                if set(item) != {'page_no', 'url', 'sha256'}:
                    raise CaptureError('stored accepted page shape differs')
                accepted.append((item['page_no'], item['url'], item['sha256']))
            return manifest, accepted
        except CaptureError:
            raise
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            raise CaptureError(f'stored capture state is unreadable: {exc}') from exc

    def save_state(self, accepted):
        manifest = self.manifest
        obj = {'version': STATE_VERSION, 'manifest': manifest.descriptor(),
               'accepted': [{'page_no': no, 'url': url, 'sha256': digest}
                            for no, url, digest in accepted]}
        descriptor, temporary = tempfile.mkstemp(prefix='.capture-', dir=self.registry)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(canonical_snapshot_bytes(obj))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path(manifest.batch_id))
        finally:
            Path(temporary).unlink(missing_ok=True)

    def accepted_pages(self, batch_id):
        if not self.state_path(batch_id).exists():
            return None
        manifest, accepted = self.load_state(batch_id)
        for page_no, url, digest in accepted:
            if type(page_no) is not int or not 1 <= page_no <= len(manifest.pages):
                raise CaptureError('accepted page outside declaration')
            source = manifest.pages[page_no - 1]
            if (url, digest) != (source.url, source.sha256):
                raise CaptureError('accepted page differs from declaration')
            try:
                self.pages.load(batch_id, page_no, url, digest)
            except (ValueError, OSError) as exc:
                # A published batch replays from its own bound copies; the
                # transient intake copies may legitimately be gone by then.
                if self.archive.find_batch(batch_id) is None:
                    raise CaptureError(f'accepted page is unreadable: {exc}') from exc
        return manifest, accepted

    def register(self, manifest):
        if not isinstance(manifest, SourceManifest):
            raise CaptureError('a SourceManifest is required')
        try:
            validate_manifest(manifest)
        except ValueError as exc:
            raise CaptureError(str(exc)) from exc
        stored = self.accepted_pages(manifest.batch_id)
        if stored is not None:
            stored_manifest, _ = stored
            try:
                compare_manifest(stored_manifest, manifest)
            except ValueError as exc:
                raise CaptureError(str(exc)) from exc
        self.manifest = manifest
        if stored is not None:
            return manifest
        if not self.state_path(manifest.batch_id).exists():
            self.adopt_published(manifest)
        return manifest

    def adopt_published(self, manifest):
        """Record an already-published declaration so exact retries stay duplicates."""
        try:
            published = self.archive.find_batch(manifest.batch_id)
        except ValueError as exc:
            raise CaptureError(str(exc)) from exc
        if published is None:
            self.save_state([])
            return
        try:
            content, _ = self.archive.load(published)
            stored = SourceManifest.from_descriptor(
                {'batch_id': content['batch_id'],
                 'table_selector': content['table_selector'],
                 'pages': [{k: p[k] for k in ('page_no', 'url', 'sha256')}
                           for p in content['pages']]})
        except ValueError as exc:
            raise CaptureError(f'published batch is unreadable: {exc}') from exc
        if content.get('format') != 2:
            raise CaptureError('batch identity is already used by a V1 publication')
        try:
            compare_manifest(stored, manifest)
        except ValueError as exc:
            raise CaptureError(str(exc)) from exc
        self.save_state([(page.page_no, page.url, page.sha256) for page in manifest.pages])

    # -- intake ------------------------------------------------------------

    def ingest_page(self, page_url, html, page_no):
        if self.manifest is None:
            raise CaptureError('a capture without a manifest is read-only')
        manifest = self.manifest
        try:
            raw = html_bytes(html)
            url = normalize_source_url(page_url)
        except ValueError as exc:
            raise CaptureError(str(exc)) from exc
        digest = sha256(raw).hexdigest()
        if type(page_no) is not int or not 1 <= page_no <= len(manifest.pages):
            raise CaptureError('page number outside manifest')
        source = manifest.pages[page_no - 1]
        if (url, digest) != (source.url, source.sha256):
            raise CaptureError('page differs from declaration')
        _, accepted = self.accepted_pages(manifest.batch_id)
        values = [(no, u, d) for no, u, d in accepted]
        existing = [item for item in values if item[0] == page_no]
        if existing:
            if existing[0] != (page_no, url, digest):
                raise CaptureError('accepted page is immutable')
            return {'status': 'duplicate', 'page_no': page_no, 'sha256': digest}
        published = self.archive.find_batch(manifest.batch_id)
        if published is not None:
            raise CaptureError('batch is already published')
        if page_no != len(values) + 1:
            raise CaptureError('pages must arrive consecutively')
        self.pages.put(manifest.batch_id, page_no, url, raw)
        values.append((page_no, url, digest))
        self.save_state(values)
        return {'status': 'accepted', 'page_no': page_no, 'sha256': digest}

    # -- publication -------------------------------------------------------

    @staticmethod
    def page_entries(manifest):
        return [{'page_no': p.page_no, 'url': p.url, 'sha256': p.sha256,
                 'path': f'pages/{p.page_no:06d}.html'} for p in manifest.pages]

    def snapshot_for(self, batch_id):
        stored = self.accepted_pages(batch_id)
        if stored is None:
            raise CaptureError('no accepted material for this batch')
        manifest, accepted = stored
        missing = [no for no in range(1, len(manifest.pages) + 1)
                   if not any(item[0] == no for item in accepted)]
        if missing:
            raise CaptureError(f'declared pages are missing: {missing}')
        raw_pages = {}
        for source in manifest.pages:
            try:
                page = self.pages.load(batch_id, source.page_no, source.url, source.sha256)
            except ValueError as exc:
                raise CaptureError(f'page {source.page_no}: {exc}') from exc
            raw_pages[source.page_no] = page.data
        try:
            content = derive_span_content(manifest.batch_id, manifest.table_selector,
                                          self.page_entries(manifest), raw_pages)
        except SpanError as exc:
            raise CaptureError(str(exc)) from exc
        return content, raw_pages

    def finish(self):
        if self.manifest is None:
            raise CaptureError('a capture without a manifest cannot publish')
        batch_id = self.manifest.batch_id
        try:
            published = self.archive.find_batch(batch_id)
        except ValueError as exc:
            raise CaptureError(str(exc)) from exc
        if published is not None:
            try:
                content, pages = self.archive.load(published)
            except ValueError as exc:
                raise CaptureError(f'published batch is unreadable: {exc}') from exc
            if content['batch_id'] != batch_id or content.get('format') != 2:
                raise CaptureError('batch identity is already used by another publication')
            try:
                stored = SourceManifest.from_descriptor(
                    {'batch_id': content['batch_id'],
                     'table_selector': content['table_selector'],
                     'pages': [{k: p[k] for k in ('page_no', 'url', 'sha256')}
                               for p in content['pages']]})
                compare_manifest(stored, self.manifest)
            except ValueError as exc:
                raise CaptureError(str(exc)) from exc
            report = verify_span_snapshot(PublicationSnapshot(published, content, pages))
            if not report.ok:
                raise CaptureError('; '.join(report.errors))
            return published
        content, raw_pages = self.snapshot_for(batch_id)
        try:
            validate_span_materials(content, raw_pages)
        except SpanError as exc:
            raise CaptureError(str(exc)) from exc
        snapshot_bytes = canonical_snapshot_bytes(content)
        candidate = None
        try:
            candidate = self.archive.stage(snapshot_bytes, raw_pages)
            return self.archive.commit(candidate)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise CaptureError(str(exc)) from exc
        finally:
            if candidate is not None:
                try:
                    self.archive.discard(candidate)
                except ValueError:
                    pass

    # -- replay ------------------------------------------------------------

    def published(self, batch_id):
        if not isinstance(batch_id, str) or not batch_id:
            raise CaptureError('invalid batch id')
        try:
            identifier = self.archive.find_batch(batch_id)
        except ValueError as exc:
            raise CaptureError(str(exc)) from exc
        if identifier is None:
            raise CaptureError(f'unknown or unpublished batch: {batch_id}')
        try:
            content, pages = self.archive.load(identifier)
        except ValueError as exc:
            raise CaptureError(str(exc)) from exc
        if content.get('format') != 2:
            raise CaptureError('batch is not a V2 cross-page publication')
        report = verify_span_snapshot(PublicationSnapshot(identifier, content, pages))
        if not report.ok:
            raise CaptureError('; '.join(report.errors))
        return identifier, content, pages

    def read(self, batch_id):
        try:
            identifier, content, pages = self.published(batch_id)
        except CaptureError:
            raise
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise CaptureError(str(exc)) from exc
        return {'publication_id': identifier, 'records': deepcopy(content['records'])}

    def verify(self, batch_id):
        try:
            identifier, content, pages = self.published(batch_id)
        except (CaptureError, ValueError, OSError, KeyError, TypeError) as exc:
            return VerificationReport(False, str(batch_id), 0, (str(exc),))
        report = verify_span_snapshot(PublicationSnapshot(identifier, content, pages))
        if not report.ok:
            return report
        return VerificationReport(True, identifier, len(content['records']), ())
