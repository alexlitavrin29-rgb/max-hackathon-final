/* Planner v1 boundary. No storage, logging, transport retries or implicit timezone. */
(function(root, factory) {
  const value=factory();
  if(typeof module==='object' && module.exports) module.exports=value;
  else root.PlannerCore=value;
})(typeof window==='object'?window:globalThis, function() {
  'use strict';
  const pad=n=>String(n).padStart(2,'0');
  function parts(instant, zone) {
    if(!zone) throw new Error('Выберите часовой пояс.');
    try {
      const values=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(instant));
      return Object.fromEntries(values.filter(p=>p.type!=='literal').map(p=>[p.type,Number(p.value)]));
    } catch { throw new Error('Проверьте дату и часовой пояс.'); }
  }
  function wallNumber(p) { return Date.UTC(p.year,p.month-1,p.day,p.hour,p.minute,p.second||0); }
  function localTime(instant, zone) {
    const p=parts(instant,zone);
    return `${String(p.year).padStart(4,'0')}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}`;
  }
  function wallCandidates(local, zone) {
    if(!zone) throw new Error('Выберите часовой пояс.');
    if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(local)) throw new Error('Укажите полную дату и время.');
    const nominal=Date.parse(local+':00Z');
    if(!Number.isFinite(nominal) || localTime(nominal,'UTC')!==local || Number(local.slice(0,4))<1000) throw new Error('Проверьте дату.');
    // Sample offsets around the date, then round-trip each candidate. Gaps yield
    // zero; repeated hours yield both instants. Device timezone is never read.
    const offsets=new Set();
    for(let hours=-48;hours<=48;hours+=6) {
      const sample=nominal+hours*3600000;
      offsets.add(wallNumber(parts(sample,zone))-sample);
    }
    return [...offsets].map(offset=>nominal-offset)
      .filter(instant=>localTime(instant,zone)===local)
      .sort((a,b)=>a-b).map(instant=>new Date(instant).toISOString());
  }
  function zonedTime(instant, zone) {
    const time=new Date(instant).getTime();
    const offset=Math.round((wallNumber(parts(time,zone))-time)/60000);
    const abs=Math.abs(offset);
    return localTime(time,zone)+`:00${offset<0?'-':'+'}${pad(Math.floor(abs/60))}:${pad(abs%60)}`;
  }
  function title(value, max) {
    const text=value.trim();
    if(!text || [...text].length>max) throw new Error(`Название должно содержать от 1 до ${max} символов.`);
    return text;
  }
  function taskChanges(before, after) {
    const result={};
    const sameTime=(a,b)=>a===b || (a && b && new Date(a).getTime()===new Date(b).getTime());
    for(const field of ['title','goal_id','status','due_at','due_date','timezone','remind_before_minutes']) {
      if(field==='due_at'? !sameTime(before[field],after[field]):field==='due_date'?(before[field]??null)!==(after[field]??null):before[field]!==after[field]) result[field]=after[field];
    }
    if('due_at' in result || 'timezone' in result) result.timezone_confirmed=Boolean(after.due_at && after.timezone_confirmed);
    return result;
  }
  const messages={
    notifications_paused:'Бот остановлен. Откройте его в MAX, затем включите напоминание заново.',
    required_task_locked:'Этот поиск закреплён в плане. Можно открыть его, назначить напоминание или отметить дело выполненным.',
    draft_conflict:'Черновик изменён в другом окне. Ваши правки остались здесь; откройте актуальный черновик перед сохранением.',
    policy_expired:'Срок предложения истёк. Получите новый план; сохранённые дела останутся.',
    invalid_policy:'Не удалось подтвердить предложение плана. Получите его заново.',
    reserved_plan:'Это предложение нужно сохранить целиком через редактор плана.',
    materials_changed:'Материалы обновились. Откройте справку заново перед сохранением плана.',
    planner_migration_required:'Планировщик ещё обновляется. Попробуйте немного позже.',
    stale_version:'Дело или цель изменились в другом окне. Обновите список и откройте форму заново.',
    goal_not_empty:'В цели ещё есть дела. Сначала перенесите их из цели или удалите отдельно.',
    notification_in_past:'Время напоминания уже прошло. Выберите будущее время или выключите напоминание.',
    timezone_confirmation_required:'Проверьте настройки даты и времени устройства и откройте форму заново.',
    invalid_timezone:'Не удалось использовать настройки времени устройства. Откройте форму заново.',
    invalid_time_offset:'Проверьте дату и время устройства и откройте форму заново.',
    invalid_schedule:'Проверьте дату, время и напоминание.',
    idempotency_conflict:'Этот запрос отличается от предыдущего. Обновите список перед новым действием.',
    id_conflict:'Это дело или цель уже существуют. Обновите список.',
    not_found:'Дело или цель больше недоступны. Обновите список.'
  };
  class ApiError extends Error {
    constructor(message,status=0,code='network') {
      super(message);this.status=status;this.code=code;
      this.retryable=status===0 || status===401 || status>=500;
    }
  }
  class Client {
    constructor(getToken,fetcher=globalThis.fetch.bind(globalThis)) {this.getToken=getToken;this.fetcher=fetcher;}
    operation(method,path,body) {
      const wire=JSON.stringify(body);
      if(new TextEncoder().encode(wire).length>24000) throw new Error('План слишком большой. Сократите названия или число дел.');
      return Object.freeze({method,path,body:wire,key:globalThis.crypto.randomUUID()});
    }
    async request(path,options={}) {
      const aiProposal=['/ai/find-help-route','/ai/draft-plan'].includes(path);
      const aiFailure='Не удалось получить ответ AI. Повторите запрос позже или составьте план вручную.';
      const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),aiProposal?75000:20000);
      try {
        const response=await this.fetcher('/api/planner'+path,{
          ...options,signal:controller.signal,
          headers:{Authorization:'Bearer '+this.getToken(),...options.headers}
        });
        let data;
        try {data=await response.json();}
        catch {throw new ApiError(aiProposal?aiFailure:'Ответ сервера не прочитан. Проверьте список перед новым действием.',response.status>=400?response.status:0);}
        if(!response.ok) {
          const code=data?.detail?.code || (response.status===401?'auth':'request');
          const message=response.status===401?'Сеанс завершён. Начните новый сеанс, затем повторите тот же запрос.':
            messages[code] || (response.status===422?'Проверьте поля формы, дату и время.':response.status===404?'План пока недоступен. Попробуйте позже.':response.status>=500?(aiProposal?aiFailure:'Сервер временно недоступен. Результат сохранения пока неизвестен.'):'Не получилось выполнить запрос. Попробуйте ещё раз.');
          throw new ApiError(message,response.status,code);
        }
        return data;
      } catch(e) {
        if(e instanceof ApiError) throw e;
        if(aiProposal)throw new ApiError(aiFailure);
        throw new ApiError(options.method && options.method!=='GET'?
          'Не удалось получить ответ. Результат сохранения пока неизвестен. Проверьте связь и повторите тот же запрос.':
          'Не удалось загрузить план. Проверьте связь и повторите загрузку.');
      } finally {clearTimeout(timer);}
    }
    mutate(operation) {
      return this.request(operation.path,{method:operation.method,body:operation.body,
        headers:{'Content-Type':'application/json','Idempotency-Key':operation.key}});
    }
    async all(path,field) {
      const rows=[];const seen=new Set();let after=null,asOf=null;
      do {
        const query=new URLSearchParams({limit:'100'});
        if(after) query.set('after',after);
        if(asOf) query.set('as_of',asOf);
        const page=await this.request(path+'?'+query);
        rows.push(...page[field]);asOf=page.as_of || asOf;after=page.next_after;
        if(after && seen.has(after)) throw new Error('Не удалось прочитать весь список. Повторите загрузку.');
        seen.add(after);
      } while(after);
      return rows;
    }
  }
  return {wallCandidates,localTime,zonedTime,title,taskChanges,Client,ApiError};
});
