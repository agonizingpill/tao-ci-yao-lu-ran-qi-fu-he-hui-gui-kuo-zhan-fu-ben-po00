import unittest

from kiln.model_gates import compare_predictions


class ComparePredictionsTest(unittest.TestCase):
    def test_no_confirmed_batches_blocks(self):
        kilns, batches, failures = compare_predictions([])
        self.assertEqual(kilns, [])
        self.assertEqual(batches, [])
        self.assertEqual([item['code'] for item in failures], ['MISSING_PREDICTION'])

    def test_missing_prediction_blocks(self):
        _, batches, failures = compare_predictions([
            {'code': 'K2-1', 'kiln': 'K2 辊道窑', 'actual_m3': 100.0, 'old_m3': 101.0, 'new_m3': 99.0},
            {'code': 'K2-2', 'kiln': 'K2 辊道窑', 'error': 'ValueError'},
        ])
        self.assertEqual(batches[1]['error'], 'ValueError')
        self.assertEqual([item['code'] for item in failures], ['MISSING_PREDICTION'])
        self.assertEqual(failures[0]['batch'], 'K2-2')

    def test_mae_increase_per_kiln_blocks(self):
        kilns, _, failures = compare_predictions([
            {'code': 'K2-1', 'kiln': 'K2', 'actual_m3': 100.0, 'old_m3': 100.0, 'new_m3': 105.0},
            {'code': 'K1-1', 'kiln': 'K1', 'actual_m3': 50.0, 'old_m3': 52.0, 'new_m3': 51.0},
        ])
        self.assertEqual([item['code'] for item in failures], ['MAE_REGRESSED'])
        self.assertEqual(failures[0]['kiln'], 'K2')
        by_kiln = {item['kiln']: item for item in kilns}
        self.assertEqual(by_kiln['K2']['old_mae'], 0.0)
        self.assertEqual(by_kiln['K2']['new_mae'], 5.0)
        self.assertEqual(by_kiln['K1']['new_mae'], 1.0)

    def test_equal_or_better_passes(self):
        _, _, failures = compare_predictions([
            {'code': 'K2-1', 'kiln': 'K2', 'actual_m3': 100.0, 'old_m3': 102.0, 'new_m3': 101.0},
            {'code': 'K2-2', 'kiln': 'K2', 'actual_m3': 200.0, 'old_m3': 201.0, 'new_m3': 201.0},
        ])
        self.assertEqual(failures, [])


if __name__ == '__main__':
    unittest.main()
