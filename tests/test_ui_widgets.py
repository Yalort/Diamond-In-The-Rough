"""Regression coverage for the shared UI form value converter."""
import unittest

from ctp500_app.ui_widgets import DelayedTreeviewTooltip, FormField, parse_field_value


class FormFieldTests(unittest.TestCase):
    def test_choice_must_come_from_options(self):
        field = FormField("difficulty", "Difficulty", "choice", "Standard", ("Easy", "Standard", "Hard"))
        self.assertEqual(parse_field_value(field, "Hard"), "Hard")
        with self.assertRaisesRegex(ValueError, "listed value"):
            parse_field_value(field, "Hadr")

    def test_int_validation(self):
        field = FormField("level", "Power Level", "int", 5, minimum=1, maximum=30)
        self.assertEqual(parse_field_value(field, "12"), 12)
        for bad in ("", "three", "0", "31", "5.5"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_field_value(field, bad)

    def test_optional_numbers_are_none_when_blank(self):
        self.assertIsNone(parse_field_value(FormField("seed", "Seed", "optional_int"), ""))
        self.assertIsNone(parse_field_value(FormField("timer", "Timer", "optional_int"), "  "))
        self.assertEqual(parse_field_value(FormField("seed", "Seed", "optional_int"), "-12"), -12)

    def test_float_bounds_and_boolean(self):
        field = FormField("discount", "Discount", "float", 0, minimum=0, maximum=1)
        self.assertAlmostEqual(parse_field_value(field, "0.15"), 0.15)
        with self.assertRaises(ValueError):
            parse_field_value(field, "1.2")
        self.assertTrue(parse_field_value(FormField("minion", "Minion", "bool"), True))
        self.assertFalse(parse_field_value(FormField("minion", "Minion", "bool"), False))

    def test_text_can_be_blank(self):
        self.assertEqual(parse_field_value(FormField("notes", "Notes"), " "), "")


class DelayedTreeviewTooltipTests(unittest.TestCase):
    class TreeStub:
        def __init__(self):
            self.bindings = {}
            self.scheduled = []
            self.cancelled = []
            self.row = "gear-id"

        def bind(self, event, callback, add=None):
            self.bindings[event] = (callback, add)

        def identify_row(self, _y):
            return self.row

        def after(self, delay, callback):
            self.scheduled.append((delay, callback))
            return "after-id"

        def after_cancel(self, identity):
            self.cancelled.append(identity)

    def test_hover_waits_half_a_second_and_moving_off_cancels(self):
        tree = self.TreeStub()
        tooltip = DelayedTreeviewTooltip(tree, lambda _identity: "details")
        event = type("EventStub", (), {"y": 4})()

        tooltip._on_motion(event)

        self.assertEqual(tree.scheduled[0][0], 500)
        self.assertEqual(tooltip._item_id, "gear-id")
        tree.row = ""
        tooltip._on_motion(event)
        self.assertEqual(tree.cancelled, ["after-id"])
        self.assertEqual(tooltip._item_id, "")


if __name__ == "__main__":
    unittest.main()
