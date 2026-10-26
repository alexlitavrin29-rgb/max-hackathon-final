/* MAX UI uses prefers-color-scheme. Persist only this nonpersonal preference. */
(function() {
  'use strict';
  const key='tochka-appearance';
  const root=document.documentElement;
  const media=window.matchMedia('(prefers-color-scheme: dark)');
  let preference='system',preview=null;
  try {const stored=localStorage.getItem(key);if(['light','dark'].includes(stored))preference=stored;}catch{}
  function apply() {
    const theme=preview || (preference==='system'?(media.matches?'dark':'light'):preference);
    root.dataset.theme=theme;root.style.colorScheme=theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content',theme==='dark'?'#0f0f12':'#edeef2');
    const picker=document.getElementById('appearance');if(picker)picker.value=preference;
  }
  function setPreference(value) {
    if(!['system','light','dark'].includes(value))return;
    preference=value;preview=null;
    try {if(value==='system')localStorage.removeItem(key);else localStorage.setItem(key,value);}catch{}
    apply();
  }
  apply();
  media.addEventListener('change',apply);
  window.TochkaTheme={setPreference,setPreview(value){preview=['light','dark'].includes(value)?value:null;apply();}};
  document.addEventListener('DOMContentLoaded',()=>{
    const picker=document.getElementById('appearance');
    const dialog=document.getElementById('appearance-dialog');
    picker.onchange=()=>setPreference(picker.value);
    document.getElementById('appearance-open').onclick=()=>dialog.showModal();
    apply();
  },{once:true});
})();
