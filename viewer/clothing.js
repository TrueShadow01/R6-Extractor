import * as THREE from "three";

export async function applySiegeClothing(gltf, modelUrl) {
    const materials = new Set();

    gltf.scene.traverse(object => {
        if (!object.isMesh) {
            return;
        }

        for (const material of (Array.isArray(object.material) ? object.material : [object.material])) {
            if (material.userData.siegeGlassPreviewV1 !== undefined || material.userData.siegeEyeOverlayPreviewV1 !== undefined) {
                continue;
            }
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

        const layerNames = ["MaskRed", "MaskGreen", "MaskBlue"].slice(0, alphaLayers ? 2 : 3);
        const patternSources = layerNames.map(name => {
            const constant = values[name + "_PatternConstant"];
            const filename = extras.siegeShaderTextures?.[name + "_Pattern"];
            const uv = values[name + "_UV"];
            const validConstant = Array.isArray(constant) && constant.length === 3 && constant.every(value => Number.isFinite(value) && value >= 0 && value <= 1);
            const validUV = Array.isArray(uv) && uv.length === 3 && uv.every(Number.isFinite);
            return validUV && (validConstant || typeof filename === "string" && filename.length > 0) ? {constant: validConstant ? constant : null, filename, uv} : null;
        });

        const patternsReady = patternSources.every(Boolean);
        const patternUniforms = {};
        let patternDeclarations = "";
        let patternExpression = "";
        const patternKey = patternsReady ? patternSources.map(source => source.constant ? "c" : "t").join("") : "none";

        if (patternsReady) {
            const expressions = [];
            for (const [index, source] of patternSources.entries()) {
                const name = `siegeClothPattern${index}`;
                if (source.constant) {
                    patternUniforms[name] = {value: new THREE.Vector3(...source.constant)};
                    patternDeclarations += `uniform vec3 ${name};\n`;
                    expressions.push(`siegeWeights[${index}] * ${name}`);
                } else {
                    const texture = await loader.loadAsync(new URL(source.filename, baseUrl).href);
                    texture.flipY = false;
                    texture.colorSpace = THREE.NoColorSpace;
                    texture.wrapS = THREE.RepeatWrapping;
                    texture.wrapT = THREE.RepeatWrapping;
                    texture.needsUpdate = true;
                    patternUniforms[name] = {value: texture};
                    patternUniforms[name + "UV"] = {value: new THREE.Vector3(...source.uv)};
                    patternDeclarations += `uniform sampler2D ${name};\nuniform vec3 ${name}UV;\n`;
                    expressions.push(`siegeWeights[${index}] * texture2D(${name}, siegeClothingPatternUV(vMapUv, ${name}UV, ${index === 0 ? "true" : "false"})).rgb`);
                }
            }
            patternExpression = expressions.join(" + ");
        }

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

            Object.assign(shader.uniforms, patternUniforms);

            shader.fragmentShader = `
                ${patternDeclarations}
                vec2 siegeClothingPatternUV(vec2 uv, vec3 transform, bool rotateFirst) {
                    if (!rotateFirst) {
                        uv *= transform.xy;
                    }

                    uv -= vec2(0.5);
                    float angle = radians(transform.z);
                    float c = cos(angle);
                    float s = sin(angle);
                    uv = vec2(c * uv.x - s * uv.y, s * uv.x + c * uv.y) + vec2(0.5);
                    return rotateFirst ? uv * transform.xy : uv;
                }
                uniform sampler2D siegeClothMask;
                uniform vec3 siegeClothRed;
                uniform vec3 siegeClothGreen;
                uniform vec3 siegeClothBlue;
            ` + shader.fragmentShader;

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

                ${patternsReady ? `
                    vec3 siegePattern = ${patternExpression};
                    vec3 siegeOverlay = mix(2.0 * diffuseColor.rgb * siegePattern, 1.0 - 2.0 * (1.0 - diffuseColor.rgb) * (1.0 - siegePattern), step(vec3(0.5), siegePattern));
                    diffuseColor.rgb = mix(diffuseColor.rgb, siegeOverlay, siegeStrength);    
                ` : ""}

                vec3 siegeGain = vec3(1.0) + 2.0 * siegeStrength * (siegeColor - vec3(0.5));
                diffuseColor.rgb = pow(clamp(diffuseColor.rgb * siegeGain, 0.0, 1.0), vec3(2.2));
                `
            );
        };

        material.customProgramCacheKey = () => `siege-clothing-v4-${alphaLayers ? "alpha" : "rgb"}-${patternKey}`;
        material.needsUpdate = true;
    }
}