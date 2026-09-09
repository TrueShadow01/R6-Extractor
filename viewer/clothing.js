import * as THREE from "three";

export async function applySiegeClothing(gltf, modelUrl) {
    const materials = new Set();

    gltf.scene.traverse(object => {
        if (!object.isMesh) {
            return;
        }

        for (const material of (Array.isArray(object.material) ? object.material : [object.material])) {
            if (material.userData.siegeShaderUid == "0000001397A32F38" && material.userData.siegeMaskTexture) {
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

        const colors = names.map(name => new THREE.Color().setRGB(...values[name].slice(0, 3)).convertSRGBToLinear());

        const maskUrl = new URL(extras.siegeMaskTexture, baseUrl);
        const mask = await loader.loadAsync(maskUrl.href);
        mask.flipY = false;
        mask.colorSpace = THREE.NoColorSpace;
        mask.wrapS = THREE.RepeatWrapping;
        mask.wrapT = THREE.RepeatWrapping;
        mask.needsUpdate = true;

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

                vec3 siegeMask = texture2D(siegeClothMask, vMapUv).rgb;
                vec3 siegeInverse = vec3(1.0) - siegeMask;
                vec3 siegeWeights = vec3(
                    siegeMask.r * siegeInverse.g * siegeInverse.b,
                    siegeMask.g * siegeInverse.b * siegeInverse.r,
                    siegeMask.b * siegeInverse.r * siegeInverse.g
                );

                vec3 siegeOriginal = diffuseColor.rgb;
                vec3 siegeTinted = mix(
                    siegeOriginal,
                    siegeOriginal * siegeClothRed,
                    siegeWeights.r
                );
                siegeTinted = mix(
                    siegeTinted,
                    siegeOriginal * siegeClothGreen,
                    siegeWeights.g
                );
                diffuseColor.rgb = mix(
                    siegeTinted,
                    siegeOriginal * siegeClothBlue,
                    siegeWeights.b
                );
                `
            );
        };

        material.customProgramCacheKey = () => "siege-clothing-v1";
        material.needsUpdate = true;
    }
}