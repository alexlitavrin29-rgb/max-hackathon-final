"""Supplemented links preserve published facts and access boundaries."""
import copy
import json
from pathlib import Path

import pytest
from project.llm.services.handbook import handbook_catalog, handbook_material
from project.llm.services.planner_content import material_catalog, material_access, build_information, enrich


@pytest.fixture
def config():return json.loads(Path('submission/scenario.json').read_text())['config']


def test_employment_gets_direct_document_and_official_links(config):
    card=handbook_material(config,'employment_docs')
    urls={a['url'] for a in card['actions']}
    assert '/?handbook=doc_passport' in urls
    assert '/?handbook=doc_snils_proof' in urls
    assert 'https://mintrud.gov.ru/ministry/programms/trudotn/dogovor' in urls
    link=next(a for a in card['actions'] if a['url']=='/?handbook=doc_snils_proof')
    assert 'подтверждение регистрации в системе индивидуального учёта' in link['anchors']
    assert link['description']


def test_sources_do_not_change_published_text_or_catalog_trust(config):
    before=copy.deepcopy(config);catalog_before=handbook_catalog(config)
    materials={m['id']:m for m in material_catalog(config)};allowed=set(materials)
    access=material_access(config)
    for mid in ['employment_docs','job_contract','job_minor','study_debts','resume_first']:
        card=handbook_material(config,mid)
        assert materials[mid]['text'] in card['text']
        assert card['content_status']==access[mid]['content_status']
    assert config==before and handbook_catalog(config)==catalog_before
    assert {m['id'] for m in material_catalog(config)}==allowed
    if 'doc_reg_without' in allowed:
        assert access['doc_reg_without']['content_status']=='source_checked_reference'


def test_external_gap_cards_get_topic_specific_sources(config):
    for mid in ['job_contract','job_minor','job_study','resume_first','cover_letter','portfolio','study_debts']:
        card=handbook_material(config,mid)
        assert any(a['url'].startswith('https:') and a.get('description') for a in card['actions']),mid


def test_changed_text_or_invisible_target_cannot_inherit_supplement(config):
    from project.llm.services.handbook_sources import supplemental_actions
    node=next(n for n in config['nodes'] if n['id']=='employment_docs')
    assert supplemental_actions(config,node,{'employment_docs'})
    assert not any(a['url'].startswith('/?handbook=') for a in supplemental_actions(config,node,{'employment_docs'}))
    changed=dict(node,text=node['text']+' Altered publication meaning.')
    assert supplemental_actions(config,changed,set())==[]


def test_initial_answer_keeps_anchors_and_only_intent_search(config):
    p=enrich(config,{'topic':'work','material_ids':['employment_docs']},'Хочу найти работу',suggest_actions=False)
    assert any(s.get('anchors') for s in p['information']['sources'])
    assert any(s['id']=='app_work' for s in p['information']['sources'])
    gas=enrich(config,{'topic':'housing','material_ids':['hq_household_gas']},'В квартире пахнет газом',suggest_actions=False)
    assert not any(s['id'].startswith('app_') for s in gas['information']['sources'])


def test_article_65_keeps_literal_published_url_and_existing_sources(config):
    info=build_information(config,['employment_docs'])
    original=next(n for n in config['nodes'] if n['id']=='employment_docs')['editorial_sources'][0]
    assert any(s['url']==original for s in info['sources'])
    assert any('паспорт' in s.get('anchors',[]) for s in info['sources'])


def test_existing_legal_sources_get_precise_article_anchors():
    from project.llm.services.handbook_sources import citation_anchors
    actions=[{'label':'159-ФЗ, ст. 8: право на жильё','url':'https://example.org/housing'},
             {'label':'159-ФЗ, ст. 6: обучение','url':'https://example.org/study'}]
    enriched=citation_anchors('Основания из ст. 8 закона № 159-ФЗ. Учёба — статья 6 закона № 159-ФЗ.',actions)
    assert 'ст. 8 закона № 159-ФЗ' in enriched[0]['anchors']
    assert 'статья 6 закона № 159-ФЗ' in enriched[1]['anchors']
    assert '159-ФЗ' not in enriched[0]['anchors']
    assert actions[0].get('anchors') is None
