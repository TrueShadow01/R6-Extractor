import struct
import unittest

from src.mesh import read_lod0_islands

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

if __name__ == "__main__":
    unittest.main()