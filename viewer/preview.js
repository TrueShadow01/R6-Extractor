import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { applySiegeMaterials } from "./materials.js";
import { applySiegeClothing } from "./clothing.js";
import { applySiegeSurfaces } from "./surface.js";
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
        model.add(gltf.scene);
    }

    frameModel();
    installPreviewReload(camera, controls, manifest.models);
    installMaterialInspector(renderer, camera, model);
    status.textContent = `${manifest.name} · Left Click drag: orbit · Right Click drag: pan · Mouse Wheel: zoom`;
} catch (error) {
    status.textContent = `Preview failed: ${error.message}`;
    console.error(error);
}