from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import zipfile
from .reader import PublicationSnapshot, load_publication, validate_snapshot_materials
from .archive_codec import (build_snapshot, decode_snapshot, publication_fingerprint,
                            canonical_snapshot_bytes)
from .source_manifest import SourceManifest, SourcePage
from .html_parser import parse_table
from .record_normalizer import normalize_rows
from .page_store import StoredPage
from .evidence import bind_row_evidence
from .query import matching_records
from .filter_rules import parse_filters


@dataclass(frozen=True)
class VerificationReport:
    ok: bool
    publication_id: str
    checked_records: int
    errors: tuple


def verify_snapshot(snapshot):
    try:
        content = snapshot.content
        if publication_fingerprint(content) != snapshot.publication_id:
            raise ValueError('snapshot fingerprint mismatch')
        validate_snapshot_materials(content, snapshot.pages)
        sources, parsed, refs = [], [], []
        for descriptor in content['pages']:
            no = descriptor['page_no']
            raw = snapshot.pages[no]
            page = StoredPage(no, descriptor['url'], descriptor['sha256'], raw)
            table = parse_table(raw, content['table_selector'])
            parsed.append((no, table.rows))
            sources.append(SourcePage(no, page.url, page.sha256))
            refs.extend(bind_row_evidence(page, row) for row in table.rows)
        result = normalize_rows(parsed)
        if result.errors:
            raise ValueError(f'normalization: {result.errors}')
        manifest = SourceManifest(content['batch_id'], content['table_selector'], tuple(sources))
        rebuilt = build_snapshot(manifest, result.records, refs)
        if rebuilt != content:
            raise ValueError('semantic replay differs: records, identity or source coordinates')
        return VerificationReport(True, snapshot.publication_id, len(result.records), ())
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return VerificationReport(False, snapshot.publication_id, 0, (str(exc),))


def verify_publication(repository, publication_id):
    try:
        content, pages = repository.load(publication_id)
    except (ValueError, OSError):
        return VerificationReport(False, publication_id, 0, ('unknown or damaged publication',))
    from .archive_codec import SPAN_SNAPSHOT_FORMAT_VERSION
    if content.get('format') == SPAN_SNAPSHOT_FORMAT_VERSION:
        from .span_capture import verify_span_snapshot
        return verify_span_snapshot(PublicationSnapshot(publication_id, content, pages))
    try:
        return verify_snapshot(load_publication(repository, publication_id))
    except (ValueError, OSError) as exc:
        return VerificationReport(False, publication_id, 0, (str(exc),))


def verify_review_bundle(bundle):
    identifier = ''
    try:
        raw = bundle if isinstance(bundle, bytes) else Path(bundle).read_bytes()
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError('duplicate bundle member')
            content = decode_snapshot(archive.read('snapshot.json'))
            selection_raw = archive.read('selection.json')
            selection = json.loads(selection_raw)
            if canonical_snapshot_bytes(selection) != selection_raw:
                raise ValueError('noncanonical selection')
            if set(selection) != {'publication_id', 'filters', 'row_keys'}:
                raise ValueError('invalid selection fields')
            identifier = selection['publication_id']
            expected_names = {'snapshot.json', 'selection.json'} | {
                p['path'] for p in content['pages']}
            if set(names) != expected_names:
                raise ValueError('bundle member set differs')
            pages = {p['page_no']: archive.read(p['path']) for p in content['pages']}
        snapshot = PublicationSnapshot(identifier, content, pages)
        report = verify_snapshot(snapshot)
        if not report.ok:
            return report
        filters = parse_filters(selection['filters'])
        if filters.as_dict() != selection['filters']:
            raise ValueError('noncanonical filters')
        expected = [r['row_key'] for r in matching_records(snapshot, filters)]
        if selection['row_keys'] != expected:
            raise ValueError('selection index differs')
        return report
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError) as exc:
        return VerificationReport(False, identifier, 0, (str(exc),))
