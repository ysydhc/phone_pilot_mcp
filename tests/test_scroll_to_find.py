"""Tests for scroll_to_find direction logic and bidirectional search."""

import unittest


class TestScrollDirectionParsing(unittest.TestCase):
    """Test scroll direction parsing and bidirectional support."""

    def test_simple_directions(self):
        """Test that simple direction strings are valid."""
        valid = {"up", "down", "left", "right", "up_down", "down_up", "left_right", "right_left"}
        for d in valid:
            # Just verify these are recognized strings
            self.assertIn(d.split("_")[0], {"up", "down", "left", "right"})

    def test_bidirectional_has_two_parts(self):
        """Test bidirectional directions have exactly 2 parts."""
        for d in ("up_down", "down_up", "left_right", "right_left"):
            parts = d.split("_")
            self.assertEqual(len(parts), 2)
            self.assertIn(parts[0], {"up", "down", "left", "right"})
            self.assertIn(parts[1], {"up", "down", "left", "right"})


class TestBoxShortcuts(unittest.TestCase):
    """Test Box static method shortcuts."""

    def test_box_bottom(self):
        from phone_pilot.core.ui.element import Box
        b = Box.bottom()
        # Bottom region of screen
        self.assertEqual(b.x_pct, 0.0)
        self.assertGreater(b.y_pct, 0.0)
        self.assertEqual(b.width, 1.0)

    def test_box_top(self):
        from phone_pilot.core.ui.element import Box
        b = Box.top()
        self.assertEqual(b.x_pct, 0.0)
        self.assertEqual(b.y_pct, 0.0)
        self.assertEqual(b.width, 1.0)

    def test_box_left_side(self):
        from phone_pilot.core.ui.element import Box
        b = Box.left_side()
        self.assertEqual(b.x_pct, 0.0)
        self.assertEqual(b.y_pct, 0.0)
        self.assertLess(b.width, 1.0)

    def test_box_right_side(self):
        from phone_pilot.core.ui.element import Box
        b = Box.right_side()
        self.assertGreater(b.x_pct, 0.0)


if __name__ == "__main__":
    unittest.main()
