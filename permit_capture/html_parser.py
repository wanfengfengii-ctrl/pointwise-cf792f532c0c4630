from dataclasses import dataclass
from .schema import COLUMN_ORDER
from .table_locator import TableDocumentParser, locate_table
from .footnotes import collect_footnote_ranges
from .grid import build_page_grid


@dataclass(frozen=True)
class ParsedTable:
    columns: tuple
    rows: tuple
    footnotes: tuple


def parse_table(page_html, table_selector):
    document = TableDocumentParser().parse(page_html)
    table = locate_table(document, table_selector)
    rows = build_page_grid(table)
    return ParsedTable(COLUMN_ORDER, rows, collect_footnote_ranges(table))
