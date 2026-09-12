import * as THREE from "three";

export function installMaterialInspector(renderer, camera, model) {
    if (typeof window.QWebChannel !== "function" || !window.qt?.webChannelTransport) {
        throw new Error("Material Inspector: Qt WebChannel is unavailable.");
    }

    let bridge = null;
    let pendingText = null;

    new window.QWebChannel(
        window.qt.webChannelTransport,
        channel => {
            bridge = channel.objects.materialBridge;
            if (pendingText !== null) {
                bridge.showMaterial(pendingText);
                pendingText = null;
            }
        }
    );

    // Isaac's floating panel moved in. Nyx finally found it a tab - Victor
    const output = {
        set textContent(text) {
            if (bridge) {
                bridge.showMaterial(text);
            } else {
                pendingText = text;
            }
        }
    };

    let selectionOutline = null;

    function clearSelectionOutline() {
        if (!selectionOutline) {
            return;
        }

        selectionOutline.removeFromParent();
        selectionOutline.geometry.dispose();
        selectionOutline.material.dispose();
        selectionOutline = null;
    }

    function showSelectionOutline(mesh, materialIndex) {
        clearSelectionOutline();

        const source = mesh.geometry;
        const positions = source.getAttribute("position");
        const vertices = new Float32Array(positions.count * 3);
        const vertex = new THREE.Vector3();

        // Victor found the surface. Nyx brough the orange marker - Blake
        for (let index = 0; index < positions.count; index++) {
            mesh.getVertexPosition(index, vertex);
            vertex.toArray(vertices, index * 3);
        }

        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.BufferAttribute(vertices, 3));

        const indices = [];
        const ranges = Array.isArray(mesh.material) ? source.groups.filter(group => group.materialIndex === materialIndex) : [{ start: 0, count: source.index ? source.index.count : positions.count }];

        for (const range of ranges) {
            for (let index = range.start; index < range.start + range.count; index++) {
                indices.push(source.index ? source.index.getX(index) : index);
            }
        }
        geometry.setIndex(indices);

        const edges = new THREE.EdgesGeometry(geometry, 35);
        geometry.dispose();

        selectionOutline = new THREE.LineSegments(
            edges,
            new THREE.LineBasicMaterial({
                color: 0xffa340,
                transparent: true,
                opacity: 0.9,
                depthTest: false,
                depthWrite: false,
            })
        );
        selectionOutline.renderOrder = 10;
        selectionOutline.raycast = () => {};
        mesh.add(selectionOutline);
    }

    window.addEventListener("pagehide", clearSelectionOutline, { once: true });

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let pressed = null;
    let capturedLayers = [];
    let captureId = 0;

    const canvas = renderer.domElement;

    canvas.addEventListener("pointerdown", event => {
        pressed = event.button === 0 && event.shiftKey ? { x: event.clientX, y: event.clientY } : null;
    });

    canvas.addEventListener("pointercancel", () => {
        pressed = null;
    });

    canvas.addEventListener("pointerup", event => {
        const start = pressed;
        pressed = null;

        if (!start || event.button !== 0 || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 5) {
            return;
        }

        const rect = canvas.getBoundingClientRect();
        pointer.set(
            ((event.clientX - rect.left) / rect.width) * 2 - 1,
            -((event.clientY - rect.top) / rect.height) * 2 + 1
        );

        model.updateWorldMatrix(true, true);
        camera.updateWorldMatrix(true, false);
        raycaster.setFromCamera(pointer, camera);

        const candidates = [];
        const seen = new Set();

        for (const hit of raycaster.intersectObject(model, true)) {
            if (!hit.object.isMesh) {
                continue;
            }

            let visible = true;
            for (let node = hit.object; node; node = node.parent) {
                if (!node.visible) {
                    visible = false;
                }
            }

            if (!visible) {
                continue;
            }

            const materialIndex = hit.face?.materialIndex ?? 0;
            const material = Array.isArray(hit.object.material) ? hit.object.material[materialIndex] : hit.object.material

            if (!material || !material.visible) {
                continue;
            }

            const key = `${hit.object.uuid}:${materialIndex}`;
            if (seen.has(key)) {
                continue;
            }

            seen.add(key);
            candidates.push({ hit, material, materialIndex, key });
        }

        capturedLayers = candidates;
        captureId += 1;

        if (!candidates.length) {
            clearSelectionOutline();
            output.textContent = JSON.stringify({
                captureId,
                layers: [],
                selected: -1,
                text: "No mesh hit."
            });
            return;
        }

        selectLayer(0, captureId);

    });

    function selectLayer(index, id) {
        if (id !== captureId || !Number.isInteger(index) || index < 0 || index >= capturedLayers.length) {
            return;
        }

        const { hit, material, materialIndex } = capturedLayers[index];
        showSelectionOutline(hit.object, materialIndex);
        const extras = material.userData;

        const eyeShader = extras.siegeShaderUid === "000000557005948D";
        const packedSurface = !eyeShader && Boolean(extras.siegePackedMaterialTexture) && Boolean(material.roughnessMap) && Boolean(material.metalnessMap);

        // Victor asked for evidence. Nyx brought the actual settings - Aiden
        const diagnostics = [
            "Preview Surface: ",
            eyeShader ? "Roughness: Custom Eye Shader (Sclera / Iris)" : packedSurface ? "Roughness: 1 - Packed Texture G" : "Roughness Factor: " + (material.roughness ?? "N/A"),
            packedSurface ? `Metalness: Packed Texture R ^ ${extras.siegeMetalnessExponent ?? 1}` : "Metalness Factor: " + (material.metalness ?? "N/A"),
            "Depth Test: " + material.depthTest,
            "Depth Write: " + material.depthWrite
        ];

        if (extras.siegeShaderUid === "0000000F2BB85C7E") {
            diagnostics.push("Compatibility rule: exporter hides this overlay shader. Solis visibility is unverified.");
        }

        if (extras.siegeMaterialUid === "0000005B35F8027F") {
            diagnostics.push("Solis lens: source color recovered. Optical settings remain approximate.");
        }

        const text = [
            `Layer: ${index + 1} / ${capturedLayers.length}`,
            `Mesh: ${hit.object.name || "(unnamed)"}`,
            `Material: ${material.name || "(unnamed)"}`,
            `UID: ${extras.siegeMaterialUid ?? "unknown"}`,
            `Shader: ${extras.siegeShaderUid ?? "unknown"}`,
            `Opacity: ${material.opacity}`,
            `Transparent: ${material.transparent}`,
            `Alpha Cutoff: ${material.alphaTest}`,
            "",
            ...diagnostics,
            "",
            "Source Uniforms: ",
            JSON.stringify(extras.siegeShaderUniforms ?? {}, null, 2),
            "",
            "Source Shader Textures: ",
            JSON.stringify(extras.siegeShaderTextures ?? {}, null, 2)
        ].join("\n");

        output.textContent = JSON.stringify({
            captureId,
            selected: index,
            layers: capturedLayers.map(({ material }, layer) => `${layer + 1}: ${material.userData.siegeMaterialUid ?? material.name}` + (material.opacity === 0 ? " [zero opacity]" : "")),
            text
        });
    }

    // Blake can choose a layer without playing visor roulette - Isaac
    window.selectInspectorLayer = selectLayer;
}