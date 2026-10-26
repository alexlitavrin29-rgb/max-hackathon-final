(function(root,factory){const model=factory();if(typeof module==='object')module.exports=model;else root.ScreenModel=model;})(typeof window==='object'?window:globalThis,()=>{
  'use strict';
  return {searchTarget:category=>({housing:'rental',work:'work',benefits:'work'})[category]||null,categories:data=>(data.categories||[]).filter(c=>(data.entries||[]).some(e=>e.category===c.id)),validQuestion:text=>Boolean(text.trim())&&[...text.trim()].length<=160};
});
