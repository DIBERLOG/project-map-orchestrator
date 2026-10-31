import threading
from pathlib import Path
import sys
import unittest
from unittest.mock import patch as mockpatch
import test_jobs
import integration
import semantic


class IntegrationTests(unittest.TestCase):
    setUp=test_jobs.WorkerTests.setUp
    tearDown=test_jobs.WorkerTests.tearDown
    node=test_jobs.WorkerTests.node

    def reviewed(self,scope):
        n=self.node(scope)
        current=self.store.snapshot(self.project)['nodes'][-1]
        self.store.update_node(n['id'],current['version'],status='review')
        return next(x for x in self.store.snapshot(self.project)['nodes'] if x['id']==n['id'])

    def test_combine_and_check_without_modifying_source(self):
        a,b=self.reviewed('a.txt'),self.reviewed('b.txt')
        Path(a['worktree'],'a.txt').write_text('A')
        Path(b['worktree'],'b.txt').write_text('B')
        result=integration.prepare(self.store,self.project,a['id']+','+b['id'])
        self.assertEqual(result['state'],'prepared')
        self.assertEqual(Path(result['path'],'a.txt').read_text(),'A')
        self.assertEqual(Path(result['path'],'b.txt').read_text(),'B')
        self.assertEqual((self.repo/'a.txt').read_text(),'old')
        self.store.set_runner(self.project,[sys.executable,'-c','from pathlib import Path; assert Path("a.txt").read_text()=="A"; assert Path("b.txt").read_text()=="B"'])
        self.assertEqual(integration.check(self.store,result['id'])['state'],'passed')
        Path(result['path'],'a.txt').write_text('changed')
        self.assertEqual(integration.listing(self.store,self.project)[0]['state'],'stale')

    def test_conflict_is_retained_and_not_accepted(self):
        a,b=self.reviewed('a.txt'),self.reviewed('a.txt')
        Path(a['worktree'],'a.txt').write_text('A\n')
        Path(b['worktree'],'a.txt').write_text('B\n')
        result=integration.prepare(self.store,self.project,a['id']+','+b['id'])
        self.assertEqual(result['state'],'conflict')
        with self.assertRaisesRegex(ValueError,'конфликты'):integration.check(self.store,result['id'])
        self.assertEqual((self.repo/'a.txt').read_text(),'old')

    def test_new_file_and_private_index(self):
        n=self.reviewed('new.txt')
        Path(n['worktree'],'new.txt').write_text('new')
        before=integration.git(n['worktree'],'diff','--cached')
        result=integration.prepare(self.store,self.project,n['id'])
        self.assertEqual(Path(result['path'],'new.txt').read_text(),'new')
        self.assertEqual(before,integration.git(n['worktree'],'diff','--cached'))

    def test_apply_requires_fresh_receipt_and_clean_source(self):
        n=self.reviewed('a.txt');Path(n['worktree'],'a.txt').write_text('A')
        result=integration.prepare(self.store,self.project,n['id'])
        with self.assertRaises(ValueError):integration.apply(self.store,result['id'])
        self.store.set_runner(self.project,[sys.executable,'-c','print("checked")'])
        integration.check(self.store,result['id'])
        (self.repo/'b.txt').write_text('user draft')
        with self.assertRaisesRegex(ValueError,'изменения'):integration.apply(self.store,result['id'])
        self.assertEqual((self.repo/'b.txt').read_text(),'user draft')
        (self.repo/'b.txt').write_text('old')
        applied=integration.apply(self.store,result['id'])
        self.assertEqual(applied['state'],'applied')
        self.assertEqual((self.repo/'a.txt').read_text(),'A')
        self.assertTrue(integration.git(self.repo,'diff','--cached'))

    def test_semantic_cache_and_current_sources(self):
        (self.repo/'notes.md').write_text('unusual content',encoding='utf-8')
        calls=[]
        def encode(texts):
            calls.append(texts)
            return [[1.,0.] if t=='different words' or 'unusual' in t else [0.,1.] for t in texts]
        with mockpatch.object(semantic,'embed',encode):
            result=semantic.retrieve(self.store,self.project,'different words')
            self.assertEqual(result['results'][0]['path'],'notes.md')
            result=semantic.retrieve(self.store,self.project,'different words')
            self.assertEqual(result['embedded_chunks'],0)
            (self.repo/'notes.md').unlink()
            self.assertNotIn('notes.md',[x['path'] for x in semantic.retrieve(self.store,self.project,'different words')['results']])
