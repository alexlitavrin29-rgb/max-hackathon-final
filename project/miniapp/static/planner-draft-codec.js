/* Lossless source interning keeps rich answer snapshots within the existing draft bound. */
(function(root,factory){const codec=factory();if(typeof module==='object'&&module.exports)module.exports=codec;else root.PlannerDraftCodec=codec;})(typeof window==='object'?window:globalThis,function(){
 'use strict';
 const poolKey='information_pool_v1',refKey='planner_information_ref_v1';
 function information(value){return value&&typeof value==='object'&&!Array.isArray(value)&&typeof value.text==='string'&&Array.isArray(value.sources)&&(Array.isArray(value.points)||value.priority==='urgent');}
 function encode(state){
  const pool=[],known=new Map();
  function pack(info){
   const sources=[],seen=new Map();const sourceRef=source=>{const key=JSON.stringify(source);if(!seen.has(key)){seen.set(key,sources.length);sources.push(source);}return seen.get(key);};
   const packed={...info,sources:info.sources.map(sourceRef)};
   for(const field of ['points','details'])if(Array.isArray(info[field]))packed[field]=info[field].map(row=>({...row,sources:(row.sources||[]).map(sourceRef)}));
   const assembled=(info.intro||'')+(info.points?.length?'\n\n'+info.points.map(point=>'• '+point.text).join('\n'):'')+(info.note?'\n\n'+info.note:'');
   if(assembled===info.text){delete packed.text;packed.text_from_parts_v1=true;}
   packed.source_pool_v1=sources;return packed;
  }
  function visit(value){
   if(information(value)){const key=JSON.stringify(value);if(!known.has(key)){known.set(key,pool.length);pool.push(pack(value));}return {[refKey]:known.get(key)};}
   if(Array.isArray(value))return value.map(visit);
   if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,visit(item)]));
   return value;
  }
  const result=visit(state);return pool.length?{...result,[poolKey]:pool}:result;
 }
 function decode(state){
  if(!state||!Array.isArray(state[poolKey]))return state;
  const pool=state[poolKey];
  function unpack(index){
   if(!Number.isInteger(index)||index<0||index>=pool.length)throw new Error('Не удалось восстановить материалы черновика.');
   const packed=pool[index];if(!packed||!Array.isArray(packed.source_pool_v1))throw new Error('Не удалось восстановить источники черновика.');
   const source=index=>{if(!Number.isInteger(index)||index<0||index>=packed.source_pool_v1.length)throw new Error('Не удалось восстановить ссылку черновика.');return packed.source_pool_v1[index];};
   const info={...packed,sources:(packed.sources||[]).map(source)};
   delete info.source_pool_v1;delete info.text_from_parts_v1;
   for(const field of ['points','details'])if(Array.isArray(packed[field]))info[field]=packed[field].map(row=>({...row,sources:(row.sources||[]).map(source)}));
   if(packed.text_from_parts_v1)info.text=(info.intro||'')+(info.points?.length?'\n\n'+info.points.map(point=>'• '+point.text).join('\n'):'')+(info.note?'\n\n'+info.note:'');
   return info;
  }
  function visit(value){
   if(value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===1&&Object.hasOwn(value,refKey))return unpack(value[refKey]);
   if(Array.isArray(value))return value.map(visit);
   if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).filter(([key])=>key!==poolKey).map(([key,item])=>[key,visit(item)]));
   return value;
  }
  return visit(state);
 }
 return {encode,decode};
});
