from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from .batch_state import IntakeError


def html_bytes(html):
    try:
        if isinstance(html, str):
            raw = html.encode('utf-8', 'strict')
        elif isinstance(html, bytes):
            raw = html
        else:
            raise TypeError('HTML must be str or bytes')
        raw.decode('utf-8', 'strict')
        return raw
    except (UnicodeError, TypeError) as exc:
        raise IntakeError('UTF8', str(exc)) from exc


def batch_token(batch_id):
    return sha256(batch_id.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class StoredPage:
    page_no: int
    url: str
    sha256: str
    data: bytes


class PageStore:
    def __init__(self, directory):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, batch_id, page_no):
        return self.root / batch_token(batch_id) / f'{page_no:06d}.html'

    def put(self, batch_id, page_no, url, html):
        raw = html_bytes(html)
        path = self.path(batch_id, page_no)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open('xb') as stream:
                stream.write(raw)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise IntakeError('STORED_PAGE_CHANGED', 'existing capture differs', page_no=page_no)
        return StoredPage(page_no, url, sha256(raw).hexdigest(), raw)

    def load(self, batch_id, page_no, url, digest):
        try:
            raw = self.path(batch_id, page_no).read_bytes()
        except OSError as exc:
            raise IntakeError('PAGE_MISSING', str(exc), page_no=page_no) from exc
        if sha256(raw).hexdigest() != digest:
            raise IntakeError('STORED_PAGE_CHANGED', 'capture digest mismatch', page_no=page_no)
        html_bytes(raw)
        return StoredPage(page_no, url, digest, raw)
