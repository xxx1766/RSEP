import unittest

try:
    from issue12_image_server import verify_output
except ImportError:
    verify_output = None


class ImageOutputTests(unittest.TestCase):
    def test_correctness_requires_shape_finite_and_matching_reference(self):
        self.assertIsNotNone(verify_output, "image inference verifier is missing")
        self.assertTrue(verify_output([1, 3549, 85], True, "abc", "abc"))
        for args in (([1, 3549, 85], False, "abc", "abc"),
                     ([1, 3549, 85], True, "abc", "def"),
                     ([1, 2], True, "abc", "abc")):
            with self.assertRaises(ValueError):
                verify_output(*args)


if __name__ == "__main__":
    unittest.main()
