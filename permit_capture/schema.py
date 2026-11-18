from dataclasses import dataclass

COLUMN_ORDER = ('permit_no', 'issued_on', 'status', 'district', 'applicant')
OPTIONAL_COLUMNS = frozenset({'applicant'})


class ParseError(ValueError):
    def __init__(self, code, detail):
        self.code = code
        self.detail = detail
        super().__init__(f'{code}: {detail}')


@dataclass(frozen=True)
class ColumnSchema:
    names: tuple = COLUMN_ORDER
    optional: frozenset = OPTIONAL_COLUMNS

    def check_width(self, width):
        if width != len(self.names):
            raise ParseError('ROW_WIDTH', f'expected {len(self.names)}, got {width}')


def validate_header(values):
    if tuple(values) != COLUMN_ORDER:
        raise ParseError('HEADER', f'expected {COLUMN_ORDER}, got {tuple(values)}')
