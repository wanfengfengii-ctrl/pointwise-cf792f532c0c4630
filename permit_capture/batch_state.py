from dataclasses import dataclass, field
from enum import Enum


class IntakeError(ValueError):
    def __init__(self, code, detail, **context):
        self.code = code
        self.detail = detail
        self.context = context
        super().__init__(f'{code}: {detail}')

    def as_dict(self):
        return {'code': self.code, 'detail': self.detail, **self.context}


class BatchStatus(str, Enum):
    PREPARING = 'PREPARING'
    PREPARED = 'PREPARED'
    PUBLISHED = 'PUBLISHED'


@dataclass
class CaptureBatch:
    batch_id: str
    expected_pages: int
    accepted: dict = field(default_factory=dict)
    status: BatchStatus = BatchStatus.PREPARING
    errors: list = field(default_factory=list)

    def check_page(self, page_no, url, digest):
        if self.status == BatchStatus.PUBLISHED:
            raise IntakeError('BATCH_PUBLISHED', self.batch_id)
        if type(page_no) is not int or not 1 <= page_no <= self.expected_pages:
            raise IntakeError('PAGE_NUMBER', 'page number outside manifest', page_no=page_no)
        if page_no in self.accepted:
            if self.accepted[page_no] != (url, digest):
                raise IntakeError('PAGE_CONFLICT', 'accepted page is immutable', page_no=page_no)
            return True
        if self.status != BatchStatus.PREPARING:
            raise IntakeError('BATCH_PREPARED', self.batch_id)
        if page_no != len(self.accepted) + 1:
            raise IntakeError('PAGE_SEQUENCE', 'pages must arrive consecutively', page_no=page_no)
        return False

    def accept_page(self, page):
        duplicate = self.check_page(page.page_no, page.url, page.sha256)
        if not duplicate:
            self.accepted[page.page_no] = (page.url, page.sha256)
        return duplicate

    def mark_prepared(self):
        missing = sorted(set(range(1, self.expected_pages + 1)) - set(self.accepted))
        if missing:
            raise IntakeError('MISSING_PAGES', 'declared pages are missing', missing=missing)
        if self.status != BatchStatus.PUBLISHED:
            self.status = BatchStatus.PREPARED

    def remember(self, error):
        item = error.as_dict() if isinstance(error, IntakeError) else {
            'code': getattr(error, 'code', type(error).__name__), 'detail': str(error)}
        if not self.errors or self.errors[-1] != item:
            self.errors.append(item)
