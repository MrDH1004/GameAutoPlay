import unittest

import cv2
import numpy as np

from bot2.map_vision import analyze_map, map_translation, shortest_path


class MapVisionTest(unittest.TestCase):
    def test_extracts_connected_beige_road(self):
        image = np.full((360, 640, 3), (45, 56, 68), np.uint8)
        points = np.array([[40, 300], [170, 250], [300, 130], [590, 80]], np.int32)
        cv2.polylines(image, [points], False, (115, 150, 180), 25, cv2.LINE_AA)

        graph = analyze_map(image)

        self.assertGreater(np.mean(graph.mask > 0), .005)
        self.assertGreater(cv2.countNonZero(graph.skeleton), 30)

    def test_astar_follows_skeleton(self):
        skeleton = np.zeros((120, 220), np.uint8)
        cv2.line(skeleton, (10, 100), (100, 50), 255, 1)
        cv2.line(skeleton, (100, 50), (210, 20), 255, 1)

        path = shortest_path(skeleton, (8, 102), (212, 18))

        self.assertGreater(len(path), 150)
        self.assertEqual(path[0], (10, 100))
        self.assertEqual(path[-1], (210, 20))

    def test_map_translation_recovers_shift(self):
        origin = np.zeros((360, 640, 3), np.uint8)
        cv2.rectangle(origin, (180, 120), (430, 260), (110, 140, 170), -1)
        cv2.line(origin, (100, 300), (550, 80), (210, 220, 230), 12)
        matrix = np.float32([[1, 0, -85], [0, 1, 42]])
        current = cv2.warpAffine(origin, matrix, (640, 360))

        shift = map_translation(origin, current)

        self.assertIsNotNone(shift)
        self.assertAlmostEqual(shift[0], -85, delta=2)
        self.assertAlmostEqual(shift[1], 42, delta=2)


if __name__ == "__main__":
    unittest.main()
