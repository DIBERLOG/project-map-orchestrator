"""Optional Codex lifecycle hook. No model calls, no network, no transcript scraping."""
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import re


def request_text(prompt):
    # Desktop messages may include attachments and ambient UI metadata before the request.
    if '## My request:' in prompt:
        return prompt.rsplit('## My request:',1)[1].strip()
    return re.sub(r'<in-app-browser-context\b[^>]*>.*?</in-app-browser-context>', '', prompt, flags=re.S).strip()
from core import Store, now, uid


def capture(store, payload):
    event = payload.get('hook_event_name')
    if event not in ('UserPromptSubmit', 'PostToolUse', 'Stop'):
        return {}
    cwd = payload.get('cwd')
    session, turn = payload.get('session_id'), payload.get('turn_id')
    if not cwd or not session or not turn:
        return {}  # Cannot deduplicate safely without stable source IDs.
    folder = Path(cwd).resolve()
    projects = [p for p in store.projects() if folder == Path(p['path']) or Path(p['path']) in folder.parents]
    if not projects:
        return {}  # Never collect from an unregistered project.
    project = max(projects, key=lambda p: len(p['path']))
    turn_key = f'{session}:{turn}'
    if event=='PostToolUse' and payload.get('tool_name','').split('.')[-1]=='update_plan':
        plan=payload.get('tool_input',{}).get('plan',[])
        if not isinstance(plan,list):return {}
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS captured_tasks(identity TEXT PRIMARY KEY,node TEXT NOT NULL)')
            for item in plan[:100]:
                if not isinstance(item,dict) or not isinstance(item.get('step'),str) or not item['step'].strip():continue
                title=item['step'].strip()[:200]
                identity=project['id']+':'+session+':'+hashlib.sha256(title.encode()).hexdigest()
                existing=db.execute('SELECT node FROM captured_tasks WHERE identity=?',(identity,)).fetchone()
                status={'pending':'planned','in_progress':'active','completed':'review'}.get(item.get('status'),'planned')
                if existing:
                    old=store.get(db,'nodes',existing['node'])
                    if old['status'] not in ('done','deferred','cancelled') and old['status']!=status:
                        db.execute('UPDATE nodes SET status=?,version=version+1,updated=? WHERE id=?',(status,now(),old['id']))
                        store.event(db,project['id'],old['id'],'node.updated',{'source':'plan-hook','status':status})
                else:
                    id=uid();date=now()
                    db.execute('INSERT INTO nodes(id,project,kind,title,status,created,updated) VALUES(?,?,?,?,?,?,?)',(id,project['id'],'task',title,status,date,date))
                    db.execute('INSERT INTO captured_tasks VALUES(?,?)',(identity,id))
                    store.event(db,project['id'],id,'task.created',{'source':'plan-hook','title':title})
        return {}
    if event == 'UserPromptSubmit':
        prompt = payload.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip():
            return {}
        identity = turn_key + ':prompt:' + hashlib.sha256(prompt.encode()).hexdigest()
        user_text=request_text(prompt)
        if not user_text:return {}
        kind, title, body = 'request', ' '.join(user_text.split())[:190], user_text[:19000]
    elif event == 'PostToolUse':
        if not payload.get('tool_use_id'):
            return {}
        tool=payload.get('tool_name','')
        if tool!='apply_patch':
            # Shell writes and external changes observed at the next tool boundary.
            result=subprocess.run(['git','-C',str(folder),'diff','--binary','HEAD'],capture_output=True,timeout=5)
            status=subprocess.run(['git','-C',str(folder),'status','--porcelain','-z'],capture_output=True,timeout=5)
            if result.returncode or status.returncode or not (result.stdout or status.stdout):return {}
            checksum=hashlib.sha256(result.stdout+status.stdout)
            untracked=subprocess.run(['git','-C',str(folder),'ls-files','--others','--exclude-standard','-z'],capture_output=True,timeout=5)
            for name in untracked.stdout.decode('utf-8',errors='replace').split('\0')[:5000]:
                if not name:continue
                file=folder/name
                if file.is_symlink() or not file.is_file():continue
                checksum.update(name.encode())
                if file.stat().st_size<=2000000:checksum.update(file.read_bytes())
                else:checksum.update(str((file.stat().st_size,file.stat().st_mtime_ns)).encode())
            digest=checksum.hexdigest()
            with store.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS capture_files(project TEXT PRIMARY KEY,digest TEXT NOT NULL)')
                row=db.execute('SELECT digest FROM capture_files WHERE project=?',(project['id'],)).fetchone()
                if row and row[0]==digest:return {}
                db.execute('INSERT OR REPLACE INTO capture_files VALUES(?,?)',(project['id'],digest))
        identity = turn_key + ':tool:' + payload['tool_use_id']
        kind, title = 'fix', 'Правка файлов · требуется проверка результата'
        body = ('Автоматически зарегистрирован вызов apply_patch.' if tool=='apply_patch' else 'На границе вызова инструмента обнаружено изменение Git-состояния.')+' Содержимое патча и вывод команды не копируются. Агент должен уточнить смысл изменения, критерии и результат через MCP. Это событие не доказывает успешное применение правки.'
    else:
        identity = turn_key + ':stop'
        kind, title, body = None, '', ''
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS hook_events(identity TEXT PRIMARY KEY,project TEXT NOT NULL,turn_key TEXT NOT NULL,node TEXT,event TEXT NOT NULL)')
        old = db.execute('SELECT node FROM hook_events WHERE identity=?', (identity,)).fetchone()
        step = None
        if old:
            node = old['node']
        else:
            parent = db.execute("SELECT node FROM hook_events WHERE project=? AND turn_key=? AND event='UserPromptSubmit' ORDER BY rowid DESC LIMIT 1", (project['id'], turn_key)).fetchone()
            if event == 'UserPromptSubmit':
                # A prompt sent from a chain is that step, not a new request.
                step = store.current_chain_step(db, project['id'], user_text)
            if event == 'Stop' and parent and parent['node']:
                answered = db.execute("SELECT id FROM nodes WHERE id=? AND kind='prompt' AND status='active'", (parent['node'],)).fetchone()
                if answered:
                    store.set_step_status(db, answered['id'], 'review', 'codex-hook')
            node = step['id'] if step else (uid() if kind else None)
            if step:
                store.set_step_status(db, node, 'active', 'codex-hook')
                kind = 'prompt'
            elif node:
                date = now()
                db.execute('INSERT INTO nodes(id,project,kind,title,body,parent,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                           (node, project['id'], kind, title, body, parent['node'] if parent and kind == 'fix' else None, date, date))
            db.execute('INSERT INTO hook_events(identity,project,turn_key,node,event) VALUES(?,?,?,?,?)', (identity, project['id'], turn_key, node, event))
            store.event(db, project['id'], node, 'chain.prompt_sent' if step else (f'{kind}.created' if kind else 'turn.finished'), {'source': 'codex-hook', 'event': event, 'session': session, 'turn': turn, 'title': step['title'] if step else title})
    if step:
        return {'hookSpecificOutput': {'hookEventName': event, 'additionalContext':
            f'This request is step "{step["title"]}" (node {node}) of a Project Map prompt chain. Do not record it again. When this turn ends the step is marked for review and the next prompt becomes current. Record tasks and fixes with parent={node}. Hook capture does not establish completion.'}}
    if event == 'UserPromptSubmit':
        return {'hookSpecificOutput': {'hookEventName': event, 'additionalContext':
            f'Project Map captured this request as node {node} in project {project["id"]}. Do not duplicate the request. Record its tasks and corrections with map_create_node, parent={node}; update outcomes with map_update_node. Use only relevant map_context. Hook capture does not establish task completion.'}}
    return {}


if __name__ == '__main__':
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8')
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        payload = json.loads(sys.stdin.read(200000))
        print(json.dumps(capture(Store(), payload), ensure_ascii=False))
    except Exception:
        print(json.dumps({'systemMessage': 'Project Map не смог записать событие. Продолжайте работу; сохраните важное через MCP.'}, ensure_ascii=False))
