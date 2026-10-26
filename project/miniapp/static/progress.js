/* One current action and a connected route, shared by housing and career. */
window.createStageCard=function({title,stages,open,act}){
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const button=(text,fn,cls)=>{const b=node('button',text,cls);b.type='button';b.onclick=fn;return b;};
  const root=node('section',undefined,'progress-card'),icons=node('div',undefined,'progress-icons');
  root.setAttribute('aria-label',title);icons.setAttribute('aria-label','Этапы маршрута');root.append(node('h2',title),icons);
  const markers=stages.map((stage,i)=>{const b=button('',()=>open(i),'progress-icon'),img=node('img');img.src='/static/figma/'+stage.icon;img.alt='';if(stage.art)img.className='natural-art';b.setAttribute('aria-label',stage.title);b.append(img);icons.append(b);return b;});
  const heading=node('h3'),copy=node('p',undefined,'progress-copy'),status=node('p',undefined,'progress-status');status.setAttribute('role','status');
  const primary=button('',act,'progress-primary'),details=button('Подробнее об этапах',()=>open(),'progress-details');
  root.append(heading,copy,primary,details,status);
  return {root,status,primary,details,paint(index,{title,text,action,complete=false}){
    heading.textContent=title;copy.textContent=text;primary.textContent=action;
    markers.forEach((b,i)=>{b.classList.toggle('is-current',i===index);b.classList.toggle('is-complete',complete||i<index);b.setAttribute('aria-current',i===index?'step':'false');});
  }};
};
