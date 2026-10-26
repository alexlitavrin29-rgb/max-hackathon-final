/* Execute the real aiForm state transitions without a provider or browser network. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const core=require('../static/planner-core.js');
const source=fs.readFileSync(path.join(__dirname,'../static/plan.js'),'utf8');
const start=source.indexOf('    function aiForm('),end=source.indexOf('    function planOrder(',start);
assert.ok(start>=0&&end>start);

function form(provider,restored=null,operation='find-help-route',initial='Хочу восстановить паспорт'){
  const nodes=[],buttons=[],inputs=[],calls=[];
  const node=(tag,text='')=>{const n={tag,textContent:text,hidden:false,disabled:false,dataset:{},classList:{toggle(){}},append(){},replaceChildren(){},setAttribute(){}};nodes.push(n);return n;};
  const ctx={window:{},modal:null,C:core,el:node,
    field(parent,label,type,value){const n=node('input');n.value=value;inputs.push(n);return n;},
    button(text,click,className){const n=node('button',text);n.click=click;n.className=className;buttons.push(n);return n;},
    showModal(title,submit,build){ctx.modal={error:node('error')};build(node('fields'));return true;},
    setEntryMode(){},appendInformation(){},appendSuggestions(){},draftControls(){},changed(){},retainedView(){return null;},
    suspendedView:null,readMaterials:new Set(),readTitles:new Map(),
    setError(n,text){n.textContent=text;},failed(e,n){n.textContent=e.message;},close(){},draft(){},
    client:{async request(url,options){const call={url,body:JSON.parse(options.body)};calls.push(call);return provider(call);}}};
  vm.createContext(ctx);vm.runInContext(source.slice(start,end)+';this.openAI=aiForm;',ctx);
  ctx.openAI(operation,initial,restored);
  return {ctx,nodes,buttons,ask:buttons.find(b=>b.className==='plan-primary'),inputs,calls,state:()=>ctx.modal.serialize()};
}

for(const reason of ['unavailable','busy']){
  test(reason+' preserves editable question and repeats the original operation',async()=>{
    const h=form(()=>({status:'unsupported',reason,message:'Временный отказ'}));
    await h.ask.click();
    assert.equal(h.state().phase,'question');
    assert.equal(h.inputs[0].disabled,false);
    assert.equal(h.state().original,'Хочу восстановить паспорт');
    assert.match(h.ctx.modal.error.textContent,reason==='busy'?/много запросов/:/Временный отказ/);
    await h.ask.click();
    assert.equal(h.calls[1].url,h.calls[0].url);
    assert.deepEqual(h.calls[1].body,h.calls[0].body);
  });
}

test('Не знаю sends the current clarification instead of only filling the field',async()=>{
 const h=form(call=>call.body.answer?{status:'draft',information:{text:'Проверить варианты жилья',sources:[]},context:{material_ids:['housing_start'],required_resources:['app_rental']},tasks:[]}:{status:'clarify',question_id:'housing_kind',question:'Какое жильё нужно?'});
 await h.ask.click();
 await h.buttons.find(b=>b.textContent==='Не знаю').click();
 assert.equal(h.calls.length,2);
 assert.equal(h.calls[1].body.question_id,'housing_kind');
 assert.equal(h.calls[1].body.answer,'Не знаю');
 assert.equal(h.state().phase,'info');
});

test('temporary failure preserves clarification and its operation',async()=>{
  const saved={kind:'consultation',original:'Хочу жильё',operation:'find-help-route',phase:'clarify',questionID:'housing_kind',question:'Какое жильё?',clarification:'Аренда',clarifyingProposal:false};
  const h=form(()=>({status:'unsupported',reason:'unavailable',message:'Повторите позже'}),saved);
  await h.ask.click();await h.ask.click();
  assert.equal(h.state().phase,'clarify');
  assert.equal(h.state().clarification,'Аренда');
  assert.equal(h.calls[1].url,'/ai/find-help-route');
  assert.equal(h.calls[1].body.answer,'Аренда');
});

test('plan mode survives a category clarification and retains its answer',async()=>{
  const h=form(({url})=>url==='/ai/find-help-route'
    ?{status:'clarify',question_id:'topic_choice',question:'С какой темы начнём?',choices:[{id:'work',title:'Работа'}]}
    :{status:'draft',information:{text:'Подготовка к работе',sources:[]},tasks:[],context:{material_ids:[]}},null,'draft-plan');
  await h.ask.click();
  h.buttons.find(b=>b.textContent==='Работа').click();
  await h.ask.click();
  assert.equal(h.calls[0].url,'/ai/find-help-route');
  assert.equal(h.calls[1].url,'/ai/draft-plan');
  assert.equal(h.calls[1].body.answer,'work');
  assert.equal(h.state().answers[0].answer,'work');
});

test('information to plan retains the agreed material',async()=>{
  const h=form(({url})=>url==='/ai/find-help-route'
    ?{status:'draft',information:{text:'Резюме',sources:[]},context:{material_ids:['resume_first']},tasks:[]}
    :{status:'draft',information:{text:'Резюме',sources:[]},context:{material_ids:['resume_first']},tasks:[]});
  await h.ask.click();await h.ask.click();
  assert.equal(h.calls[1].url,'/ai/draft-plan');
  assert.deepEqual(h.calls[1].body.selected_material_ids,['resume_first']);
});

test('the authoritative plan removes superseded provisional cards from the restored draft',async()=>{
  const saved={kind:'consultation',original:'Нужна помощь с поликлиникой',operation:'find-help-route',phase:'info',
    description:'Меня не приняли без бумажного полиса',selectedMaterials:['clinic_attachment','care_denial'],
    information:{text:'Прикрепление к поликлинике',sources:[]}};
  const h=form(()=>({status:'draft',information:{text:'Отказ в приёме',sources:[]},
    context:{material_ids:['care_denial']},tasks:[]}),saved);
  await h.ask.click();
  assert.equal(h.calls[0].url,'/ai/draft-plan');
  assert.deepEqual(Array.from(h.state().selectedMaterials),['care_denial']);
  assert.deepEqual(h.state().information,{text:'Отказ в приёме',sources:[]});
});

test('a priority choice keeps the second goal and both materials for the plan',async()=>{
  const h=form(({url,body})=>{
    if(url==='/ai/draft-plan')return {status:'draft',information:{text:'Обе цели',sources:[]},context:{material_ids:['resume_first','hq_rent_view']},tasks:[]};
    if(body.answer==='work')return {status:'draft',information:{text:'Работа',sources:[]},context:{material_ids:['resume_first']},related_topics:[{id:'housing',title:'Жильё'}],tasks:[]};
    if(body.answer==='housing')return {status:'draft',information:{text:'Аренда',sources:[]},context:{material_ids:['hq_rent_view']},tasks:[]};
    return {status:'clarify',question_id:'topic_choice',question:'Что сначала?',choices:[{id:'work',title:'Работа'},{id:'housing',title:'Жильё'}]};
  },null,'find-help-route','Ищу комнату и первую работу');
  await h.ask.click();
  h.buttons.find(b=>b.textContent==='Работа').click();await h.ask.click();
  assert.deepEqual(h.calls[1].body.pending_topics,['work','housing']);
  h.buttons.find(b=>b.textContent==='Также: Жильё').click();await h.ask.click();
  assert.deepEqual(h.calls[2].body.pending_topics,['housing']);
  assert.deepEqual(Array.from(h.state().selectedMaterials),['resume_first','hq_rent_view']);
  await h.ask.click();
  assert.equal(h.calls[3].url,'/ai/draft-plan');
  assert.deepEqual(h.calls[3].body.selected_material_ids,['resume_first','hq_rent_view']);
});

test('explicitly removing the pending goal leaves the active material',async()=>{
  const h=form(({body})=>body.answer==='work'
    ?{status:'draft',information:{text:'Работа',sources:[]},context:{material_ids:['resume_first']},related_topics:[{id:'housing',title:'Жильё'}],tasks:[]}
    :{status:'clarify',question_id:'topic_choice',question:'Что сначала?',choices:[{id:'work',title:'Работа'},{id:'housing',title:'Жильё'}]});
  await h.ask.click();h.buttons.find(b=>b.textContent==='Работа').click();await h.ask.click();
  h.buttons.find(b=>b.textContent==='Убрать тему «Жильё»').click();
  assert.deepEqual(Array.from(h.state().pendingTopics),[]);
  assert.deepEqual(Array.from(h.state().selectedMaterials),['resume_first']);
});

test('switching topic clears prior material and answered clarifications',async()=>{
  const h=form(()=>({status:'draft',information:{text:'Резюме',sources:[]},
    context:{material_ids:['resume_first']},tasks:[]}));
  await h.ask.click();
  h.state().answers.push({question_id:'work_kind',answer:'Первая'});
  h.buttons.find(b=>b.textContent==='Сменить тему').click();
  assert.equal(h.state().selectedMaterials.length,0);
  assert.equal(h.state().answers.length,0);
});

test('network failure clears pending status and restores the action',async()=>{
  const h=form(()=>{throw new Error('Нет связи');});
  await h.ask.click();
  assert.equal(h.ctx.modal.error.textContent,'Нет связи');
  assert.equal(h.ask.disabled,false);
  assert.ok(!h.nodes.some(n=>n.textContent==='Ищем справку…'||n.textContent==='Составляем предложение…'));
});

for(const failure of ['network','http503','invalid-json']){
  for(const route of ['/ai/find-help-route','/ai/draft-plan']){
    test(route+' '+failure+' does not claim a saved plan',async()=>{
      const c=new core.Client(()=> 'synthetic',async()=>{
        if(failure==='network')throw new TypeError('Нет связи');
        if(failure==='invalid-json')return {ok:true,status:200,json:async()=>{throw new SyntaxError('Не JSON');}};
        return {ok:false,status:503,json:async()=>({detail:{code:'unavailable'}})};
      });
      await assert.rejects(c.request(route,{method:'POST',body:'{}'}),e=>{
        assert.doesNotMatch(e.message,/Результат сохранения|Проверьте список/);
        assert.match(e.message,/ответ|справк|предложени/i);
        return true;
      });
    });
  }
}

test('real commit retains uncertain-save handling and exact retry key',async()=>{
  const seen=[];
  const c=new core.Client(()=> 'synthetic',async(url,options)=>{seen.push(options);throw new TypeError('Нет связи');});
  const op=c.operation('POST','/commit',{confirmed:true});
  for(let i=0;i<2;i++)await assert.rejects(c.mutate(op),e=>/Результат сохранения/.test(e.message));
  assert.equal(seen[0].body,seen[1].body);
  assert.equal(seen[0].headers['Idempotency-Key'],seen[1].headers['Idempotency-Key']);
});

test('unsupported topic remains a deliberate manual continuation',async()=>{
  const h=form(()=>({status:'unsupported',reason:'manual',message:'Составьте вручную'}));
  await h.ask.click();assert.equal(h.state().phase,'info');
});
