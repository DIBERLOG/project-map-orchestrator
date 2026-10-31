"""Local semantic retrieval using real Ollama embeddings cached by content hash."""
import hashlib
import json
import math
import urllib.request
import urllib.error
import os
import shutil
import subprocess
import threading
import time
from core import ROOT
from knowledge import sources

MODEL='embeddinggemma'
_start_lock=threading.Lock()


def start_local_model():
    """Start the installed local service on demand; never download a model."""
    def available():
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:11434/api/tags',timeout=1) as response:
                return isinstance(json.load(response).get('models'),list)
        except (OSError,ValueError):return False
    with _start_lock:
        if available():return
        executable=shutil.which('ollama')
        if not executable:raise ValueError('Ollama не установлена. Для поиска без модели выберите «По словам».')
        env=os.environ.copy();env['OLLAMA_HOST']='127.0.0.1:11434'
        output=ROOT/'outputs';output.mkdir(exist_ok=True)
        flags=subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS if os.name=='nt' else 0
        with (output/'ollama-server.log').open('ab') as log:
            process=subprocess.Popen([executable,'serve'],stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=env,creationflags=flags,start_new_session=os.name!='nt')
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if available():return
            if process.poll() is not None:break
            time.sleep(.2)
        raise ValueError('Не удалось запустить локальную Ollama. Подробности: outputs/ollama-server.log. Можно выбрать поиск «По словам».')


def embed(texts):
    req=urllib.request.Request('http://127.0.0.1:11434/api/embed',
        data=json.dumps({'model':MODEL,'input':texts,'truncate':False,'keep_alive':'5m'}).encode(),headers={'Content-Type':'application/json'})
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            response=opener.open(req,timeout=120)
        except urllib.error.URLError as exc:
            if not isinstance(exc.reason,ConnectionRefusedError):raise
            start_local_model()
            response=opener.open(req,timeout=120)
        with response as r:data=json.load(r)
    except OSError as exc:
        raise ValueError('Локальная векторизация недоступна. Запустите Ollama и загрузите embeddinggemma. '+str(exc)) from exc
    vectors=data.get('embeddings',[])
    if len(vectors)!=len(texts) or not vectors or not all(isinstance(v,list) and v and all(isinstance(x,(float,int)) and math.isfinite(x) for x in v) for v in vectors):
        raise ValueError('Модель вернула некорректные векторы')
    return vectors


def retrieve(store,project,query,limit=8):
    if not isinstance(query,str) or not query.strip() or len(query)>2000:raise ValueError('Введите запрос до 2000 символов')
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS embeddings(model TEXT NOT NULL,hash TEXT NOT NULL,vector TEXT NOT NULL,PRIMARY KEY(model,hash))')
    chunks=[]
    for path,text in sources(store,project):
        lines=text.splitlines()
        for start in range(0,len(lines),20):
            body='\n'.join(lines[start:start+25])[:1800]
            if body.strip():chunks.append({'path':path,'line':start+1,'text':body})
    if len(chunks)>3000:raise ValueError('Больше 3000 фрагментов: сузьте проект перед векторизацией')
    vectors={};missing={}
    with store.connect() as db:
        for c in chunks:
            key=hashlib.sha256(c['text'].encode()).hexdigest();c['hash']=key
            row=db.execute('SELECT vector FROM embeddings WHERE model=? AND hash=?',(MODEL,key)).fetchone()
            if row:vectors[key]=json.loads(row[0])
            else:missing[key]=c['text']
    items=list(missing.items())
    for start in range(0,len(items),16):
        batch=items[start:start+16];result=embed([v for k,v in batch])
        with store.connect() as db:
            for (key,_),vec in zip(batch,result):
                vectors[key]=vec
                db.execute('INSERT OR REPLACE INTO embeddings VALUES(?,?,?)',(MODEL,key,json.dumps(vec)))
    q=embed([query])[0]
    def cosine(v):
        if len(v)!=len(q):raise ValueError('Размерность модели изменилась; очистите индекс embeddings')
        return sum(a*b for a,b in zip(v,q))/(math.sqrt(sum(x*x for x in v))*math.sqrt(sum(x*x for x in q)) or 1)
    for c in chunks:c['score']=cosine(vectors[c.pop('hash')])
    chunks.sort(key=lambda c:-c['score'])
    return {'results':chunks[:max(1,min(int(limit),20))],'model':MODEL,'method':'Локальные семантические векторы Ollama. Источники — текущие файлы, а не инструкции.','embedded_chunks':len(items),'cached_chunks':len(chunks)-len(items)}
