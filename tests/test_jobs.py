import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from core import Store
import jobs


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root/'repo'; self.repo.mkdir()
        (self.repo/'a.txt').write_text('old')
        (self.repo/'b.txt').write_text('old')
        def git(*args):
            subprocess.run(['git','-C',str(self.repo),*args],capture_output=True,check=True)
        git('init'); git('add','.'); git('-c','user.name=Test','-c','user.email=t@example.invalid','commit','-m','fixture')
        self.store = Store(self.root/'map.sqlite3')
        self.project = self.store.create_project('test',str(self.repo))['id']
        jobs.configure(self.store,self.project,[sys.executable,'-c','import sys; print(sys.stdin.read())'],2,5)

    def tearDown(self):
        self.tmp.cleanup()

    def node(self, scope='a.txt'):
        n=self.store.create_node(self.project,'task','Change',criteria='Check it',scope=scope)
        self.store.worktree(n['id'])
        return n

    def test_parallel_scope_reservation_and_persistence(self):
        a,b,c=self.node(),self.node(),self.node('b.txt')
        for n in (a,b,c): jobs.enqueue(self.store,n['id'])
        first=jobs.claim(self.store);second=jobs.claim(self.store)
        self.assertEqual({first['node'],second['node']},{a['id'],c['id']})
        self.assertIsNone(jobs.claim(self.store))
        self.assertEqual(len(jobs.list_jobs(Store(self.store.path),self.project)),3)

    def test_success_is_review_never_done(self):
        n=self.node();jobs.enqueue(self.store,n['id']);job=jobs.claim(self.store)
        jobs.perform(self.store,job,threading.Event())
        result=jobs.list_jobs(self.store,self.project)[0]
        self.assertEqual(result['state'],'review')
        self.assertIn('Критерии',result['output'])
        self.assertEqual(self.store.snapshot(self.project)['nodes'][0]['status'],'review')

    def test_cancel_queued_and_running(self):
        n=self.node();job=jobs.enqueue(self.store,n['id']);jobs.cancel(self.store,job['id'])
        self.assertIsNone(jobs.claim(self.store))
        jobs.configure(self.store,self.project,[sys.executable,'-c','import time; time.sleep(20)'],1,5)
        job=jobs.enqueue(self.store,n['id']); claimed=jobs.claim(self.store)
        thread=threading.Thread(target=jobs.perform,args=(self.store,claimed,threading.Event()));thread.start()
        jobs.cancel(self.store,job['id']);thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(jobs.list_jobs(self.store,self.project)[0]['state'],'cancelled')

    def test_timeout_and_scope_violation(self):
        n=self.node()
        jobs.configure(self.store,self.project,[sys.executable,'-c','from pathlib import Path; Path("b.txt").write_text("bad")'],1,5)
        jobs.enqueue(self.store,n['id']);jobs.perform(self.store,jobs.claim(self.store),threading.Event())
        self.assertEqual(jobs.list_jobs(self.store,self.project)[0]['state'],'scope_violation')
        n=self.node()
        jobs.configure(self.store,self.project,[sys.executable,'-c','import time; time.sleep(20)'],1,1)
        jobs.enqueue(self.store,n['id']);jobs.perform(self.store,jobs.claim(self.store),threading.Event())
        self.assertEqual(jobs.list_jobs(self.store,self.project)[0]['state'],'timed_out')

    def test_dependencies_and_duplicate_queue(self):
        a,b=self.node(),self.node('b.txt');self.store.depend(a['id'],b['id'])
        jobs.enqueue(self.store,a['id'])
        with self.assertRaises(ValueError):jobs.enqueue(self.store,a['id'])
        self.assertIsNone(jobs.claim(self.store))

    def test_acceptance_blocked_while_job_is_pending(self):
        n=self.node();jobs.enqueue(self.store,n['id'])
        current=self.store.snapshot(self.project)['nodes'][0]
        with self.assertRaisesRegex(ValueError,'исполнителя'):
            self.store.update_node(n['id'],current['version'],status='done',evidence='claim')

    def test_coordinator_shutdown_preserves_queue(self):
        n=self.node();jobs.enqueue(self.store,n['id'])
        stopping=threading.Event();stopping.set();jobs.coordinate(self.store,stopping)
        self.assertEqual(jobs.list_jobs(self.store,self.project)[0]['state'],'queued')

    def test_cancel_and_defer_queued_tasks_then_resume(self):
        for status in ('cancelled','deferred'):
            n=self.node();jobs.enqueue(self.store,n['id'])
            with self.store.connect() as db:current=self.store.get(db,'nodes',n['id'])
            updated=self.store.update_node(n['id'],current['version'],status=status)
            self.assertEqual(jobs.list_jobs(self.store,self.project)[0]['state'],'cancelled')
            self.assertIsNone(jobs.claim(self.store))
            with self.assertRaises(ValueError):jobs.enqueue(self.store,n['id'])
            self.store.update_node(n['id'],updated['version'],status='planned')
            jobs.enqueue(self.store,n['id'])
            queued=jobs.list_jobs(self.store,self.project)[0]
            jobs.cancel(self.store,queued['id'])

    def test_active_worker_stops_without_overwriting_user_status(self):
        for status in ('cancelled','deferred'):
            n=self.node()
            jobs.configure(self.store,self.project,[sys.executable,'-c','import time; time.sleep(20)'],1,10)
            jobs.enqueue(self.store,n['id']);job=jobs.claim(self.store)
            worker=threading.Thread(target=jobs.perform,args=(self.store,job,threading.Event()));worker.start()
            try:
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    if jobs.list_jobs(self.store,self.project)[0]['pid']:break
                    time.sleep(.02)
                with self.store.connect() as db:current=self.store.get(db,'nodes',n['id'])
                self.store.update_node(n['id'],current['version'],status=status)
                worker.join(10)
                self.assertFalse(worker.is_alive())
                with self.store.connect() as db:after=self.store.get(db,'nodes',n['id'])
                self.assertEqual(after['status'],status)
                self.assertEqual(jobs.list_jobs(self.store,self.project)[0]['state'],'cancelled')
            finally:
                if worker.is_alive():jobs.cancel(self.store,job['id']);worker.join(10)

    def test_changed_contract_invalidates_queued_job(self):
        n=self.node();jobs.enqueue(self.store,n['id'])
        current=self.store.snapshot(self.project)['nodes'][0]
        self.store.update_node(n['id'],current['version'],criteria='Changed')
        self.assertIsNone(jobs.claim(self.store))
        self.assertEqual(jobs.list_jobs(self.store,self.project)[0]['state'],'stale')

    def test_live_or_unknown_orphan_is_not_restarted(self):
        import os
        n=self.node();job=jobs.enqueue(self.store,n['id']);jobs.claim(self.store)
        stop=threading.Event();stop.set();jobs.coordinate(self.store,stop)
        self.assertEqual(jobs.list_jobs(self.store,self.project)[0]['state'],'orphaned')
        with self.assertRaisesRegex(ValueError,'PID'):jobs.recover(self.store,job['id'])
        with self.store.connect() as db:db.execute('UPDATE jobs SET pid=? WHERE id=?',(os.getpid(),job['id']))
        with self.assertRaisesRegex(ValueError,'PID'):jobs.recover(self.store,job['id'])

    def test_real_parallel_processes_and_usage(self):
        script='import time,json; time.sleep(1); print(json.dumps({"type":"turn.completed","usage":{"input_tokens":123,"output_tokens":8}}))'
        jobs.configure(self.store,self.project,[sys.executable,'-c',script],2,5)
        a,b=self.node(),self.node('b.txt')
        jobs.enqueue(self.store,a['id']);jobs.enqueue(self.store,b['id'])
        stop=threading.Event()
        thread=threading.Thread(target=jobs.coordinate,args=(self.store,stop));thread.start()
        try:
            deadline=time.monotonic()+8
            observed_parallel=False
            while time.monotonic()<deadline:
                rows=jobs.list_jobs(self.store,self.project)
                if sum(r['state']=='running' and r['pid'] is not None for r in rows)==2:observed_parallel=True
                if all(r['state']=='review' for r in rows):break
                time.sleep(.05)
            self.assertTrue(observed_parallel)
            self.assertTrue(all(r['state']=='review' for r in rows))
            self.assertEqual(json.loads(rows[0]['usage'])['input_tokens'],123)
        finally:
            stop.set();thread.join(10)
        self.assertFalse(thread.is_alive())
