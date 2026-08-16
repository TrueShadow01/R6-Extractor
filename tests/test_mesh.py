import struct
import unittest

from src.mesh import (
    read_bone_palettes,
    read_lod0_islands,
    read_tangent_frame
)

class MeshIslandTests(unittest.TestCase):
    def test_lod0_islands_preserve_face_ranges_and_material_ids(self):
        chunk_size = 64 * 6
        tris = 0
        tail = chunk_size * 2

        payload = bytearray(tail + 2 * 36)

        struct.pack_into("<HHH", payload, 0, 0, 1, 2)
        struct.pack_into("<HHH", payload, chunk_size, 3, 4, 5)
        struct.pack_into("<9I", payload, tail, 3, 0, 3, 0, 1, 4, 0, 0, 0)
        struct.pack_into("<9I", payload, tail + 36, 3, 3, 3, 1, 1, 7, 0, 0, 0)

        islands = read_lod0_islands(payload, tris, tail, 2)

        self.assertEqual([island.material_id for island in islands], [4, 7])
        self.assertEqual(islands[0].faces, ((0, 1, 2),))
        self.assertEqual(islands[1].faces, ((3, 4, 5),))

    def test_parallel_tangent_uses_perpendicular_fallback(self):
        payload = bytes(
            (
                255, 128, 128, 0,
                255, 128, 128, 0,
                128, 255, 128, 0
            )
        )

        normals, tangents = read_tangent_frame(payload, 0, 1)

        normal = normals[0]
        tangent = tangents[0][:3]

        dot = sum(
            normal_component * tangent_component
            for normal_component, tangent_component in zip(normal, tangent)
        )

        tangent_length = sum(
            component * component
            for component in tangent
        ) **  0.5

        self.assertAlmostEqual(dot, 0.0, places=6)
        self.assertAlmostEqual(tangent_length, 1.0, places=6)

    def test_bone_palette_preserves_nonzero_first_bone(self):
        payload = bytearray(68 + 268)

        struct.pack_into(
            "<HBBIB",
            payload,
            68,
            1,
            2,
            0,
            0,
            2
        )

        payload[77:79] = bytes(
            (
                3,
                7
            )
        )

        palettes = read_bone_palettes(payload, 0, 1, 1)

        self.assertEqual(
            palettes,
            (
                (
                    3,
                    7
                ),
            )
        )

if __name__ == "__main__":
    unittest.main()