import * as THREE from "three";

export function applySiegeMaterials(gltf) {
    const processed = new Set();

    gltf.scene.traverse(object => {
        if (!object.isMesh) {
            return;
        }

        const materials = Array.isArray(object.material) ? object.material : [object.material];

        for (const material of materials) {
            if (processed.has(material)) {
                continue;
            }

            processed.add(material);

            const extras = material.userData;
            if (extras.siegeShaderUid != "000000557005948D") {
                continue;
            }

            const values = extras.siegeShaderUniforms ?? {};
            const required = [
                "ScleraColorWhite", "ScleraColorBlack",
                "IrisColorWhite", "IrisColorBlack",
                "IrisUVRadius", "IrisGlossiness", "ScleraGlossiness"
            ]

            if (!material.map || required.some(name => !Array.isArray(values[name]) || !values[name].length || !values[name].every(Number.isFinite))) {
                throw new Error(`Missing eye data: ${material.name}`);
            }

            const radius = values.IrisUVRadius[0];
            if (!(radius > 0 && radius < 0.5)) {
                throw new Error(`Invalid eye radius: ${material.name}`);
            }

            function color(name) {
                const components = values[name];
                if (components.length < 3) {
                    throw new Error(`Invalid ${name}: ${material.name}`);
                }
                return new THREE.Color().setRGB(...components.slice(0, 3)).convertSRGBToLinear();
            }

            const uniforms = {
                siegeScleraWhite: {
                    value: color("ScleraColorWhite")
                },
                siegeScleraBlack: {
                    value: color("ScleraColorBlack")
                },
                siegeIrisWhite: {
                    value: color("IrisColorWhite")
                },
                siegeIrisBlack: {
                    value: color("IrisColorBlack")
                },
                siegeIrisRadius: {
                    value: radius
                },
                siegeScleraRoughness: {
                    value: 1 - values.ScleraGlossiness[0]
                },
                siegeIrisRoughness: {
                    value: 1 - values.IrisGlossiness[0]
                }
             }

            // Eye channels are data. Keep shared eyelash textuers unchanged.
            material.map = material.map.clone();
            material.map.colorSpace = THREE.NoColorSpace;
            material.map.needsUpdate = true;
            material.color.setRGB(1, 1, 1);
            material.metalness = 0;
            material.metalnessMap = null;
            material.roughnessMap = null;

            material.onBeforeCompile = shader => {
                Object.assign(shader.uniforms, uniforms);

                shader.fragmentShader = `
                uniform vec3 siegeScleraWhite;
                    uniform vec3 siegeScleraBlack;
                    uniform vec3 siegeIrisWhite;
                    uniform vec3 siegeIrisBlack;
                    uniform float siegeIrisRadius;
                    uniform float siegeScleraRoughness;
                    uniform float siegeIrisRoughness;
                ` + shader.fragmentShader;

                shader.fragmentShader = shader.fragmentShader.replace(
                    "#include <map_fragment>", 
                    `
                    vec4 siegeEye = texture2D(map, vMapUv);
                        float siegeDistance = length((vMapUv - vec2(0.75, 0.5)) * vec2(2.0, 1.0));
                        float siegeIrisMask = clamp((siegeIrisRadius - siegeDistance) / 0.01, 0.0, 1.0);

                        vec3 siegeSclera = mix(siegeScleraBlack, siegeScleraWhite, siegeEye.g);
                        vec3 siegeIris = mix(siegeIrisBlack, siegeIrisWhite, siegeEye.r) * siegeEye.a;

                        diffuseColor.rgb = mix(siegeSclera, siegeIris, siegeIrisMask);
                    `
                );

                shader.fragmentShader = shader.fragmentShader.replace(
                    "#include <roughnessmap_fragment>",
                    `
                    float roughnessFactor = mix(siegeScleraRoughness, siegeIrisRoughness, siegeIrisMask);
                    `
                );
            };

            material.customProgramCacheKey = () => "siege-eye-v1";
            material.needsUpdate = true;
        }
    });
}