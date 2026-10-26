/* Housing has its own progress; reading a stage never creates or changes a plan. */
window.createHousingScreen=function({request,openHandbook,search}){
  let activeDialog=null;
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const button=(text,fn,cls='plan-secondary')=>{const b=node('button',text,cls);b.type='button';b.onclick=fn;return b;};
  const art=file=>{const img=node('img');img.src='/static/figma/'+file;img.alt='';return img;};
  const law='https://www.consultant.ru/document/cons_doc_LAW_12778/785ff748eda8db09e9275dc8231632fa1a2d0062/';
  const stages=[
    {title:'Встань на учёт',icon:'housing-step-register.svg',material:'hq_state_housing_apply',
      guide:'Для Новосибирской области есть прямая ссылка на Госуслуги ниже. Перед подачей проверь регион в описании услуги. Для другого региона найди на его официальном сайте раздел «Опека и попечительство» или «Обеспечение жильём детей-сирот» и услугу включения в список. Если не получается найти, спроси в опеке по месту жительства, какое ведомство ведёт список и как подать заявление.',
      prompt:'Хочу подать заявление о включении в список на жильё для детей-сирот. Подскажите уполномоченный орган, способ подачи и перечень документов. Если услуга доступна онлайн, пришлите прямую ссылку на неё.',
      text:'Проверь включение в специальный список на жильё. С 14 лет заявление подаёт законный представитель.',
      detail:'Попроси решение о включении в список. Представитель подаёт заявление в течение трёх месяцев после достижения 14 лет или возникновения оснований. С 18 лет или при полной дееспособности можно обратиться самостоятельно, если тебя ещё не включили. По правилам № 397 решение принимают не позднее 60 рабочих дней после подачи; выписку направляют в течение 5 рабочих дней после решения, сведения вносят в список в течение 10 рабочих дней после акта. Документы и способ подачи уточни в своём регионе.',
      links:[['Подать заявление на Госуслугах — Новосибирская область','https://www.gosuslugi.ru/676953/1'],['Правила, сроки и форма заявления — № 397','https://www.consultant.ru/document/cons_doc_LAW_322144/93e354c4ca554513570cdcc1bc4582f8b5b0493c/'],['Условия права на жильё — статья 8',law]],
      next:'in_list',mark:'Меня включили в список'},
    {title:'Подай заявление',icon:'housing-step-apply.svg',material:'hq_state_housing_when',
      guide:'На официальном сайте ведомства, которое ведёт твой список, найди информацию о предоставлении жилья детям-сиротам. Уточни форму заявления именно на предоставление квартиры и способ подачи. Если на сайте нет нужной услуги, позвони в ведомство или обратись туда лично.',
      prompt:'Я включён(а) в список на жильё для детей-сирот. Как подать заявление о предоставлении квартиры? Подскажите форму, необходимые документы и способ подачи. Как получить подтверждение, что заявление принято?',
      text:'Обратись в уполномоченный орган региона за предоставлением квартиры. По общему правилу — с 18 лет.',
      detail:'Включение в список и заявление на предоставление квартиры — разные этапы. Закон предусматривает исключения для предоставления до 18 лет. Попроси форму заявления именно на предоставление жилья детям-сиротам, перечень документов и подтверждение регистрации обращения. Сохрани номер и дату подачи.',
      links:[['Когда предоставляют жильё — статья 8',law]],
      next:'application_sent',mark:'Заявление подано'},
    {title:'Ожидай предоставления жилья',icon:'housing-step-wait.svg',material:'hq_state_housing_delay',
      text:'Уточняй ход предоставления жилья. При задержке можно обратиться в прокуратуру и суд.',
      detail:'Не считай срок ответа сроком выдачи квартиры. Общий срок рассмотрения письменного обращения — 30 дней со дня регистрации; в исключительных случаях его продлевают ещё максимум на 30 дней с уведомлением. Для конкретной госуслуги проверь её регламент. К жалобе приложи решение о включении в список, заявление и полученные ответы.',
      links:[['Обратиться в прокуратуру','https://epp.genproc.gov.ru/ru/gprf/internet-reception/personal-receptionrequest/'],['Бесплатная юридическая помощь','https://xn--80adbm1cg.xn--p1ai/'],['Сроки ответа на обращение — 59-ФЗ, статья 12','https://mintrud.gov.ru/docs/laws/108']],
      next:'offered',mark:'Мне предложили квартиру'},
    {title:'Получи ключи и заключи договор',icon:'housing-step-key.svg',material:'hq_state_housing_agreement',
      text:'Проверь квартиру и договор специализированного найма. Ключи дают право жить в квартире; собственность оформляют отдельно.',
      detail:'Запроси у предоставляющего жильё органа проект договора и акт передачи. При осмотре зафиксируй недостатки, проверь адрес, площадь, состояние жилья и показания счётчиков. Получи свой подписанный экземпляр договора и акта; при спорных условиях обратись к бесплатному юристу.',
      links:[['Типовой договор специализированного найма — № 548','https://base.garant.ru/70408456/'],['Правила специализированного найма — статья 8',law],['Проверить договор с бесплатным юристом','https://xn--80adbm1cg.xn--p1ai/']],
      next:'received',mark:'Ключи и договор получены'},
    {title:'Перейди на социальный наём',icon:'housing-step-social.svg',material:'hq_hq_sale_owner',
      guide:'Обратись к наймодателю, указанному в твоём договоре специализированного найма. Уточни порядок перехода на социальный наём, форму заявления и документы. Информацию о таком переходе также можно найти на официальном сайте своего региона в разделе о жилье для детей-сирот.',
      prompt:'Срок моего договора специализированного найма заканчивается. Как оформить переход на социальный наём? Подскажите, куда подать заявление, какие документы нужны и как получить решение и новый договор.',
      text:'После специализированного найма при выполнении условий оформляют социальный наём. Приватизация — отдельный следующий шаг.',
      detail:'Обычный срок специализированного найма — 5 лет. Обратись к наймодателю за решением о переводе и договором социального найма. При предусмотренных законом обстоятельствах специализированный наём могут продлить. Для лиц от 23 лет при выполнении условий возможно досрочное сокращение срока.',
      links:[['Досрочное сокращение срока — Госуслуги','https://www.gosuslugi.ru/633526/1/form'],['Условия перехода — статья 8',law]],
      next:'social_rent',mark:'Социальный наём оформлен'}
  ];
  const statuses={unknown:['Не знаю, на каком я этапе',-1],not_in_list:['Ещё не в списке',0],in_list:['В списке на жильё',1],application_sent:['Заявление подано, жду жильё',2],offered:['Мне предложили квартиру',3],received:['Жильё получено',4],social_rent:['Социальный наём оформлен',5]};
  function render(parent){
    let saved=null,loaded=false,saving=false,loading=true;
    const root=node('div',undefined,'housing-overview');parent.append(root);
    function open(index){dialog.showModal();const active=Math.max(0,Math.min(4,statuses[saved?.status]?.[1]??0));for(const [i,d] of steps.entries())d.open=i===(index??active);dialog.scrollTop=0;}
    const card=window.createStageCard({title:'Постоянное жильё',stages,open,act:()=>open()});
    card.root.classList.add('housing-permanent');card.status.classList.add('housing-status');card.details.classList.add('housing-details');
    const badge=card.status;root.append(card.root);
    const temporary=node('section',undefined,'housing-temporary');temporary.append(node('h2','Временное жильё'),node('p','Выбери, где жить сейчас или пока ждёшь квартиру.','housing-temporary-copy'));
    const options=node('div',undefined,'housing-options');temporary.append(options);
    for(const [title,copy,image,id] of [
      ['Социальная гостиница','Временное размещение через соцзащиту','housing-hotel.svg','shelter'],
      ['Общежитие','Место на время учёбы','housing-dormitory.svg','dorm'],
      ['Аренда квартиры','Бюджет, договор и заселение','housing-apartment.svg','rent']]){
      const b=button('',()=>openHandbook(id),'housing-option');b.append(art(image));const text=node('span');text.append(node('strong',title),node('small',copy));b.append(text,node('span','›','housing-option-arrow'));options.append(b);
    }
    root.append(temporary);
    const find=button('',()=>search('rental'),'search-entry housing-search');find.append(art('search-housing.svg'));const copy=node('span');copy.append(node('strong','Найти квартиру в аренду'),node('small','Объявления в твоём городе'));find.append(copy,node('span','↗','search-arrow'));root.append(find);
    const dialog=node('dialog',undefined,'housing-route');activeDialog=dialog;dialog.setAttribute('aria-label','Получение постоянного жилья');root.append(dialog);
    const handle=node('div',undefined,'housing-sheet-handle');handle.setAttribute('aria-hidden','true');dialog.append(handle);
    const head=node('div',undefined,'housing-route-heading');head.append(node('h2','Получение постоянного жилья'),button('×',()=>dialog.close(),'housing-close'));head.lastChild.setAttribute('aria-label','Закрыть маршрут жилья');dialog.append(head);
    const routeStatus=node('p',undefined,'housing-route-status'),edit=button('Изменить статус',()=>{editor.hidden=!editor.hidden;if(!editor.hidden)select.focus();},'quiet');
    dialog.append(routeStatus,edit,node('p','Статус ты отмечаешь сам.','housing-personal-note'));
    const editor=node('form',undefined,'housing-status-editor');editor.hidden=true;
    const label=node('label','Мой статус'),select=node('select');select.id='housing-status-select';label.htmlFor=select.id;
    for(const [value,[title]] of Object.entries(statuses)){const option=node('option',title);option.value=value;select.append(option);}
    const save=button('Сохранить статус',()=>{});save.type='submit';editor.append(label,select,save);dialog.append(editor);
    const feedback=node('p',undefined,'housing-feedback');feedback.setAttribute('role','status');
    const refresh=button('Обновить статус',()=>load(),'quiet');refresh.hidden=true;dialog.append(feedback,refresh);
    dialog.append(node('p','Способ подачи зависит от региона. На каждом этапе — что искать на официальном сайте и куда обратиться.','housing-region-note'));
    const timeline=node('ol',undefined,'housing-timeline'),steps=[],marks=[];dialog.append(timeline);
    for(const [index,stage] of stages.entries()){
      const li=node('li'),details=node('details',undefined,'housing-step'),summary=node('summary'),icon=node('span',undefined,'housing-step-icon');icon.append(art(stage.icon));
      const copy=node('span');copy.append(node('strong',stage.title),node('small',stage.text));summary.append(icon,copy);details.append(summary);
      const body=node('div',undefined,'housing-step-body'),more=node('details',undefined,'housing-step-more');more.append(node('summary','Что подготовить и где проверить'),node('p',stage.detail));
      if(stage.guide){const guide=node('section',undefined,'housing-submission-guide'),help=node('details',undefined,'housing-request-template');guide.append(node('strong','Куда обратиться'),node('p',stage.guide));help.append(node('summary','Что спросить в ведомстве'),node('p',stage.prompt));guide.append(help);body.append(guide);}
      for(const [i,[title,url]] of stage.links.entries()){const a=node('a',undefined,'housing-action-link');a.append(node('span',title),node('span','↗'));a.href=url;a.target='_blank';a.rel='noopener noreferrer';(i===0&&(!stage.guide||index===0)?body:more).append(a);}
      more.append(button('Документы и подробности',()=>{dialog.close();openHandbook(stage.material);},'housing-material-link'));body.append(more);
      const mark=button(stage.mark,()=>update(stage.next),'housing-mark');body.append(mark);marks.push(mark);details.append(body);li.append(details);timeline.append(li);steps.push(details);
    }
    dialog.append(button('Понятно',()=>dialog.close(),'housing-done'));
    function paint(){
      const item=statuses[saved?.status],active=item?.[1]??-1;
      badge.textContent=routeStatus.textContent=loaded?(item?.[0]||'Статус не указан'):loading?'Загружаем статус…':'Статус не загружен';
      const i=Math.max(0,Math.min(4,active)),complete=active===5;
      card.paint(i,{title:complete?'Социальный наём оформлен':stages[i].title,text:complete?'Маршрут получения жилья пройден. Документы и ссылки остаются под рукой.':stages[i].text,action:complete?'Открыть документы этапа':i<2?'Как подать заявление':'Что делать на этом этапе',complete});
      save.disabled=select.disabled=edit.disabled=!loaded||saving;
      for(const [i,details] of steps.entries()){
        details.classList.toggle('is-current',i===active);details.classList.toggle('is-complete',i<active);
        details.querySelector('summary').setAttribute('aria-current',i===active?'step':'false');
        marks[i].disabled=!loaded||saving||active>i;
      }
    }
    async function load(){
      loaded=false;loading=true;feedback.textContent='Загружаем статус…';refresh.disabled=true;paint();
      try{const data=await request('/api/housing');if(!root.isConnected)return;saved=data.housing;loaded=true;select.value=saved?.status||'unknown';feedback.textContent='';refresh.hidden=true;const active=statuses[saved?.status]?.[1]??0;for(const [i,d] of steps.entries())d.open=i===active;}
      catch(e){if(!root.isConnected)return;feedback.textContent=e.message;refresh.hidden=false;}
      finally{if(root.isConnected){loading=false;refresh.disabled=false;paint();}}
    }
    async function update(status){
      if(!loaded||saving)return;saving=true;feedback.textContent='Сохраняем…';refresh.hidden=true;paint();
      try{const data=await request('/api/housing',{method:'PUT',body:JSON.stringify({status,expected_version:saved?.version||0})});if(!root.isConnected)return;saved=data.housing;select.value=saved.status;editor.hidden=true;feedback.textContent='Статус сохранён';const active=statuses[saved.status][1];for(const [i,d] of steps.entries())d.open=i===active;}
      catch(e){if(!root.isConnected)return;feedback.textContent=e.message;refresh.hidden=false;}
      finally{if(root.isConnected){saving=false;paint();}}
    }
    editor.onsubmit=e=>{e.preventDefault();update(select.value);};
    load();
  }
  return {render,back(){if(activeDialog?.isConnected&&activeDialog.open){activeDialog.close();return true;}return false;}};
};
