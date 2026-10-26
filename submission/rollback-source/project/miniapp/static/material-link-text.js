/* Display-only links: source words stay unchanged and no HTML is interpreted. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.MaterialLinkText=api;})(typeof window==='object'?window:globalThis,function(){
 'use strict';
 const escaped=s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
 function target(value,origin){
  if(typeof value!=='string'||/[\s\\\x00-\x1f]/u.test(value))return null;
  try{const u=new URL(value,origin);if(!['https:','http:'].includes(u.protocol)||u.username||u.password||!u.hostname||u.port==='0')return null;
   return {url:u.href,external:u.origin!==origin||!u.searchParams.has('handbook')&&!u.searchParams.has('section')};
  }catch{return null;}
 }
 function segments(value,links=[],origin='https://local.invalid'){
  const text=String(value),terms=new Map();
  for(const source of links||[]){const t=target(source.url,origin);if(!t)continue;for(const term of source.anchors||[]){if(typeof term==='string'&&term.length&&term.length<=160&&!terms.has(term.toLocaleLowerCase('ru')))terms.set(term.toLocaleLowerCase('ru'),t);}}
  const pattern=terms.size?new RegExp('(?<![\\p{L}\\p{N}_])('+[...terms.keys()].sort((a,b)=>b.length-a.length).map(escaped).join('|')+')(?![\\p{L}\\p{N}_])','giu'):null;
  const result=[];
  function plain(chunk){let at=0;if(pattern){pattern.lastIndex=0;for(const m of chunk.matchAll(pattern)){if(m.index>at)result.push({text:chunk.slice(at,m.index)});result.push({text:m[0],...terms.get(m[0].toLocaleLowerCase('ru'))});at=m.index+m[0].length;}}if(at<chunk.length)result.push({text:chunk.slice(at)});}
  let at=0;const markdown=/\[([^\]\n]+)\]\(([^\s)]+)\)|(https?:\/\/[^\s<>]+)/g;
  for(const m of text.matchAll(markdown)){plain(text.slice(at,m.index));const url=m[2]||m[3],t=target(url,origin);result.push({text:m[1]||m[3],...(t||{})});at=m.index+m[0].length;}
  plain(text.slice(at));return result;
 }
 function append(parent,text,links=[]){for(const part of segments(text,links,location.origin)){if(part.url){const a=document.createElement('a');a.textContent=part.text;a.href=part.url;a.className='material-inline-link';if(part.external){a.target='_blank';a.rel='noopener noreferrer';}parent.append(a);}else parent.append(document.createTextNode(part.text));}}
 return {segments,append};
});
