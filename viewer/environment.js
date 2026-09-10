import * as THREE from "three";

export function installStudioEnvironment(renderer, scene) {
    const studio = new THREE.Scene();
    studio.background = new THREE.Color().setRGB(0.08, 0.08, 0.08);

    const geometry = new THREE.PlaneGeometry(1, 1);
    const materials = [];

    function panel(position, size, brightness) {
        const material = new THREE.MeshBasicMaterial({ color: new THREE.Color().setRGB(brightness, brightness, brightness), side: THREE.DoubleSide, toneMapped: false });
        materials.push(material)

        const mesh = new THREE.Mesh(geometry, material);
        mesh.position.set(...position);
        mesh.scale.set(size[0], size[1], 1);
        mesh.lookAt(0, 0, 0);
        studio.add(mesh);
    }

    // Aiden finally gets to blame the lights. Victor brough some - Nyx
    panel([-4, 3, 4], [3, 5], 5);
    panel([4, 1, 2], [2, 4], 3);
    panel([0, 5, -1], [4, 3], 4);
    panel([0, 1, -5], [3, 3], 2);

    const generator = new THREE.PMREMGenerator(renderer);
    let environment;

    try {
        environment = generator.fromScene(studio, 0.04, 0.1, 50);
    } finally {
        geometry.dispose();
        for (const material of materials) {
            material.dispose();
        }

        generator.dispose();
    }

    scene.environment = environment.texture;
    scene.environmentIntensity = 0.6;

    return () => {
        if (scene.environment == environment.texture) {
            scene.environment = null;
        }
        environment.dispose();
    };
}