import unittest

from sima_parity.geometry import Box, LetterboxTransform


class LetterboxTransformTests(unittest.TestCase):
    def test_landscape_transform_round_trip(self):
        transform = LetterboxTransform.create(1280, 720)
        self.assertEqual(transform.pad_top, 140.0)
        source = Box(100, 50, 300, 200)
        restored = transform.to_source(transform.to_model(source))
        self.assertAlmostEqual(restored.x, source.x)
        self.assertAlmostEqual(restored.y, source.y)
        self.assertAlmostEqual(restored.width, source.width)
        self.assertAlmostEqual(restored.height, source.height)

    def test_square_transform_round_trip(self):
        transform = LetterboxTransform.create(2000, 2000)
        self.assertEqual(transform.pad_left, 0.0)
        self.assertEqual(transform.pad_top, 0.0)
        source = Box(200, 400, 800, 600)
        self.assertEqual(transform.to_source(transform.to_model(source)), source)

    def test_inverse_clips_only_after_mapping(self):
        transform = LetterboxTransform.create(1280, 720)
        source = transform.to_source(Box(-20, 100, 100, 200))
        self.assertEqual(source.x, 0.0)
        self.assertGreater(source.width, 0.0)

    def test_invalid_dimensions_fail(self):
        with self.assertRaises(ValueError):
            LetterboxTransform.create(0, 720)


if __name__ == "__main__":
    unittest.main()
