'use strict';
const $ = s => document.querySelector(s);
const kinds = {request:'Запрос',goal:'Цель',question:'Вопрос',decision:'Решение',spec:'Спецификация',instruction:'Инструкция',task:'Задача',fix:'Исправление',test:'Тест',prompt:'Промт'};
const states = {planned:'В плане',active:'В работе',blocked:'Заблокировано',review:'На проверке',done:'Завершено',deferred:'Отложено',cancelled:'Отменено'};
const promptStates = {planned:'Не завершено',active:'Не завершено · сейчас',review:'Проверить',done:'Завершено',blocked:'Заблокировано',deferred:'Отложено',cancelled:'Отменено'};
const stateLabel = n => (n.kind==='prompt'?promptStates:states)[n.status] || n.status;
const titles = {integration:'Объединение результатов',workers:'Исполнители',knowledge:'Знания проекта',map:'Карта проекта',tasks:'Задачи и исправления',history:'История работы',docs:'Документы проекта',checks:'Проверки результата'};
const eventNames = {'job.queued':'Задание в очереди','job.started':'Исполнитель начал работу','job.finished':'Исполнитель завершил работу','job.orphaned':'Запуск требует восстановления','job.cancel_requested':'Запрошена остановка','worker.configured':'Настроен исполнитель','project.created':'Подключён проект','node.updated':'Изменён узел','dependency.created':'Добавлена зависимость','test.started':'Начата проверка','test.finished':'Завершена проверка','worktree.created':'Создана рабочая копия','runner.configured':'Настроены проверки','test.interrupted':'Проверка прервана','dependency.removed':'Удалена зависимость','worktree.removed':'Удалена рабочая копия','job.stale':'Запуск устарел','job.cancelled':'Запуск отменён','job.recovered':'Запуск восстановлен','job.budget_exceeded':'Бюджет токенов исчерпан','worker.budget':'Изменён бюджет токенов','project.imported':'Проект импортирован из архива','integration.prepared':'Собраны результаты','integration.checked':'Проверена сборка','integration.applied':'Сборка применена','integration.removed':'Сборка удалена','chain.created':'Создана цепочка промтов','chain.advanced':'Цепочка перешла к следующему промту','chain.prompt_sent':'Промт отправлен в чат','chain.status':'Изменился статус цепочки'};
let snapshot = null, selected = localStorage.getItem('project-map-selection') || '', view = 'map', editing = null, loading = false;
const collapsedBranches=new Set();
let detachMapNavigation=null;
let connectionObserver=null, connectionFrame=0, showDependencies=true;
let mapLayout=localStorage.getItem('pmap-layout')||'mindmap', mapZoom=100;
const descriptions={map:'Свяжи идеи, решения и действия в одну картину.',tasks:'От следующего шага до проверенного результата.',history:'Как менялся проект и какие решения к этому привели.',docs:'Цели, спецификации и договорённости в одном месте.',checks:'Проверки, которые подтверждают результат.',workers:'Кому поручена работа и чем она закончилась.',integration:'Соедини проверенные изменения в общий результат.',knowledge:'Найди нужное по смыслу или исследуй связи в коде.'};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = value => new Date(value).toLocaleString('ru-RU', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
const options = (values, selectedValue='') => Object.entries(values).map(([k,v])=>`<option value="${esc(k)}" ${k===selectedValue?'selected':''}>${esc(v)}</option>`).join('');
$('#node-kind').innerHTML = options(Object.fromEntries(Object.entries(kinds).filter(([k])=>k!=='prompt')), 'task'); $('#filter').innerHTML += options(kinds); $('#node-status').innerHTML = options(states);
async function request(url, body) { const response = await fetch(url, body ? {method:'POST',headers:{'Content-Type':'application/json','X-Project-Map':'1'},body:JSON.stringify(body)} : {}); const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Ошибка подключения'); return result; }
const call = (name, args={}) => request('/api/call', {name, arguments:args});
function notice(message) { $('#notice').textContent = message; $('#notice').hidden = !message; }
async function loadProjects() {
  const projects = await request('/api/projects');
  if (!projects.some(p=>p.id===selected)) selected = projects[0]?.id || '';
  $('#project').innerHTML = projects.length ? projects.map(p=>`<option value="${esc(p.id)}" ${p.id===selected?'selected':''}>${esc(p.name)}</option>`).join('') : '<option value="">Нет проектов</option>';
  await refresh();
}
async function refresh() {
  if (loading) return;
  loading = true; const project = selected;
  try {
    const next = project ? await request('/api/snapshot?project='+encodeURIComponent(project)) : null;
    if (project !== selected) return;
    const changed = JSON.stringify(snapshot) !== JSON.stringify(next);
    snapshot = next; $('#connection').textContent = '● Сохранение на компьютере';
    $('#project-label').textContent = snapshot?.project.name || 'Новый проект';
    if (changed || !snapshot) { const canvas=$('.canvas');const scroll=canvas?{left:canvas.scrollLeft,top:canvas.scrollTop}:null;render();if(scroll&&$('.canvas')){$('.canvas').scrollLeft=scroll.left;$('.canvas').scrollTop=scroll.top;} }
  } catch (e) { $('#connection').textContent = 'Нет связи · повторяем подключение'; notice(e.message + '. Несохранённый текст остаётся в форме.'); }
  finally { loading = false; }
}
function matches(n) { const q = $('#search').value.toLocaleLowerCase(); return (!$('#filter').value || n.kind === $('#filter').value) && (n.title+' '+n.body).toLocaleLowerCase().includes(q); }
function badge(n) { return `<span class="badge ${esc(n.status)}">${esc(stateLabel(n))}</span>`; }
function card(n) {
  const dependencies = snapshot.edges.filter(e=>e.source===n.id);
  const unresolved = dependencies.filter(e=>snapshot.nodes.find(x=>x.id===e.target)?.status!=='done');
  return `<button class="node kind-${esc(n.kind)} status-${esc(n.status)}" data-node="${esc(n.id)}"><span class="node-top"><span class="kind">${kinds[n.kind]}</span>${badge(n)}</span><span class="node-title">${esc(n.title)}</span>${n.body?`<span class="node-body">${esc(n.body.slice(0,130))}</span>`:''}<span class="node-foot"><span>${esc(n.owner || 'Исполнитель не назначен')}</span><span>${unresolved.length?'Ожидает: '+unresolved.length:'v'+n.version}</span></span></button>`;
}
function empty(title, text, action=false) { return `<div class="empty"><div class="eyebrow">СВОБОДНОЕ МЕСТО ДЛЯ СЛЕДУЮЩЕГО ШАГА</div><h2>${title}</h2><p>${text}</p>${action?'<button class="primary" data-new>＋ Добавить первый узел</button>':''}</div>`; }
function focusPanel(nodes) {
  const active=nodes.filter(n=>n.status==='active'&&n.kind!=='prompt');
  const questions=nodes.filter(n=>n.kind==='question'&&!['done','cancelled','deferred'].includes(n.status));
  const next=nodes.filter(n=>['task','fix'].includes(n.kind)&&['planned','review'].includes(n.status)).slice(0,3);
  const items=list=>list.map(n=>`<button class="focus-item" data-node="${esc(n.id)}"><span>${esc(n.title)}</span>${badge(n)}</button>`).join('');
  const done=nodes.filter(n=>['task','fix'].includes(n.kind)&&n.status==='done').length;
  const total=nodes.filter(n=>['task','fix'].includes(n.kind)&&n.status!=='cancelled').length;
  const prompts=nodes.filter(n=>n.kind==='prompt'&&n.status==='active');
  const chainBlock=prompts.length?`<h3>Текущие промты <span>${prompts.length}</span></h3>${items(prompts.slice(0,3))}`:'';
  return `<aside class="focus-panel"><div class="focus-heading"><span class="eyebrow">СЕЙЧАС В ПРОЕКТЕ</span><span class="live-label">● Локально</span></div><h2>Держим фокус</h2><p class="focus-copy">Следующий шаг всегда перед глазами.</p><div class="completion"><span>Завершено задач <b>${done} / ${total}</b></span><progress value="${done}" max="${total||1}" aria-label="Завершено ${done} из ${total} задач"></progress></div>${chainBlock}<h3>В работе <span>${active.length}</span></h3>${items(active.slice(0,3))||'<p class="focus-empty">Сейчас нет активных задач.</p>'}<h3>Открытые вопросы <span>${questions.length}</span></h3>${items(questions.slice(0,2))||'<p class="focus-empty">Все записанные вопросы разобраны.</p>'}<h3>Следующие шаги</h3>${items(next)||'<p class="focus-empty">Добавь задачу, чтобы начать.</p>'}<button class="quiet focus-queue" data-view="tasks">Перейти к задачам ↗</button></aside>`;
}
function render() {
  if(detachMapNavigation){detachMapNavigation();detachMapNavigation=null;}
  if(connectionObserver){connectionObserver.disconnect();connectionObserver=null;}
  $('#view-title').textContent = titles[view];
  $('#subtitle').textContent=descriptions[view]||'';
  document.querySelectorAll('[data-view].nav').forEach(b=>{b.classList.toggle('active',b.dataset.view===view);if(b.dataset.view===view)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  $('#add-node').disabled = !selected; $('#add-chain').disabled = !selected; $('#export').disabled = !selected;
  const nodes = snapshot?.nodes || [];
  const stats = [[nodes.length,'узлов в карте'],[nodes.filter(n=>['task','fix'].includes(n.kind)&&!['done','deferred','cancelled'].includes(n.status)).length,'открытых задач'],[nodes.filter(n=>n.kind==='question'&&!['done','cancelled','deferred'].includes(n.status)).length,'вопросов ждут ответа'],[nodes.filter(n=>n.status==='done').length,'завершено']];
  $('#stats').innerHTML = stats.map(([n,label])=>`<div class="stat"><strong>${n.toString().padStart(2,'0')}</strong><span>${label}</span></div>`).join('');
  if (!snapshot) { $('#content').innerHTML = empty('Начнём с проекта','Подключи папку — здесь появится его собственная карта.'); return; }
  const filtered = nodes.filter(matches);
  const navCounts={map:nodes.length,tasks:nodes.filter(n=>['task','fix'].includes(n.kind)&&!['done','cancelled','deferred'].includes(n.status)).length,history:snapshot.events.length,docs:nodes.filter(n=>['spec','instruction','decision','goal'].includes(n.kind)).length,checks:snapshot.runs.length};
  document.querySelectorAll('.nav[data-view]').forEach(button=>{if(!(button.dataset.view in navCounts))return;let count=button.querySelector('.nav-count');if(!count){count=document.createElement('span');count.className='nav-count';count.setAttribute('aria-hidden','true');button.appendChild(count);}count.textContent=navCounts[button.dataset.view];});
  if (view === 'map') {
    const included = new Set(filtered.map(n=>n.id));
    for (const node of filtered) { let parent=node.parent; let guard=0; while(parent && guard++<100){included.add(parent);parent=nodes.find(n=>n.id===parent)?.parent;} }
    const mapNodes=nodes.filter(n=>included.has(n.id));
    const controls=`<div class="map-controls"><div class="map-label"><span class="dot"></span>Живая карта <b>${filtered.length}</b></div><div class="map-buttons"><button data-layout="mindmap" aria-pressed="${mapLayout==='mindmap'}" title="Горизонтальная карта">Карта</button><button data-layout="outline" aria-pressed="${mapLayout==='outline'}" title="Вертикальное дерево">Дерево</button><span class="control-divider"></span><button data-dependencies aria-pressed="${showDependencies}" title="Показать зависимости задач">Зависимости</button><button data-zoom="out" aria-label="Уменьшить карту" ${mapZoom===70?'disabled':''}>−</button><span class="zoom-label">${mapZoom}%</span><button data-zoom="in" aria-label="Увеличить карту" ${mapZoom===115?'disabled':''}>+</button><button data-fold="collapse" title="Свернуть все ветки">Свернуть</button><button data-fold="expand" title="Развернуть все ветки">Развернуть</button></div></div>`;
    $('#content').innerHTML=filtered.length?`<div class="map-layout"><section class="map-surface">${controls}<div class="canvas ${mapLayout}" tabindex="0" role="region" aria-label="Карта проекта. Прокрутите, чтобы увидеть ветки"><div class="map-stage zoom-${mapZoom}"><svg class="map-connections" aria-hidden="true"></svg>${MapView.tree(mapNodes,card,collapsedBranches,!!$('#search').value||!!$('#filter').value)}</div></div><div class="map-legend"><span>Перетаскивай фон мышью · нажми на карточку для деталей</span><span class="connection-keys"><span><i class="line-key"></i>Структура проекта</span><span><i class="line-key dependency"></i>Зависит от</span></span></div></section>${focusPanel(nodes)}</div>`:empty(nodes.length?'Ничего не найдено':'У каждой идеи есть продолжение',nodes.length?'Попробуй изменить запрос или сбросить фильтр.':'Добавь первую цель. Затем свяжи с ней задачи, решения и вопросы.',!nodes.length);

    requestAnimationFrame(observeConnections);
  } else if (view === 'tasks') {
    const groups = [['Очередь',['planned','blocked']],['Выполнение и проверка',['active','review']],['Завершено',['done']],['Отложено и отменено',['deferred','cancelled']]];
    $('#content').innerHTML = '<p class="view-help">Задачи можно отправлять исполнителю из карточки. Для запуска нужны рабочая копия и настроенный координатор.</p><div class="board">'+groups.map(([label,statuses])=>`<section class="column"><h2>${label}<span class="column-count">${filtered.filter(n=>['task','fix'].includes(n.kind)&&statuses.includes(n.status)).length}</span></h2>${filtered.filter(n=>['task','fix'].includes(n.kind)&&statuses.includes(n.status)).map(card).join('') || '<p class="muted">Задач пока нет</p>'}</section>`).join('')+'</div>';
  } else if (view === 'integration') {
    const eligible=filtered.filter(n=>['review','done'].includes(n.status)&&n.worktree);
    $('#content').innerHTML='<p class="view-help">Выбранные изменения собираются в отдельную рабочую копию. Основная папка не меняется. После сборки запустите общие проверки.</p><form id="integration-form">'+eligible.map(n=>'<label><input type="checkbox" name="integration-node" value="'+esc(n.id)+'"> '+esc(n.title)+'</label>').join('')+'<button type="submit">Собрать выбранные результаты</button></form><button data-load-integrations>Обновить сборки</button><div id="integration-list"></div>';loadIntegrations();
  } else if (view === 'workers') {
    $('#content').innerHTML='<p class="view-help">Запуск использует настроенного исполнителя и может расходовать лимиты модели. Сначала создайте рабочую копию задачи. Результат всегда проходит отдельную приёмку.</p><button data-load-jobs>Обновить запуски</button><div id="job-list"></div>';
    loadJobs();
  } else if (view === 'knowledge') {
    $('#content').innerHTML='<p class="view-help">Ищи по смыслу с локальной моделью или по точным словам. Карта кода поддерживает Python.</p><form id="knowledge-form"><label>Что найти<input id="knowledge-query" required maxlength="300"></label><label>Метод<select id="retrieval-mode"><option value="map_semantic_search">По смыслу · локальная модель</option><option value="map_retrieve">По словам · без модели</option></select></label><button type="submit">Найти в исходниках</button><button type="button" data-code-graph>Карта Python-кода</button></form><div id="knowledge-results"></div>';
  } else if (view === 'docs') {
    const docs = filtered.filter(n=>['spec','instruction','decision','goal'].includes(n.kind));
    $('#content').innerHTML = docs.length ? '<div class="documents">'+docs.map(card).join('')+'</div>' : empty('Здесь будут договорённости','Добавь спецификацию, инструкцию, цель или решение.');
  } else if (view === 'history') {
    const counts={}; for(const e of snapshot.events){const day=new Date(e.created).toLocaleDateString('ru-RU',{day:'2-digit',month:'2-digit'});counts[day]=(counts[day]||0)+1;}
    const max=Math.max(1,...Object.values(counts));
    const chart='<aside class="chart"><h2>События по дням</h2>'+Object.entries(counts).slice(0,14).reverse().map(([day,count])=>`<div class="bar-row"><span>${day}</span><progress class="bar" value="${count}" max="${max}" aria-label="${day}: ${count} событий"></progress><span>${count}</span></div>`).join('')+'<p class="muted">По последним 300 событиям.<br>Это записи работы, не расход токенов.</p></aside>';
    const events=snapshot.events.filter(e=>{const n=nodes.find(x=>x.id===e.node);return (!$('#search').value&&!$('#filter').value)||n&&matches(n);});
    $('#content').innerHTML='<div class="history"><div>'+events.map(e=>{const n=nodes.find(x=>x.id===e.node);const label=eventNames[e.kind] || ((kinds[e.kind.split('.')[0]]||'Узел')+' добавлен');return `<article class="event"><time>${fmt(e.created)}</time><div><h3>${esc(label)}</h3><p>${esc(n?.title || snapshot.project.name)}</p>${e.payload.after?.status?`<p>Статус: ${esc(n?stateLabel({...n,status:e.payload.after.status}):states[e.payload.after.status])}</p>`:''}${e.payload.status?`<p>${esc(e.payload.status)}</p>`:''}${e.node?`<button class="quiet" data-node="${esc(e.node)}">Открыть узел ↗</button>`:''}<details><summary class="muted">Подробности события</summary><pre>${esc(JSON.stringify(e.payload,null,2))}</pre></details></div></article>`;}).join('')+'</div>'+chart+'</div>';
  } else {
    $('#content').innerHTML='<p class="view-help">Запускается только заранее настроенная локальная команда. Лимит — 60 секунд. Успех проверки не заменяет проверку всех требований.</p>'+(snapshot.runs.length?snapshot.runs.map(r=>`<article class="run"><span class="eyebrow">${fmt(r.created)} · ${esc(r.status)}</span><h2>${esc(nodes.find(n=>n.id===r.node)?.title||r.node)}</h2><p class="muted">${esc(r.command)}<br>${esc(r.cwd)}<br>Отпечаток: ${esc(r.fingerprint.slice(0,16))} · код выхода: ${r.code ?? '—'}</p><details><summary>Журнал прогона</summary><pre>${esc(r.output || 'Результат ещё не получен. Если процесс был прерван, статус требует проверки.')}</pre></details></article>`).join(''):empty('Проверок ещё нет','Открой задачу и выбери «Запустить проверку». Команда настраивается через CLI — смотри README.'));
  }
}
function openEditor(id=null, parent=null, kind='task') {
  editing = id ? {...snapshot.nodes.find(n=>n.id===id)} : null;
  if (id && !editing?.id) return;
  const form=$('#node-form'); form.reset(); $('#edit-error').textContent='';
  $('#node-status').innerHTML=options(editing?.kind==='prompt'?promptStates:states);
  $('#editor-title').textContent=editing?'Детали узла':'Добавить в карту';
  $('#editor-caption').textContent=editing?kinds[editing.kind].toUpperCase():'НОВЫЙ УЗЕЛ';
  $('#node-parent').innerHTML='<option value="">Без родителя</option>'+snapshot.nodes.map(n=>`<option value="${n.id}">${esc(n.title)}</option>`).join('');
  $('#check-project').innerHTML='<option value="">Текущий проект</option>'+[...$('#project').options].filter(p=>p.value&&p.value!==selected).map(p=>'<option value="'+esc(p.value)+'">'+esc(p.textContent)+'</option>').join('');
  $('#check-project-label').hidden=!editing;
  for(const key of ['kind','title','body','criteria','scope','owner','evidence','status','parent','check_project']) form.elements[key].value=editing?.[key] ?? ({kind,status:'planned',parent:parent||''}[key]||'');
  $('#node-kind').disabled=!!editing; $('#parent-label').hidden=!!editing; $('#node-status').disabled=!editing;
  $('#version-label').textContent=editing?'Версия '+editing.version+' · '+fmt(editing.updated):'Сохранится в карте и истории';
  $('#node-actions').innerHTML=editing?.kind==='prompt'?'<button type="button" data-copy-prompt>Копировать промт</button><button type="button" data-set-status="review">Ответ получен → Проверить</button><button type="button" data-set-status="done">Завершено</button><button type="button" data-set-status="planned">Не завершено</button><button type="button" data-set-status="deferred">Отложить</button><button type="button" data-set-status="cancelled" class="cancel-action">Отменить</button><button type="button" data-fix>＋ Исправление</button>':editing?'<button type="button" data-set-status="deferred">Отложить</button><button type="button" data-set-status="cancelled" class="cancel-action">Отменить задачу</button><button type="button" data-set-status="planned">Вернуть в план</button><button type="button" data-child>＋ Подзадача</button><button type="button" data-fix>＋ Исправление</button><button type="button" data-depend>Зависит от…</button><button type="button" data-check>Запустить проверку</button><button type="button" data-worktree>Создать ветку</button><button type="button" data-enqueue>В очередь исполнителя</button>'+(editing.worktree?'<button type="button" data-remove-worktree class="cancel-action">Удалить рабочую копию</button>':''):'';
  if(editing){const deps=snapshot.edges.filter(e=>e.source===id);if(deps.length)$('#node-actions').insertAdjacentHTML('beforeend',`<p class="dependency-note">Зависит от: ${deps.map(e=>`<span class="dependency-chip">${esc(snapshot.nodes.find(n=>n.id===e.target)?.title||e.target)}<button type="button" class="icon" data-undepend="${esc(e.target)}" aria-label="Убрать зависимость" title="Убрать зависимость">×</button></span>`).join(' ')}</p>`);if(editing.worktree)$('#node-actions').insertAdjacentHTML('beforeend',`<p class="dependency-note">Рабочая копия: ${esc(editing.worktree)}</p>`);}
  $('#editor').showModal();
}
$('#node-form').addEventListener('submit',async e=>{
  e.preventDefault();const button=e.submitter;button.disabled=true;$('#edit-error').textContent='';
  const values=Object.fromEntries(new FormData(e.target));
  try { if(editing){const args={id:editing.id,version:editing.version};for(const k of ['title','body','criteria','scope','owner','evidence','status','check_project'])args[k]=values[k];await call('map_update_node',args);}else{await call('map_create_node',{project:selected,kind:values.kind,title:values.title,body:values.body,criteria:values.criteria,scope:values.scope,owner:values.owner,evidence:values.evidence,parent:values.parent||undefined});}
    $('#editor').close();notice('');await refresh();
  }catch(err){$('#edit-error').textContent=err.message;}finally{button.disabled=false;}
});
$('#project-form').addEventListener('submit',async e=>{e.preventDefault();e.submitter.disabled=true;try{const p=await call('map_create_project',Object.fromEntries(new FormData(e.target)));selected=p.id;localStorage.setItem('project-map-selection',selected);$('#project-dialog').close();e.target.reset();await loadProjects();}catch(err){$('#project-error').textContent=err.message;}finally{e.submitter.disabled=false;}});
$('#dependency-form').addEventListener('submit',async e=>{e.preventDefault();e.submitter.disabled=true;try{await call('map_depend',{source:editing.id,target:$('#dependency-target').value});$('#dependency-dialog').close();$('#editor').close();await refresh();}catch(err){$('#dependency-error').textContent=err.message;}finally{e.submitter.disabled=false;}});
document.addEventListener('click',async e=>{
  const button=e.target.closest('button');if(!button)return;
  if(button.dataset.setStatus){$('#node-status').value=button.dataset.setStatus;$('#node-form').requestSubmit($('#node-form button[type="submit"]'));return;}
  if(button.hasAttribute('data-dependencies')){showDependencies=!showDependencies;button.setAttribute('aria-pressed',String(showDependencies));scheduleConnections();return;}
  if(button.dataset.branch){const id=button.dataset.branch;if(collapsedBranches.has(id))collapsedBranches.delete(id);else collapsedBranches.add(id);const canvas=$('.canvas');const left=canvas.scrollLeft,top=canvas.scrollTop;render();$('.canvas').scrollLeft=left;$('.canvas').scrollTop=top;document.querySelectorAll('[data-branch]').forEach(b=>{if(b.dataset.branch===id)b.focus({preventScroll:true});});return;}
  if(button.dataset.layout){mapLayout=button.dataset.layout;localStorage.setItem('pmap-layout',mapLayout);render();return;}
  if(button.dataset.zoom){const levels=[70,85,100,115];mapZoom=levels[Math.max(0,Math.min(3,levels.indexOf(mapZoom)+(button.dataset.zoom==='in'?1:-1)))];render();return;}
  if(button.dataset.fold){collapsedBranches.clear();if(button.dataset.fold==='collapse')snapshot.nodes.forEach(n=>{if(snapshot.nodes.some(x=>x.parent===n.id))collapsedBranches.add(n.id);});render();return;}
  if(button.hasAttribute('data-load-integrations')){await loadIntegrations();return;}
  if(button.dataset.applyIntegration){button.disabled=true;try{const r=await call('map_apply_integration',{id:button.dataset.applyIntegration});notice(r.message);await loadIntegrations();}catch(err){notice(err.message);}finally{button.disabled=false;}return;}
  if(button.dataset.checkIntegration){button.disabled=true;try{await call('map_check_integration',{id:button.dataset.checkIntegration});await loadIntegrations();}catch(err){notice(err.message);}finally{button.disabled=false;}return;}
  if(button.hasAttribute('data-enqueue')){button.disabled=true;try{await call('map_enqueue',{node:editing.id});$('#edit-error').textContent='В очереди. Закройте и откройте узел перед дальнейшим сохранением.';}catch(err){$('#edit-error').textContent=err.message;}finally{button.disabled=false;}return;}
  if(button.hasAttribute('data-load-jobs')){await loadJobs();return;}
  if(button.hasAttribute('data-copy-prompt')){try{await navigator.clipboard.writeText(editing.body);$('#edit-error').textContent='Промт скопирован. Вставь его в чат Codex — после ответа цепочка перейдёт к следующему.';}catch{$('#edit-error').textContent='Браузер не дал доступ к буферу. Выдели текст в поле «Описание» и скопируй вручную.';}return;}
  if(button.hasAttribute('data-undepend')||button.dataset.undepend){button.disabled=true;try{await call('map_undepend',{source:editing.id,target:button.dataset.undepend});button.closest('.dependency-chip').remove();await refresh();}catch(err){$('#edit-error').textContent=err.message;button.disabled=false;}return;}
  if(button.hasAttribute('data-remove-worktree')){button.disabled=true;$('#edit-error').textContent='';try{const r=await removeWithConfirm('map_remove_worktree',{node:editing.id});if(r){$('#edit-error').textContent='Рабочая копия удалена'+(r.branch_kept?'. Ветка '+r.branch_kept+' сохранена: в ней есть коммиты.':'.');await refresh();$('#version-label').textContent='Узел изменился. Закройте и откройте его перед сохранением.';}}catch(err){$('#edit-error').textContent=err.message;}finally{button.disabled=false;}return;}
  if(button.dataset.removeIntegration){button.disabled=true;try{if(await removeWithConfirm('map_remove_integration',{id:button.dataset.removeIntegration}))await loadIntegrations();}catch(err){notice(err.message);}finally{button.disabled=false;}return;}
  if(button.dataset.cancelJob||button.dataset.recoverJob){try{await call(button.dataset.cancelJob?'map_cancel_job':'map_recover_job',{job:button.dataset.cancelJob||button.dataset.recoverJob});await loadJobs();}catch(err){notice(err.message);}return;}
  if(button.hasAttribute('data-code-graph')){await loadKnowledge('map_code_graph');return;}
  if(button.dataset.close){$('#'+button.dataset.close).close();return;}
  if(button.dataset.view){view=button.dataset.view;render();return;}
  if(button.dataset.node){openEditor(button.dataset.node);return;}
  if(button.hasAttribute('data-new')){openEditor();return;}
  if(button.hasAttribute('data-child')||button.hasAttribute('data-fix')){const parent=editing.id;$('#editor').close();openEditor(null,parent,button.hasAttribute('data-fix')?'fix':'task');return;}
  if(button.hasAttribute('data-depend')){$('#dependency-target').innerHTML=snapshot.nodes.filter(n=>n.id!==editing.id).map(n=>`<option value="${n.id}">${esc(n.title)}</option>`).join('');$('#dependency-error').textContent='';$('#dependency-dialog').showModal();return;}
  if(button.hasAttribute('data-check')||button.hasAttribute('data-worktree')){
    button.disabled=true;$('#edit-error').textContent='';
    try{const result=await call(button.hasAttribute('data-check')?'map_run_check':'map_worktree',{node:editing.id});$('#edit-error').textContent= result.status ? 'Результат проверки: '+result.status : 'Создана ветка: '+result.branch;await refresh();if(result.branch)$('#version-label').textContent='Узел изменился. Закройте и откройте его перед сохранением.';}
    catch(err){$('#edit-error').textContent=err.message;}finally{button.disabled=false;}
  }
});
$('#add-node').onclick=()=>openEditor();$('#add-project').onclick=()=>{$('#project-error').textContent='';$('#project-dialog').showModal();};
$('#project').onchange=async e=>{selected=e.target.value;localStorage.setItem('project-map-selection',selected);snapshot=null;await refresh();};
$('#search').oninput=render;$('#filter').onchange=render;
$('#export').onclick=async()=>{
  const button=$('#export'),project=selected,format=$('#export-format').value;button.disabled=true;notice('');
  try{
    let content,type;
    if(format==='json'){content=JSON.stringify(await request('/api/archive?project='+encodeURIComponent(project)),null,2);type='application/json';}
    else{content='# '+snapshot.project.name+'\n\n'+snapshot.nodes.map(n=>`## ${kinds[n.kind]}: ${n.title}\n\nСтатус: ${states[n.status]} · ID: ${n.id}\n\n${n.body}\n\nКритерии: ${n.criteria || 'не заданы'}\n\nРезультат: ${n.evidence || 'не записан'}\n`).join('\n');type='text/markdown';}
    const url=URL.createObjectURL(new Blob([content],{type})),a=document.createElement('a');a.href=url;a.download='project-map.'+format;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(err){notice(err.message);}finally{button.disabled=!selected;}
};
loadProjects().catch(e=>notice(e.message));setInterval(refresh,3000);

async function loadJobs(){try{const [list,status]=await Promise.all([call('map_jobs',{project:selected}),call('map_worker_status',{project:selected})]);if(!$('#job-list'))return;$('#job-list').innerHTML=workerSummary(status)+(list.length?list.map(j=>`<article class="run"><h2>${esc(snapshot.nodes.find(n=>n.id===j.node)?.title||j.node)}</h2><p>${esc(j.state)} · ${fmt(j.created)} · код ${j.code??'—'}</p>${j.usage?'<p>Расход из отчёта исполнителя: '+esc(j.usage)+'</p>':'<p class="muted">Данные о токенах не получены</p>'}${['queued','running'].includes(j.state)?`<button data-cancel-job="${esc(j.id)}">Остановить</button>`:''}${j.state==='orphaned'?`<button data-recover-job="${esc(j.id)}">Проверить восстановление</button>`:''}<details><summary>Результат исполнителя</summary><pre>${esc(j.output||'Вывод появится после завершения')}</pre></details></article>`).join(''):'<p class="muted">Запусков ещё нет. Настройка — в README.</p>');}catch(err){notice(err.message);}}
async function loadKnowledge(tool,query){
  const form=$('#knowledge-form'),target=$('#knowledge-results'),project=selected;
  if(!form||form.getAttribute('aria-busy')==='true')return;
  const controls=[...form.querySelectorAll('button')];controls.forEach(b=>b.disabled=true);
  form.setAttribute('aria-busy','true');target.setAttribute('role','status');target.textContent='Ищем в текущих файлах…';notice('');
  try{
    const result=await call(tool,{project,query});
    if(project!==selected||!form.isConnected)return;
    const items=tool==='map_code_graph'?result.symbols.map(x=>'<article class="run">'+esc(x.name)+' · '+esc(x.path)+':'+x.line+'</article>').join(''):result.results.map(r=>'<article class="run"><h2>'+esc(r.path)+':'+r.line+'</h2><pre>'+esc(r.text)+'</pre></article>').join('');
    target.innerHTML='<p>'+esc(result.method||result.limits)+'</p>'+(items||'<p>Совпадений не найдено. Измени запрос и повтори поиск.</p>');
  }catch(err){if(project===selected&&form.isConnected)target.textContent=err.message+' Можно повторить поиск.';}
  finally{form.removeAttribute('aria-busy');controls.forEach(b=>b.disabled=false);}
}
document.addEventListener('submit',async e=>{if(e.target.id!=='knowledge-form')return;e.preventDefault();await loadKnowledge($('#retrieval-mode').value,$('#knowledge-query').value);});

async function loadIntegrations(){try{const rows=(await call('map_integrations',{project:selected})).filter(r=>r.state!=='removed');if(!$('#integration-list'))return;$('#integration-list').innerHTML=rows.map(r=>`<article class="run"><h2>${esc(r.state)}</h2><p>${esc(r.path)}</p><p class="muted">Исходная версия: ${esc(r.base)}</p><button data-check-integration="${esc(r.id)}">Проверить общий результат</button>${r.state==='passed'?`<button data-apply-integration="${esc(r.id)}">Применить проверенные изменения в проект</button>`:''}<button class="cancel-action" data-remove-integration="${esc(r.id)}">Удалить сборку</button><pre>${esc(r.output)}</pre></article>`).join('')||'<p>Сборок пока нет.</p>';}catch(err){notice(err.message);}}
document.addEventListener('submit',async e=>{if(e.target.id!=='integration-form')return;e.preventDefault();e.submitter.disabled=true;try{const ids=[...document.querySelectorAll('[name="integration-node"]:checked')].map(x=>x.value);await call('map_integrate',{project:selected,nodes:ids.join(',')});await loadIntegrations();}catch(err){notice(err.message);}finally{e.submitter.disabled=false;}});

function scheduleConnections(){cancelAnimationFrame(connectionFrame);connectionFrame=requestAnimationFrame(drawConnections);}
function drawConnections(){
  const stage=$('.map-stage'),svg=$('.map-connections');if(!stage||!svg||!snapshot)return;
  const rect=stage.getBoundingClientRect(),scale=rect.width/stage.offsetWidth;
  if(!Number.isFinite(scale)||scale<=0)return;
  const boxes=Object.create(null);
  stage.querySelectorAll('.node[data-node]').forEach(el=>{const r=el.getBoundingClientRect();boxes[el.dataset.node]={x:(r.left-rect.left)/scale,y:(r.top-rect.top)/scale,width:r.width/scale,height:r.height/scale};});
  const paths=MapView.connections(snapshot.nodes,showDependencies?snapshot.edges:[],boxes,mapLayout);
  svg.setAttribute('viewBox',`0 0 ${stage.offsetWidth} ${stage.offsetHeight}`);
  svg.innerHTML='<defs><marker id="dependency-tip" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 1 1 L 6 4 L 1 7" fill="none" stroke="currentColor" stroke-width="1.3"/></marker></defs>';
  for(const link of paths){const p=document.createElementNS('http://www.w3.org/2000/svg','path');p.setAttribute('d',link.path);p.setAttribute('class','connection '+link.type);p.dataset.source=link.source;p.dataset.target=link.target;if(link.type==='dependency')p.setAttribute('marker-end','url(#dependency-tip)');svg.appendChild(p);}
  const focused=stage.querySelector('.node:focus')||stage.querySelector('.node:hover');highlightConnections(focused?.dataset.node||null);
}
function observeConnections(){
  const stage=$('.map-stage');if(!stage)return;
  if(detachMapNavigation)detachMapNavigation();
  detachMapNavigation=MapNavigation.attach($('.canvas'));
  if(connectionObserver)connectionObserver.disconnect();
  connectionObserver=new ResizeObserver(scheduleConnections);connectionObserver.observe(stage);stage.querySelectorAll('.node').forEach(el=>connectionObserver.observe(el));scheduleConnections();
}
function highlightConnections(id){
  const stage=$('.map-stage');if(!stage)return;
  const related=new Set(id?[id]:[]);
  stage.querySelectorAll('.connection').forEach(p=>{const connected=id&&(p.dataset.source===id||p.dataset.target===id);p.classList.toggle('is-highlighted',!!connected);if(connected){related.add(p.dataset.source);related.add(p.dataset.target);}});
  stage.classList.toggle('has-focus',!!id);
  stage.querySelectorAll('.node').forEach(n=>n.classList.toggle('is-related',related.has(n.dataset.node)));
}
document.addEventListener('pointerover',e=>{const n=e.target.closest('.map-stage .node');if(n)highlightConnections(n.dataset.node);});
document.addEventListener('pointerout',e=>{const n=e.target.closest('.map-stage .node');if(n&&!n.contains(e.relatedTarget))highlightConnections(null);});
document.addEventListener('focusin',e=>{const n=e.target.closest('.map-stage .node');if(n)highlightConnections(n.dataset.node);});
document.addEventListener('focusout',e=>{if(e.target.closest('.map-stage .node'))highlightConnections(null);});
window.addEventListener('resize',scheduleConnections);
if(document.fonts)document.fonts.ready.then(scheduleConnections);

function workerSummary(s){
  if(!s.configured)return '<p class="worker-summary muted">Исполнитель не настроен. Команда: python app.py worker-codex PROJECT_ID</p>';
  const budget=s.budget?`${s.spent.toLocaleString('ru-RU')} из ${s.budget.toLocaleString('ru-RU')} токенов · осталось ${s.remaining.toLocaleString('ru-RU')}`:`${s.spent.toLocaleString('ru-RU')} токенов · без ограничения`;
  return `<p class="worker-summary">До ${s.parallel} параллельно · лимит ${s.timeout} с · ${budget}</p>${s.budget&&!s.remaining?'<p class="error">Бюджет исчерпан: новые запуски не начнутся. Увеличить: python app.py worker-budget PROJECT_ID ТОКЕНЫ</p>':''}`;
}
async function removeWithConfirm(tool,args){
  try{return await call(tool,args);}
  catch(err){
    if(!/изменения/.test(err.message))throw err;
    if(!confirm(err.message+'\n\nУдалить вместе с несохранёнными изменениями? Это нельзя отменить.'))return null;
    return await call(tool,{...args,discard:true});
  }
}
function splitPrompts(text){
  text=text.replace(/\r\n/g,'\n').trim();
  const parts=/^\s*-{3,}\s*$/m.test(text)?text.split(/^\s*-{3,}\s*$/m):/\n\s*\n/.test(text)?text.split(/\n\s*\n/):text.split('\n');
  return parts.map(p=>p.trim().replace(/^\s*(?:\d{1,3}[.)]|[-*•])\s+/,'').trim()).filter(Boolean);
}
function chainPreview(){
  const steps=splitPrompts($('#chain-prompts').value);
  $('#chain-preview').innerHTML=steps.length?`<b>${steps.length}</b> ${promptWord(steps.length)} в цепочке`+(steps.length>40?' · максимум 40':'')+'<ol>'+steps.slice(0,6).map(p=>`<li>${esc(p.split('\n')[0].slice(0,120))}</li>`).join('')+(steps.length>6?'<li>…</li>':'')+'</ol>':'Вставь промты: по одному на строку, через пустую строку или через строку ---';
}
$('#add-chain').onclick=()=>{$('#chain-form').reset();$('#chain-error').textContent='';$('#chain-parent').innerHTML='<option value="">Без родителя</option>'+snapshot.nodes.filter(n=>n.kind!=='prompt').map(n=>`<option value="${esc(n.id)}">${esc(n.title)}</option>`).join('');chainPreview();$('#chain-dialog').showModal();};
for(const type of ['input','change'])$('#chain-prompts').addEventListener(type,chainPreview);
function promptWord(n){const d=n%10,h=n%100;return d===1&&h!==11?'промт':d>=2&&d<=4&&(h<12||h>14)?'промта':'промтов';}
$('#chain-form').addEventListener('submit',async e=>{
  e.preventDefault();e.submitter.disabled=true;$('#chain-error').textContent='';
  try{const values=Object.fromEntries(new FormData(e.target));const r=await call('map_create_chain',{project:selected,prompts:values.prompts,title:values.title,parent:values.parent||undefined});
    $('#chain-dialog').close();view='map';collapsedBranches.clear();await refresh();notice(`Цепочка создана: ${r.steps.length} ${promptWord(r.steps.length)}. Текущий шаг подсвечен; отправь его в чат Codex.`);}
  catch(err){$('#chain-error').textContent=err.message;}finally{e.submitter.disabled=false;}
});
$('#import-project').onclick=()=>{$('#import-error').textContent='';$('#import-form').reset();importArchive=null;$('#import-dialog').showModal();};
let importArchive=null;
$('#import-file').onchange=async e=>{
  importArchive=null;$('#import-error').textContent='';const file=e.target.files[0];if(!file)return;
  try{importArchive=JSON.parse(await file.text());if(importArchive?.format_version!==1||!importArchive.project)throw new Error('Это не архив pmap формата 1');
    const form=$('#import-form');form.elements.name.value=(importArchive.project.name||'Импорт')+' · копия';form.elements.path.value=importArchive.project.path||'';}
  catch(err){importArchive=null;$('#import-error').textContent=err.message;}
};
$('#import-form').addEventListener('submit',async e=>{
  e.preventDefault();if(!importArchive){$('#import-error').textContent='Выберите файл JSON-архива';return;}
  e.submitter.disabled=true;
  try{const values=Object.fromEntries(new FormData(e.target));const r=await request('/api/import',{archive:importArchive,name:values.name,path:values.path});
    selected=r.id;localStorage.setItem('project-map-selection',selected);$('#import-dialog').close();notice(r.message);await loadProjects();}
  catch(err){$('#import-error').textContent=err.message;}finally{e.submitter.disabled=false;}
});
