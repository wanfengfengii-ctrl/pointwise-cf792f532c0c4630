from .table_locator import HtmlNode, walk_nodes


def is_footnote_node(node):
    return (isinstance(node, HtmlNode) and node.tag not in ('td', 'th')
            and 'footnote' in (node.attrs.get('class') or '').split())


def extract_cell_text(node):
    if isinstance(node, str):
        return node
    if is_footnote_node(node):
        return ''
    if node.tag == 'br':
        return '\n'
    return ''.join(extract_cell_text(child) for child in node.children)


def collect_footnote_ranges(node):
    return tuple((n.start, n.end) for n in walk_nodes(node) if is_footnote_node(n))
