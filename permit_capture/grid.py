from dataclasses import dataclass
import re
from .table_locator import HtmlNode, walk_nodes
from .footnotes import is_footnote_node, extract_cell_text
from .schema import ParseError, ColumnSchema, COLUMN_ORDER, validate_header


@dataclass(frozen=True)
class CellEvent:
    text: str
    row_no: int
    column_no: int
    start: int
    end: int


@dataclass(frozen=True)
class RowEvent:
    row_no: int
    row_key: str | None
    cells: tuple
    start: int
    end: int


@dataclass(frozen=True)
class SpanCell:
    text: str
    rowspan: int
    colspan: int
    start: int
    end: int


@dataclass(frozen=True)
class SpanRow:
    row_no: int
    row_key: str | None
    cells: tuple
    start: int
    end: int


def validate_unit_span(node):
    for name in ('rowspan', 'colspan'):
        value = node.attrs.get(name, '1')
        if not isinstance(value, str) or not re.fullmatch('[0-9]+', value) or not value.lstrip('0'):
            raise ParseError('SPAN', f'invalid {name}')
        if value.lstrip('0') != '1':
            raise ParseError('UNSUPPORTED_SPAN', f'V1 requires {name}=1')


def structural_children(node, allowed):
    result = []
    for child in node.children:
        if isinstance(child, str):
            if child.strip():
                raise ParseError('STRUCTURE', f'text directly inside {node.tag}')
        elif is_footnote_node(child):
            continue
        elif child.tag not in allowed:
            raise ParseError('STRUCTURE', f'{child.tag} inside {node.tag}')
        else:
            result.append(child)
    return result


def row_cells(row, row_no):
    nodes = structural_children(row, {'td', 'th'})
    ColumnSchema().check_width(len(nodes))
    events = []
    for column, node in enumerate(nodes, 1):
        validate_unit_span(node)
        if any(c is not node and c.tag in
               {'table', 'tr', 'td', 'th', 'tbody', 'thead'} for c in walk_nodes(node)
               if not is_footnote_node(c)):
            raise ParseError('STRUCTURE', 'structural tag inside cell')
        events.append(CellEvent(extract_cell_text(node), row_no, column, node.start, node.end))
    return tuple(events)


SPAN_PATTERN = re.compile('[0-9]+')


def _decimal_int(digits):
    """Convert an arbitrarily long decimal spelling without str->int limits."""
    value = 0
    for start in range(0, len(digits), 9):
        chunk = digits[start:start + 9]
        value = value * (10 ** len(chunk)) + int(chunk)
    return value


def parse_span_value(node, name):
    if name not in node.attrs:
        return 1
    value = node.attrs[name]
    if not isinstance(value, str) or not SPAN_PATTERN.fullmatch(value):
        raise ParseError('SPAN', f'invalid {name}')
    digits = value.lstrip('0')
    if not digits:
        raise ParseError('SPAN', f'{name} must be greater than zero')
    return _decimal_int(digits)


def span_cells(row):
    nodes = structural_children(row, {'td', 'th'})
    cells = []
    for node in nodes:
        if any(c is not node and c.tag in
               {'table', 'tr', 'td', 'th', 'tbody', 'thead'} for c in walk_nodes(node)
               if not is_footnote_node(c)):
            raise ParseError('STRUCTURE', 'structural tag inside cell')
        cells.append(SpanCell(extract_cell_text(node), parse_span_value(node, 'rowspan'),
                              parse_span_value(node, 'colspan'), node.start, node.end))
    return tuple(cells)


def build_span_page_grid(table):
    """Parse one page's data rows with rowspan/colspan kept intact."""
    sections = structural_children(table, {'thead', 'tbody'})
    if [s.tag for s in sections] != ['thead', 'tbody']:
        raise ParseError('STRUCTURE', 'one thead followed by one tbody required')
    heads = structural_children(sections[0], {'tr'})
    if len(heads) != 1:
        raise ParseError('HEADER', 'one header row required')
    for node in structural_children(heads[0], {'td', 'th'}):
        validate_unit_span(node)
    header = row_cells(heads[0], 0)
    validate_header([c.text.strip() for c in header])
    rows = []
    number = 0
    for node in structural_children(sections[1], {'tr'}):
        cells = span_cells(node)
        if ('data-row-key' not in node.attrs and len(cells) == len(COLUMN_ORDER)
                and all(c.rowspan == 1 and c.colspan == 1 for c in cells)
                and tuple(c.text.strip() for c in cells) == COLUMN_ORDER):
            continue
        number += 1
        key = node.attrs.get('data-row-key')
        if 'data-row-key' in node.attrs and key is None:
            key = ''
        rows.append(SpanRow(number, key, cells, node.start, node.end))
    return tuple(rows)


def build_page_grid(table):
    sections = structural_children(table, {'thead', 'tbody'})
    if [s.tag for s in sections] != ['thead', 'tbody']:
        raise ParseError('STRUCTURE', 'one thead followed by one tbody required')
    heads = structural_children(sections[0], {'tr'})
    if len(heads) != 1:
        raise ParseError('HEADER', 'one header row required')
    header = row_cells(heads[0], 0)
    validate_header([c.text.strip() for c in header])
    rows = []
    for i, node in enumerate(structural_children(sections[1], {'tr'}), 1):
        cells = row_cells(node, i)
        if tuple(c.text.strip() for c in cells) == COLUMN_ORDER:
            raise ParseError('REPEATED_HEADER', 'tbody headers are unsupported in V1')
        key = node.attrs.get('data-row-key')
        if 'data-row-key' in node.attrs and key is None:
            key = ''
        rows.append(RowEvent(i, key, cells, node.start, node.end))
    return tuple(rows)
