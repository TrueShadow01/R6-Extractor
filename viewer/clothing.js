import * as THREE from "three";

export async function applySiegeClothing(gltf, modelUrl) {
    const materials = new Set();

    gltf.scene.traverse(object => {
        if (!object.isMesh) {
            return;
        }

        for (const material of (Array.isArray(object.material) ? object.material : [object.material])) {
            if (material.userData.siegeShaderUid == "0000001397A32F38" && (material.userData.siegeMaskTexture || material.userData.siegeShaderUniforms?.ClothingMaskMode?.[0] === 0)) {
                materials.add(material);
            }
        }
    });

    const loader = new THREE.TextureLoader();
    const baseUrl = new URL(modelUrl, window.location.href);

    for (const material of materials) {
        const extras = material.userData;
        const values = extras.siegeShaderUniforms ?? {};
        const names = [
            "MaskRed_Color",
            "MaskGreen_Color",
            "MaskBlue_Color"
        ];

        if (!material.map || names.some(name => !Array.isArray(values[name]) || values[name].length < 3 || !values[name].slice(0, 3).every(Number.isFinite))) {
            throw new Error(`Missing clothing data: ${material.name}`);
        }

        const colors = names.map(name => new THREE.Vector3(...values[name].slice(0, 3)));

        const alphaLayers = values.ClothingMaskMode?.[0] === 0;
        let mask = null;

        if (!alphaLayers) {
            const maskUrl = new URL(extras.siegeMaskTexture, baseUrl);
            mask = await loader.loadAsync(maskUrl.href)
            mask.flipY = false;
            mask.colorSpace = THREE.NoColorSpace;
            mask.wrapS = THREE.RepeatWrapping;
            mask.wrapT = THREE.RepeatWrapping;
            mask.needsUpdate = true;
        }

        material.map = material.map.clone();
        material.map.colorSpace = THREE.NoColorSpace;
        material.map.needsUpdate = true;
        material.color.setRGB(1, 1, 1)

        material.onBeforeCompile = shader => {
            Object.assign(shader.uniforms, {
                siegeClothMask: {
                    value: mask
                },
                siegeClothRed: {
                    value: colors[0]
                },
                siegeClothGreen: {
                    value: colors[1]
                },
                siegeClothBlue: {
                    value: colors[2]

                }
            });

            shader.fragmentShader = `
                uniform sampler2D siegeClothMask;
                uniform vec3 siegeClothRed;
                uniform vec3 siegeClothGreen;
                uniform vec3 siegeClothBlue;
            ` + shader.fragmentShader;

            // Nyx leave white and black mask areas alone lol - Isaac
            shader.fragmentShader = shader.fragmentShader.replace(
                "#include <map_fragment>",
                `
                 #include <map_fragment>

                ${alphaLayers ? `
                    // This alpha chooses fabric layers, not transparency - Nyx
                    float siegeAlpha = texture2D(map, vMapUv).a;
                    float siegeRed = step(0.75, siegeAlpha);
                    float siegeGreen = float(siegeAlpha > 0.25) * (1.0 - siegeRed);
                    vec3 siegeWeights = vec3(siegeRed, siegeGreen, 0.0);
                    ` : `
                    vec3 siegeMask = texture2D(siegeClothMask, vMapUv).rgb;
                    vec3 siegeWeights = siegeMask / max(siegeMask.r + siegeMask.g + siegeMask.b, 1.0);
                `}

                float siegeStrength = clamp(length(siegeWeights), 0.0, 1.0);
                vec3 siegeColor =
                    siegeWeights.r * siegeClothRed +
                    siegeWeights.g * siegeClothGreen +
                    siegeWeights.b * siegeClothBlue;

                vec3 siegeGain = vec3(1.0) + 2.0 * siegeStrength * (siegeColor - vec3(0.5));
                diffuseColor.rgb = pow(clamp(diffuseColor.rgb * siegeGain, 0.0, 1.0), vec3(2.2));
                `
            );
        };

        material.customProgramCacheKey = () => `siege-clothing-v3-${alphaLayers ? "alpha" : "rgb"}`;
        material.needsUpdate = true;
    }
}