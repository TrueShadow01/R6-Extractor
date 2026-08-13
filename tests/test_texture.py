import unittest
from PIL import Image
from src.texture import (
    TEXMAPDATA_MAGIC,
    parse_texture,
    reconstruct_bc5_z
)

def make_texture_payload(format_code: int, surface: bytes, texture_type: int = 1) -> bytes:
    width = 64
    height = 64

    trailer = (width.to_bytes(4, "little") + height.to_bytes(4, "little") + b"\x00" * 24 + format_code.to_bytes(4, "little") + b"\x00" * 8 + texture_type.to_bytes(4, "little"))

    return (TEXMAPDATA_MAGIC + b"\x00" * 8 + surface + trailer)

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

    def test_accepts_surface_size_for_known_format(self):
        blocks = (64 // 4) * (64 // 4)

        payload = make_texture_payload(6, b"\x00" * (blocks * 16))

        (
            width,
            height,
            format_code,
            texture_type,
            surface
        ) = parse_texture(payload)

        self.assertEqual((width, height), (64, 64))
        self.assertEqual(format_code, 6)
        self.assertEqual(texture_type, 1)
        self.assertEqual(len(surface), blocks * 16)

    def test_rejects_surface_size_for_wrong_format(self):
        blocks = (64 // 4) * (64 // 4)

        payload = make_texture_payload(6, b"\x00" * (blocks * 8))

        with self.assertRaises(ValueError):
            parse_texture(payload)

if __name__ == "__main__":
    unittest.main()