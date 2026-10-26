'use strict';
/* Presentation and topic entry points. Operations stay in the existing controllers. */
window.createTopicScreens=function({planner,request,showPlan,showTasks,search,openHandbook,openResume,leave,home,isBusy=()=>false}){
  const $=id=>document.getElementById(id),M=window.ScreenModel;
  const topics={
    housing:{title:'Жильё',subtitle:'Своё жильё и место, где можно жить сейчас',image:'housing-house-cropped.svg',question:'Что хочешь решить с жильём?',example:'Выпускаюсь через месяц, пока негде жить',branches:[['Постоянное жильё','Документы, очередь и получение жилья','permanent-housing.svg','housing'],['Временное жильё','Где жить сейчас и пока ждёшь своё','housing-hotel.svg','temporary']]},
    career:{title:'Карьера',subtitle:'Работа, резюме и первые шаги',image:'career-briefcase.svg',question:'С чем помочь в работе?',example:'Хочу найти первую работу, опыта пока нет',branches:[['Поиск работы','Найти подходящие вакансии','career-briefcase.svg','work'],['Резюме','Рассказать о себе работодателю','art-documents.svg','resume'],['Трудоустройство и права','Договор, зарплата и условия','art-rights.svg','work-materials']]},
    finance:{title:'Финансы',subtitle:'Выплаты и льготы, бюджет и платежи',image:'finance-wallet.svg',question:'Что хочешь решить с деньгами?',example:'Не получается распределить деньги на месяц',branches:[['Выплаты и льготы','Что положено и как оформить','finance-wallet.svg','benefits'],['Мой бюджет','Распределить деньги на важное','finance-wallet.svg','money_budget'],['Обязательные платежи','Разобраться с долгом за жильё','housing-apartment.svg','debt_utilities'],['Долги и займы','Что делать, если трудно платить','art-plans.svg','debt_credit']]},
    support:{title:'Поддержка',subtitle:'Помощь рядом и защита твоих прав',image:'support-heart-cropped.svg',question:'Какая помощь тебе нужна?',example:'Нужна помощь с едой и одеждой',branches:[['Места помощи','Еда, одежда, гигиена и ночлег','support-heart-cropped.svg','help'],['Помощь и защита прав','Разобраться, куда обратиться','art-rights.svg','help-materials']]}
  };
  let current=null,branch=null,origin=null,catalog=null,ticket=0,housing=null,career=null;
  const drafts=new Map(),scrolls=new Map();
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  function button(text,fn,cls='plan-secondary'){const b=node('button',text,cls);b.type='button';b.onclick=fn;return b;}
  function illustration(file,cls=''){const img=node('img',undefined,cls);img.src='/static/figma/'+file;img.alt='';img.draggable=false;return img;}
  function view(name){const changed=document.body.dataset.view!==name;$('topic').hidden=true;document.body.dataset.view=name;if(changed)navigateHistory();}
  function stash(){if(current&&!$('topic').hidden){drafts.set(current,$('topic-question')?.value??drafts.get(current)??'');scrolls.set(current+':'+(branch||''),window.scrollY);}}
  function root(name){stash();current=null;branch=null;origin=null;view(name);}
  function remember(){if(current&&!$('topic').hidden){stash();origin={id:current,branch};} }
  function tool(fn){remember();fn();}
  async function catalogData(){const data=await request('/api/handbook');catalog=data;return data;}
  function selectPrompt(text){const input=$('topic-question');input.value=text;drafts.set(current,text);input.dispatchEvent(new Event('input'));input.focus();input.scrollIntoView({block:'center',behavior:'smooth'});}
  function start(text,answer=false){if(isBusy())return;const id=current;tool(async()=>{showPlan();if(answer&&!id)origin={home:true};const started=await (answer?planner.startAnswer(text):planner.startQuestion(text));if(started){if(id)drafts.delete(id);}else if(!document.getElementById('plan-dialog').open){id?topic(id):home();} });}
  function searchCard(target,cls){const rental=target==='rental',b=button('',()=>tool(()=>search(target)),'search-entry '+cls);b.dataset.searchTarget=target;const copy=node('span');copy.append(node('strong',rental?'Поиск жилья':'Поиск работы'),node('small',rental?'Квартиры в твоём городе':'Подходящие вакансии'));b.append(illustration(rental?'search-housing.svg':'search-work.svg'),copy,node('span','↗','search-arrow'));b.lastChild.setAttribute('aria-hidden','true');return b;}
  function heading(title,subtitle){const h=node('div',undefined,'topic-heading');h.append(node('h1',title));if(subtitle)h.append(node('p',subtitle));return h;}
  function navigateHistory(){window.dispatchEvent(new CustomEvent('screen-navigation'));}
  async function topic(id,sub=null,prefill=null){
    if(!topics[id]||isBusy()||!leave())return;stash();if(prefill!==null)drafts.set(id,prefill);current=id;branch=sub;const turn=++ticket;
    for(const name of ['landing','search','saved','material','plan','tasks','resume','handbook','share-panel'])$(name).hidden=true;
    document.body.classList.remove('search-open');document.body.dataset.view='topic';$('topic').hidden=false;$('topic').replaceChildren();
    window.WebApp?.BackButton?.show();
    const t=topics[id],back=button('Назад',()=>sub?topic(id):home(),'back topic-back');
    $('topic').append(back,heading(sub?branchTitle(t,sub):t.title,sub||id==='housing'?null:t.subtitle));
    if(id==='housing'&&!sub){housing??=window.createHousingScreen({request,openHandbook:id=>tool(()=>openHandbook(id)),search:target=>tool(()=>search(target))});housing.render($('topic'));navigateHistory();window.scrollTo(0,scrolls.get(id+':')||0);return;}
    if(id==='career'&&!sub){career??=window.createCareerScreen({request,search:()=>tool(()=>search('work')),openResume:()=>tool(()=>openResume(()=>topic('career'))),openHandbook:id=>tool(()=>openHandbook(id)),materials:()=>topic('career','work-materials')});career.render($('topic'));navigateHistory();window.scrollTo(0,scrolls.get(id+':')||0);return;}
    const target=M.searchTarget(id==='career'?'work':id==='finance'?'benefits':id);if(target)$('topic').append(searchCard(target,'topic-search'));
    if(!sub){
      const form=node('form',undefined,'question-card');form.append(node('h2',t.question),node('p','Поможем разобраться и составить план.'));
      const label=node('label','Твой вопрос','question-label');label.htmlFor='topic-question';
      const input=node('textarea');input.id='topic-question';input.rows=2;input.placeholder='Например: '+t.example;input.value=drafts.get(id)||'';
      const note=node('small','Коротко, до 160 символов. Детали уточним дальше.','question-note');note.id='topic-question-note';input.setAttribute('aria-describedby',note.id);
      const submit=node('button','Составить план →','plan-primary');submit.type='submit';
      input.oninput=()=>{drafts.set(id,input.value);input.style.height='auto';input.style.height=Math.min(240,input.scrollHeight)+'px';const n=[...input.value.trim()].length;note.textContent=n>130?`${n} / 160 · ${n>160?'Сократи вопрос, текст сохранён':'Детали уточним дальше'}`:'Коротко, до 160 символов. Детали уточним дальше.';note.classList.toggle('over-limit',n>160);submit.disabled=!M.validQuestion(input.value);};
      form.onsubmit=e=>{e.preventDefault();if(M.validQuestion(input.value))start(input.value.trim());};form.append(label,input,note,submit);$('topic').append(form);input.oninput();
      const grid=node('div',undefined,'branch-grid');for(const [title,copy,img,action] of t.branches){const b=button('',()=>actionClick(action,t,title),'branch-card');b.append(node('strong',title),node('span',copy),illustration(img));grid.append(b);}$('topic').append(grid);
      $('topic').append(node('h2','Полезное по теме','topic-section-title'));
    }
    const list=node('div',undefined,'topic-materials');list.append(node('p','Загружаем материалы…','plan-footnote'));$('topic').append(list);
    navigateHistory();window.scrollTo(0,scrolls.get(id+':'+(sub||''))||0);
    try{const data=await catalogData();if(turn!==ticket||$('topic').hidden)return;renderMaterials(list,data,id,sub);}catch(e){if(turn!==ticket)return;list.replaceChildren(node('p','Не удалось загрузить материалы.','plan-footnote'),button('Повторить',()=>topic(id,sub)));}
  }
  function branchTitle(t,id){if(id==='benefits')return 'Выплаты и льготы';if(id==='temporary')return 'Временное жильё';if(id==='housing')return 'Постоянное жильё';return t.branches.find(b=>b[3]===id)?.[0]||'Материалы';}
  function actionClick(action,t,title){
    if(['work','help'].includes(action)){tool(()=>search(action));return;}
    if(action==='resume'){tool(()=>openResume(()=>topic(current,branch)));return;}
    if(action==='ask'||action==='ask-refusal'){selectPrompt(action==='ask-refusal'?'Мне отказали в выплате. Что делать дальше?':t.example);return;}
    if(['money_budget','debt_utilities','debt_credit'].includes(action)){
      if(catalog?.entries?.some(e=>e.id===action))tool(()=>openHandbook(action));else selectPrompt(title+': '+t.example);return;
    }
    topic(current,action);
  }
  function renderMaterials(list,data,id,sub){
    list.replaceChildren();
    if(sub==='temporary'){
      const cards=node('div',undefined,'temporary-cards');for(const [title,img,prompt] of [['Социальная гостиница','housing-hotel.svg','Где найти социальную гостиницу?'],['Общежитие','housing-dormitory.svg','Как получить место в общежитии?'],['Аренда квартиры','housing-apartment.svg',null]]){const b=button('',()=>prompt?topic(current,null,prompt):tool(()=>search('rental')),'branch-card');b.append(node('strong',title),illustration(img));cards.append(b);}list.append(cards);
    }
    const cats={housing:['housing'],career:['work'],benefits:['benefits'],finance:['benefits'],support:['help']};
    const financeIDs=['money_budget','debt_credit','debt_utilities','debt_collection','bank_access','fraud_money_loss'];
    const entries=(data.entries||[]).filter(e=>id==='finance'?(sub==='benefits'?e.category==='benefits':financeIDs.includes(e.id)||e.category==='benefits'):cats[id].includes(e.category)).filter(e=>!e.parent);
    if(!entries.length){list.append(node('p','Опиши свой вопрос выше — поможем выбрать дальнейшие шаги.','plan-footnote'));return;}
    for(const e of entries.slice(0,sub?entries.length:4)){const b=button('',()=>tool(()=>openHandbook(e.id)),'material-row');b.append(node('strong',e.title),node('span','Открыть материал →'));list.append(b);}
    if(!sub&&entries.length>4)list.append(button('Все материалы по теме',()=>topic(id,id==='career'?'work-materials':id==='support'?'help-materials':id),'quiet'));
  }
  for(const [id,t] of Object.entries(topics)){const b=button('',()=>topic(id),'theme-card theme-'+id);b.append(node('strong',t.title),illustration(t.image));$('theme-grid').append(b);}
  $('home-answer').onclick=()=>start('',true);
  document.querySelectorAll('[data-plan-mode]').forEach(b=>b.onclick=()=>b.dataset.planMode==='tasks'?showTasks():showPlan());
  return {topic,root,view,remember,answer:()=>start('',true),active:()=>current&&!$('topic').hidden,back(){if(this.active()){if(current==='housing'&&housing?.back())return true;if(current==='career'&&career?.back())return true;branch?topic(current):home();return true;}if(origin){const prev=origin;origin=null;prev.home?home():topic(prev.id,prev.branch);return true;}return false;},clear(){origin=null;},topics};
};
