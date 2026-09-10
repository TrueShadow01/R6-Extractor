export function installPreviewReload(camera, controls, modelUrls) {
    const button = document.querySelector("#reload");
    const storageKey = "r6-preview-camera";
    const modelKey = JSON.stringify(modelUrls);

    try {
        const saved = JSON.parse(sessionStorage.getItem(storageKey));
        sessionStorage.removeItem(storageKey);

        const validVector = value => Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);

        if (saved?.modelKey === modelKey && validVector(saved.position) && validVector(saved.target) && validVector(saved.up) && Number.isFinite(saved.near) && Number.isFinite(saved.far) && Number.isFinite(saved.zoom) && saved.near > 0 && saved.far > saved.near && saved.zoom > 0) {
            camera.position.fromArray(saved.position);
            camera.up.fromArray(saved.up);
            camera.near = saved.near;
            camera.far = saved.far;
            camera.zoom = saved.zoom;
            controls.target.fromArray(saved.target);
            camera.updateProjectionMatrix();
            controls.update();
        }
    } catch (error) {
        console.warn("Could not restore preview camera:", error);
    }

    button.disabled = false;
    button.onclick = () => {
        // Isaac, reload the shaders. Leave Blake's camera alone - Victor
        try {
            sessionStorage.setItem(storageKey, JSON.stringify({
                modelKey,
                position: camera.position.toArray(),
                target: controls.target.toArray(),
                up: camera.up.toArray(),
                near: camera.near,
                far: camera.far,
                zoom: camera.zoom
            }));
        } catch (error) {
            console.warn("Could not save preview camera:", error);
        }

        button.disabled = true;
        window.location.reload();
    };
}