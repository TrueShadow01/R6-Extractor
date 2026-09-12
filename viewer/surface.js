import * as THREE from "three";

export async function applySiegeSurfaces(gltf, modelUrl) {
    const materials = new Set();

    gltf.scene.traverse(object => {
        if (!object.isMesh) {
            return;
        }

        for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
            const extras = material.userData;

            if (extras.siegePackedMaterialTexture && ![
                "000000557005948D", // eyes
                "000000003051C028" // hair uses a different shader
            ].includes(extras.siegeShaderUid)) {
                materials.add(material);
            }
        }
    });

    const loader = new THREE.TextureLoader()
    const baseUrl = new URL(modelUrl, window.location.href);
    const textures = new Map();

    for (const material of materials) {
        const filename = material.userData.siegePackedMaterialTexture;

        if (!textures.has(filename)) {
            const texture = await loader.loadAsync(new URL(filename, baseUrl).href);
            texture.flipY = false;
            texture.colorSpace = THREE.NoColorSpace;
            texture.wrapS = THREE.RepeatWrapping;
            texture.wrapT = THREE.RepeatWrapping;
            texture.needsUpdate = true;
            textures.set(filename, texture);
        }

        const texture = textures.get(filename);
        material.metalnessMap = texture;
        material.roughnessMap = texture;
        material.metalness = 1;
        material.roughness = 1;

        const metalnessExponent = [
            "000000003BD13B9E",
            "000000003CAE6B71",
            "0000001397A32F38"
        ].includes(material.userData.siegeShaderUid) ? 2.2 : 1.0;
        material.userData.siegeMetalnessExponent = metalnessExponent;

        const previousCompile = material.onBeforeCompile;
        const previousKey = material.customProgramCacheKey();

        material.onBeforeCompile = function (shader, renderer) {
            previousCompile.call(this, shader, renderer);

            // Victor, green is gloss. Aiden already blamed the lights - Blake
            shader.fragmentShader = shader.fragmentShader.replace(
                "#include <roughnessmap_fragment>",
                `
                float roughnessFactor = 1.0 - texture2D(roughnessMap, vRoughnessMapUv).g;
                `
            );

            shader.fragmentShader = shader.fragmentShader.replace(
                "#include <metalnessmap_fragment>",
                `
                 float metalnessFactor = pow(clamp(texture2D(metalnessMap, vMetalnessMapUv).r, 0.0, 1.0), ${metalnessExponent.toFixed(1)});
                `
            );

            if (metalnessExponent === 2.2) {
                shader.fragmentShader = shader.fragmentShader.replace(
                    "#include <lights_physical_fragment>",
                    `
                    #include <lights_physical_fragment>
                    float siegeReflectance = pow(clamp(texture2D(metalnessMap, vMetalnessMapUv).b, 0.0, 1.0), 2.2);
                    material.specularColor = mix(vec3(0.04 * siegeReflectance), diffuseColor.rgb, metalnessFactor);
                    `
                );
            }
        };

        material.customProgramCacheKey = () => `${previousKey}|siege-packed-v3|${metalnessExponent}`;
        material.needsUpdate = true;
    }
}