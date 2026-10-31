"""Combine worker patches in an isolated integration worktree, without commits."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from core import uid, now, fingerprint, execute


def git(folder, *args, **kwargs):
    result=subprocess.run(['git','-C',str(folder),*args],capture_output=True,timeout=60,**kwargs)
    if result.returncode:
        raise ValueError(result.stderr.decode('utf-8',errors='replace')[-3000:])
    return result.stdout


def setup(store):
    with store.connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS integrations(id TEXT PRIMARY KEY,project TEXT NOT NULL REFERENCES projects(id),nodes TEXT NOT NULL,path TEXT NOT NULL,state TEXT NOT NULL,base TEXT NOT NULL,output TEXT NOT NULL DEFAULT '',fingerprint TEXT NOT NULL DEFAULT '',command TEXT NOT NULL DEFAULT '',created TEXT NOT NULL)''')


def patch(folder):
    # A private index includes new files without touching the worker's staged state.
    with tempfile.TemporaryDirectory() as temp:
        env=os.environ.copy();env['GIT_INDEX_FILE']=str(Path(temp)/'index')
        git(folder,'read-tree','HEAD',env=env)
        git(folder,'add','-A','--','.',env=env)
        return git(folder,'diff','--cached','--binary','HEAD',env=env)


def prepare(store,project,nodes):
    setup(store)
    ids=list(dict.fromkeys(x.strip() for x in nodes.split(',') if x.strip()))
    if not ids or len(ids)>20:raise ValueError('Нужно от 1 до 20 ID задач через запятую')
    with store.connect() as db:
        p=store.get(db,'projects',project)
        selected=[store.get(db,'nodes',id) for id in ids]
        for n in selected:
            if n['project']!=project or n['status'] not in ('review','done') or not n['worktree']:
                raise ValueError('Нужны проверяемые результаты из рабочих копий этого проекта')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='jobs'").fetchone():
            for id in ids:
                if db.execute("SELECT 1 FROM jobs WHERE node=? AND state IN ('queued','running','orphaned')",(id,)).fetchone():
                    raise ValueError('Сначала завершите исполнителей')
    if git(p['path'],'status','--porcelain').strip():raise ValueError('Исходная папка должна быть чистой')
    base=git(p['path'],'rev-parse','HEAD').decode().strip()
    for n in selected:
        if git(n['worktree'],'rev-parse','HEAD').decode().strip()!=base:
            raise ValueError('Рабочие копии и исходный проект должны иметь общую исходную версию HEAD')
    patches=[patch(n['worktree']) for n in selected]
    if not any(patches):raise ValueError('Нет изменений для объединения')
    id=uid();folder=store.path.parent/'integrations'/id
    folder.parent.mkdir(parents=True,exist_ok=True)
    git(p['path'],'worktree','add','-b','codex/integration-'+id,str(folder),base)
    state='prepared';output=''
    for n,content in zip(selected,patches):
        if not content:continue
        result=subprocess.run(['git','-C',str(folder),'apply','--3way','--index','-'],input=content,capture_output=True,timeout=60)
        if result.returncode:
            state='conflict';output=n['title']+'\n'+result.stderr.decode('utf-8',errors='replace');break
    with store.connect() as db:
        db.execute('INSERT INTO integrations(id,project,nodes,path,state,base,output,created) VALUES(?,?,?,?,?,?,?,?)',
                   (id,project,json.dumps(ids),str(folder),state,base,output,now()))
        store.event(db,project,None,'integration.prepared',{'id':id,'state':state,'path':str(folder),'nodes':ids})
    return {'id':id,'path':str(folder),'state':state,'output':output,'base':base}


def check(store,id):
    setup(store)
    with store.connect() as db:
        row=db.execute('SELECT * FROM integrations WHERE id=?',(id,)).fetchone()
        if not row:raise ValueError('Сборка не найдена')
        p=store.get(db,'projects',row['project'])
    folder=Path(row['path'])
    if git(folder,'ls-files','--unmerged').strip():raise ValueError('Сначала разрешите конфликты в рабочей копии сборки')
    command=json.loads(p['runner'])
    if not command:raise ValueError('Не настроена команда проверки проекта')
    stamp=fingerprint(folder)
    code,output=execute(command,folder,60)
    state='passed' if code==0 and stamp==fingerprint(folder) else 'failed'
    with store.connect() as db:
        db.execute('UPDATE integrations SET state=?,fingerprint=?,command=?,output=? WHERE id=?',(state,stamp,p['runner'],output,id))
        store.event(db,p['id'],None,'integration.checked',{'id':id,'state':state,'code':code})
    return {'id':id,'state':state,'output':output,'fingerprint':stamp,'path':str(folder)}


def listing(store,project):
    setup(store)
    with store.connect() as db:
        result=[dict(r) for r in db.execute('SELECT * FROM integrations WHERE project=? ORDER BY created DESC',(project,))]
        command=store.get(db,'projects',project)['runner']
    for row in result:
        if row['state']=='passed':
            try:
                if command!=row['command'] or fingerprint(Path(row['path']))!=row['fingerprint']:row['state']='stale'
            except OSError:row['state']='unavailable'
    return result


def apply(store,id):
    """Explicit local application; requires a current receipt and untouched source."""
    setup(store)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM integrations WHERE id=?',(id,)).fetchone()
        if not row or row['state']!='passed':raise ValueError('Нужна успешно проверенная сборка')
        p=store.get(db,'projects',row['project'])
        if row['command']!=p['runner'] or fingerprint(Path(row['path']))!=row['fingerprint']:
            raise ValueError('Проверка устарела: повторите общий прогон')
        if git(p['path'],'rev-parse','HEAD').decode().strip()!=row['base']:
            raise ValueError('Исходная версия проекта изменилась: создайте новую сборку')
        if git(p['path'],'status','--porcelain').strip():raise ValueError('В исходном проекте есть несохранённые изменения')
        content=patch(row['path'])
        if not content:raise ValueError('Сборка не содержит изменений')
        git(p['path'],'apply','--check','--index','-',input=content)
        git(p['path'],'apply','--index','-',input=content)
        db.execute("UPDATE integrations SET state='applied' WHERE id=?",(id,))
        store.event(db,p['id'],None,'integration.applied',{'id':id,'base':row['base'],'path':p['path']})
    return {'id':id,'state':'applied','path':p['path'],'message':'Изменения применены локально и находятся в Git index. Коммит и публикация не выполнялись.'}
