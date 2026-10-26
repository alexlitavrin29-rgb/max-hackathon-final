"""Deterministic text units for published material previews (no generated facts)."""
import re

# Keep labeled URLs opaque while finding punctuation, including balanced URL
# parentheses used by reference sites. Never slice inside a Markdown link.
MARKDOWN_LINK = re.compile(r'\[([^\]\n]+)\]\((https?://(?:[^\s()]|\([^\s()]*\))+)\)')
URL = re.compile(r'https?://[^\s<>]+')
ABBREVIATION = re.compile(r'(?:\b(?:ст|стр|гл|п|пп|ч|г|гг|ул|д|кв|им|т|е|др|руб|коп)|\b[А-ЯA-Z])\.$', re.I)
LIST_LINE = re.compile(r'^\s*(?:[•☐▪*-]|\d+[.)])\s+')


def sentences(text):
    """Return complete sentences; preserve unpunctuated trailing source text."""
    links = [(m.start(), m.end()) for m in MARKDOWN_LINK.finditer(text)]
    protected = links + [(m.start(), m.start() + len(m.group().rstrip('.,!?;:')))
                         for m in URL.finditer(text)
                         if not any(a <= m.start() < b for a, b in links)]
    result, start = [], 0
    for match in re.finditer(r'[.!?…]+[»”\")\]]*(?=\s|$)', text):
        end = match.end()
        if any(a <= match.start() < b for a, b in protected):
            continue
        prefix = text[start:end]
        if text[match.start()] == '.' and ABBREVIATION.search(prefix.rstrip('»”")]')):
            continue
        # Multi-part abbreviations and initials must stay with their continuation.
        if re.search(r'\b(?:т\.\s*е|т\.\s*д|т\.\s*п)\.$', prefix, re.I):
            continue
        result.append(prefix.strip())
        start = end
    if text[start:].strip():
        result.append(text[start:].strip())
    return result


def text_units(text):
    """Headings attach to their following paragraph; lists remain whole blocks."""
    result, heading = [], ''
    for block in re.split(r'\n\s*\n', text.strip()):
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if any(LIST_LINE.match(line) for line in lines):
            units = [block]
        elif not re.search(r'[.!?…]', block.rstrip('?')) and (len(block) < 100 or block.endswith(':')):
            heading = (heading + '\n' + block).strip()
            continue
        else:
            units = sentences(block)
        if heading:
            units[0] = heading + '\n' + units[0]
            heading = ''
        result.extend(units)
    if heading:
        result.append(heading)
    return result


def sentence_preview(text, target=240):
    """Soft length target: the first whole unit is always included, never ellipsis."""
    selected = []
    for unit in text_units(text):
        if (selected and not re.search(r'[.!?…][»”\")\]]*$', unit)
                and not any(LIST_LINE.match(line) for line in unit.splitlines())):
            break
        if selected and len(' '.join([*selected, unit])) > target:
            break
        selected.append(unit)
    return ' '.join(selected)
