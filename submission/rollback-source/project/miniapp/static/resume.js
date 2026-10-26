/* Explicitly saved server resume; unsaved edits live only in this page. */
(function(){
  'use strict';
  const fields=[
    ['name','Как вас зовут','Например: Анна Иванова',240],
    ['contacts','Как с вами связаться','Укажите только те контакты, которыми готовы поделиться: телефон, почту или MAX.',1000],
    ['city','В каком городе ищете работу','Например: Казань',240],
    ['desired_role','Какую работу ищете','Например: помощник повара',240],
    ['education','Образование','Где и чему учились, специальность, год. Можно указать незавершённое обучение.',4000],
    ['experience','Опыт работы','Где работали и что делали. Можно указать практику или волонтёрство, если они были.',4000],
    ['skills','Что умеете','Укажите свои навыки: например, работа с таблицами или приготовление блюд — только если вы это умеете.',4000],
    ['about','Что ещё хотите рассказать','Например: какой график подходит. Это поле можно оставить пустым.',4000]
  ];
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const button=(text,fn,cls='plan-secondary')=>{const b=node('button',text,cls);b.type='button';b.onclick=fn;return b;};
  const blank=()=>Object.fromEntries([...fields.map(([key])=>[key,'']),['no_experience',false]]);
  window.createResume=function({request,requestBlob,onBack,onAuthExpired}){
    const root=document.getElementById('resume');let active=false,loaded=false,version=0,saved=null,draft=blank(),ticket=0,busy=false,loading=false,stage=0;
    let status,preview,save,download,inputs={},noExperience;
    function dirty(){return JSON.stringify(draft)!==JSON.stringify(saved||blank());}
    function showError(error){status.textContent=error.message||'Не удалось выполнить действие. Попробуйте ещё раз.';if(error.status===401)onAuthExpired?.();}
    function sync(){
      const changed=dirty();save.disabled=busy||!loaded;download.disabled=busy||!loaded||!saved||changed;
      for(const input of Object.values(inputs))input.disabled=loading||!loaded;
      noExperience.disabled=loading||!loaded;
      for(const b of root.querySelectorAll('.resume-actions button'))if(b!==save&&b!==download)b.disabled=busy||!loaded;
      root.querySelector('.resume-reload').disabled=busy;
      preview.replaceChildren(node('h2','Предпросмотр'));
      preview.append(node('h3',draft.name||'Ваше имя'));
      for(const [key,label] of fields){if(key==='name')continue;const value=key==='experience'&&draft.no_experience?'Пока нет опыта работы.':draft[key];if(!value)continue;preview.append(node('h4',label),node('p',value));}
      inputs.experience.disabled=loading||!loaded||draft.no_experience;
      if(!busy)status.textContent=changed?'Есть несохранённые изменения. PDF станет доступен после сохранения.':saved?'Резюме сохранено. Можно скачать PDF.':'Заполните поля и нажмите «Сохранить резюме».';
    }
    function render(){
      root.replaceChildren();inputs={};
      root.append(button('← Назад',()=>onBack?.(),'back'),node('h1','Моё резюме'),node('p','Заполните только то, чем хотите поделиться с работодателем. Можно начать без опыта работы. Паспорт, СНИЛС и другие документы здесь не нужны.','plan-footnote'));
      const progress=node('p','Шаг '+(stage+1)+' из 3: '+['О вас и работе','Учёба и опыт','Навыки и проверка'][stage],'resume-progress');progress.setAttribute('aria-live','polite');root.append(progress);
      const form=node('form',undefined,'resume-form');
      for(const [key,label,hint,max] of fields){
        const fieldStage=['name','contacts','city','desired_role'].includes(key)?0:['education','experience'].includes(key)?1:2;
        const wrapper=node('div',undefined,'resume-field'),id='resume-'+key,labelNode=node('label',label);labelNode.htmlFor=id;
        const input=node(max>240?'textarea':'input');input.id=id;input.name=key;input.maxLength=max;input.value=draft[key];if(max>240)input.rows=key==='contacts'?3:5;else input.type='text';
        const help=node('small',hint);help.id=id+'-hint';input.setAttribute('aria-describedby',help.id);
        input.oninput=()=>{draft[key]=input.value;sync();};inputs[key]=input;wrapper.append(labelNode,input,help);wrapper.hidden=fieldStage!==stage;form.append(wrapper);
        if(key==='experience'){
          const label=node('label',undefined,'resume-check');label.hidden=stage!==1;noExperience=node('input');noExperience.type='checkbox';noExperience.checked=draft.no_experience;noExperience.onchange=()=>{draft.no_experience=noExperience.checked;sync();};label.append(noExperience,document.createTextNode('Пока нет опыта работы'));form.append(label);
        }
      }
      const actions=node('div',undefined,'resume-actions');save=button('Сохранить резюме',()=>{},'plan-primary');save.type='submit';
      download=button('Скачать сохранённое PDF',exportPDF);save.hidden=download.hidden=stage!==2;if(stage>0)actions.append(button('← Предыдущий шаг',()=>{stage--;render();root.scrollIntoView({block:'start'});}));if(stage<2)actions.append(button('Далее →',()=>{stage++;render();root.scrollIntoView({block:'start'});},'plan-primary'));actions.append(save,download);form.append(actions);
      status=node('p','', 'resume-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');form.append(status);
      const reload=button('Загрузить сохранённое резюме заново (заменит изменения)',()=>load());reload.classList.add('resume-reload');form.append(reload);
      form.onsubmit=e=>{e.preventDefault();if(busy||!loaded)return;if(stage<2){stage++;render();}else persist();};preview=node('article',undefined,'resume-preview');preview.hidden=stage!==2;root.append(form,preview);sync();
    }
    async function load(){
      if(busy)return;const turn=++ticket;busy=true;loading=true;if(status){sync();status.textContent='Загружаем резюме…';}
      try{const data=await request('/api/resume');if(turn!==ticket)return;saved=data.resume?.state||null;version=data.resume?.version||0;draft={...blank(),...(saved||{})};loaded=true;busy=false;loading=false;render();}
      catch(error){if(turn!==ticket)return;busy=false;loading=false;if(!status)render();else sync();showError(error);}
    }
    async function persist(){
      if(busy||!loaded)return;const turn=ticket;busy=true;sync();status.textContent='Сохраняем…';
      const submitted={...draft};
      try{const data=await request('/api/resume',{method:'PUT',body:JSON.stringify({expected_version:version,state:submitted})});if(turn!==ticket)return;version=data.resume.version;saved=data.resume.state;
        // Keep edits typed while this request was in flight.
        if(JSON.stringify(draft)===JSON.stringify(submitted)){draft={...saved};for(const [key] of fields)inputs[key].value=draft[key];}
        busy=false;sync();
      }catch(error){if(turn!==ticket)return;busy=false;sync();showError(error);}
    }
    async function exportPDF(){
      if(busy||!saved||dirty())return;const turn=ticket;busy=true;sync();status.textContent='Готовим PDF…';
      try{
        if(!requestBlob)throw new Error('Загрузка PDF пока недоступна.');
        const blob=await requestBlob('/api/resume/pdf');if(turn!==ticket)return;
        const url=URL.createObjectURL(blob),link=node('a');link.href=url;link.download='resume.pdf';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
        busy=false;sync();status.textContent='PDF готов. В нём только сохранённая версия резюме.';
      }catch(error){if(turn!==ticket)return;busy=false;sync();showError(error);}
    }
    function hide(){active=false;root.hidden=true;}
    return {async open(){active=true;root.hidden=false;if(!loaded){if(!status)render();await load();}else sync();},hide,isVisible:()=>active,reset(){ticket++;busy=false;loading=false;loaded=false;version=0;saved=null;draft=blank();stage=0;status=null;inputs={};root.replaceChildren();hide();}};
  };
})();
