import unittest
from collections import Counter
from probes.cost_pilot import select_instances


class SamplingTests(unittest.TestCase):
    def data(self):
        counts = {"django":231,"sympy":75,"sphinx":44,"matplotlib":34,"sklearn":32,
                  "astropy":22,"xarray":22,"pytest":19,"pylint":10,"requests":8,
                  "seaborn":2,"flask":1}
        return [{"repo":repo,"instance_id":f"{repo}-{i}"} for repo,n in counts.items() for i in range(n)]

    def test_covers_all_repos_without_dropping_singleton(self):
        rows=select_instances(self.data())
        counts=Counter(row["repo"] for row in rows)
        self.assertEqual(len(rows),24)
        self.assertEqual(len(counts),12)
        self.assertEqual(counts["flask"],1)
        self.assertEqual(counts["django"],3)
        self.assertEqual(len({row["instance_id"] for row in rows}),24)

    def test_selection_independent_of_input_order_and_results(self):
        data=self.data()
        first=select_instances(data)
        for row in data:
            row["resolved"]=True
        self.assertEqual([r["instance_id"] for r in first],
                         [r["instance_id"] for r in select_instances(list(reversed(data)))])

    def test_cannot_expand_into_full_run(self):
        with self.assertRaises(ValueError):
            select_instances(self.data(),count=500)


if __name__=="__main__":
    unittest.main()
