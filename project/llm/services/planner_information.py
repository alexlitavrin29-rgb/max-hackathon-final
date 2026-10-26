"""Compact extractive answers with source-local provenance and full originals.

The model selects material IDs only. Every answer unit is copied whole from a
published card: qualifications share their paragraph with the claim they limit.
"""
import re


DISCLOSURE = 'Полные условия и исключения — в подробностях материалов ниже.'
QUALIFIER = re.compile(
    r'23 года|не полн|не включены|пошлин|не заменяет|не отказывай|'
    r'не гарант|не всегда|сохранени|исключен|при наличии|не нужны|не выдум', re.I)


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
    normalized = lambda value: re.sub(r'\s+', ' ', value).strip().casefold()
    seen_text = {normalized(intro): None}

    def append(index, position):
        key = (index, position)
        if key in selected:
            return
        card = cards[index]
        value = normalized(card['units'][position])
        if value in seen_text:
            previous = seen_text[value]
            if previous is not None:
                urls = {source['url'] for source in points[previous]['sources']}
                points[previous]['sources'].extend(source for source in card['sources']
                                                   if source['url'] not in urls)
            selected.add(key)
            return
        if len(points) >= 5:
            return
        selected.add(key)
        points.append(dict(text=card['units'][position], sources=card['sources']))
        seen_text[value] = len(points) - 1

    # Preserve essential eligibility/negative exceptions visibly. Other
    # qualifications stay with their complete source paragraph whenever that
    # paragraph is selected; omitted claims and conditions remain in details.
    critical = []
    for index, card in enumerate(cards):
        for position, unit in enumerate(card['units']):
            if QUALIFIER.search(unit) and (index, position) not in selected:
                critical.append((index, position))
    procedural = bool(primary['node'].get('tasks'))
    if procedural:
        for task in primary['node'].get('tasks', []):
            append(0, primary['units'].index(task))
        for index, position in critical:
            if index == 0:
                append(index, position)
    represented = {0}
    # Optional secondary advice must not displace the primary concrete actions.
    # If its claim/qualifications cannot fit, its full card remains in details.
    for index, card in enumerate(cards[1:], 1):
        if not card['units']:
            continue
        positions = list(dict.fromkeys([0, *[p for i, p in critical if i == index]]))
        size = len(intro) + len(DISCLOSURE) + 48 + sum(len(p['text']) + 1 for p in points)
        if procedural and (len(points) + len(positions) > 5
                           or size + sum(len(card['units'][p]) + 1 for p in positions) > 850):
            continue
        represented.add(index)
        for position in positions:
            append(index, position)
    for index, position in critical:
        if index in represented:
            append(index, position)
    # Fill remaining slots round-robin instead of restarting five per card.
    for position in range(max(len(card['units']) for card in cards)):
        for index, card in enumerate(cards):
            if index in represented and position < len(card['units']):
                append(index, position)

    # Short cards can expose three readable points without adding content.
    # Keep the remaining sentences together when splitting one whole paragraph.
    if len(points) < 3:
        from .content_text import sentences
        for index, point in enumerate(points):
            if re.search(r'(?m)^\d+[.)]\s', point['text']):
                continue
            units = sentences(point['text'])
            if len(units) > 1:
                points[index:index + 1] = [
                    dict(text=units[0], sources=point['sources']),
                    dict(text=' '.join(units[1:]), sources=point['sources'])]
                if len(points) >= 3:
                    break

    # If more than five whole qualifiers exist, keep the overflow visibly next
    # to the introduction. A soft length budget never deletes an exception.
    overflow = [cards[i]['units'][p] for i, p in critical if i in represented and (i, p) not in selected]
    if overflow:
        intro += '\n\n' + '\n\n'.join(overflow)
    intro += '\n\nПомогу разобраться. Начнём с самого нужного.'
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
