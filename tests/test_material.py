import struct
import unittest

from src.material import (
    CURRENT_MATERIAL,
    CURRENT_MESH,
    CURRENT_TEXTURE_MAP,
    CURRENT_TEXTURE_MAP_SPEC,
    CURRENT_SHADER_UNIFORMS,
    CURRENT_SHADER_DEFINES,
    CURRENT_TEXTURE_SELECTOR,
    UNIFORM_MARKER,
    SOLID_COSMETIC_SHADER,
    TINTED_HEADGEAR_SHADER,
    ShaderUniform,
    ShaderBinding,
    MaterialTextureSelector,
    resolve_material_texture_sets,
    read_shader_uniforms,
    read_shader_bindings,
    read_solid_material_color,
    apply_material_uniform_overrides
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
    def test_uses_current_season_material_type_hashes(self):
        self.assertEqual(
            (
                CURRENT_MATERIAL,
                CURRENT_TEXTURE_MAP_SPEC,
                CURRENT_TEXTURE_MAP,
                CURRENT_TEXTURE_SELECTOR,
                CURRENT_MESH,
                CURRENT_SHADER_DEFINES,
                CURRENT_SHADER_UNIFORMS,
            ),
            (
                0xB3110874,
                0xB9B8043D,
                0xD555965D,
                0x7C4A77EA,
                0xEAE0EA75,
                0x208C12C4,
                0xD1E7D4EE,
            )
        )

    def test_preserves_textured_headgear_tint(self):
        color = (0.25, 0.25, 0.25, 1.0)
        material_blob = struct.pack("<I", UNIFORM_MARKER) + b"\x00" * 36 + struct.pack("<4f", *color)

        resolved = read_solid_material_color(material_blob, TINTED_HEADGEAR_SHADER, has_diffuse=True)

        for actual, expected in zip(resolved, color):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_preserves_untextured_solid_cosmetic_material(self):
        material_uid = 0x4000
        geometry_uid = 0x5000
        color = (0.73, 0.73, 0.73, 1.0)

        material_data = struct.pack("<IQ", CURRENT_MATERIAL, SOLID_COSMETIC_SHADER) + struct.pack("<I", UNIFORM_MARKER | 2) + b"\x00" * 36 + struct.pack("<4f", *color)
        payload = b"".join(
            [
                make_entry(CURRENT_MATERIAL, material_uid, material_data),
                make_entry(CURRENT_MESH, 0x4001, struct.pack("<QQQ", geometry_uid, material_uid, material_uid)),
            ]
        )

        materials = resolve_material_texture_sets(payload, set(), geometry_uids=(geometry_uid,))

        self.assertEqual(len(materials[0]), 2)

        for material in materials[0]:
            self.assertEqual(material.shader_uid, SOLID_COSMETIC_SHADER)

            for actual, expected in zip(material.solid_color, color):
                self.assertAlmostEqual(actual, expected, places=6)

    def test_preserves_known_untextured_shader_colors(self):
        geometry_uid = 0x5000
        material_uids = (0x4000, 0x4001)
        color = (0.25, 0.5, 0.75, 1.0)
        shader_parameters = (
            (
                0x0000000099E2C950,
                0
            ),
            (
                0x0000005DB6637AD7,
                2
            )
        )

        entries = []

        for material_uid, (shader_uid, parameter) in zip(material_uids, shader_parameters):
            material_data = (
                struct.pack("<IQ", CURRENT_MATERIAL,shader_uid)
                + struct.pack("<I", UNIFORM_MARKER | parameter)
                + b"\x00" * 36
                + struct.pack("<4f", *color)
            )

            entries.append(make_entry(CURRENT_MATERIAL, material_uid, material_data))

        entries.append(make_entry(CURRENT_MESH, 0x4002, struct.pack("<QQQ", geometry_uid, *material_uids)))

        materials = resolve_material_texture_sets(b"".join(entries), set(), geometry_uids=(geometry_uid,))[0]

        self.assertEqual(len(materials), 2)

        for material, (shader_uid, _) in zip(materials, shader_parameters):
            self.assertEqual(material.shader_uid, shader_uid)

            for actual, expected in zip(material.solid_color, color):
                self.assertAlmostEqual(actual, expected, places=6)

    def test_resolves_material_roles_and_preserves_later_detail_map(self):
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

        shader_uid = 0x6000

        def base_selector(spec_uid: int) -> bytes:
            return struct.pack("<I8xQ", CURRENT_TEXTURE_SELECTOR, spec_uid)

        def detail_selector(spec_uid: int) -> bytes:
            return struct.pack("<I8xQ", UNIFORM_MARKER | 1, spec_uid)

        material_data = (
            struct.pack("<IQ", CURRENT_MATERIAL, shader_uid)
            + base_selector(diffuse_spec)
            + base_selector(normal_spec)
            + base_selector(specular_spec)
            + base_selector(mask_spec)
            + detail_selector(detail_spec)
        )

        payload = struct.pack("<QQ", base_material, override_material) + b"".join(
            [
                make_entry(CURRENT_MATERIAL, override_material, material_data),
                make_entry(CURRENT_MESH, 0x4002, struct.pack("<QQ", geometry, base_material)),
                make_entry(CURRENT_MATERIAL, base_material, struct.pack("<I", CURRENT_MATERIAL)),
                make_entry(CURRENT_SHADER_DEFINES, shader_uid, struct.pack("<I", CURRENT_SHADER_DEFINES) + b"\x00" * 24 + b"#define NormalDetail _CustomParamTexture0\r\n"),

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

        material = materials[0][0]

        self.assertEqual(material.shader_uid, shader_uid)
        self.assertEqual(
            material.shader_bindings,
            (
                ShaderBinding(
                    shader_uid=shader_uid,
                    name="NormalDetail",
                    target="_CustomParamTexture0"
                ),
            )
        )
        self.assertEqual(material.diffuse_uids, (diffuse,))
        self.assertEqual(material.normal_uids, (normal,))
        self.assertEqual(material.specular_uids, (specular,))
        self.assertEqual(material.mask_uids, (mask,))

        self.assertEqual(
            material.selectors,
            (
                MaterialTextureSelector(
                    role=0,
                    spec_uid=diffuse_spec,
                    texture_map_uid=diffuse_map,
                    texture_uids=(diffuse,),
                    source="base"
                ),
                MaterialTextureSelector(
                    role=1,
                    spec_uid=normal_spec,
                    texture_map_uid=normal_map,
                    texture_uids=(normal,),
                    source="base"
                ),
                MaterialTextureSelector(
                    role=2,
                    spec_uid=specular_spec,
                    texture_map_uid=specular_map,
                    texture_uids=(specular,),
                    source="base"
                ),
                MaterialTextureSelector(
                    role=7,
                    spec_uid=mask_spec,
                    texture_map_uid=mask_map,
                    texture_uids=(mask,),
                    source="base"
                ),
                MaterialTextureSelector(
                    role=0,
                    spec_uid=detail_spec,
                    texture_map_uid=detail_map,
                    texture_uids=(detail,),
                    source="detail"
                )
            )
        )

    def test_reads_embedded_shader_uniform_names_and_values(self):
        owner_uid = 0x6000
        texture_spec_uid = 0x7000

        def make_uniform(index: int, name: str, uniform_type: int, value_data: bytes) -> bytes:
            encoded_name = name.encode("utf-8")

            return (
                b"\x00"
                + struct.pack(
                    "<IIIII",
                    0xFBF80000 | index,
                    0,
                    0x12345678,
                    uniform_type,
                    len(encoded_name)
                )
                + encoded_name
                + b"\x00"
                + value_data
            )

        texture_value = struct.pack("<II16xQ", 2, 0, texture_spec_uid)
        scalar_value = struct.pack("<II16xf", 0, 0, 0.75)
        uniform_data = struct.pack("<II", CURRENT_SHADER_UNIFORMS, 2) + make_uniform(0, "SecondaryEnv", 0, texture_value) + make_uniform(1, "IrisGlossiness", 1, scalar_value)

        payload = make_entry(CURRENT_SHADER_UNIFORMS, owner_uid, uniform_data)

        self.assertEqual(
            read_shader_uniforms(payload),
            (
                ShaderUniform(
                    owner_uid=owner_uid,
                    index=0,
                    name="SecondaryEnv",
                    uniform_type=0,
                    texture_spec_uid=texture_spec_uid
                ),
                ShaderUniform(
                    owner_uid=owner_uid,
                    index=1,
                    name="IrisGlossiness",
                    uniform_type=1,
                    values=(0.75,)
                )
            )
        )

    def test_reads_custom_shader_parameter_bindings(self):
        shader_uid = 0x7000

        shader_data = (
            struct.pack("<I", CURRENT_SHADER_DEFINES)
            + b"\x00" * 24
            + b"#define NormalDetail _CustomParamTexture0\r\n"
            + b"#define AlbedoDetail _CustomParamTexture1\r\n"
            + b"#define UnrelatedValue SOMETHING_ELSE\r\n"
        )

        payload = make_entry(CURRENT_SHADER_DEFINES, shader_uid, shader_data)

        self.assertEqual(
            read_shader_bindings(payload),
            (
                ShaderBinding(
                    shader_uid=shader_uid,
                    name="NormalDetail",
                    target="_CustomParamTexture0"
                ),
                ShaderBinding(
                    shader_uid=shader_uid,
                    name="AlbedoDetail",
                    target="_CustomParamTexture1"
                )
            )
        )

    def test_applies_material_vector_overrides(self):
        shader_uid = 0x7000

        uniforms = (
            ShaderUniform(
                owner_uid=0x7002,
                index=2,
                name="ScleraColorWhite",
                uniform_type=1,
                values=(1.0, 1.0, 1.0, 0.0)
            ),
            ShaderUniform(
                owner_uid=0x7002,
                index=6,
                name="IrisGlossiness",
                uniform_type=1,
                values=(0.0,)
            )
        )

        bindings = (
            ShaderBinding(
                shader_uid=shader_uid,
                name="ScleraColorWhite",
                target="UM_CustomParamVector0"
            ),
            ShaderBinding(
                shader_uid=shader_uid,
                name="IrisGlossiness",
                target="UM_CustomParamVector4.x"
            )
        )

        vectors = (
            0.73, 0.73, 0.73, 1.0,
            0.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 0.0, 0.0,
            0.25, 0.5, 0.75, 1.0
        )

        material_blob = struct.pack("<I", UNIFORM_MARKER | 2) + b"\x00" * 36 + struct.pack("<20f", *vectors)

        resolved = apply_material_uniform_overrides(material_blob, uniforms, bindings)

        self.assertEqual(resolved[0].name, "ScleraColorWhite")

        for actual, expected in zip(resolved[0].values, (0.73, 0.73, 0.73, 1.0)):
            self.assertAlmostEqual(actual, expected, places=5)

        self.assertEqual(resolved[1].name, "IrisGlossiness")
        self.assertAlmostEqual(resolved[1].values[0], 0.25, places=5)

if __name__ == "__main__":
    unittest.main()