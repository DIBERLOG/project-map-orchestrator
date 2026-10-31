"""Start the local workspace once and open it only after readiness is confirmed."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT=Path(__file__).resolve().parent


def ready(port):
    try:
        # Do not route localhost through proxy environment variables.
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/api/health',timeout=1) as response:
            result=json.load(response)
        return result.get('app')=='pmap' and result.get('root')==str(ROOT)
    except (OSError,ValueError):
        return False


def ensure_server(port=4317,timeout=20):
    if ready(port):return None
    # Never stop another app or attach a browser to an unrelated local server.
    with socket.socket() as connection:
        occupied=connection.connect_ex(('127.0.0.1',port))==0
    if occupied:
        # Another copy of the launcher might have just started our server.
        for _ in range(10):
            if ready(port):return None
            time.sleep(.2)
        raise RuntimeError(f'Порт {port} занят, но pmap не отвечает. Закройте прежний сервер или проверьте outputs/server-launch.log.')
    output=ROOT/'outputs';output.mkdir(exist_ok=True)
    executable=Path(sys.executable)
    if executable.name.lower()=='pythonw.exe':executable=executable.with_name('python.exe')
    flags=(subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW) if sys.platform=='win32' else 0
    with (output/'server-launch.log').open('ab') as log:
        process=subprocess.Popen([str(executable),str(ROOT/'app.py'),'serve','--port',str(port)],cwd=ROOT,
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=flags,start_new_session=sys.platform!='win32')
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if ready(port):return process
        if process.poll() is not None:
            # A simultaneous launcher can lose the port race to the first server.
            if ready(port):return None
            raise RuntimeError('pmap не запустился. Подробности: outputs/server-launch.log')
        time.sleep(.2)
    raise RuntimeError('pmap ещё не ответил. Проверьте outputs/server-launch.log и попробуйте открыть карту снова.')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--port',type=int,default=4317)
    args=parser.parse_args()
    try:
        ensure_server(args.port)
        if not args.no_browser:webbrowser.open(f'http://127.0.0.1:{args.port}/')
        if sys.stdout:print('pmap ready')
    except Exception as exc:
        (ROOT/'outputs').mkdir(exist_ok=True)
        with (ROOT/'outputs/launcher-error.log').open('a',encoding='utf-8') as log:log.write(str(exc)+'\n')
        if sys.platform=='win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,str(exc),'Запуск pmap',0x10)
        elif sys.stderr:print(str(exc),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
