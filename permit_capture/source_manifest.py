from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from .batch_state import IntakeError
from .page_store import html_bytes
from .url_rules import normalize_source_url


@dataclass(frozen=True)
class SourcePage:
    page_no: int
    url: str
    sha256: str
    html: bytes | None = None


@dataclass(frozen=True)
class SourceManifest:
    batch_id: str
    table_selector: str
    pages: tuple

    def descriptor(self):
        return {'batch_id': self.batch_id, 'table_selector': self.table_selector,
                'pages': [{'page_no': p.page_no, 'url': p.url, 'sha256': p.sha256}
                          for p in self.pages]}

    @classmethod
    def from_descriptor(cls, obj):
        try:
            result = cls(obj['batch_id'], obj['table_selector'],
                         tuple(SourcePage(**p) for p in obj['pages']))
            validate_manifest(result)
            return result
        except (KeyError, TypeError) as exc:
            raise IntakeError('MANIFEST', 'invalid stored declaration') from exc


def validate_manifest(manifest):
    if not isinstance(manifest.batch_id, str) or not manifest.batch_id.strip():
        raise IntakeError('MANIFEST', 'batch_id must be nonempty')
    if not isinstance(manifest.table_selector, str) or not re.fullmatch(
            r'#[A-Za-z_][A-Za-z0-9_.:-]*', manifest.table_selector):
        raise IntakeError('MANIFEST', 'single id selector required')
    if not manifest.pages:
        raise IntakeError('MANIFEST', 'at least one page required')
    for i, page in enumerate(manifest.pages, 1):
        if type(page.page_no) is not int or page.page_no != i:
            raise IntakeError('MANIFEST', 'page list must be ordered 1..N')
        if normalize_source_url(page.url) != page.url:
            raise IntakeError('MANIFEST', 'URL must be normalized')
        if not isinstance(page.sha256, str) or not re.fullmatch('[0-9a-f]{64}', page.sha256):
            raise IntakeError('MANIFEST', 'SHA-256 required')
        if page.html is not None and sha256(html_bytes(page.html)).hexdigest() != page.sha256:
            raise IntakeError('MANIFEST', 'declared page digest differs')
    return manifest


def compare_manifest(left, right):
    if left.descriptor() != right.descriptor():
        raise IntakeError('BATCH_CONFLICT', 'batch declaration differs')
    return True


def manifest_from_object(obj, base=None):
    try:
        pages = []
        for item in obj['pages']:
            if ('html' in item) == ('path' in item):
                raise IntakeError('MANIFEST', 'exactly one of html/path is required')
            if 'html' in item:
                raw = html_bytes(item['html'])
            else:
                if base is None:
                    raise IntakeError('MANIFEST', 'path needs manifest directory')
                root = Path(base).resolve()
                path = (root / item['path']).resolve()
                if not path.is_relative_to(root):
                    raise IntakeError('MANIFEST', 'page path escapes manifest directory')
                raw = html_bytes(path.read_bytes())
            pages.append(SourcePage(item['page_no'], normalize_source_url(item['url']),
                                    sha256(raw).hexdigest(), raw))
        return validate_manifest(SourceManifest(obj['batch_id'], obj['table_selector'], tuple(pages)))
    except (KeyError, TypeError, OSError) as exc:
        raise IntakeError('MANIFEST', str(exc)) from exc


def load_manifest(path, case=None):
    path = Path(path)
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
        if case is not None:
            obj = obj[case]
        return manifest_from_object(obj, path.parent)
    except (ValueError, KeyError, OSError) as exc:
        if isinstance(exc, IntakeError):
            raise
        raise IntakeError('MANIFEST', str(exc)) from exc
