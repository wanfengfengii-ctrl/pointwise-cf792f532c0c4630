"""Cross-page rowspan/colspan capture: expand pages, build records, replay."""
from dataclasses import dataclass
from hashlib import sha256
from .archive_codec import SPAN_RULES, publication_fingerprint
from .field_rules import normalize_field, FieldRuleError
from .identity import make_row_key, IdentityConflict
from .replay import VerificationReport
from .schema import COLUMN_ORDER, ParseError
from .table_locator import TableDocumentParser, locate_table
from .grid import build_span_page_grid

COLUMN_COUNT = len(COLUMN_ORDER)


class SpanError(ValueError):
    pass


@dataclass(frozen=True)
class Origin:
    page_no: int
    row_no: int
    column_no: int
    start: int
    end: int
    text: str
    global_row: int


@dataclass(frozen=True)
class ExpandedRow:
    global_index: int
    page_no: int
    row_no: int
    start: int
    end: int
    row_key: str | None
    slots: tuple
    inherited: tuple
    has_cells: bool


def expand_pages(pages):
    """Expand page-local span rows into one five-column logical grid.

    ``carry[column]`` holds ``[origin, rows_including_current]`` for a span
    that reaches the current row from above.  Spans are counted in actual data
    rows only, so repeated headers, footnotes and empty pages never advance a
    span.  No per-future-row structure is materialised, which keeps very large
    rowspan spellings cheap; they simply stay unresolved until the end.
    """
    carry = {}
    expanded = []
    index = 0
    for page_no, rows in pages:
        for row in rows:
            index += 1
            occupied = {column: state[0] for column, state in carry.items()}
            column = 1
            new_cells = 0
            for cell in row.cells:
                while column <= COLUMN_COUNT and column in occupied:
                    column += 1
                if column > COLUMN_COUNT:
                    raise SpanError(
                        f'page {page_no} row {row.row_no}: cell exceeds five columns')
                last = column + cell.colspan - 1
                if last > COLUMN_COUNT:
                    raise SpanError(
                        f'page {page_no} row {row.row_no}: colspan exceeds five columns')
                if any(c in occupied for c in range(column, last + 1)):
                    raise SpanError(
                        f'page {page_no} row {row.row_no}: span overlaps an existing cell')
                origin = Origin(page_no, row.row_no, column, cell.start, cell.end,
                                cell.text, index)
                for current in range(column, last + 1):
                    occupied[current] = origin
                    if cell.rowspan > 1:
                        carry[current] = [origin, cell.rowspan]
                column = last + 1
                new_cells += 1
            missing = [c for c in range(1, COLUMN_COUNT + 1) if c not in occupied]
            if missing:
                raise SpanError(
                    f'page {page_no} row {row.row_no}: row does not cover five columns')
            expanded.append(ExpandedRow(
                index, page_no, row.row_no, row.start, row.end, row.row_key,
                tuple(occupied[c] for c in range(1, COLUMN_COUNT + 1)),
                tuple(occupied[c].global_row != index for c in range(1, COLUMN_COUNT + 1)),
                new_cells > 0))
            expired = []
            for current, state in carry.items():
                state[1] -= 1
                if state[1] <= 0:
                    expired.append(current)
            for current in expired:
                del carry[current]
    if carry:
        raise SpanError('rowspan extends beyond the last actual data row')
    return expanded


def _normalized_values(row):
    values, problems = [], []
    for position, name in enumerate(COLUMN_ORDER, 1):
        try:
            values.append(normalize_field(name, row.slots[position - 1].text))
        except FieldRuleError as exc:
            problems.append(f'{exc.code} at column {position} '
                            f'(page {row.page_no} row {row.row_no})')
            values.append(None)
    if problems:
        raise SpanError('; '.join(problems))
    return tuple(values)


def _evidence(slot, raw_pages, page_sha):
    raw = raw_pages[slot.page_no]
    if not 0 <= slot.start < slot.end <= len(raw):
        raise SpanError(f'page {slot.page_no}: invalid source range')
    return {'page_no': slot.page_no, 'row_no': slot.row_no, 'start': slot.start,
            'end': slot.end, 'page_sha256': page_sha[slot.page_no],
            'fragment_sha256': sha256(raw[slot.start:slot.end]).hexdigest()}


def _source(slot, raw_pages, page_sha):
    item = _evidence(slot, raw_pages, page_sha)
    item['column_no'] = slot.column_no
    return item


def assemble_records(expanded, raw_pages, page_sha):
    records, closed = [], set()
    current = None
    for row in expanded:
        values = _normalized_values(row)
        try:
            key = make_row_key(row.row_key, values[0])
        except IdentityConflict as exc:
            raise SpanError(
                f'page {row.page_no} row {row.row_no}: {exc}') from exc
        if current is not None and key == current['row_key']:
            if not any(row.inherited):
                raise SpanError(
                    f'page {row.page_no} row {row.row_no}: repeated key {key!r} '
                    'without an inherited cell')
            if tuple(values) != current['fields']:
                raise SpanError(
                    f'page {row.page_no} row {row.row_no}: continuation fields '
                    f'differ from key {key!r}')
            current['members'].append(row)
            continue
        if key in closed:
            raise SpanError(f'row key {key!r} is not contiguous')
        if current is not None:
            closed.add(current['row_key'])
        current = {'row_key': key, 'fields': tuple(values), 'members': [row]}
        records.append(current)
    result = []
    for record in records:
        members = record['members']
        sources = []
        for column in range(COLUMN_COUNT):
            seen, refs = set(), []
            for member in members:
                slot = member.slots[column]
                signature = (slot.page_no, slot.row_no, slot.column_no,
                             slot.start, slot.end)
                if signature in seen:
                    continue
                seen.add(signature)
                refs.append(_source(slot, raw_pages, page_sha))
            refs.sort(key=lambda ref: (ref['page_no'], ref['row_no'],
                                       ref['column_no'], ref['start']))
            sources.append(refs)
        member_refs = []
        for member in members:
            raw = raw_pages[member.page_no]
            if not 0 <= member.start < member.end <= len(raw):
                raise SpanError(f'page {member.page_no}: invalid row range')
            member_refs.append({
                'page_no': member.page_no, 'row_no': member.row_no,
                'start': member.start, 'end': member.end,
                'page_sha256': page_sha[member.page_no],
                'fragment_sha256': sha256(raw[member.start:member.end]).hexdigest()})
        result.append({'row_key': record['row_key'],
                       'fields': list(record['fields']),
                       'member_rows': member_refs,
                       'field_sources': sources})
    result.sort(key=lambda item: (item['member_rows'][0]['page_no'],
                                  item['member_rows'][0]['row_no']))
    return result


def derive_span_content(batch_id, selector, page_entries, raw_pages):
    """Rebuild the complete logical snapshot from bound original pages."""
    parsed, page_sha = [], {}
    for entry in page_entries:
        number = entry['page_no']
        if number not in raw_pages:
            raise SpanError(f'page {number}: original bytes are missing')
        raw = raw_pages[number]
        digest = sha256(raw).hexdigest()
        if digest != entry['sha256']:
            raise SpanError(f'page {number}: digest mismatch')
        try:
            document = TableDocumentParser().parse(raw)
            table = locate_table(document, selector)
            rows = build_span_page_grid(table)
        except ParseError as exc:
            raise SpanError(f'page {number}: {exc}') from exc
        parsed.append((number, rows))
        page_sha[number] = digest
    if set(raw_pages) != set(page_sha):
        raise SpanError('page set differs from the declaration')
    records = assemble_records(expand_pages(parsed), raw_pages, page_sha)
    return {'format': 2, 'rules': SPAN_RULES, 'batch_id': batch_id,
            'table_selector': selector, 'columns': list(COLUMN_ORDER),
            'pages': [dict(entry) for entry in page_entries], 'records': records}


def validate_span_materials(content, pages):
    rebuilt = derive_span_content(content['batch_id'], content['table_selector'],
                                  content['pages'], pages)
    if rebuilt != content:
        raise SpanError('semantic replay differs: records or source relationships')


def verify_span_snapshot(snapshot):
    try:
        content = snapshot.content
        if publication_fingerprint(content) != snapshot.publication_id:
            raise SpanError('snapshot fingerprint mismatch')
        validate_span_materials(content, snapshot.pages)
        return VerificationReport(True, snapshot.publication_id,
                                  len(content['records']), ())
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return VerificationReport(False, snapshot.publication_id, 0, (str(exc),))
