from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
from .schema import ParseError

VOID_TAGS = frozenset('area base br col embed hr img input link meta param source track wbr'.split())


@dataclass
class HtmlNode:
    tag: str
    attrs: dict
    start: int
    end: int
    children: list = field(default_factory=list)


class TableDocumentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode('document', {}, 0, 0)
        self.stack = [self.root]

    def byte_position(self):
        line, column = self.getpos()
        return self.byte_offsets[self.line_offsets[line - 1] + column]

    def handle_starttag(self, tag, attrs):
        if len({key for key, _ in attrs}) != len(attrs):
            raise ParseError('ATTRIBUTES', 'duplicate attribute')
        start = self.byte_position()
        end = start + len(self.get_starttag_text().encode('utf-8'))
        node = HtmlNode(tag, dict(attrs), start, end)
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.stack.pop()

    def handle_endtag(self, tag):
        if len(self.stack) == 1 or self.stack[-1].tag != tag:
            raise ParseError('CLOSURE', f'unexpected closing {tag}')
        node = self.stack.pop()
        start = self.byte_position()
        end = self.raw.find(b'>', start)
        if end < 0:
            raise ParseError('CLOSURE', 'truncated closing tag')
        node.end = end + 1

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def unknown_decl(self, data):
        raise ParseError('DECLARATION', 'unsupported declaration')

    def parse(self, html):
        if self.root.children:
            raise ParseError('PARSER_REUSE', 'create one parser per document')
        try:
            self.raw = html.encode('utf-8') if isinstance(html, str) else bytes(html)
            text = self.raw.decode('utf-8', 'strict')
        except (UnicodeError, TypeError, ValueError) as exc:
            raise ParseError('UTF8', str(exc)) from exc
        self.byte_offsets = [0]
        for char in text:
            self.byte_offsets.append(self.byte_offsets[-1] + len(char.encode('utf-8')))
        self.line_offsets = [0] + [m.end() for m in re.finditer('\n', text)]
        self.feed(text)
        self.close()
        if len(self.stack) != 1:
            raise ParseError('CLOSURE', f'unclosed {self.stack[-1].tag}')
        self.root.end = len(self.raw)
        return self.root


def walk_nodes(node):
    yield node
    for child in node.children:
        if isinstance(child, HtmlNode):
            yield from walk_nodes(child)


def locate_table(document, selector):
    if not isinstance(selector, str) or not re.fullmatch(r'#[A-Za-z_][A-Za-z0-9_.:-]*', selector):
        raise ParseError('SELECTOR', 'single id selector required')
    matches = [n for n in walk_nodes(document) if n.attrs.get('id') == selector[1:]]
    if len(matches) != 1 or matches[0].tag != 'table':
        raise ParseError('TARGET', 'expected exactly one target table')
    table = matches[0]
    if any(n is not table and n.tag == 'table' for n in walk_nodes(table)):
        raise ParseError('NESTED_TABLE', 'nested table is unsupported')
    return table
