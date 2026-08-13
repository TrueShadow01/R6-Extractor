import struct
import unittest

from src.material import (
    CURRENT_MATERIAL,
    CURRENT_MESH,
    CURRENT_TEXTURE_MAP,
    CURRENT_TEXTURE_MAP_SPEC,
    MaterialTextureSet,
    resolve_material_texture_sets
)


def make_entry(file_type: int, uid: int, data: bytes) -> bytes:
    name_hash = b"nested-test"

    metadata = struct.pack(
        "<HHI",
        len(name_hash),
        2,
        0
    )

    metadata += name_hash
    metadata += struct.pack("<IQ", file_type, uid)

    return metadata + data


def make_spec(uid: int, role: int, texture_map_uid: int) -> bytes:
    data = struct.pack(
        "<II3xQ",
        CURRENT_TEXTURE_MAP_SPEC,
        role,
        texture_map_uid
    )

    return make_entry(CURRENT_TEXTURE_MAP_SPEC, uid, data)


def make_texture_map(uid: int, compiled_uid: int) -> bytes:
    data = struct.pack(
        "<IQ",
        CURRENT_TEXTURE_MAP,
        compiled_uid
    )

    return make_entry(CURRENT_TEXTURE_MAP, uid, data)


class MaterialTests(unittest.TestCase):
    def test_resolves_material_roles_and_ignores_later_detail_map(self):
        diffuse_spec = 0x1001
        normal_spec = 0x1002
        specular_spec = 0x1003
        mask_spec = 0x1004
        detail_spec = 0x1005

        diffuse_map = 0x2001
        normal_map = 0x2002
        specular_map = 0x2003
        mask_map = 0x2004
        detail_map = 0x2005

        diffuse = 0x3001
        normal = 0x3002
        specular = 0x3003
        mask = 0x3004
        detail = 0x3005

        base_material = 0x4000
        override_material = 0x4001
        geometry = 0x5000

        material_data = struct.pack(
            "<I5Q",
            CURRENT_MATERIAL,
            diffuse_spec,
            normal_spec,
            specular_spec,
            mask_spec,
            detail_spec
        )

        payload = struct.pack("<QQ", base_material, override_material) + b"".join(
            [
                make_entry(CURRENT_MATERIAL, override_material, material_data),
                make_entry(CURRENT_MESH, 0x4002, struct.pack("<QQ", geometry, base_material)),
                make_entry(CURRENT_MATERIAL, base_material, struct.pack("<I", CURRENT_MATERIAL)),

                make_spec(diffuse_spec, 0, diffuse_map),
                make_spec(normal_spec, 1, normal_map),
                make_spec(specular_spec, 2, specular_map),
                make_spec(mask_spec, 7, mask_map),

                # A later role-0 map is a detail map and must not
                # replace the first diffuse selector
                make_spec(detail_spec, 0, detail_map),

                make_texture_map(diffuse_map, diffuse),
                make_texture_map(normal_map, normal),
                make_texture_map(specular_map, specular),
                make_texture_map(mask_map, mask),
                make_texture_map(detail_map, detail)
            ]
        )

        materials = resolve_material_texture_sets(
            payload,
            {
                diffuse,
                normal,
                specular,
                mask,
                detail
            },
            geometry_uids=(geometry,)
        )

        self.assertEqual(
            materials,
            (
                (
                    MaterialTextureSet(
                        diffuse_uids=(diffuse,),
                        normal_uids=(normal,),
                        specular_uids=(specular,),
                        mask_uids=(mask,)
                    ),
                ),
            )
        )


if __name__ == "__main__":
    unittest.main()