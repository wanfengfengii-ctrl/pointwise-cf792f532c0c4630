from pathlib import Path
import os
import re
import shutil
import tempfile
from .archive_codec import decode_snapshot, publication_fingerprint


class ArchiveError(ValueError):
    pass


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ArchiveRepository:
    def __init__(self, directory):
        self.root = Path(directory)
        self.initialize()

    def initialize(self):
        self.publications = self.root / 'publications'
        self.staging = self.root / 'staging'
        self.publications.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)

    def find_batch(self, batch_id):
        matches = []
        for path in sorted(self.publications.iterdir()):
            if not path.is_dir():
                continue
            obj, _ = self.load(path.name)
            if obj['batch_id'] == batch_id:
                matches.append(path.name)
        if len(matches) > 1:
            raise ArchiveError('multiple publications for one batch')
        return matches[0] if matches else None

    def stage(self, snapshot_bytes, pages):
        snapshot = decode_snapshot(snapshot_bytes)
        from .reader import validate_snapshot_materials
        validate_snapshot_materials(snapshot, pages)
        candidate = Path(tempfile.mkdtemp(prefix='candidate-', dir=self.staging))
        try:
            (candidate / 'pages').mkdir()
            materials = {'snapshot.json': snapshot_bytes}
            for page in snapshot['pages']:
                materials[page['path']] = pages[page['page_no']]
            for name, raw in materials.items():
                with (candidate / name).open('xb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            sync_directory(candidate / 'pages')
            sync_directory(candidate)
            return candidate
        except BaseException:
            shutil.rmtree(candidate)
            raise

    def commit(self, candidate):
        candidate = Path(candidate)
        if candidate.parent != self.staging:
            raise ArchiveError('foreign candidate')
        snapshot = decode_snapshot((candidate / 'snapshot.json').read_bytes())
        identifier = publication_fingerprint(snapshot)
        from .reader import PublicationSnapshot
        from .replay import verify_snapshot
        pages = {p['page_no']: (candidate / p['path']).read_bytes() for p in snapshot['pages']}
        report = verify_snapshot(PublicationSnapshot(identifier, snapshot, pages))
        if not report.ok:
            raise ArchiveError('; '.join(report.errors))
        old = self.find_batch(snapshot['batch_id'])
        if old is not None:
            if old != identifier:
                raise ArchiveError('BATCH_CONFLICT')
            self.discard(candidate)
            return old
        target = self.publications / identifier
        os.rename(candidate, target)
        sync_directory(self.publications)
        return identifier

    def discard(self, candidate):
        candidate = Path(candidate)
        if candidate.parent != self.staging:
            raise ArchiveError('foreign candidate')
        if candidate.exists():
            shutil.rmtree(candidate)

    def load(self, publication_id):
        if not isinstance(publication_id, str) or not re.fullmatch('[0-9a-f]{64}', publication_id):
            raise ArchiveError('invalid publication id')
        folder = self.publications / publication_id
        try:
            raw = (folder / 'snapshot.json').read_bytes()
            snapshot = decode_snapshot(raw)
            if publication_fingerprint(snapshot) != publication_id:
                raise ArchiveError('snapshot fingerprint mismatch')
            pages = {p['page_no']: (folder / p['path']).read_bytes() for p in snapshot['pages']}
            return snapshot, pages
        except (OSError, ValueError) as exc:
            if isinstance(exc, ArchiveError):
                raise
            raise ArchiveError(f'unknown or damaged publication: {exc}') from exc
