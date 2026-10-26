"""MAX presentation/state adapter for the shared, prepared planner catalogue."""
import copy
import os
import secrets
from urllib.parse import urlsplit,parse_qs

from ..channels.max import callback, link, message
from . import planner_ai
from .adjacent_guides import needs_support, prepared_route
from .handbook import handbook_material
from .help_routes import bot_name, plan_button
from .planner_content import urgent_response, money_urgent_response, SEARCHES


def enabled(session):
    return (os.getenv('PLANNER_AI_ENABLED')=='1' and os.getenv('LLM_ENABLED','1')!='0'
            and session.values.get('role')=='child')


def stale(bot):
    return [message('Эта кнопка сейчас неактивна. Вернись к текущему вопросу или открой главное меню.',
                    [callback('Главное меню','menu')])]


def chunks(text):
    while len(text)>3500:
        end=max(text.rfind('\n',0,3500),text.rfind(' ',0,3500))
        if end<1:end=3500
        yield text[:end]
        text=text[end:]
    if text:yield text


def snapshot(state):
    return copy.deepcopy({k:v for k,v in state.items() if k not in {'history','issued','nonce'}})


def action_button(bot,item):
    url=item.get('url','');title=item.get('label') or item.get('title') or 'Открыть источник'
    if url.startswith(('https://','tel:')):return link(title,url)
    parsed=urlsplit(url);section=parse_qs(parsed.query).get('section',[''])[0]
    app=bot_name(bot.config)
    if app and not parsed.netloc and parsed.path=='/' and section in {'work','rental','help'}:
        return dict(type='open_app',text=title,web_app=app,payload=section)
    identifier=parse_qs(parsed.query).get('handbook',[''])[0]
    if app and not parsed.netloc and parsed.path=='/' and identifier and handbook_material(bot.config,identifier):
        return dict(type='open_app',text=title,web_app=app,payload='handbook_'+identifier)
    return None


def render(bot,session):
    state=session.coverage
    response=state['response']
    state['nonce']=secrets.token_hex(5);state['issued']={}
    controls=[]
    def action(title,kind,value=None):
        index=str(len(state['issued']))
        state['issued'][index]=dict(kind=kind,value=value)
        return callback(title,f'cov:{bot.revision}:{state["nonce"]}:{index}')
    if response['status']=='clarify':
        for choice in response.get('choices',[]):
            controls.append(action(choice['title'],'choice',choice['id']))
        if response.get('other_choices'):controls.append(action('Другая тема','all_topics'))
        controls.append(action('Пока не знаю' if state.get('mode') in {'situations','topics'}
                               or response.get('question_id')=='topic_choice' else 'Не знаю','unknown'))
        text=response.get('question',planner_ai.QUESTIONS['topic_choice'])
        information=response.get('information',{})
        replies=[message(part) for part in chunks(information.get('text',''))]+[message(text)]
        for item in information.get('sources',[]):
            button=action_button(bot,item)
            if button and button not in controls:controls.append(button)
    else:
        information=response.get('information',{})
        identifiers=list(dict.fromkeys([*response.get('context',{}).get('material_ids',[]),
                                       *(s.get('id') for s in information.get('sources',[]))]))
        cards=[card for identifier in identifiers if identifier
               and (card:=handbook_material(bot.config,identifier))]
        replies=[]
        for card in cards:
            replies.extend(message(part) for part in chunks(card['title']+'\n\n'+card['text']))
            for item in card.get('actions',[]):
                button=action_button(bot,item)
                if button:controls.append(button)
            app=bot_name(bot.config)
            if app:
                controls.append(dict(type='open_app',text='Открыть карточку в приложении',
                                     web_app=app,payload='handbook_'+card['id']))
        if not replies:
            replies=[message(information.get('text') or response.get('message') or
                             'Пока у нас недостаточно проверенных материалов по этой ситуации')]
        for source in information.get('sources',[]):
            if source.get('id') in SEARCHES:
                button=action_button(bot,source)
                if button and button not in controls:controls.append(button)
        if response.get('context') and information.get('priority')!='urgent':
            button=plan_button(bot.config)
            if button:controls.append(button)
        controls.append(action('Сменить тему','restart'))
    if state.get('history'):controls.append(action('Назад','back'))
    controls.append(callback('Главное меню','menu'))
    replies[-1]=message(replies[-1]['text'],controls)
    return replies


async def propose(bot,user,session,question_id=None,answer=None):
    state=session.coverage
    response=await planner_ai.PlannerAI().propose(str(user),'find_help_route',state['original'],
        question_id=question_id,answer=answer,config=bot.config,
        description=state.get('description'),situation=state.get('situation'),prepared_only=True)
    state['response']=response
    state['question_id']=response.get('question_id')
    state['mode']='topics' if state['question_id']=='topic_choice' else 'answer'
    return render(bot,session)


def start(bot,session,text=''):
    session.coverage=dict(original=text,description='',publication=bot.revision,
                          history=[],mode='topics',question_id='topic_choice',
                          response=planner_ai.topic_clarification())
    return render(bot,session)


async def handle(bot,user,session,text='',payload=None,*,safety_only=False):
    if not enabled(session):
        return stale(bot) if payload and payload.startswith('cov:') else None
    if safety_only:
        if payload or not text.strip() or text.strip().startswith('/') or len(text)>2500:return None
        response=urgent_response(bot.config,text) or money_urgent_response(bot.config,text)
        if not response:return None
        session.coverage=dict(original=text,description='',publication=bot.revision,
                              history=[],mode='answer',question_id=None,response=response)
        return render(bot,session)
    if payload=='cov:start':
        if session.node!=bot.config['menu']:return stale(bot)
        session.pending=None
        return start(bot,session)
    state=session.coverage
    if state and state.get('publication')!=bot.revision:
        session.coverage={}
        return stale(bot)
    if (not payload and state and state.get('question_id')
            and text.strip().casefold().replace('ё','е') in {'не знаю','пока не знаю'}):
        index=next((i for i,v in state.get('issued',{}).items() if v['kind']=='unknown'),None)
        if index is not None:payload=f'cov:{bot.revision}:{state["nonce"]}:{index}'
    if payload:
        if not state or len(payload)>64:return stale(bot)
        parts=payload.split(':')
        if len(parts)!=4 or parts[:3]!=['cov',str(bot.revision),state.get('nonce')]:return stale(bot)
        selected=state.get('issued',{}).get(parts[3])
        if not selected:return stale(bot)
        action=selected['kind'];value=selected.get('value')
        if action=='back':
            if not state.get('history'):return stale(bot)
            previous=state['history'].pop();history=state['history']
            session.coverage={**previous,'history':history}
            return render(bot,session)
        if action=='restart':return start(bot,session)
        previous=snapshot(state)
        state['history']=(state.get('history',[])+[previous])[-8:]
        state['issued']={}
        if action=='all_topics':
            state.update(mode='topics',question_id='topic_choice',response=planner_ai.topic_clarification())
            return render(bot,session)
        if action=='unknown' and (state.get('question_id')=='topic_choice' or state.get('mode')=='situations'):
            if state.get('mode')=='situations':
                state.update(mode='description',question_id='situation_description',response=dict(
                    status='clarify',question_id='situation_description',question=planner_ai.QUESTIONS['situation_description']))
            else:
                state.update(mode='situations',response=dict(status='clarify',question_id='topic_choice',
                    question='Что сейчас происходит? Можно выбрать ситуацию или рассказать своими словами.',
                    choices=[dict(id=k,title=v) for k,v in planner_ai.TOPIC_TITLES.items()]))
            return render(bot,session)
        return await propose(bot,user,session,state.get('question_id'),
                             'Не знаю' if action=='unknown' else value)
    if not text.strip():return None
    if len(text)>2500:return [message(bot.text('too_long'))]
    if not state:
        q=text.strip().casefold().replace('ё','е')
        if q.rstrip('.!?') in {'не знаю','не знаю, с чего начать','не знаю с чего начать'}:
            return start(bot,session,text)
        from .federal_guides import match_ids
        if not (prepared_route(text) or needs_support(text) or match_ids(text)):return None
        session.coverage=dict(original=text,description='',publication=bot.revision,history=[],mode='answer')
        return await propose(bot,user,session)
    state['history']=(state.get('history',[])+[snapshot(state)])[-8:]
    if state.get('mode')=='description':
        state['description']=text
        return await propose(bot,user,session)
    if state.get('question_id'):
        return await propose(bot,user,session,state['question_id'],text)
    session.coverage=dict(original=text,description='',publication=bot.revision,history=[],mode='answer')
    return await propose(bot,user,session)
