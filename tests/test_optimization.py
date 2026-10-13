import hashlib
import json
import random
import tempfile
import unittest
from pathlib import Path

from grapholog.model import File, digest, shingles
from grapholog.scan import scan
from grapholog.compare import compare, compatible
from grapholog.similarity import search


class OptimizationTests(unittest.TestCase):
    def test_fingerprints_match_v1_byte_contract(self):
        rng = random.Random(419)
        alphabet = ['(', ')', 'x', '10', '1000', '\\', '"', '\n', '\x00', 'zażółć', '😀', '\u2028']
        for size in (0, 1, 4, 5, 6, 80, 1000):
            tokens = [rng.choice(alphabet) for _ in range(size)]
            for width in (1, 5, 10):
                expected = sorted({digest(tokens[i:i+width])[:16]
                    for i in range(max(1, len(tokens)-width+1))}) if tokens else []
                self.assertEqual(shingles(tokens, width), expected)

    def test_index_matches_brute_force_with_pair_budgets(self):
        rng = random.Random(391)
        def files(prefix):
            return [File(f'{prefix}{i:03}', language='js', parser='p' if i % 5 else 'q',
                status='parsed' if i % 7 else 'parse-error', nodes=20,
                fingerprints=sorted(rng.sample([f'{n:03}' for n in range(70)], rng.randint(1, 65))))
                for i in range(40)]
        left, right = files('a'), files('b')
        right[1].fingerprints = left[1].fingerprints.copy()
        for threshold in (0, .1, 1/3, .7, .9999999999999999, 1):
            for limit in (0, 1, 33, 700, 10000):
                expected, seen = [], 0
                for a in left:
                    for b in right:
                        if not compatible(a, b) or seen >= limit:
                            continue
                        seen += 1
                        one, two = set(a.fingerprints), set(b.fingerprints)
                        score = len(one & two) / len(one | two)
                        if score >= threshold:
                            expected.append({'left':a.path,'right':b.path,'score':round(score,4),
                                'kind':'syntax-shingle-candidate'})
                actual, stats = search(left, right, threshold, limit)
                self.assertEqual(actual, expected, (threshold, limit))
                self.assertEqual(stats['considered'], seen)
                self.assertEqual(stats['verified'] + stats['pruned'], seen)
                self.assertEqual(stats['eligible'], sum(compatible(a,b) for a in left for b in right))

    def test_literals_indentation_and_async_are_not_discarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp)/'a', Path(tmp)/'b'
            a.mkdir(); b.mkdir()
            samples = {
                'number.py': ('def f(x):\n    return x < 10\n', 'def f(x):\n    return x < 1000\n'),
                'string.py': ('def f():\n    return "allow"\n', 'def f():\n    return "deny"\n'),
                'indent.py': ('def f(x):\n    if x:\n        x += 1\n        return x\n',
                              'def f(x):\n    if x:\n        x += 1\n    return x\n'),
                'async.py': ('async def f(x):\n    return x + 1\n', 'async def f(x):\n    return x - 1\n'),
                'nested.py': ('def f():\n    def g():\n        return 1\n    return g()\n',
                              'def f():\n    def g():\n        return 2\n    return g()\n'),
            }
            for name, (old, new) in samples.items():
                (a/name).write_text(old); (b/name).write_text(new)
            left, right = scan(a), scan(b)
            self.assertTrue(all(p['status']=='changed' for p in compare(left,right)['pairs']))
            nested = next(f for f in left.files if f.path=='nested.py')
            self.assertEqual([s.qualname for s in nested.symbols], ['f', 'f.g'])


if __name__ == '__main__': unittest.main()
