import unittest
from PIL import Image
from src.texture import reconstruct_bc5_z

class TextureTests(unittest.TestCase):
    def test_reconstructs_bc5_normal_z_and_preserves_other_channels(self):
        source = Image.new("RGBA", (2, 1))

        source.putdata(
            [
                (128, 128, 0, 255),
                (255, 128, 0, 64)
            ]
        )

        result = reconstruct_bc5_z(source)

        self.assertEqual(
            result.getpixel((0, 0)), (128, 128, 255, 255))
        self.assertEqual(
            result.getpixel((1, 0)), (255, 128, 128, 64))

if __name__ == "__main__":
    unittest.main()