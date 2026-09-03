import unittest

from sima_parity.comparison import ComparisonPolicy, compare_detections
from sima_parity.results import validate_result


def detection(class_name, confidence, x, y, width, height):
    box = {"x": x, "y": y, "width": width, "height": height}
    return {"class_name": class_name, "confidence": confidence, "box_input": box}


class ComparisonTests(unittest.TestCase):
    def test_matching_detection_passes(self):
        baseline = [detection("Major", 0.80, 10, 10, 100, 100)]
        demo = [detection("Major", 0.76, 12, 10, 100, 100)]
        result = compare_detections(baseline, demo)
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["matches"]), 1)

    def test_wrong_class_fails(self):
        baseline = [detection("Major", 0.80, 10, 10, 100, 100)]
        demo = [detection("Minor", 0.80, 10, 10, 100, 100)]
        result = compare_detections(baseline, demo)
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_baseline_indexes"], [0])

    def test_score_delta_fails(self):
        baseline = [detection("Major", 0.80, 10, 10, 100, 100)]
        demo = [detection("Major", 0.70, 10, 10, 100, 100)]
        result = compare_detections(baseline, demo, ComparisonPolicy(0.9, 0.05))
        self.assertFalse(result["passed"])

    def test_extra_detection_fails(self):
        baseline = [detection("Major", 0.80, 10, 10, 100, 100)]
        demo = baseline + [detection("Minor", 0.80, 200, 200, 20, 20)]
        self.assertFalse(compare_detections(baseline, demo)["passed"])

    def test_invalid_transform_fails_result_validation(self):
        result = {
            "schema_version": "1.1", "frame_id": "frame", "pipeline_version": "p", "model_version": "m",
            "image": {"path": "frame.png", "sha256": "a" * 64, "width": 1280, "height": 720},
            "transform": {
                "source_to_input": {"source_width": 1280, "source_height": 720, "input_width": 1280, "input_height": 720, "resize_scale_x": 1, "resize_scale_y": 1, "pad_left": 0, "pad_top": 0, "pad_right": 0, "pad_bottom": 0},
                "input_to_model": {"source_width": 1280, "source_height": 720, "model_width": 640, "model_height": 640, "resize_scale_x": 0.5, "resize_scale_y": 0.5, "pad_left": 0, "pad_top": 0, "pad_right": 0, "pad_bottom": 0},
            },
            "detections": [], "timings_ms": {"total": 1},
        }
        with self.assertRaises(ValueError):
            validate_result(result, "frame")


if __name__ == "__main__":
    unittest.main()
