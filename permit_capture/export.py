from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import os
import tempfile
import zipfile
from .archive_codec import canonical_snapshot_bytes
from .query import matching_records
from .filter_rules import parse_filters
from .replay import verify_snapshot
from .archive import sync_directory


@dataclass(frozen=True)
class ExportManifest:
    publication_id: str
    filters: dict
    row_keys: tuple

    def as_dict(self):
        return {'publication_id': self.publication_id, 'filters': self.filters,
                'row_keys': list(self.row_keys)}


def build_review_bundle(snapshot, filters=None):
    report = verify_snapshot(snapshot)
    if not report.ok:
        raise ValueError('; '.join(report.errors))
    filters = parse_filters(filters)
    selection = ExportManifest(snapshot.publication_id, filters.as_dict(),
                               tuple(r['row_key'] for r in matching_records(snapshot, filters)))
    members = {'snapshot.json': canonical_snapshot_bytes(snapshot.content),
               'selection.json': canonical_snapshot_bytes(selection.as_dict())}
    for page in snapshot.content['pages']:
        members[page['path']] = snapshot.pages[page['page_no']]
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, members[name])
    return output.getvalue()


def write_review_bundle(snapshot, destination, filters=None):
    raw = build_review_bundle(snapshot, filters)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.review-', dir=destination.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
            sync_directory(destination.parent)
        except FileExistsError:
            if destination.read_bytes() != raw:
                raise FileExistsError('export destination contains different bytes')
        return destination
    finally:
        temporary.unlink(missing_ok=True)
