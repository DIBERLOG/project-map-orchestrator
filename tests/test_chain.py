from pathlib import Path
import tempfile
import unittest
from core import Store, split_prompts
from capture import capture


class ChainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'db.sqlite3')
        self.project = self.store.create_project('Chain fixture', str(self.root))['id']

    def tearDown(self):
        self.temp.cleanup()

    def statuses(self, ids):
        nodes = {n['id']: n for n in self.store.snapshot(self.project)['nodes']}
        return [nodes[i]['status'] for i in ids]

    def node(self, id):
        with self.store.connect() as db:
            return self.store.get(db, 'nodes', id)

    def test_splitting_rules(self):
        self.assertEqual(split_prompts('1. Сделай А\n2) Сделай Б\n- Сделай В'), ['Сделай А', 'Сделай Б', 'Сделай В'])
        self.assertEqual(split_prompts('Первый\nещё строка\n\nВторой'), ['Первый\nещё строка', 'Второй'])
        self.assertEqual(split_prompts('Один\n\nабзац\n---\nДва'), ['Один\n\nабзац', 'Два'])
        self.assertEqual(split_prompts('  \n '), [])

    def test_chain_is_linked_in_order_and_first_step_is_current(self):
        chain = self.store.create_chain(self.project, 'Создай модель\nДобавь API\nНапиши тесты', title='Бэкенд')
        root, steps = chain['root'], chain['steps']
        self.assertEqual([self.node(s)['parent'] for s in steps], [root, steps[0], steps[1]])
        self.assertEqual(self.node(steps[1])['title'], '2. Добавь API')
        self.assertEqual(self.node(root)['title'], 'Бэкенд')
        self.assertEqual(self.statuses(steps), ['active', 'planned', 'planned'])
        with self.assertRaises(ValueError):
            self.store.create_chain(self.project, '\n'.join(str(i) + ' шаг' for i in range(41)))
        with self.assertRaises(ValueError):
            self.store.create_chain(self.project, '   ')

    def test_review_moves_to_next_step_and_done_finishes_chain(self):
        chain = self.store.create_chain(self.project, 'A\nB')
        root, (a, b) = chain['root'], chain['steps']
        n = self.node(a)
        self.store.update_node(a, n['version'], status='review')
        self.assertEqual(self.statuses([a, b, root]), ['review', 'active', 'active'])
        self.store.update_node(b, self.node(b)['version'], status='review')
        self.assertEqual(self.node(root)['status'], 'review')
        for id in (a, b):
            self.store.update_node(id, self.node(id)['version'], status='done')
        self.assertEqual(self.node(root)['status'], 'done')
        # Reopening a step reopens the chain.
        self.store.update_node(b, self.node(b)['version'], status='planned')
        self.assertEqual(self.statuses([b, root]), ['active', 'active'])

    def test_codex_hook_advances_chain_prompt_by_prompt(self):
        chain = self.store.create_chain(self.project, 'Сделай форму входа\n---\nДобавь проверку пароля')
        a, b = chain['steps']
        base = {'cwd': str(self.root), 'session_id': 's1'}
        # Sending the second prompt early does not skip the first step.
        response = capture(self.store, base | {'turn_id': 't0', 'hook_event_name': 'UserPromptSubmit', 'prompt': 'Добавь проверку пароля'})
        self.assertNotIn('prompt chain', response['hookSpecificOutput']['additionalContext'])
        self.assertEqual(self.statuses([a, b]), ['active', 'planned'])
        response = capture(self.store, base | {'turn_id': 't1', 'hook_event_name': 'UserPromptSubmit', 'prompt': '## My request:\nсделай  форму входа'})
        self.assertIn(a, response['hookSpecificOutput']['additionalContext'])
        capture(self.store, base | {'turn_id': 't1', 'hook_event_name': 'PostToolUse', 'tool_name': 'apply_patch', 'tool_use_id': 'x'})
        capture(self.store, base | {'turn_id': 't1', 'hook_event_name': 'Stop'})
        capture(self.store, base | {'turn_id': 't1', 'hook_event_name': 'Stop'})
        self.assertEqual(self.statuses([a, b]), ['review', 'active'])
        nodes = self.store.snapshot(self.project)['nodes']
        fix = next(n for n in nodes if n['kind'] == 'fix')
        self.assertEqual(fix['parent'], a)
        capture(self.store, base | {'turn_id': 't2', 'hook_event_name': 'UserPromptSubmit', 'prompt': 'Добавь проверку пароля'})
        capture(self.store, base | {'turn_id': 't2', 'hook_event_name': 'Stop'})
        self.assertEqual(self.statuses([a, b, chain['root']]), ['review', 'review', 'review'])
        events = [e['kind'] for e in self.store.snapshot(self.project)['events']]
        self.assertEqual(events.count('chain.prompt_sent'), 2)


if __name__ == '__main__':
    unittest.main()
