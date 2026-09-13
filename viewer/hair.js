import * as THREE from "three";

// Experimental order: shift, gloss1, strength1, gloss2, strength2, tint2, diffuse strength, unused
export async function applyExperimentalHair(gltf, modelUrl) {
    const replacements = new Map();
    const loader = new THREE.TextureLoader();
    const base = new URL(modelUrl, window.location.href);
    const textureCache = new Map();
    const jobs = [];

    gltf.scene.traverse(object => {
        if (!object.isMesh) {
            return;
        }

        for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
            if (material.userData.siegeShaderUid != "000000003051C028") {
                continue;
            }
            if (material.userData.siegeExperimentalHair) {
                continue;
            }

            const p = material.userData.siegeShaderUniforms?.ExperimentalHairSourceV1;
            if (!Array.isArray(p) || p.length !== 8 || !p.every(Number.isFinite)) {
                continue;
            }
            if (Math.abs(p[0]) > 3 || p.slice(1).some(v => v < 0 || v > 1)) {
                continue;
            }

            if (!replacements.has(material)) {
                const hair = new THREE.MeshPhysicalMaterial();
                THREE.MeshStandardMaterial.prototype.copy.call(hair, material);
                hair.defines = { STANDARD: "", PHYSICAL: "" };
                hair.name = material.name;
                hair.userData.siegeExperimentalHair = "v1: inferred fields. approximate environment";
                hair.metalness = 0;
                hair.metalnessMap = null;
                hair.roughnessMap = null;
                hair.anisotropy = 0.8; // Approximate environment highlights only

                const gloss = THREE.MathUtils.clamp(1.035 - 1.15 * (1 - p[1]), 0, 1);
                hair.roughness = Math.max(0.08, Math.pow(2 / (2 ** (19 * gloss) + 2), 0.25));

                replacements.set(material, hair);
                jobs.push({ hair, p });
            }
        }
    });

    for (const { hair, p } of jobs) {
        const filename = hair.userData.siegePackedMaterialTexture;
        let mask = null;

        if (filename) {
            if (!textureCache.has(filename)) {
                const texture = await loader.loadAsync(new URL(filename, base).href);
                texture.flipY = false;
                texture.colorSpace = THREE.NoColorSpace;
                texture.wrapS = texture.wrapT = THREE.RepeatWrapping; // same as defining both on separate lines for the same value
                textureCache.set(filename, texture);
            }
            mask = textureCache.get(filename);
        }

        hair.onBeforeCompile = shader => {
            shader.uniforms.hairA = { value: new THREE.Vector4(...p.slice(0, 4)) };
            shader.uniforms.hairB = { value: new THREE.Vector4(...p.slice(4, 8)) };
            shader.uniforms.hairMask = { value: mask };
            shader.uniforms.hairHasMask = { value: mask ? 1 : 0 };

            shader.vertexShader = "varying vec2 vHairUv;\n" + shader.vertexShader;
            shader.vertexShader = shader.vertexShader.replace("#include <uv_vertex>", "#include <uv_vertex>\nvHairUv = uv;");

            shader.fragmentShader = `
                uniform vec4 hairA;
                uniform vec4 hairB;
                uniform sampler2D hairMask;
                uniform float hairHasMask;
                varying vec2 vHairUv;
            ` + shader.fragmentShader;

            const direct = "reflectedLight.directSpecular += irradiance * BRDF_GGX( directLight.direction, geometryViewDir, geometryNormal, material );";
            let lighting = THREE.ShaderChunk.lights_physical_pars_fragment;

            if (!lighting.includes(direct)) {
                throw new Error("Hair Shader: Incompatible Three.js lighting chunk");
            }

            lighting = lighting.replace(
                "struct PhysicalMaterial {",
                "struct PhysicalMaterial {\nvec3 hairStrand;\nvec3 hairBase;\nfloat hairMaskValue;"
            ).replace(direct, `
                vec3 hairHalfRaw = directLight.direction + geometryViewDir;
                vec3 hairHalf = hairHalfRaw / max(length(hairHalfRaw), 0.00001);
                float hairTH = dot(material.hairStrand, hairHalf);
                float hairNH = dot(geometryNormal, hairHalf);
                float hairG1 = clamp(1.035 - 1.15 * clamp(1.0 - hairA.y, 0.0, 1.0), 0.0, 1.0);
                float hairN1 = exp2(19.0 * hairG1);
                float hairN2 = exp2(19.0 * hairA.w);
                // Experimental studio-light softening, not a recovered game value.
                float hairLightSpread = 0.12;
                hairN1 /= 1.0 + hairN1 * hairLightSpread * hairLightSpread;
                hairN2 /= 1.0 + hairN2 * hairLightSpread * hairLightSpread;
                float hairShape = -hairTH * hairTH / max(1.0 + hairNH, 0.00001);
                float hairLobe1 = sqrt(hairN1 + 1.0) * exp(hairN1 * hairShape);
                float hairLobe2 = sqrt(hairN2 + 1.0) * exp(hairN2 * hairShape);
                vec3 hairSecondColor = mix(material.hairBase, vec3(1.0), hairB.y);

                reflectedLight.directSpecular += irradiance * material.hairMaskValue * 0.125 * (material.hairBase * hairA.z * hairLobe1 + hairSecondColor * hairB.x * hairLobe2);
            `);

            shader.fragmentShader = shader.fragmentShader.replace("#include <lights_physical_pars_fragment>", lighting).replace(
                "#include <lights_physical_fragment>", `
                    #include <lights_physical_fragment>
                    material.hairBase = diffuseColor.rgb;
                    material.hairMaskValue = hairHasMask > 0.5 ? texture2D(hairMask, vHairUv).r : 1.0;
                    vec3 hairDirection = tbn[1] + normal * hairA.x;
                    material.hairStrand = hairDirection / max(length(hairDirection), 0.00001);
                    material.diffuseColor = diffuseColor.rgb * hairB.z;

                    // The studio reflection is still an approximation - Nyx
                    material.specularColor = clamp(material.hairMaskValue * 0.125 * (diffuseColor.rgb * hairA.z + mix(diffuseColor.rgb, vec3(1.0), hairB.y) * hairB.x), vec3(0.0), vec3(1.0));
                    material.specularF90 = clamp(material.hairMaskValue * 0.125 * (hairA.z + hairB.x), 0.0, 1.0);
                `
            );
        };

        hair.customProgramCacheKey = () => "siege-experimental-hair-v3";
        hair.needsUpdate = true;
    }

    gltf.scene.traverse(object => {
        if (!object.isMesh) {
            return;
        }

        const replace = material => replacements.get(material) ?? material;
        object.material = Array.isArray(object.material) ? object.material.map(replace) : replace(object.material);
    });
}