"""Compact extractive answers with source-local provenance and full originals.

The model selects material IDs only. Every answer unit is copied whole from a
published card: qualifications share their paragraph with the claim they limit.
"""
import re


DISCLOSURE = 'Полные условия и исключения — в подробностях материалов ниже.'
QUALIFIER = re.compile(
    r'23 года|не полн|не включены|пошлин|не заменяет|не отказывай|'
    r'не гарант|не всегда|сохранени|исключен', re.I)


def _units(node):
    from .planner_content import _paragraphs
    paragraphs = _paragraphs(node)
    units = []
    for paragraph in paragraphs:
        # Each numbered instruction stays whole, including following caveats.
        if re.match(r'^\d+[.)]\s', paragraph):
            units.extend(re.split(r'\n(?=\d+[.)]\s)', paragraph))
        else:
            units.append(paragraph)
    return list(dict.fromkeys([*units, *node.get('tasks', [])]))


def build_information(config, identifiers):
    from .planner_content import NO_ANSWER, selected_materials
    from .handbook import handbook_material
    materials = selected_materials(config, identifiers)
    if not materials:
        return dict(text=NO_ANSWER, sources=[], intro=NO_ANSWER,
                    points=[], details=[], priority='normal')
    from .reference_guides import nodes as reference_nodes
    nodes = {node['id']: node for node in [*config.get('nodes', []), *reference_nodes(config)]}
    cards, sources, seen_urls = [], [], set()
    for item in materials:
        node = nodes[item['id']]
        links = [dict(id=item['id'], title=item['title'], url=item['url'])]
        card = handbook_material(config, item['id'])
        if card:
            links.extend(dict(id=item['id'], title=a['label'], url=a['url'],
                              **{k:a[k] for k in ('description','anchors') if a.get(k)}) for a in card['actions'])
        if item['id'] == 'employment_docs':
            from .content_text import sentences
            description = next((sentence for paragraph in _units(node)
                                for sentence in sentences(paragraph)
                                if sentence.startswith('По статье 65 ТК РФ ')), None)
            for link in links[1:]:
                if description and link['url'] in node.get('editorial_sources', []):
                    link['description'] = description
        for link in links:
            if link['url'] not in seen_urls:
                sources.append(link)
                seen_urls.add(link['url'])
        cards.append(dict(node=node, units=_units(node), sources=links))

    primary = cards[0]
    intro = primary['units'][0] if primary['units'] else primary['node']['text']
    selected = {(0, 0)}
    points = []

    def append(index, position):
        key = (index, position)
        if key in selected or len(points) >= 5:
            return
        card = cards[index]
        selected.add(key)
        points.append(dict(text=card['units'][position], sources=card['sources']))

    # Preserve essential eligibility/negative exceptions visibly. Other
    # qualifications stay with their complete source paragraph whenever that
    # paragraph is selected; omitted claims and conditions remain in details.
    critical = []
    for index, card in enumerate(cards):
        for position, unit in enumerate(card['units']):
            if QUALIFIER.search(unit) and (index, position) not in selected:
                critical.append((index, position))
    # Give every supporting card a directly attributable source unit.
    for index, card in enumerate(cards[1:], 1):
        if card['units']:
            append(index, 0)
    for index, position in critical:
        append(index, position)
    if len(cards) == 1:
        for task in primary['node'].get('tasks', []):
            append(0, primary['units'].index(task))
    # Fill remaining slots round-robin instead of restarting five per card.
    for position in range(max(len(card['units']) for card in cards)):
        for index, card in enumerate(cards):
            if position < len(card['units']):
                append(index, position)

    # Short cards can expose three readable points without adding content.
    # Keep the remaining sentences together when splitting one whole paragraph.
    if len(points) < 3:
        from .content_text import sentences
        for index, point in enumerate(points):
            units = sentences(point['text'])
            if len(units) > 1:
                points[index:index + 1] = [
                    dict(text=units[0], sources=point['sources']),
                    dict(text=' '.join(units[1:]), sources=point['sources'])]
                if len(points) >= 3:
                    break

    # If more than five whole qualifiers exist, keep the overflow visibly next
    # to the introduction. A soft length budget never deletes an exception.
    overflow = [cards[i]['units'][p] for i, p in critical if (i, p) not in selected]
    if overflow:
        intro += '\n\n' + '\n\n'.join(overflow)
    details = []
    for card in cards:
        node = card['node']
        full = node['text']
        if node.get('tasks'):
            full += '\n\n' + '\n'.join('☐ ' + task for task in node['tasks'])
        details.append(dict(title=node['title'], text=full, sources=card['sources']))
    text = intro
    if points:
        text += '\n\n' + '\n'.join('• ' + point['text'] for point in points)
    text += '\n\n' + DISCLOSURE
    return dict(text=text, sources=sources, intro=intro, points=points,
                details=details, priority='normal', note=DISCLOSURE)
