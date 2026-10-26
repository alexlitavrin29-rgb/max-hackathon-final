"""Explicitly added mini-app reference, independent of bot publication.

One source-checked guide, not an automatic trust upgrade of published material.
No runtime fetch or model-written facts. Its enabling anchor must stay child
reachable, non-draft and match the reviewed publication fingerprint.
"""
from .handbook_sources import fingerprint
from .help_routes import _allowed, _path

WAGE_ID = 'work_wages'
INSTRUCTION = 'https://xn--80akibcicpdbetz7e2g.xn--p1ai/instruction/7'
COMPLAINT = 'https://xn--80akibcicpdbetz7e2g.xn--p1ai/problems'
REVIEW = {
 'checked': '2026-10-19', 'kind': 'source_checked_reference',
 'source': INSTRUCTION, 'availability': 'CONTENT_READ',
 'claim_relation': 'instruction steps 1, 3, 5 and opening note; complaints service/login read',
 'currentness': 'Named process checked; no universal legal-currentness certification',
 'editor_acceptance': 'PENDING_OWNER_REVIEW',
}


def nodes(config):
    if any(n.get('id') == WAGE_ID for n in config.get('nodes', [])):
        return []
    anchor = next((n for n in config.get('nodes', []) if n.get('id') == 'job_contract'), None)
    branches = {b['id']: b for b in config.get('branches', [])}
    values = {'role': 'child'}
    if (not anchor or anchor.get('content_status') in {'unverified_reference', 'draft', 'archived'}
            or fingerprint(anchor) != 'd1e171d4bfab508bbea5badfdf88a4d17a0b99f49d396333001c5dca095f4116'
            or not _allowed(anchor, branches, values) or not _path(config, 'job_contract', values)):
        return []
    return [dict(id=WAGE_ID, title='Если задерживают зарплату', kind='message', branch=anchor['branch'],
        content_status='source_checked_reference',
        text=('За помощью при задержке зарплаты можно обратиться в государственную инспекцию труда. '
              'По памятке Роструда это можно сделать сразу или параллельно с обращением к работодателю.'),
        tasks=[
            'Проверь дату выплаты в трудовом договоре, коллективном договоре или правилах внутреннего трудового распорядка.',
            'Направь работодателю письменное требование о выплате. Сохрани свой экземпляр с отметкой о принятии.',
            'Открой «Сообщить о проблеме» на портале Роструда и выбери вопрос о заработной плате. Для отправки потребуется вход.',
        ], sources=[], buttons=[],
        editorial_sources=['wage_instruction', 'wage_complaint'])]


def actions():
    return [dict(label='Роструд: что делать при задержке зарплаты', url=INSTRUCTION,
                 anchors=['памятке Роструда'],
                 description='Порядок действий и образцы заявлений; прочитай условия для своей ситуации.'),
            dict(label='Роструд: сообщить о проблеме с зарплатой', url=COMPLAINT,
                 anchors=['Сообщить о проблеме'],
                 description='Обращение в инспекцию труда; для отправки потребуется вход.')]
