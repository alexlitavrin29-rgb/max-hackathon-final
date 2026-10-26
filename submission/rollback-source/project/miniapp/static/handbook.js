/* Handbook navigation stays inside the miniapp; external official actions are explicit. */
(function(){
  'use strict';
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const btn=(text,fn,cls='plan-secondary')=>{const b=node('button',text,cls);b.type='button';b.onclick=fn;return b;};
  function richText(parent,text,links=[]){
    if(window.MaterialLinkText)return window.MaterialLinkText.append(parent,text,links);
    const pattern=/\[([^\]\n]+)\]\(([^\s)]+)\)|(https?:\/\/[^\s<>]+)/g;let start=0;
    for(const match of String(text).matchAll(pattern)){
      parent.append(document.createTextNode(text.slice(start,match.index)));
      try{const url=new URL(match[2]||match[3],location.origin);if(['http:','https:'].includes(url.protocol)){const link=node('a',match[1]||'Открыть источник','plan-route-link');link.href=url.href;if(!url.searchParams.has('handbook')&&!url.searchParams.has('section')){link.target='_blank';link.rel='noopener noreferrer';}parent.append(link);}else parent.append(document.createTextNode(match[1]||''));}catch{parent.append(document.createTextNode(match[1]||''));}
      start=match.index+match[0].length;
    }
    parent.append(document.createTextNode(text.slice(start)));
  }
  window.createHandbook=function({request,onOpen,onReturn,planner,hasReturn,onSaved}){
    const root=document.getElementById('handbook');let stack=[],ticket=0,active=false,read=new Set(),route=null;
    const categories={housing:'Жильё',work:'Работа',documents:'Документы',benefits:'Выплаты',study:'Учёба',help:'Помощь',family:'Семья',daily:'Повседневная жизнь'};
    async function open(id='',query={}) {
      route={id,query,title:query.category?categories[query.category]||'Все темы':'Справочник'};onOpen();active=true;root.hidden=false;const turn=++ticket;root.replaceChildren();
      const head=node('div',undefined,'plan-heading');head.append(node('h1','Справочник'));root.append(head);
      if(hasReturn?.()){const returnBar=node('div',undefined,'context-return handbook-return');returnBar.append(btn('← Вернуться к плану',()=>{hide();onReturn();},'plan-primary'));root.append(returnBar);}
      if(stack.length)root.append(btn('← '+(stack.at(-1).title||'Справочник'),()=>{const previous=stack.pop();open(previous.id,previous.query);},'back'));
      const form=node('form');form.className='handbook-search';const input=node('input');input.type='search';input.placeholder='Найти материал';input.setAttribute('aria-label','Поиск в справочнике');input.title='Поиск по названию и тексту';input.value=query.q||'';const search=btn('Найти',()=>{});search.type='submit';form.append(input,search);form.onsubmit=e=>{e.preventDefault();stack.push({id,query,title:'Назад к материалам'});open('',{q:input.value.trim()});};root.append(form);
      const status=node('p','Загружаем…','plan-footnote');status.setAttribute('role','status');root.append(status);
      try {
        await planner?.prepareHandbook?.();
        const data=await request('/api/handbook'+(id?'/'+encodeURIComponent(id):'?'+new URLSearchParams(query)));
        if(turn!==ticket||!active)return;status.textContent='';
        if(data.card){
          const c=data.card;route.title=c.title;read.add(c.id);planner?.markRead?.(c.id,c.title);
          const article=node('article',undefined,'handbook-card');const heading=node('div',undefined,'card-heading');heading.append(node('h2',c.title));
          let saved=data.saved;const favorite=btn(saved?'♥':'♡',async()=>{
            favorite.disabled=true;try{await request('/api/handbook/'+encodeURIComponent(c.id)+'/favorite',{method:'POST',body:JSON.stringify({saved:!saved,content_hash:c.content_hash})});saved=!saved;favorite.textContent=saved?'♥':'♡';favorite.setAttribute('aria-pressed',String(saved));onSaved?.();}catch(e){status.textContent=e.message;}finally{favorite.disabled=false;}
          },'favorite');favorite.setAttribute('aria-label','Сохранить материал в Моё');favorite.setAttribute('aria-pressed',String(saved));heading.append(favorite);article.append(heading);const summary=node('p',undefined,'handbook-summary');richText(summary,c.summary||'',c.actions);article.append(summary);
          const details=node('details'),text=node('div',undefined,'handbook-text');richText(text,c.text||'',c.actions);details.append(node('summary','Читать полностью'),text);article.append(details);
          if(data.updated){const update=btn('Сохранить обновлённую версию',async()=>{try{await request('/api/handbook/'+encodeURIComponent(c.id)+'/favorite',{method:'POST',body:JSON.stringify({saved:true,replace:true,content_hash:c.content_hash})});update.textContent='Обновлённая версия сохранена';update.disabled=true;onSaved?.();}catch(e){status.textContent=e.message;}});article.append(node('p','В Моём сохранена предыдущая версия.','plan-footnote'),update);}
          for(const action of c.actions||[]){try{const url=new URL(action.url,location.origin);if(!['https:','http:'].includes(url.protocol))continue;const a=node('a',action.label||'Открыть официальный сайт','plan-route-link');a.href=url.href;if(url.origin!==location.origin||!url.searchParams.has('handbook')&&!url.searchParams.has('section')){a.target='_blank';a.rel='noopener noreferrer';}article.append(a);if(action.description)article.append(node('p',action.description,'plan-footnote'));else if(url.origin!==location.origin)article.append(node('p','Подробности и актуальные условия — на сайте источника.','plan-footnote'));}catch{}}
          if(planner?.canAttach())article.append(btn('В мой план',async()=>{try{await planner.attachMaterial(c);status.textContent='Материал выбран. Проверьте дело в редакторе и сохраните план.';}catch(e){status.textContent=e.message;}}));
          if(/резюме/i.test(c.title))article.append(btn('Составить и сохранить резюме',()=>window.HandbookNavigation?.openResume(()=>open(id,query))));
          root.append(article);entries(c.children||[],c.title,id,query);
          if(c.search_target)root.append(btn('Открыть поиск',()=>window.openHandbookSearch?.(c.search_target)));
        }else{
          if(!query.parent&&!query.category&&!query.q){const grid=node('div',undefined,'handbook-categories');for(const c of data.categories||Object.entries(categories).map(([id,title])=>({id,title}))){grid.append(btn(c.title,()=>{stack.push({id:'',query:{},title:'Все темы'});open('',{category:c.id});}));}root.append(grid);}
          if(query.category||query.q||query.parent)entries((data.entries||[]).filter(entry=>query.q||query.parent||!entry.parent),'Все темы','',query);
          if(query.category&&!data.entries?.length)root.append(node('p','В этой теме пока нет материалов. Попробуйте поиск по справочнику.'));
          if(query.q&&!data.entries?.length)root.append(node('p','По этому запросу ничего не найдено. Попробуйте другие слова.'));
        }
      }catch(e){if(turn===ticket)status.textContent=e.message;}
    }
    function entries(items,title,id,query){const list=node('div',undefined,'handbook-entries');for(const item of items){const row=btn('',()=>{stack.push({id,query,title});open(item.id);},'handbook-entry');row.append(node('strong',item.title));if(item.summary)row.append(node('span',item.summary));if(read.has(item.id))row.append(node('small','Вы уже открывали этот материал'));list.append(row);}root.append(list);}
    function hide(){active=false;ticket++;root.hidden=true;}
    function navigate(id=''){if(!id)stack=[];else if(active&&route)stack.push(route);return open(id);}
    return {open:navigate,hide,isVisible:()=>active,reset(){stack=[];read.clear();hide();}};
  };
})();
