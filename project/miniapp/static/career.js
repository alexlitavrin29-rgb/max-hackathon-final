/* Personal career progress. Opening tools or reading cards never advances it. */
window.createCareerScreen=function({request,search,openResume,openHandbook,materials}){
  let activeDialog=null;
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const button=(text,fn,cls='plan-secondary')=>{const b=node('button',text,cls);b.type='button';b.onclick=fn;return b;};
  const stages=[
    {title:'Поиск работы',text:'Найди подходящие вакансии в своём городе и проверь условия.',icon:'career-briefcase.svg',art:true,action:'Найти работу',next:'resume',mark:'Перейти к резюме'},
    {title:'Составление резюме',text:'Расскажи об учёбе, навыках и опыте. Можно начать без опыта работы.',icon:'career-step-resume.svg',action:'Составить резюме',next:'interview',mark:'Резюме готово, готовлюсь к собеседованию'},
    {title:'Собеседование',text:'Уточни условия и подготовь документы для трудоустройства.',icon:'career-step-interview.svg',action:'Посмотреть документы',next:'employed',mark:'Я устроился на работу'}
  ];
  const statuses={unknown:['Этап не указан',-1],searching:['Ищу работу',0],resume:['Составляю резюме',1],interview:['Готовлюсь к собеседованию',2],employed:['Работа найдена',3]};
  function render(parent){
    let saved=null,loaded=false,loading=true,saving=false;
    const root=node('div',undefined,'career-overview');parent.append(root);
    function open(index){dialog.showModal();const active=Math.max(0,Math.min(2,statuses[saved?.status]?.[1]??0));for(const [i,d] of steps.entries())d.open=i===(index??active);dialog.scrollTop=0;}
    function act(index){if(index===0)search();else if(index===1)openResume();else open(2);}
    const card=window.createStageCard({title:'Мой путь к работе',stages,open,act:()=>act(Math.max(0,Math.min(2,statuses[saved?.status]?.[1]??0)))});root.append(card.root,button('Трудоустройство и права',materials,'quiet career-materials'));
    const dialog=node('dialog',undefined,'housing-route career-route');activeDialog=dialog;dialog.setAttribute('aria-label','Путь к работе');root.append(dialog);
    const handle=node('div',undefined,'housing-sheet-handle');handle.setAttribute('aria-hidden','true');dialog.append(handle);
    const head=node('div',undefined,'housing-route-heading'),close=button('×',()=>dialog.close(),'housing-close');close.setAttribute('aria-label','Закрыть маршрут работы');head.append(node('h2','Путь к работе'),close);dialog.append(head);
    const status=node('p',undefined,'housing-route-status'),edit=button('Изменить этап',()=>{editor.hidden=!editor.hidden;if(!editor.hidden)select.focus();},'quiet');dialog.append(status,edit,node('p','Текущий этап ты отмечаешь сам.','housing-personal-note'));
    const editor=node('form',undefined,'housing-status-editor');editor.hidden=true;
    const label=node('label','Мой этап'),select=node('select');select.id='career-status-select';label.htmlFor=select.id;
    for(const [value,[title]] of Object.entries(statuses)){const option=node('option',title);option.value=value;select.append(option);}
    const save=button('Сохранить этап',()=>{});save.type='submit';editor.append(label,select,save);dialog.append(editor);
    const feedback=node('p',undefined,'housing-feedback');feedback.setAttribute('role','status');const refresh=button('Обновить этап',()=>load(),'quiet');refresh.hidden=true;dialog.append(feedback,refresh);
    const timeline=node('ol',undefined,'housing-timeline career-timeline'),steps=[],marks=[];dialog.append(timeline);
    for(const [index,stage] of stages.entries()){
      const li=node('li'),details=node('details',undefined,'housing-step career-stage'),summary=node('summary'),icon=node('span',undefined,'housing-step-icon'),img=node('img');img.src='/static/figma/'+stage.icon;img.alt='';if(stage.art)img.className='natural-art';icon.append(img);
      const copy=node('span');copy.append(node('strong',stage.title),node('small',stage.text));summary.append(icon,copy);details.append(summary);
      const body=node('div',undefined,'housing-step-body');
      if(index<2)body.append(button(stage.action,()=>{dialog.close();act(index);},'housing-action-link'));
      else appendDocuments(body);
      const mark=button(stage.mark,()=>update(stage.next),'housing-mark');body.append(mark);marks.push(mark);details.append(body);li.append(details);timeline.append(li);steps.push(details);
    }
    dialog.append(button('Понятно',()=>dialog.close(),'housing-done'));
    function appendDocuments(body){
      body.classList.add('career-documents');body.append(node('p','Для оформления на работу. Перед собеседованием уточни у работодателя, что взять с собой.'));
      for(const [title,copy,id] of [['Паспорт','Или другой документ, удостоверяющий личность','doc_passport'],['Подтверждение СНИЛС','Бумажный или электронный документ о регистрации','doc_snils_proof'],['Аттестат или диплом','Если работа требует специальных знаний или подготовки','doc_edu_duplicate']]){
        const b=button('',()=>{dialog.close();openHandbook(id);},'career-document');b.append(node('strong',title),node('span',copy),node('small','Открыть карточку →'));body.append(b);
      }
      const more=node('details',undefined,'housing-step-more career-document-conditions');more.append(node('summary','Что ещё может понадобиться'));
      for(const copy of ['Если уже работал — трудовая книжка и/или сведения о трудовой деятельности. При первой работе они не нужны.','Документы воинского учёта — для военнообязанных и тех, кто подлежит призыву.','Справки и другие документы — только в случаях, предусмотренных законом для конкретной работы.'])more.append(node('p',copy));
      const source=node('a','Полный список и условия — Минтруд','housing-action-link');source.href='https://mintrud.gov.ru/ministry/programms/trudotn/dogovor';source.target='_blank';source.rel='noopener noreferrer';more.append(source);body.append(more,button('Трудовой договор: что проверить',()=>{dialog.close();openHandbook('job_contract');},'housing-material-link career-contract'));
    }
    function paint(){
      const item=statuses[saved?.status],active=item?.[1]??-1,i=Math.max(0,Math.min(2,active)),complete=active===3;
      card.status.textContent=status.textContent=loaded?(item?.[0]||'Этап не указан'):loading?'Загружаем этап…':'Этап не загружен';
      card.paint(i,{title:complete?'Работа найдена':stages[i].title,text:complete?'Маршрут пройден. Документы и памятка о договоре остаются под рукой.':stages[i].text,action:complete?'Открыть документы':stages[i].action,complete});
      save.disabled=select.disabled=edit.disabled=!loaded||saving;
      for(const [i,d] of steps.entries()){d.classList.toggle('is-current',i===active);d.classList.toggle('is-complete',i<active);d.querySelector('summary').setAttribute('aria-current',i===active?'step':'false');marks[i].disabled=!loaded||saving||active>i;}
    }
    async function load(){
      loaded=false;loading=true;feedback.textContent='Загружаем этап…';refresh.disabled=true;paint();
      try{const data=await request('/api/career');if(!root.isConnected)return;saved=data.career;loaded=true;select.value=saved?.status||'unknown';feedback.textContent='';refresh.hidden=true;}
      catch(e){if(!root.isConnected)return;feedback.textContent=e.message;refresh.hidden=false;}
      finally{if(root.isConnected){loading=false;refresh.disabled=false;paint();}}
    }
    async function update(value){
      if(!loaded||saving)return;saving=true;feedback.textContent='Сохраняем…';refresh.hidden=true;paint();
      try{const data=await request('/api/career',{method:'PUT',body:JSON.stringify({status:value,expected_version:saved?.version||0})});if(!root.isConnected)return;saved=data.career;select.value=saved.status;editor.hidden=true;feedback.textContent='Этап сохранён';for(const [i,d] of steps.entries())d.open=i===statuses[saved.status][1];}
      catch(e){if(!root.isConnected)return;feedback.textContent=e.message;refresh.hidden=false;}
      finally{if(root.isConnected){saving=false;paint();}}
    }
    editor.onsubmit=e=>{e.preventDefault();update(select.value);};load();
  }
  return {render,back(){if(activeDialog?.isConnected&&activeDialog.open){activeDialog.close();return true;}return false;}};
};
