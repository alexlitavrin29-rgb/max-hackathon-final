/* Manual planner UI. Durable personal drafts use the authenticated server; pending receipts stay in memory. */
(function() {
  'use strict';
  const C=window.PlannerCore;
  const statuses={not_started:'Не начато',in_progress:'В работе',done:'Готово'};
  const groups={overdue:'Просрочено',today:'Сегодня',upcoming:'Предстоит',undated:'Без даты',completed:'Готово'};
  function el(tag,text,className) {
    const node=document.createElement(tag);if(text!==undefined)node.textContent=text;
    if(className)node.className=className;return node;
  }
  function button(text,action,className='plan-secondary',label) {
    const b=el('button',text,className);b.type='button';b.onclick=action;if(label)b.setAttribute('aria-label',label);return b;
  }
  function field(parent,label,type='text',value='',id) {
    const wrap=el('label',undefined,'plan-field');wrap.append(el('span',label));
    const input=el(type==='text'?'textarea':'input');
    if(type==='text') {input.rows=1;input.addEventListener('input',()=>autosize(input));}
    else input.type=type;
    input.value=value;if(id)input.id=id;
    wrap.append(input);parent.append(wrap);if(type==='text')requestAnimationFrame(()=>autosize(input));return input;
  }
  function autosize(input) {
    input.style.height='auto';input.style.height=Math.max(52,input.scrollHeight)+'px';
  }
  function select(parent,label,options,value='') {
    const wrap=el('label',undefined,'plan-field');wrap.append(el('span',label));
    const input=el('select');input.setAttribute('aria-label',label);
    for(const [key,text] of options) {const o=el('option',text);o.value=key;input.append(o);}
    input.value=value;wrap.append(input);parent.append(wrap);return input;
  }
  function check(parent,label,checked=false) {
    const wrap=el('label',undefined,'plan-check');const input=el('input');input.type='checkbox';input.checked=checked;
    wrap.append(input,el('span',label));parent.append(wrap);return input;
  }
  window.createPlanner=function({getToken,onAuthExpired,onRenew,onNavigateTasks,onNavigatePlan}) {
    const $=id=>document.getElementById(id);
    const client=new C.Client(getToken);
    const section=$('plan'),dialog=$('plan-dialog'),tasksSection=$('tasks');
    let parkedDraft=null,parkingDraft=false;
    let tasksVisible=false,sourceGoal=null,dayFilter='',calendarMonth=new Date(new Date().getFullYear(),new Date().getMonth(),1),openGoals=new Set();
    const active=()=>visible||tasksVisible;
    const statusNode=()=>tasksVisible&&$('tasks-status')?$('tasks-status'):$('plan-status');
    const errorNode=()=>tasksVisible&&$('tasks-error')?$('tasks-error'):$('plan-error');
    let visible=false,goals=[],tasks=[],zones=[],generation=0,filter='',modal=null,activeElement=null,pendingTask=null,pendingReview=false;
    let durableVersion=0,durableWire='',draftTimer=null,draftSaving=false,draftWaiters=[],draftDirty=false,draftConflict=false,restoring=false,suspended=false,suspendedView=null,lastControlView=null,readMaterials=new Set(),readTitles=new Map();
    function currentView(focus=document.activeElement){const inputs=[...dialog.querySelectorAll('input,select,textarea')];return {scroll:dialog.scrollTop,index:inputs.indexOf(focus),start:focus?.selectionStart??null,end:focus?.selectionEnd??null};}
    dialog.addEventListener('focusout',event=>{if(event.target.matches?.('input,select,textarea'))lastControlView=currentView(event.target);});
    function retainedView(){const view=currentView();return view.index>=0?view:lastControlView?{...lastControlView,scroll:dialog.scrollTop}:view;}
    const rawValues=()=>[...dialog.querySelectorAll('input,select,textarea')].map(n=>({value:n.value,checked:n.checked}));
    function restoreValues(values){if(!values)return;[...dialog.querySelectorAll('input,select,textarea')].forEach((n,i)=>{if(values[i]){n.value=values[i].value;n.checked=values[i].checked;n.dispatchEvent(new Event('change'));if(n.tagName==='TEXTAREA')autosize(n);}});}
    const draftWire=state=>JSON.stringify(window.PlannerDraftCodec?.encode(state)||state);
    function changed(){if(restoring||!modal?.serialize||modal.operation||modal.busy)return;if(draftConflict){modal.note.hidden=false;modal.note.textContent='Черновик изменён в другом окне. Автосохранение остановлено; ваши изменения остаются здесь.';return;}draftDirty=true;modal.note.hidden=false;modal.note.textContent='Есть несохранённые изменения черновика.';clearTimeout(draftTimer);draftTimer=setTimeout(saveDraft,400);}
    async function saveDraft(){
      if(draftSaving||draftConflict||!modal?.serialize||modal.operation)return;
      const snapshot=modal.serialize();const contentWire=draftWire({...snapshot,view:null});const wire=draftWire(snapshot);if(wire===durableWire){draftDirty=false;if(durableVersion){modal.note.hidden=false;modal.note.textContent='Черновик сохранён на 24 часа.';}return;}
      if(new TextEncoder().encode(wire).length>18000){modal.note.hidden=false;modal.note.textContent='Черновик слишком большой для восстановления. Сократите текст.';return;}
      draftSaving=true;const state=modal;state.note.hidden=false;state.note.textContent='Сохраняем черновик…';
      try{const result=await client.request('/draft',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_version:durableVersion,state:JSON.parse(wire)})});durableVersion=result.draft?.version??result.version;durableWire=wire;draftDirty=modal===state&&draftWire({...state.serialize(),view:null})!==contentWire;if(modal===state)state.note.textContent=draftDirty?'Есть несохранённые изменения черновика.':'Черновик сохранён на 24 часа.';}
      catch(e){if(e.status===409)draftConflict=true;if(modal===state)state.note.textContent=e.status===409?'Черновик изменён в другом окне. Автосохранение остановлено; ваши изменения остаются здесь.':'Черновик не сохранён. Проверьте связь; изменения остаются в этом окне.';}
      finally{draftSaving=false;for(const resolve of draftWaiters.splice(0))resolve();if(draftDirty&&!draftConflict&&modal?.serialize&&draftWire({...modal.serialize(),view:null})!==contentWire)changed();}
    }
    function resetDraftReceipt(){clearTimeout(draftTimer);durableVersion=0;durableWire='';draftDirty=false;draftConflict=false;}
    function draftControls(){
      if(!modal?.serialize)return;const state=modal;state.discard=button('Удалить черновик',async()=>{
        if(state.operation||state.busy||!window.confirm('Удалить незавершённый черновик?'))return;
        clearTimeout(draftTimer);state.discard.disabled=true;state.busy=true;state.fields.disabled=true;
        try{if(draftSaving)await new Promise(resolve=>draftWaiters.push(resolve));if(durableVersion)await client.request('/draft',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_version:durableVersion})});resetDraftReceipt();state.busy=false;close(true);$('plan-resume').hidden=true;}
        catch(e){state.note.hidden=false;state.note.textContent='Не удалось удалить черновик. '+e.message;state.discard.disabled=false;state.busy=false;state.fields.disabled=false;}
      },'quiet');state.form.append(state.discard);
    }
    const setError=(node,text)=>{node.textContent=text;node.hidden=!text;};
    function failed(error,node=errorNode()) {
      setError(node,error.message);
      if(error.status===401) {
        goals=[];tasks=[];$('plan-list').replaceChildren();$('tasks-list')?.replaceChildren();onAuthExpired();
        if(modal)modal.renew.hidden=false;
      }
    }
    function capture() {
      return JSON.stringify([[...dialog.querySelectorAll('input,select,textarea')].map(n=>[n.value,n.checked]),[...dialog.querySelectorAll('[aria-pressed]')].map(n=>n.getAttribute('aria-pressed'))]);
    }
    async function prepareAuxiliary(){
      if(!modal?.serialize)return true;
      if(parkingDraft)return false;
      parkingDraft=true;const state=modal;
      try{
        clearTimeout(draftTimer);
        if(draftSaving)await new Promise(resolve=>draftWaiters.push(resolve));
        await saveDraft();clearTimeout(draftTimer);
        if(modal!==state||!active()||!modal?.serialize||modal.operation||modal.busy)return false;
        parkedDraft={state:modal,nodes:[...dialog.childNodes],view:retainedView(),activeElement};
        if(dialog.open)dialog.close();modal=null;dialog.replaceChildren();return true;
      }finally{parkingDraft=false;}
    }
    function restoreParked(){
      if(!parkedDraft)return;
      const saved=parkedDraft;parkedDraft=null;modal=saved.state;dialog.replaceChildren(...saved.nodes);activeElement=saved.activeElement;suspendedView=saved.view;
      $('plan-resume').hidden=false;
    }
    function resumeModal(){
      if(!modal)return;if(!dialog.open)dialog.showModal();
      if(suspendedView){const view=suspendedView,n=[...dialog.querySelectorAll('input,select,textarea')][view.index];n?.focus({preventScroll:true});if(n?.setSelectionRange&&typeof view.start==='number')try{n.setSelectionRange(view.start,view.end);}catch{}dialog.scrollTop=view.scroll;suspendedView=null;}
    }
    function close(force=false) {
      if(!modal)return true;
      if(modal.busy)return false;
      const uncertain=Boolean(modal.operation);
      if(!force&&modal.serialize&&!uncertain){saveDraft();dialog.close();$('plan-resume').hidden=false;return true;}
      if(!force && (uncertain || modal.initial!==capture())) {
        const message=uncertain?'Результат запроса пока неизвестен. Закрыть форму? Запрос останется доступен для повторения в этом сеансе.':
          'Есть несохранённые изменения. Отменить их и закрыть форму?';
        if(!window.confirm(message))return false;
      }
      dialog.close();
      if(force&&modal?.serialize&&!modal.operation)clearTimeout(draftTimer);
      if(!uncertain || force) {modal=null;dialog.replaceChildren();}
      $('plan-resume').hidden=!modal?.operation;
      activeElement?.focus();if(!modal)restoreParked();return true;
    }
    dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
    function showModal(title,submitLabel,build,submit) {
      if(modal?.operation) {if(!dialog.open)dialog.showModal();return false;}
      if(!close(Boolean(modal?.serialize)))return;
      activeElement=document.activeElement;lastControlView=null;dialog.replaceChildren();
      const header=el('div',undefined,'plan-dialog-heading');const h=el('h2',title);h.id='plan-dialog-title';
      const cancel=button('×',()=>close(),'plan-close','Закрыть форму');header.append(h,cancel);
      const form=el('form');const fields=el('fieldset');fields.className='plan-fields';
      const error=el('p',undefined,'plan-error');error.setAttribute('role','alert');error.hidden=true;
      const result=build(fields);
      const submitButton=el('button',submitLabel,'plan-primary');submitButton.type='submit';submitButton.hidden=!submit;
      const retry=button('Повторить тот же запрос',()=>execute(),'plan-primary');retry.hidden=true;
      const renew=button('Начать новый сеанс',async()=>{
        renew.disabled=true;retry.disabled=true;
        try {await onRenew();renew.hidden=true;setError(error,'Новый сеанс открыт. Можно повторить тот же запрос.');}
        catch(e){setError(error,e.message);}finally{renew.disabled=false;retry.disabled=false;}
      });renew.hidden=true;
      const note=el('p',undefined,'plan-footnote');note.hidden=true;
      form.append(fields,error,submitButton,retry,renew,note);
      dialog.append(header,form);
      modal={form,fields,error,submitButton,retry,renew,note,cancel,result,submit,busy:false,operation:null,initial:capture()};
      form.addEventListener('input',changed);form.addEventListener('change',changed);
      form.onsubmit=event=>{
        event.preventDefault();if(!submit || modal.busy || modal.operation)return;
        try {const operation=submit(result);if(!operation){close(true);return;}modal.operation=operation;execute();}
        catch(e){failed(e,error);}
      };
      dialog.showModal();
      fields.querySelectorAll('textarea').forEach(autosize);
      (fields.querySelector('textarea,input:not([type=checkbox]),select') || cancel).focus();return true;
    }
    async function execute() {
      const state=modal;if(!state || state.busy || !state.operation)return;
      state.busy=true;state.fields.disabled=true;state.submitButton.disabled=true;state.cancel.disabled=true;state.retry.disabled=true;
      setError(state.error,'');state.note.hidden=false;state.note.textContent='Сохраняем…';
      try {
        clearTimeout(draftTimer);if(state.discard)state.discard.disabled=true;
        if(draftSaving)await new Promise(resolve=>draftWaiters.push(resolve));
        await client.mutate(state.operation);
        if(modal!==state)return;
        const aggregate=state.operation.path==='/commit'||state.operation.path.endsWith('/edit-plan');state.operation=null;state.busy=false;if(aggregate)resetDraftReceipt();close(true);
        statusNode().textContent='Сохранено на сервере.';
        if(active())await load('Сохранено на сервере.');
      } catch(e) {
        if(modal!==state)return;
        if(!e.retryable)state.operation=null;
        state.fields.disabled=Boolean(state.operation);state.submitButton.hidden=Boolean(state.operation);
        state.retry.hidden=!state.operation;state.note.hidden=!state.operation;
        state.note.textContent='Не меняйте запрос до подтверждения результата. Повтор использует прежний ключ и не создаст копию.';
        failed(e,state.error);
      } finally {
        if(modal===state) {state.busy=false;state.submitButton.disabled=false;state.cancel.disabled=false;state.retry.disabled=false;if(state.discard)state.discard.disabled=Boolean(state.operation);}
      }
    }
    function deviceZone() {
      const zone=Intl.DateTimeFormat().resolvedOptions().timeZone;
      if(!zone)throw new Error('Не удалось определить время устройства. Проверьте настройки даты и времени и откройте форму заново.');
      return zone;
    }
    const dayKey=date=>`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
    const taskDay=task=>task.due_date||(task.due_at?dayKey(new Date(task.due_at)):'');
    function timeFields(parent,initial={},suffix='',notification=false) {
      let phoneZone;try {phoneZone=deviceZone();}catch {phoneZone=null;}
      const block=el('div',undefined,'plan-time');parent.append(block);
      const dated=check(block,suffix?'Указать срок дела '+suffix:'Указать срок',Boolean(initial.due_at||initial.due_date));
      const body=el('div');block.append(body);
      const original=initial.due_at&&phoneZone?C.localTime(initial.due_at,phoneZone):'';
      const quick=el('div',undefined,'schedule-shortcuts');body.append(quick);
      const date=field(body,suffix?'Дата дела '+suffix:'Дата','date',initial.due_date||original.slice(0,10));
      const exact=check(body,suffix?'Установить время дела '+suffix:'Установить время',Boolean(initial.due_at));
      const timeBody=el('div');body.append(timeBody);
      const time=field(timeBody,suffix?'Дата и время дела '+suffix:'Дата и время','datetime-local',original);
      const reminder=select(timeBody,suffix?'Напоминание дела '+suffix:'Напоминание',[['off','Выключено'],['0','В момент срока'],['60','За час']],initial.remind_before_minutes===null || initial.remind_before_minutes===undefined?'off':String(initial.remind_before_minutes));
      body.append(el('p',notification?'Меняется только следующее напоминание. Срок дела останется прежним.':'Без времени дело сохраняется на весь день. Для напоминания установите точное время. Время — как на вашем телефоне.','plan-footnote'));
      function syncDate(){if(date.value)time.value=date.value+'T'+(time.value.slice(11)||'09:00');}
      function update(){body.hidden=!dated.checked;date.required=dated.checked;timeBody.hidden=!exact.checked;time.required=dated.checked&&exact.checked;if(exact.checked&&!time.value)syncDate();}
      dated.onchange=update;exact.onchange=update;date.onchange=syncDate;time.onchange=()=>{if(time.value)date.value=time.value.slice(0,10);};
      for(const [label,offset] of [['Сегодня',0],['Завтра',1],['Через неделю',7]])quick.append(button(label,()=>{const chosen=new Date();chosen.setDate(chosen.getDate()+offset);date.value=dayKey(chosen);syncDate();update();changed();},'quiet'));
      quick.append(button('Выбрать дату',()=>{date.focus();try{date.showPicker?.();}catch{}},'quiet'));
      if(notification){dated.checked=true;exact.checked=true;dated.parentElement.hidden=true;exact.parentElement.hidden=true;date.parentElement.hidden=true;quick.hidden=true;reminder.parentElement.hidden=true;}
      update();
      return {relabel(number) {
        date.parentElement.firstChild.textContent='Дата дела '+number;time.parentElement.firstChild.textContent='Дата и время дела '+number;
        reminder.parentElement.firstChild.textContent='Напоминание дела '+number;reminder.setAttribute('aria-label','Напоминание дела '+number);
        dated.parentElement.lastChild.textContent='Указать срок дела '+number;exact.parentElement.lastChild.textContent='Установить время дела '+number;
      },get() {
        const empty={due_at:null,due_date:null,timezone:null,timezone_confirmed:false,remind_before_minutes:null};
        if(!dated.checked)return empty;
        if(!exact.checked){const value=date.value;if(!/^\d{4}-\d{2}-\d{2}$/.test(value)||new Date(value+'T12:00:00Z').toISOString().slice(0,10)!==value)throw new Error('Выберите дату.');return {...empty,due_date:value};}
        if(!phoneZone)throw new Error('Не удалось определить время устройства. Проверьте настройки даты и времени и откройте форму заново.');
        if(deviceZone()!==phoneZone)throw new Error('Настройки времени устройства изменились. Закройте форму и откройте её заново.');
        const unchanged=Boolean(initial.due_at)&&time.value===original;
        const choices=C.wallCandidates(time.value,phoneZone);
        if(!choices.length)throw new Error('Такого местного времени нет из-за перевода часов. Выберите другое время.');
        return {due_at:unchanged?initial.due_at:C.zonedTime(choices[0],phoneZone),due_date:null,timezone:unchanged?initial.timezone:phoneZone,
          timezone_confirmed:true,remind_before_minutes:reminder.value==='off'?null:Number(reminder.value)};
      }};
    }
    function goalOptions() {return [['','Без цели'],...goals.map(g=>[g.id,g.title])];}
    async function taskForm(task=null) {
      if(!await prepareAuxiliary())return;
      showModal(task?'Изменить дело':'Новое дело','Сохранить',fields=>{
        const name=field(fields,'Название дела','text',task?.title||'');name.required=true;
        const goal=select(fields,'Цель дела',goalOptions(),task?.goal_id||'');if(task?.required_resource){name.readOnly=true;goal.disabled=true;fields.append(el('p','Обязательный ресурс плана: название и цель закреплены.','plan-footnote'));}
        const status=select(fields,'Статус дела',Object.entries(statuses),task?.status||'not_started');
        const time=timeFields(fields,task||{});
        if(tasksVisible&&sourceGoal)fields.append(button('Вернуться к плану',()=>{if(close())navigatePlan(sourceGoal);},'plan-return-link'));
        return ()=>({title:C.title(name.value,240),goal_id:goal.value||null,status:status.value,...time.get()});
      },get=>{
        const after=get();
        if(!task)return client.operation('POST','/tasks',{id:crypto.randomUUID(),...after});
        const changes=C.taskChanges(task,after);if((task.due_date||null)!==after.due_date)changes.due_date=after.due_date;
        return Object.keys(changes).length?client.operation('PATCH','/tasks/'+task.id,{expected_version:task.version,...changes}):null;
      });
    }
    async function moveForm(task) {
      if(!await prepareAuxiliary())return;
      showModal('Перенести дело','Сохранить',fields=>{
        fields.append(el('p',task.title,'plan-dialog-intro'));
        const options=[['due','Срок дела']];
        if(task.due_at && task.remind_before_minutes!==null && task.status!=='done')options.push(['notification','Только следующее напоминание']);
        if(task.notification_override_at)options.push(['reset','Вернуть напоминание к сроку дела']);
        const mode=select(fields,'Что перенести',options);
        const holder=el('div');fields.append(holder);let time;
        function update() {
          holder.replaceChildren();
          if(mode.value==='reset') {holder.append(el('p','Напоминание вернётся к сроку дела с выбранным ранее интервалом.'));return;}
          const notification=mode.value==='notification';
          time=timeFields(holder,notification?{...task,due_at:task.notification_override_at||task.due_at,timezone_confirmed:false}:task,'',notification);
        }
        mode.onchange=update;update();return ()=>{
          if(mode.value==='reset')return {notification_override_at:null};
          const value=time.get();return mode.value==='notification'?{notification_override_at:value.due_at}:value;
        };
      },get=>client.operation('PATCH','/tasks/'+task.id,{expected_version:task.version,...get()}));
    }
    function routeForm(task) {
      const material=(task.material_ids||[]).find(id=>!['app_work','app_rental','app_help'].includes(id));
      const resource={app_work:'work',app_rental:'rental',app_help:'help_points'}[task.required_resource];
      window.HandbookNavigation?.open(material||resource||'');
    }
    async function remove(kind,item) {
      if(!await prepareAuxiliary())return;
      if(item.required_resource){setError(errorNode(),'Обязательное дело нельзя удалить.');return;}
      showModal(kind==='goals'?'Удалить цель':'Удалить дело','Удалить',fields=>{
        fields.append(el('p',item.title,'plan-dialog-intro'),el('p',kind==='goals'?'Дела непустой цели нужно сначала перенести или удалить отдельно.':'Дело и его запланированные напоминания будут удалены.'));
      },()=>{
        if(!window.confirm('Удалить? Это действие нельзя отменить.'))throw new Error('Удаление отменено.');
        return client.operation('DELETE','/'+kind+'/'+item.id,{expected_version:item.version});
      });
    }
    function editGoal(goal) {
      showModal('Изменить цель','Сохранить',fields=>{
        const name=field(fields,'Название цели','text',goal.title);name.required=true;return ()=>C.title(name.value,160);
      },get=>{const title=get();return title===goal.title?null:client.operation('PATCH','/goals/'+goal.id,{expected_version:goal.version,title});});
    }
    function draft(example=false,proposal=null,existing=null,restored=null) {
      const original=existing?.tasks||[];const removed=restored?.removed||[];let entries=[],name,goalID=restored?.goalID||existing?.goal.id||proposal?.goal_id||crypto.randomUUID();
      const opened=showModal(existing?'Редактировать план':proposal&&!proposal.manual?'Предложенный план':example?'Пример плана':'Новая цель',existing?'Сохранить план':'Подтвердить план',fields=>{
        fields.append(el('p','Проверьте цель и дела. До сохранения изменения остаются в черновике. Даты и напоминания назначаете вы.','plan-dialog-intro'));
        if(proposal?.information)appendInformation(fields,proposal.information,true);
        name=field(fields,'Название цели','text',existing?.goal.title||proposal?.title||(example?'Подготовить документы':''));name.required=true;
        const rows=el('div',undefined,'plan-draft-rows');fields.append(rows);const count=el('p',undefined,'plan-footnote');fields.append(count);
        function renumber(){entries.forEach((entry,index)=>{entry.name.parentElement.firstChild.textContent='Название дела '+(index+1);entry.time.relabel(index+1);entry.remove.setAttribute('aria-label','Убрать дело '+(index+1));});count.textContent=`${entries.length} из 50 дел.`;}
        function add(task={}){
          if(entries.length>=50){setError(modal.error,'В одном плане может быть не больше 50 дел.');return;}
          const row=el('div',undefined,'plan-draft-row'),entry={id:task.id||crypto.randomUUID(),row,task,materialIDs:task.material_ids||[]};
          entry.name=field(row,'Название дела '+(entries.length+1),'text',task.title||'');entry.name.required=true;entry.name.readOnly=Boolean(task.required_resource);
          const resourceTitles={app_work:'Посмотреть вакансии',app_rental:'Посмотреть варианты жилья',app_help:'Найти места помощи'};
          const resourceSections={app_work:'work',app_rental:'rental',app_help:'help'};
          appendLinks(row,task.links?.length?task.links:entry.materialIDs.map(id=>({title:resourceTitles[id]||task.title||'Открыть материал',url:resourceSections[id]?'/?section='+resourceSections[id]:'/?handbook='+encodeURIComponent(id)})));
          entry.status=select(row,'Статус дела '+(entries.length+1),Object.entries(statuses),task.status||'not_started');
          entry.time=timeFields(row,task,String(entries.length+1));
          entry.remove=button('Убрать дело',()=>{if(task.version)removed.push({id:entry.id,expected_version:task.version});row.remove();entries.splice(entries.indexOf(entry),1);renumber();changed();});entry.remove.disabled=Boolean(task.required_resource);
          if(task.required_resource)row.append(el('p','Обязательное дело — название закреплено.','plan-footnote'));
          row.append(entry.remove);entries.push(entry);rows.append(row);renumber();if(modal)autosize(entry.name);
        }
        fields.append(button('Добавить строку дела',()=>{add();changed();}),button('Добавить из справочника',()=>window.HandbookNavigation?.open()));
        if(restored?.entries)for(const task of restored.entries)add(task);
        else if(existing)for(const task of original)add(task);
        else if(proposal)for(const task of proposal.tasks||[])add(task);
        else if(example)for(const title of ['Уточнить список документов','Собрать нужные документы','Проверить комплект'])add({title});
        renumber();
        return {add,get:()=>({title:C.title(name.value,160),tasks:entries.map(e=>({id:e.id,...(e.task.version?{expected_version:e.task.version}:{}),title:C.title(e.name.value,240),status:e.status.value,...(e.materialIDs.length?{material_ids:e.materialIDs}:{}),...e.time.get()}))})};
      },result=>{const value=result.get();return existing?client.operation('POST','/goals/'+existing.goal.id+'/edit-plan',{expected_version:existing.goal.version,...value,removed_tasks:removed,confirmed:true}):client.operation('POST','/commit',{goal:{id:goalID,title:value.title},tasks:value.tasks,...(proposal?.context?{context:proposal.context}:{}),confirmed:true});});
      if(!opened||!modal)return;
      modal.serialize=()=>({kind:'editor',example,proposal,existing,goalID,removed,view:suspendedView||retainedView(),readMaterials:[...readMaterials],readTitles:[...readTitles],entries:entries.map(e=>({...e.task,id:e.id,title:e.name.value,material_ids:e.materialIDs})),values:rawValues()});
      modal.attachedIDs=()=>entries.flatMap(e=>e.materialIDs);
      modal.addMaterial=c=>{if(entries.some(e=>e.materialIDs.includes(c.id)))return;modal.result.add({title:'Изучить «'+c.title+'»',material_ids:[c.id],links:[{title:c.title,url:'/?handbook='+encodeURIComponent(c.id)}]});changed();};
      if(restored?.values)restoreValues(restored.values);draftControls();changed();
    }
    async function editPlan(goal,material=null){
      const rows=await client.all('/tasks','tasks');const own=rows.filter(t=>t.goal_id===goal.id).sort(planOrder);draft(false,null,{goal,tasks:own});if(material)modal?.addMaterial(material);
    }
    function appendLinks(parent,links=[]) {
      const list=el('div',undefined,'plan-source-links');
      for(const source of links || []) {
        let url;try {url=new URL(source.url,location.origin);}catch {continue;}
        if(!['https:','http:'].includes(url.protocol))continue;
        const link=el('a',source.title || 'Открыть источник','plan-route-link');
        link.href=url.href;if(!url.searchParams.has('handbook')&&!url.searchParams.has('section')){link.target='_blank';link.rel='noopener noreferrer';}list.append(link);if(source.description)list.append(el('small',source.description,'plan-source-description'));
      }
      if(list.childElementCount)parent.append(list);
    }
    function appendRichText(parent,text,links=[]){if(window.MaterialLinkText)return window.MaterialLinkText.append(parent,text,links);const pattern=/\[([^\]\n]+)\]\(([^\s)]+)\)/g;let start=0;for(const m of String(text).matchAll(pattern)){parent.append(document.createTextNode(text.slice(start,m.index)));const a=el('a',m[1],'plan-route-link');try{const u=new URL(m[2],location.origin);if(['http:','https:'].includes(u.protocol)){a.href=u.href;if(!u.searchParams.has('handbook')&&!u.searchParams.has('section')){a.target='_blank';a.rel='noopener noreferrer';}parent.append(a);}else parent.append(document.createTextNode(m[1]));}catch{parent.append(document.createTextNode(m[1]));}start=m.index+m[0].length;}parent.append(document.createTextNode(text.slice(start)));}
    function appendInformation(parent,information,collapsed=false) {
      const urgent=information.priority==='urgent';
      const box=el(collapsed?'details':'section',undefined,'plan-information'+(urgent?' plan-information-urgent':''));
      box.append(el(collapsed?'summary':'h3',urgent?'Сначала безопасность':'Информация по вашему вопросу'));
      if(!urgent&&information.points?.length){
        if(information.intro){const intro=el('p',undefined,'plan-information-text');appendRichText(intro,information.intro,information.sources);box.append(intro);}
        const points=el('ol',undefined,'plan-answer-points');
        for(const point of information.points){const row=el('li');const text=el('p');appendRichText(text,point.text,point.sources);row.append(text);appendLinks(row,(point.sources||[]).slice(0,2));points.append(row);}box.append(points);
      }else if(information.text){const text=el('p',undefined,'plan-information-text');appendRichText(text,information.text,information.sources);box.append(text);}
      if(!urgent&&information.details?.length)appendLinks(box,(information.sources||[]).filter(s=>/^app_(work|rental|help)$/.test(s.id)));
      if(information.note)box.append(el('p',information.note,'plan-footnote'));
      if(information.details?.length&&!urgent){
        const more=el('details',undefined,'plan-answer-details');more.append(el('summary','Условия, подробности и все источники'));
        for(const item of information.details){const article=el('article');article.append(el('h4',item.title));const text=el('p',undefined,'plan-information-text');appendRichText(text,item.text,item.sources);article.append(text);appendLinks(article,item.sources);more.append(article);}box.append(more);
      }else appendLinks(box,information.sources);
      parent.append(box);
    }
    function sentencePreview(text,target=240){
      const links=[];const protectedText=String(text).replace(/\[[^\]\n]+\]\([^\s)]+\)/g,link=>{links.push(link);return 'LINKTOKEN'+(links.length-1)+'ENDTOKEN';});
      const restore=value=>value.replace(/LINKTOKEN(\d+)ENDTOKEN/g,(_,index)=>links[Number(index)]);
      const parts=typeof Intl.Segmenter==='function'?[...new Intl.Segmenter('ru',{granularity:'sentence'}).segment(protectedText)].map(part=>part.segment):[protectedText.split(/\n\s*\n/)[0]];
      let result='';for(const part of parts){if(result&&restore(result+part).trim().length>target)break;result+=part;if(restore(result).trim().length>=target)break;}
      return restore(result).trim();
    }
    function appendSuggestions(parent,proposal,selection=[],onSelection=()=>{},showInformation=true) {
      if(showInformation&&proposal.information)appendInformation(parent,proposal.information);
      const selected=new Set(selection);(proposal.tasks||[]).forEach((task,index)=>{if(task.required_resource)selected.add(index);});
      const count=el('p',undefined,'plan-footnote');count.setAttribute('role','status');
      const next=button('Продолжить с выбранными',()=>{
        const chosen={...proposal,tasks:proposal.tasks.filter((_,index)=>selected.has(index))};
        close(true);draft(false,chosen);
      },'plan-primary');
      const update=()=>{onSelection([...selected]);count.textContent='Выбрано: '+selected.size;next.disabled=!selected.size;};
      const cards=el('div',undefined,'plan-suggestions');
      (proposal.tasks || []).forEach((task,index)=>{
        const card=el('article',undefined,'plan-suggestion');
        const heading=el('div',undefined,'plan-suggestion-heading');
        heading.append(el('span',String(index+1),'plan-suggestion-number'),el('h3',task.title,'plan-clamp'));
        card.append(heading);
        if(task.kind==='information')card.append(el('p','Информация и источники','plan-meta'));
        if(task.description){const preview=el('p',undefined,'plan-suggestion-description');appendRichText(preview,sentencePreview(task.description));card.append(preview);}
        const details=el('details',undefined,'plan-suggestion-details');details.append(el('summary','Подробнее'));
        details.append(el('p',task.title));
        if(task.description){const full=el('p');appendRichText(full,task.description);details.append(full);}
        card.append(details);appendLinks(card,task.links);
        if((task.material_ids||[]).some(id=>readMaterials.has(id)))card.append(el('p','Вы уже открывали этот материал','plan-footnote'));
        const choose=button('Добавить в мой план',()=>{
          if(selected.has(index))selected.delete(index);else selected.add(index);
          const active=selected.has(index);choose.textContent=active?'Убрать из выбранных':'Добавить в мой план';
          choose.setAttribute('aria-pressed',String(active));card.classList.toggle('is-selected',active);update();
        });const active=selected.has(index);choose.setAttribute('aria-pressed',String(active));choose.textContent=active?'Убрать из выбранных':'Добавить в мой план';card.classList.toggle('is-selected',active);if(task.required_resource)card.append(el('p','Обязательное дело плана','plan-footnote'));else card.append(choose);cards.append(card);
      });
      const footer=el('div',undefined,'plan-suggestion-footer');footer.append(count,next);
      parent.append(cards,footer);update();
    }
    function aiForm(operation='draft-plan',initial='',restored=null) {
      const draftState=restored||{kind:'consultation',original:initial,operation,phase:'question',questionID:null,clarification:'',information:null,proposal:null,selection:[],pendingMaterials:[]};
      const opened=showModal(operation==='find-help-route'?'Найти ответ':'Помочь составить план','',fields=>{
        fields.append(el('p','Опишите вопрос без личных данных. Сначала покажем справку, затем вы сможете получить предложение плана.','plan-dialog-intro'));
        const name=field(fields,'Что хотите сделать?','text',draftState.original);name.required=true;
        const question=el('div'),status=el('p',undefined,'plan-footnote'),results=el('div');status.setAttribute('role','status');fields.append(question,status,results);let answer;
        function paint(){question.replaceChildren();results.replaceChildren();name.disabled=draftState.phase!=='question';
          if(draftState.phase==='clarify'){answer=field(question,draftState.question||'Уточните вопрос','text',draftState.clarification);answer.oninput=()=>{draftState.clarification=answer.value;changed();};}
          if(draftState.information)appendInformation(results,draftState.information);
          if(draftState.phase==='proposal'&&draftState.proposal)appendSuggestions(results,draftState.proposal,draftState.selection,indices=>{draftState.selection=indices;changed();},false);
          ask.hidden=draftState.phase==='proposal'||draftState.information?.priority==='urgent';manual.hidden=draftState.information?.priority==='urgent';ask.textContent=draftState.phase==='info'?'Получить предложение плана':draftState.phase==='clarify'?'Продолжить':'Получить справку';
        }
        const ask=button('Получить справку',async()=>{
          const state=modal;try{
            if(draftState.phase==='question')draftState.original=C.title(name.value,160);
            if(answer)draftState.clarification=C.title(answer.value,500);
            const proposing=draftState.phase==='info'||(draftState.phase==='clarify'&&draftState.clarifyingProposal);
            const body={text:draftState.original};if(draftState.questionID){body.question_id=draftState.questionID;body.answer=draftState.clarification;}
            ask.disabled=true;status.textContent=proposing?'Составляем предложение…':'Ищем справку…';setError(state.error,'');
            const result=await client.request('/ai/'+(proposing?'draft-plan':'find-help-route'),{method:'POST',body:JSON.stringify(body),headers:{'Content-Type':'application/json'}});
            if(modal!==state)return;
            if(result.status==='clarify'&&!draftState.questionID){draftState.questionID=result.question_id;draftState.question=result.question;draftState.clarifyingProposal=proposing;draftState.phase='clarify';}
            else if(result.status==='draft'&&proposing){draftState.proposal=result;for(const material of draftState.pendingMaterials||[]){let index=result.tasks.findIndex(t=>(t.material_ids||[]).includes(material.id));if(index<0){result.tasks.push({title:'Изучить «'+material.title+'»',material_ids:[material.id],links:[{title:material.title,url:'/?handbook='+encodeURIComponent(material.id)}]});index=result.tasks.length-1;}draftState.selection.push(index);}draftState.selection=[...new Set(draftState.selection)];draftState.phase='proposal';}
            else{draftState.information=result.information||{text:result.message||'По этому вопросу пока нет справки.',sources:result.route?.url?[{title:result.route.title,url:result.route.url}]:[]};draftState.phase='info';}
            status.textContent='';paint();changed();
          }catch(e){if(modal===state)failed(e,state.error);}finally{if(modal===state)ask.disabled=false;}
        },'plan-primary');
        const manual=button('Составить вручную',()=>{const title=name.value.trim();close(true);draft(false,{title,tasks:[],manual:true});});fields.append(ask,button('Открыть справочник',()=>window.HandbookNavigation?.open()),manual);
        name.oninput=()=>{draftState.original=name.value;changed();};paint();return draftState;
      },null);
      if(opened&&modal){modal.serialize=()=>({...draftState,view:suspendedView||retainedView(),readMaterials:[...readMaterials],readTitles:[...readTitles]});modal.repaint=()=>{const values=modal.serialize();aiForm(values.operation,values.original,values);};draftControls();changed();}
    }
    function planOrder(a,b){return (a.plan_order??Number.MAX_SAFE_INTEGER)-(b.plan_order??Number.MAX_SAFE_INTEGER)||String(a.created_at||'').localeCompare(String(b.created_at||''))||a.id.localeCompare(b.id);}
    function navigatePlan(id){openGoals.add(id);if(onNavigatePlan)onNavigatePlan(id);else {tasksVisible=false;tasksSection.hidden=true;visible=true;section.hidden=false;render();}}
    function navigateTask(task,goal){if(onNavigateTasks)onNavigateTasks(task.id,goal?.id);else {tasksVisible=true;sourceGoal=goal?.id||null;visible=false;section.hidden=true;tasksSection.hidden=false;render();taskForm(task);}}
    function dueLabel(task){if(task.due_date)return new Intl.DateTimeFormat('ru-RU',{dateStyle:'medium'}).format(new Date(task.due_date+'T12:00:00'));return task.due_at?new Intl.DateTimeFormat('ru-RU',{dateStyle:'medium',timeStyle:'short'}).format(new Date(task.due_at)):'';}
    async function complete(task){if(!await prepareAuxiliary())return;showModal(task.status==='done'?'Вернуть дело в работу':'Отметить дело выполненным','Подтвердить',fields=>fields.append(el('p',task.title)),()=>client.operation('PATCH','/tasks/'+task.id,{expected_version:task.version,status:task.status==='done'?'not_started':'done'}));}
    function render(){
      const list=$('plan-list');list.replaceChildren();
      if(!goals.length)list.append(el('p',tasks.length?'Ваши отдельные дела находятся во вкладке «Задачи».':'Пока нет целей и дел.','plan-empty'));
      for(const goal of goals){
        const own=tasks.filter(t=>t.goal_id===goal.id).sort(planOrder),done=own.filter(t=>t.status==='done').length;
        const card=el('details',undefined,'plan-goal');card.dataset.goalId=goal.id;card.open=openGoals.has(goal.id);
        const heading=el('summary',goal.title);card.append(heading);card.ontoggle=()=>{if(card.open)openGoals.add(goal.id);else openGoals.delete(goal.id);};
        const progress=el('progress');progress.max=Math.max(own.length,1);progress.value=done;progress.setAttribute('aria-label','Выполненные дела цели «'+goal.title+'»');
        card.append(el('p',`${done} из ${own.length} дел выполнено`,'plan-meta'),progress);
        const steps=el('ol',undefined,'plan-steps');
        for(const task of own){const row=el('li',undefined,'plan-step');row.dataset.taskId=task.id;row.append(button(task.title,()=>navigateTask(task,goal),'plan-task-open'),el('span',statuses[task.status],'plan-meta'));if(dueLabel(task))row.append(el('span',dueLabel(task),'plan-meta'));appendLinks(row,task.links);row.append(button(task.status==='done'?'Вернуть в работу':'Готово',()=>complete(task),'quiet',(task.status==='done'?'Вернуть в работу':'Выполнить')+' «'+task.title+'»'));steps.append(row);}
        card.append(steps);if(goal.context?.information)appendInformation(card,goal.context.information,true);
        const actions=el('div',undefined,'plan-actions');actions.append(button('Редактировать план',()=>editPlan(goal),'quiet',`Редактировать план «${goal.title}»`),button('Добавить из справочника',()=>{editPlan(goal).then(()=>window.HandbookNavigation?.open());},'quiet'),button('Удалить',()=>remove('goals',goal),'quiet',`Удалить цель «${goal.title}»`));card.append(actions);list.append(card);
      }
      renderTasks();
    }
    function calendar(parent){
      const box=el('details',undefined,'tasks-calendar');box.open=Boolean(dayFilter);box.append(el('summary',dayFilter?'Календарь · '+dayFilter:'Календарь'));
      const header=el('div',undefined,'tasks-calendar-heading');const month=new Intl.DateTimeFormat('ru-RU',{month:'long',year:'numeric'}).format(calendarMonth);
      const move=offset=>{calendarMonth=new Date(calendarMonth.getFullYear(),calendarMonth.getMonth()+offset,1);renderTasks(true);$('tasks-list').querySelector('[aria-label="'+(offset<0?'Предыдущий месяц':'Следующий месяц')+'"]')?.focus({preventScroll:true});};
      header.append(button('‹',()=>move(-1),'quiet','Предыдущий месяц'),el('strong',month),button('›',()=>move(1),'quiet','Следующий месяц'));box.append(header);
      const grid=el('div',undefined,'tasks-calendar-grid');grid.setAttribute('role','group');grid.setAttribute('aria-label',month);
      for(const day of ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'])grid.append(el('span',day,'tasks-weekday'));
      const offset=(calendarMonth.getDay()+6)%7;for(let n=0;n<offset;n++)grid.append(el('span',''));
      const counts=new Map();for(const task of tasks){if(task.status==='done'||(filter&&(filter==='standalone'?task.goal_id:task.goal_id!==filter)))continue;const day=taskDay(task);counts.set(day,(counts.get(day)||0)+1);}
      const days=new Date(calendarMonth.getFullYear(),calendarMonth.getMonth()+1,0).getDate();
      for(let n=1;n<=days;n++){const day=dayKey(new Date(calendarMonth.getFullYear(),calendarMonth.getMonth(),n)),count=counts.get(day)||0;
        const b=button(String(n)+(count?' •':''),()=>{dayFilter=day===dayFilter?'':day;renderTasks(true);$('tasks-list').querySelector('[aria-label^="'+day+'"]')?.focus({preventScroll:true});},'tasks-calendar-day',day+(count?' · дел: '+count:''));b.setAttribute('aria-pressed',String(day===dayFilter));if(day===dayKey(new Date()))b.setAttribute('aria-current','date');grid.append(b);}
      box.append(grid,button('Все даты',()=>{dayFilter='';renderTasks(true);},'quiet'));parent.append(box);
    }
    function renderTasks(calendarOpen=false){
      const list=$('tasks-list');if(!list)return;list.replaceChildren();
      if(sourceGoal){const goal=goals.find(g=>g.id===sourceGoal);if(goal)list.append(button('← Вернуться к плану «'+goal.title+'»',()=>navigatePlan(sourceGoal),'plan-return-link'));}
      const toolbar=el('div',undefined,'plan-actions');toolbar.append(button('Добавить дело',()=>taskForm(),'plan-primary'),button('Обновить',()=>load(),'quiet'));list.append(toolbar);
      const picker=select(list,'Показать дела',[['','Все планы и отдельные дела'],['standalone','Без плана'],...goals.map(g=>[g.id,g.title])],filter);picker.onchange=()=>{filter=picker.value;renderTasks();};
      calendar(list);if(calendarOpen)list.querySelector('.tasks-calendar').open=true;
      const today=dayKey(new Date()),now=Date.now();
      const category=task=>task.status==='done'?'completed':!taskDay(task)?'undated':task.due_date?task.due_date<today?'overdue':task.due_date===today?'today':'upcoming':Date.parse(task.due_at)<now?'overdue':taskDay(task)===today?'today':'upcoming';
      for(const [key,label] of Object.entries(groups)){
        const rows=tasks.filter(t=>(!filter||(filter==='standalone'?!t.goal_id:t.goal_id===filter))&&(!dayFilter||taskDay(t)===dayFilter)&&category(t)===key);
        if(!rows.length)continue;const group=el(key==='completed'?'details':'section',undefined,'plan-group');group.append(el(key==='completed'?'summary':'h2',label+' · '+rows.length));
        rows.sort((a,b)=>taskDay(a).localeCompare(taskDay(b))||(Date.parse(a.due_at)||0)-(Date.parse(b.due_at)||0));
        for(const task of rows){const card=el('article',undefined,'plan-task');card.dataset.taskId=task.id;card.append(button(task.title,()=>taskForm(task),'plan-task-open'),el('p',statuses[task.status],'plan-task-status'));appendLinks(card,task.links);
          const goal=goals.find(g=>g.id===task.goal_id);if(goal)card.append(el('p',goal.title,'plan-meta'));if(dueLabel(task))card.append(el('p',dueLabel(task),'plan-meta'));
          if(task.due_at&&task.remind_before_minutes!==null)card.append(el('p',task.notification_override_at?'Следующее напоминание: '+C.localTime(task.notification_override_at,deviceZone()).replace('T',' '):task.remind_before_minutes===60?'Напоминание за час':'Напоминание в момент срока','plan-meta'));
          const actions=el('div',undefined,'plan-actions');actions.append(button('Изменить',()=>taskForm(task),'quiet',`Изменить дело «${task.title}»`),button(task.status==='done'?'Вернуть в работу':'Готово',()=>complete(task),'quiet'),button('Перенести',()=>moveForm(task),'quiet',`Перенести дело «${task.title}»`));
          if(!task.required_resource)actions.append(button('Удалить',()=>remove('tasks',task),'quiet',`Удалить дело «${task.title}»`));actions.append(button('Справка',()=>routeForm(task),'quiet',`Справка по делу «${task.title}»`));card.append(actions);group.append(card);}
        list.append(group);
      }
      if(!list.querySelector('.plan-group'))list.append(el('p','Нет дел по выбранным условиям.','plan-empty'));
    }
    async function load(receipt='') {
      const ticket=++generation;statusNode().textContent='Загружаем план…';section.setAttribute('aria-busy','true');setError(errorNode(),'');
      for(const id of ['plan-new-task','plan-new-goal','plan-ai','plan-ai-help','plan-example','plan-refresh'])$(id).disabled=true;
      try {
        const [newGoals,newTasks,timezones]=await Promise.all([client.all('/goals','goals'),client.all('/tasks','tasks'),zones.length?{timezones:zones}:client.request('/timezones')]);
        if(ticket!==generation || !active())return;
        goals=newGoals;tasks=newTasks;zones=timezones.timezones;
        if(filter && filter!=='standalone'&&!goals.some(g=>g.id===filter))filter='';
        render();statusNode().textContent=receipt;
      } catch(e) {if(ticket===generation&&active()){statusNode().textContent=receipt?receipt+' Список не обновлён.':'Список не обновлён.';failed(e);}}
      finally {if(ticket===generation){section.setAttribute('aria-busy','false');for(const id of ['plan-new-task','plan-new-goal','plan-ai','plan-ai-help','plan-example','plan-refresh'])$(id).disabled=false;}}
    }
    $('plan-new-task').onclick=()=>taskForm();$('plan-new-goal').onclick=()=>draft();$('plan-example').onclick=()=>draft(true);
    $('plan-ai').onclick=()=>aiForm();$('plan-ai-help').onclick=()=>aiForm('find-help-route');
    $('plan-refresh').onclick=()=>load();$('plan-resume').onclick=()=>resumeModal();
    const refreshTimer=setInterval(()=>{if(active() && !modal && tasks.length)render();},60000);
    window.addEventListener('pagehide',()=>clearInterval(refreshTimer),{once:true});
    return {
      async restore(){
        if(modal||restoring)return;restoring=true;
        try{const result=await client.request('/draft');if(result.draft){durableVersion=result.draft.version;const state=window.PlannerDraftCodec?.decode(result.draft.state)||result.draft.state;suspendedView=state.view||null;readMaterials=new Set(state.readMaterials||[]);readTitles=new Map(state.readTitles||[]);if(state.kind==='editor')draft(state.example,state.proposal,state.existing,state);else if(state.kind==='consultation')aiForm(state.operation,state.original,state);durableWire=draftWire(modal?.serialize?.()||state);if(dialog.open)dialog.close();$('plan-resume').hidden=false;}}
        catch(e){statusNode().textContent='Не удалось восстановить черновик. '+e.message;}finally{restoring=false;}
      },
      hasDraft:()=>Boolean(modal?.serialize||parkedDraft),
      canAttach:()=>!modal?.operation&&Boolean(goals.length||modal?.serialize),
      async prepareHandbook(){try{goals=await client.all('/goals','goals');}catch{}},
      markRead:(id,title)=>{readMaterials.add(id);if(title)readTitles.set(id,title);changed();},
      suspendForHandbook(){suspended=Boolean(modal);if(dialog.open){suspendedView=retainedView();dialog.close();}visible=false;tasksVisible=false;section.hidden=true;if(tasksSection)tasksSection.hidden=true;saveDraft();},
      returnFromHandbook(){
        const self=this;const cameFromHandbook=suspended;
        suspended=false;const view=suspendedView;
        if(modal?.repaint&&!dialog.open)modal.repaint();
        if(modal){if(!dialog.open)dialog.showModal();modal.fields.querySelectorAll('textarea').forEach(autosize);
          if(view){const n=[...dialog.querySelectorAll('input,select,textarea')][view.index];n?.focus({preventScroll:true});if(n?.setSelectionRange&&typeof view.start==='number')try{n.setSelectionRange(view.start,view.end);}catch{}dialog.scrollTop=view.scroll;}
        }
        suspendedView=null;
        if(cameFromHandbook&&modal?.serialize&&!modal.operation){
          modal.readOffer?.remove();
          const attached=new Set(modal.attachedIDs?.()||(modal.result.pendingMaterials||[]).map(c=>c.id));
          const candidates=[...readTitles].filter(([id])=>!attached.has(id));
          if(candidates.length){const offer=el('details',undefined,'plan-information');offer.append(el('summary','Добавить открытые материалы в план'));
            offer.append(el('p','Выберите материалы сами. Просмотр справочника не меняет план.','plan-footnote'));
            for(const [id,title] of candidates)offer.append(button('Добавить «'+title+'»',async()=>{await self.attachMaterial({id,title});if(!dialog.open)self.returnFromHandbook();}));
            modal.fields.append(offer);modal.readOffer=offer;
          }
        }
      },
      async attachMaterial(card){
        if(modal?.operation)throw new Error('Сначала подтвердите результат предыдущего сохранения.');
        if(modal?.addMaterial){modal.addMaterial(card);return;}
        if(modal?.serialize){
          const state=modal;const data=state.result;const pending=data.pendingMaterials||(data.pendingMaterials=[]);if(pending.some(c=>c.id===card.id))return;pending.push({id:card.id,title:card.title});
          if(data.proposal){let index=data.proposal.tasks.findIndex(t=>(t.material_ids||[]).includes(card.id));if(index<0){data.proposal.tasks.push({title:'Изучить «'+card.title+'»',material_ids:[card.id],links:[{title:card.title,url:'/?handbook='+encodeURIComponent(card.id)}]});index=data.proposal.tasks.length-1;}data.selection=[...new Set([...data.selection,index])];}
          if(state.repaint){state.repaint();if(dialog.open)dialog.close();suspended=true;}
          changed();return;
        }
        goals=await client.all('/goals','goals');
        showModal('Выберите план','',fields=>{for(const goal of goals)fields.append(button(goal.title,()=>{close(true);editPlan(goal,card).then(()=>window.HandbookNavigation?.returnToPlan());}));},null);
      },
      async showTasks(identifier,originGoal=null,mode='task'){
        if(dialog.open){if(modal?.serialize){suspendedView=retainedView();saveDraft();dialog.close();}else if(!close())return;}
        sourceGoal=originGoal;visible=false;tasksVisible=true;section.hidden=true;if(tasksSection)tasksSection.hidden=false;const ticket=generation+1;await load();if(ticket!==generation||!tasksVisible)return;
        if(identifier&&tasksVisible){try{const result=await client.request('/tasks/'+identifier);if(tasksVisible&&ticket===generation){if(mode==='move')moveForm(result.task);else taskForm(result.task);}}catch(e){if(tasksVisible&&ticket===generation)failed(e);}}
      },
      async showPlanGoal(identifier){openGoals.add(identifier);await this.show(null);const goal=[...$('plan-list').querySelectorAll('[data-goal-id]')].find(n=>n.dataset.goalId===identifier);if(goal&&visible){const summary=goal.querySelector('summary');summary.focus({preventScroll:true});goal.scrollIntoView({block:'nearest'});}},
      async show(identifier,mode='task') {
        tasksVisible=false;sourceGoal=null;if(tasksSection)tasksSection.hidden=true;
        if(mode==='goal'){openGoals.add(identifier);identifier=null;}
        if(modal?.serialize&&!dialog.open&&!suspended)resumeModal();
        if(mode==='review')pendingReview=true;
        if(identifier)pendingTask={id:identifier,mode};
        visible=true;section.hidden=false;
        // A native return to the same webview keeps the live selection/editor.
        if(pendingReview && modal) {
          pendingReview=false;if(!dialog.open)dialog.showModal();return;
        }
        await load();
        if(pendingReview && visible) {
          const ticket=generation;
          try {
            const latest=await client.request('/ai/latest');
            if(ticket!==generation || !active())return;
            pendingReview=false;
            if(modal)return;
            if(latest.proposal) {
              showModal('Предложенный план','',fields=>{
                fields.append(el('p','Выберите, что добавить в план. До подтверждения ничего не сохраняется.','plan-dialog-intro'));
                appendSuggestions(fields,latest.proposal);
              },null);
            } else statusNode().textContent='Откройте сохранённый план или получите новое предложение.';
          } catch(e) {
            if(ticket!==generation || !active())return;
            if(e.status!==401)pendingReview=false;
            failed(e);
          }
          return;
        }
        if(!pendingTask || !visible)return;
        const target=pendingTask,ticket=generation;
        try {
          const result=await client.request('/tasks/'+target.id);
          if(ticket!==generation||!visible)return;
          pendingTask=null;if(target.mode==='move')moveForm(result.task);else taskForm(result.task);
        } catch(e) {
          if(ticket!==generation||!visible)return;
          if(e.status!==401)pendingTask=null;
          failed(e);
        }
      },
      leave(){if(modal?.serialize){saveDraft();if(dialog.open)dialog.close();}else if(!close())return false;visible=false;tasksVisible=false;sourceGoal=null;generation++;section.hidden=true;if(tasksSection)tasksSection.hidden=true;return true;},
      reset(){parkedDraft=null;parkingDraft=false;clearTimeout(draftTimer);durableVersion=0;durableWire='';draftConflict=false;readMaterials.clear();readTitles.clear();generation++;visible=false;tasksVisible=false;sourceGoal=null;dayFilter='';openGoals.clear();if(tasksSection)tasksSection.hidden=true;if($('tasks-list'))$('tasks-list').replaceChildren();goals=[];tasks=[];zones=[];filter='';pendingTask=null;pendingReview=false;if(modal){modal.busy=false;modal.operation=null;close(true);}section.hidden=true;$('plan-list').replaceChildren();},
      isVisible:()=>active()
    };
  };
})();
