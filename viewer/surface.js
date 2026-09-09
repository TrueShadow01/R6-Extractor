import * as THREE from "three";

export async function applySiegeSurfaces(gltf, modelUrl) {
    const materials = new Set();

    gltf.scene.traverse(object => {
        if (!object.isMesh) {
            return;
        }

        for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
            const extras = material.userData;

            if (extras.siegePackedMaterialTexture && extras.siegeShaderUid != "000000557005948D") {
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
                 float metalnessFactor = texture2D(metalnessMap, vMetalnessMapUv).r;
                `
            );
        };

        material.customProgramCacheKey = () => `${previousKey}|siege-packed-v1`;
        material.needsUpdate = true;
    }
}