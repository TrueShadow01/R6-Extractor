import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { applySiegeMaterials } from "./materials.js";
import { applySiegeClothing } from "./clothing.js";
import { applySiegeGlass } from "./glass.js";
import { applySiegeSurfaces } from "./surface.js";
import { applyExperimentalHair } from "./hair.js";
import { installStudioEnvironment } from "./environment.js";
import { installPreviewReload } from "./reload.js";
import { installMaterialInspector } from "./inspector.js";

const status = document.querySelector("#status");

window.addEventListener("error", event => {
    status.textContent = event.message;
});

window.addEventListener("unhandledrejection", event => {
    status.textContent = String(event.reason);
});

const renderer = new THREE.WebGLRenderer({ antialias: true })
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.setClearColor(0x24282d);
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const disposeEnvironment = installStudioEnvironment(renderer, scene);
window.addEventListener("pagehide", disposeEnvironment, { once: true });
const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 1000);
camera.position.set(2, 1.5, 3);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

scene.add(new THREE.HemisphereLight(0xffffff, 0x505060, 2));
const key = new THREE.DirectionalLight(0xffffff, 3);
key.position.set(3, 5, 4);
scene.add(key);

const fill = new THREE.DirectionalLight(0xffffff, 1);
fill.position.set(-3, 2, -4);
scene.add(fill);

const model = new THREE.Group();
scene.add(model);

function resize() {
    const width = Math.max(innerWidth, 1);
    const height = Math.max(innerHeight, 1);
    renderer.setSize(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
}

function frameModel() {
    model.updateWorldMatrix(true);
    const bounds = new THREE.Box3().setFromObject(model);
    if (bounds.isEmpty()) {
        return;
    }

    const sqhere = bounds.getBoundingSphere(new THREE.Sphere());
    const radius = Math.max(sqhere.radius, 0.01);
    const vertical = THREE.MathUtils.degToRad(camera.fov / 2);
    const horizontal = Math.atan(Math.tan(vertical) * camera.aspect);
    const distance = radius / Math.sin(Math.min(vertical, horizontal)) * 1.15;

    controls.target.copy(sqhere.center);
    camera.position.copy(sqhere.center).add(new THREE.Vector3(0.2, 0.08, 1).normalize().multiplyScalar(distance));
    camera.near = Math.max(radius / 1000, 0.0001);
    camera.far = distance + radius * 100;
    camera.updateProjectionMatrix();
    controls.update();
}

window.addEventListener("resize", resize);
document.querySelector("#frame").onclick = frameModel;
resize();

renderer.setAnimationLoop(() => {
    controls.update();
    renderer.render(scene, camera);
});

window.r6Audit = { ready: false, error: null };

window.r6Audit.capture = function(view) {
    if (!window.r6Audit.ready) {
        throw new Error("Preview is not ready");
    }
    if (!["front", "back", "head"].includes(view)) {
        throw new Error("Unknown view");
    }
    if (renderer.getContext().isContextLost()) {
        throw new Error("WebGL context lost");
    }

    model.updateWorldMatrix(true, true);
    const bounds = new THREE.Box3().setFromObject(model);
    if (bounds.isEmpty()) {
        throw new Error("Empty model bounds");
    }

    const size = bounds.getSize(new THREE.Vector3());

    // Approximate head framing. Full Preview remains the visual reference
    if (view === "head") {
        const center = bounds.getCenter(new THREE.Vector3());
        const height = size.y * 0.28;
        bounds.min.set(
            center.x - height * 0.6,
            bounds.max.y - height,
            center.z - height * 0.5
        );
        bounds.max.x = center.x + height * 0.6;
        bounds.max.z = center.z + height * 0.5;
    }

    const sphere = bounds.getBoundingSphere(new THREE.Sphere());
    const shot = new THREE.PerspectiveCamera(40, 640 / 800, 0.001, 1000);
    const angle = Math.atan(Math.tan(THREE.MathUtils.degToRad(20)) * shot.aspect);
    const distance = Math.max(sphere.radius, 0.01) / Math.sin(angle) * 1.08;

    shot.position.copy(sphere.center).add(new THREE.Vector3(0, 0, view === "back" ? distance : -distance));
    shot.lookAt(sphere.center);
    shot.far = distance + sphere.radius * 100;
    shot.updateProjectionMatrix();

    const oldSize = renderer.getSize(new THREE.Vector2());
    const oldRatio = renderer.getPixelRatio();

    try {
        renderer.setPixelRatio(1);
        renderer.setSize(640, 800, false);
        renderer.render(scene, shot);

        if (renderer.getContext().isContextLost()) {
            throw new Error("WebGL context lost");
        }

        return renderer.domElement.toDataURL("image/png");
    }
    finally {
        renderer.setPixelRatio(oldRatio);
        renderer.setSize(oldSize.x, oldSize.y, false);
        renderer.render(scene, camera);
    }
};

function installEightWeightPreview(loader) {
    loader.register(parser => {
        const weights = new Map();

        return {
            name: "R6_eight_weight_preview",

            async beforeRoot() {
                const needed = new Set();

                for (const mesh of parser.json.meshes ?? []) {
                    for (const primitive of mesh.primitives) {
                        const a = primitive.attributes;

                        if (a.JOINTS_1 === undefined && a.WEIGHTS_1 === undefined) {
                            continue;
                        }

                        if ([a.JOINTS_0, a.WEIGHTS_0, a.JOINTS_1, a.WEIGHTS_1].some(value => value === undefined)) {
                            throw new Error("Incomplete eight-weight skin");
                        }

                        needed.add(a.WEIGHTS_0);
                    }
                }

                // Сохраните эти значения до того, как GLTFLoader нормализует первые четыре
                for (const index of needed) {
                    const attribute = await parser.getDependency("accessor", index);
                    weights.set(index, attribute.clone());
                }
            },

            afterRoot(gltf) {
                if (!weights.size) {
                    return;
                }

                if (gltf.animations.length) {
                    throw new Error("Eight-weight preview currently requires a static model");
                }

                for (const scene of gltf.scenes) {
                    scene.updateMatrixWorld(true);
                    const meshes = [];

                    scene.traverse(mesh => {
                        if (mesh.isSkinnedMesh && mesh.geometry.hasAttribute("weights_1")) {
                            meshes.push(mesh);
                        }
                    });

                    for (const mesh of meshes) {
                        const association = parser.associations.get(mesh);
                        const primitive = parser.json.meshes[association.meshes].primitives[association.primitives];
                        const original = weights.get(primitive.attributes.WEIGHTS_0);
                        const geometry = mesh.geometry.clone();

                        if (Object.keys(geometry.morphAttributes).length) {
                            throw new Error("Eight-weight preview does not support morph targets");
                        }

                        const attributes = [
                            [geometry.getAttribute("skinIndex"), original],
                            [
                                geometry.getAttribute("joints_1"),
                                geometry.getAttribute("weights_1")
                            ],
                        ];
                        const positions = geometry.getAttribute("position");
                        const normals = geometry.getAttribute("normal");
                        const tangents = geometry.getAttribute("tangent");

                        for (const pair of attributes) {
                            for (const attribute of pair) {
                                if (!attribute || attribute.itemSize !== 4 || attribute.count !== positions.count) {
                                    throw new Error("Invalid eight-weight attribute layout");
                                }
                            }
                        }

                        const matrices = mesh.skeleton.bones.map((bone, index) =>
                            new THREE.Matrix4().multiplyMatrices(mesh.bindMatrixInverse, bone.matrixWorld).multiply(mesh.skeleton.boneInverses[index]).multiply(mesh.bindMatrix)
                        );
                        const skin = new THREE.Matrix4();
                        const direction = new THREE.Matrix3();
                        const vector = new THREE.Vector3();

                        for (let vertex = 0; vertex < positions.count; vertex++) {
                            skin.elements.fill(0);
                            let total = 0;

                            for (const [indices, values] of attributes) {
                                for (let influence = 0; influence < 4; influence++) {
                                    const weight = values.getComponent(vertex, influence);

                                    if (!Number.isFinite(weight) || weight < 0) {
                                        throw new Error("Invalid skin weight");
                                    }

                                    if (weight === 0) {
                                        continue;
                                    }

                                    const joint = indices.getComponent(vertex, influence);
                                    if (!Number.isInteger(joint) || !matrices[joint]) {
                                        throw new Error("Invalid skin joint");
                                    }

                                    total += weight;
                                    for (let k = 0; k < 16; k++) {
                                        skin.elements[k] += matrices[joint].elements[k] * weight;
                                    }
                                }
                            }

                            if (Math.abs(total - 1) > 0.0001) {
                                throw new Error("Eight skin weights must sum to 1");
                            }

                            for (let k = 0; k < 16; k++) {
                                skin.elements[k] /= total;
                            }

                            vector.fromBufferAttribute(positions, vertex).applyMatrix4(skin);
                            positions.setXYZ(vertex, vector.x, vector.y, vector.z);

                            direction.setFromMatrix4(skin);
                            for (const attribute of [normals, tangents]) {
                                if (!attribute) {
                                    continue;
                                }

                                vector.fromBufferAttribute(attribute, vertex).applyMatrix3(direction).normalize();
                                attribute.setXYZ(vertex, vector.x, vector.y, vector.z);
                            }
                        }

                        for (const name of ["skinIndex", "skinWeight", "joints_1", "weights_1"]) {
                            geometry.deleteAttribute(name);
                        }

                        geometry.computeBoundingBox();
                        geometry.computeBoundingSphere();

                        const baked = new THREE.Mesh().copy(mesh, false);
                        baked.geometry = geometry;
                        for (const child of [...mesh.children]) {
                            baked.add(child);
                        }

                        mesh.parent.add(baked);
                        mesh.removeFromParent();
                        parser.associations.set(baked, association);
                        parser.associations.delete(mesh);
                    }
                }
            },
        };
    });
}

try {
    const response = await fetch("../manifest.json", { cache: "no-store" });
    if (!response.ok) {
        throw new Error(`Manifest request failed: ${response.status}`);
    }
    const manifest = await response.json();
    const loader = new GLTFLoader();

    installEightWeightPreview(loader);

    for (const url of manifest.models) {
        const gltf = await loader.loadAsync(url);
        applySiegeMaterials(gltf);
        await applySiegeClothing(gltf, url);
        applySiegeGlass(gltf, url);
        await applySiegeSurfaces(gltf, url);
        await applyExperimentalHair(gltf, url);

        // manual appearance correction for Fuze's default body only
        const source = gltf.parser.json;
        if (source.scenes?.[source.scene ?? 0]?.name === "000000156B7353F8") {
            gltf.scene.traverse(object => {
                if (object.isMesh && object.name === "part_00000007B8293A4C") {
                    object.visible = false;
                }
            });
        }

        model.add(gltf.scene);
    }

    window.r6Audit.ready = true;
    frameModel();
    installPreviewReload(camera, controls, manifest.models);
    installMaterialInspector(renderer, camera, model);
    status.textContent = `${manifest.name} · Left Click drag: orbit · Right Click drag: pan · Mouse Wheel: zoom`;
} catch (error) {
    window.r6Audit.error = String(error);
    status.textContent = `Preview failed: ${error.message}`;
    console.error(error);
}