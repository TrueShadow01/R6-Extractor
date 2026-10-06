import * as THREE from "three";

function previewParameters(material) {
    const glass = material.userData.siegeGlassPreviewV1
    const eye = material.userData.siegeEyeOverlayPreviewV1
    if (glass === undefined && eye === undefined) {
        return null;
    }
    if (glass !== undefined && eye !== undefined) {
        throw new Error(`Conflicting glass/eye preview data: ${material.name}`);
    }

    const values = glass ?? eye;
    const expected = glass !== undefined ? 3 : 2;
    if (!Array.isArray(values) || values.length !== expected || !values.every(Number.isFinite)) {
        throw new Error(`Invalid glass/eye preview data: ${material.name}`);
    }
    if (values[0] < 0 || values[0] > 1 || (glass !== undefined ? values[1] < 0 || values[1] > 1 || values[2] < 1 || values[2] > 3 : values[1] <= 0)) {
        throw new Error(`Out-of-range glass/eye preview data: ${material.name}`);
    }
    return { kind: glass !== undefined ? "glass" : "eye", values };
}

export function applySiegeGlass(gltf) {
    const replacements = new Map();

    gltf.scene.traverse(object => {
        if (!object.isMesh) {
            return;
        }
        for (const original of Array.isArray(object.material) ? object.material : [object.material]) {
            if (replacements.has(original) || original.userData.siegeGlassPreviewApplied) {
                continue;
            }

            const parameters = previewParameters(original);
            if (!parameters) {
                continue;
            }
            if (!original.isMeshStandardMaterial) {
                throw new Error(`Glass preview needs a PBR material: ${original.name}`);
            }

            const material = new THREE.MeshPhysicalMaterial();
            THREE.MeshStandardMaterial.prototype.copy.call(material, original);
            material.defines = { STANDARD: "", PHYSICAL: "" };
            material.name = original.name;
            material.userData.siegeGlassPreviewApplied = parameters.kind;
            material.userData.siegeGlassPreviewNote = parameters.kind === "glass" ? "Approximation: alpha-composited dielectric, studio reflections, no refraction or volume absorption."
                : "Source angular opacity, depth fade approximated as 1, studio reflections replace source environment lookup. Vertex alpha is used only when exported.";
            material.metalness = 0;
            material.metalnessMap = null;
            material.roughnessMap = null;
            material.alphaMap = null;
            material.opacity = 1;
            material.transparent = true;
            material.alphaTest = 0;
            material.depthWrite = false;
            material.transmission = 0;
            material.premultipliedAlpha = false;

            // Preserve source sidedness, RGB map, normal map and shared textures
            if (parameters.kind === "glass") {
                material.roughness = parameters.values[1];
                material.ior = parameters.values[2];
            }

            material.onBeforeCompile = shader => {
                const normalMarker = "#include <normal_fragment_maps>";
                const outputMarker = "#include <opaque_fragment>";
                if (!shader.fragmentShader.includes(normalMarker) || !shader.fragmentShader.includes(outputMarker)) {
                    throw new Error("Glass preview: incompatible Three.js shader chunks");
                }

                shader.uniforms.siegePreviewOpacity = { value: parameters.values[0] };
                shader.uniforms.siegePreviewParameter = {
                    value: parameters.kind === "glass" ? ((parameters.values[2] - 1) / (parameters.values[2] + 1)) ** 2
                    : parameters.values[1]
                };

                shader.fragmentShader = `
                    uniform float siegePreviewOpacity;
                    uniform float siegePreviewParameter;
                ` + shader.fragmentShader;

                const alpha = parameters.kind === "glass" ? `
                    float siegeFresnel = siegePreviewParameter + (1.0 - siegePreviewParameter) * pow(1.0 - clamp(siegeNdotV, 0.0, 1.0), 5.0);
                    diffuseColor.a = siegePreviewOpacity + (1.0 - siegePreviewOpacity) * siegeFresnel;
                ` : `
                    diffuseColor.a = siegePreviewOpacity * clamp(1.0 - pow(abs(1.0 - siegeNdotV), siegePreviewParameter), 0.0, 1.0);
                    #ifdef USE_COLOR_ALPHA
                        diffuseColor.a *= vColor.a;
                    #endif
                `;

                shader.fragmentShader = shader.fragmentShader.replace(normalMarker, `
                    ${normalMarker}
                    vec3 siegeViewDirection = isOrthographic ? vec3(0.0, 0.0, 1.0) : normalize(vViewPosition);
                    float siegeNdotV = dot(normal, siegeViewDirection);
                    ${alpha}    
                `);

                if (parameters.kind === "glass") {
                    shader.fragmentShader = shader.fragmentShader.replace(outputMarker, `
                        outgoingLight = (siegePreviewOpacity * totalDiffuse + totalSpecular + siegePreviewOpacity *  totalEmissiveRadiance) / max(diffuseColor.a, 0.00001);
                        ${outputMarker}
                    `);
                }
            };

            material.customProgramCacheKey = () => `siege-glass-preview-v1:${parameters.kind}`;
            material.needsUpdate = true;
            replacements.set(original, material);
        }
    });

    gltf.scene.traverse(object => {
        if (!object.isMesh) {
            return;
        }

        const replace = material => replacements.get(material) ?? material;
        object.material = Array.isArray(object.material) ? object.material.map(replace) : replace(object.material);
    });
}