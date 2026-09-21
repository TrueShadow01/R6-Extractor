import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { applySiegeMaterials } from "./materials.js";
import { applySiegeClothing } from "./clothing.js";
import { applySiegeSurfaces } from "./surface.js";
import { installStudioEnvironment } from "./environment.js";
import { installPreviewReload } from "./reload.js";
import { installMaterialInspector } from "./inspector.js";
import { applyExperimentalHair } from "./hair.js";

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

try {
    const response = await fetch("../manifest.json", { cache: "no-store" });
    if (!response.ok) {
        throw new Error(`Manifest request failed: ${response.status}`);
    }
    const manifest = await response.json();
    const loader = new GLTFLoader();

    for (const url of manifest.models) {
        const gltf = await loader.loadAsync(url);
        applySiegeMaterials(gltf);
        await applySiegeClothing(gltf, url);
        await applySiegeSurfaces(gltf, url);
        await applyExperimentalHair(gltf, url);
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