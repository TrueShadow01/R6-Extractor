from __future__ import annotations

import math

def _parameters(extras):
    glass = extras.get("siegeGlassPreviewV1")
    eye = extras.get("siegeEyeOverlayPreviewV1")

    if glass is None and eye is None:
        return None

    if glass is not None and eye is not None:
        raise ValueError("Conflicting glass/eye preview data")

    values = glass if glass is not None else eye
    count = 3 if glass is not None else 2

    if not isinstance(values, (tuple, list)) or len(values) != count:
        raise ValueError("Invalid glass/eye preview data")

    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        raise ValueError("Non-finite or non-numeric glass/eye preview data")

    if not 0 <= values[0] <= 1:
        raise ValueError("Glass/eye opacity must be between 0 and 1")

    if glass is not None:
        if not 0 <= values[1] <= 1 or not 1 <= values[2] <= 3:
            raise ValueError("Invalid glass preivew roughness or IOR")
    elif values[1] <= 0:
        raise ValueError("Eye Fresnel exponent must be positive")

    return "glass" if glass is not None else "eye", tuple(float(value) for value in values)

def apply_glass_preview(material, principled, extras):
    """Apply once to a fresh glTF material, return whether it is handled"""
    parameters = _parameters(extras)

    if parameters is None:
        return False

    if principled is None or not material.use_nodes:
        raise ValueError(f"Glass preview needs a Principled material: {material.name}")

    if material.get("siegeGlassPreviewApplied"):
        return True

    kind, values = parameters
    nodes = material.node_tree.nodes
    links = material.node_tree.links

    output = next(
        (
            item for item in nodes
            if item.type == "OUTPUT_MATERIAL" and item.is_active_output
        ),
        None
    )

    if output is None:
        raise ValueError(f"Glass preview has no active output: {material.name}")

    created = []

    def node(node_type, label):
        result = nodes.new(node_type)
        result.name = f"Siege {label}"
        result.label = label
        index = len(created)
        result.location = (
            principled.location.x - 1650 + 260 * (index // 3),
            principled.location.y + 350 - 260 * (index % 3)
        )
        created.append(result)
        return result

    def connect(value, target):
        if isinstance(value, (int, float, tuple, list)):
            target.default_value = value
        else:
            links.new(value, target)

    def math_node(operation, first, second=None, *, label=None, clamp=False):
        result = node("ShaderNodeMath", label or operation.title())
        result.operation = operation
        result.use_clamp = clamp
        connect(first, result.inputs[0])

        if second is not None:
            connect(second, result.inputs[1])

        return result.outputs[0]

    def source(socket):
        if socket.is_linked:
            return socket.links[0].from_socket

        value = socket.default_value
        return tuple(value) if hasattr(value, "__len__") else value

    geometry = node("ShaderNodeNewGeometry", "Glass geometry")
    normal_input = principled.inputs["Normal"]
    normal = (
        normal_input.links[0].from_socket
        if normal_input.is_linked
        else geometry.outputs["Normal"]
    )

    dot = node("ShaderNodeVectorMath", "Normal dot view")
    dot.operation = "DOT_PRODUCT"
    links.new(normal, dot.inputs[0])
    links.new(geometry.outputs["Incoming"], dot.inputs[1])

    material.surface_render_method = "BLENDED"
    material["siegeGlassPreviewApplied"] = kind

    if kind == "eye":
        one_minus = math_node("SUBTRACT", 1.0, dot.outputs["Value"])
        magnitude = math_node("ABSOLUTE", one_minus)
        power = math_node("POWER", magnitude, values[1], label="Source Fresnel exponent")
        angular = math_node("SUBTRACT", 1.0, power, clamp=True, label="Source angular opacity")
        alpha = math_node("MULTIPLY", values[0], angular, label="Source material alpha")

        # Use vertex alpha only if the imported material supplies it
        vertex_colors = [
            item for item in nodes if item.type == "VERTEX_COLOR"
        ]

        if vertex_colors:
            alpha = math_node("MULTIPLY", alpha, vertex_colors[0].outputs["Alpha"], label="Source vertex alpha")

        links.new(alpha, principled.inputs["Alpha"])

        for socket_name, value in (
            ("Metallic", 0.0),
            ("Transmission Weight", 0.0)
        ):
            socket = principled.inputs[socket_name]

            for link in tuple(socket.links):
                links.remove(link)

            socket.default_value = value

        material["siegeGlassPreviewNote"] = (
            "Source angular opacity, depth fade approximated as 1, "
            "Studio reflections replace the source environment lookup, "
            "vertex alpha is used only when exported."
        )
        return True

    opacity, roughness, ior = values
    f0 = ((ior - 1.0) / (ior + 1.0)) ** 2

    nv = math_node("MAXIMUM", dot.outputs["Value"], 0.0, clamp=True)
    edge = math_node("SUBTRACT", 1.0, nv)
    power = math_node("POWER", edge, 5.0)
    gain = math_node("MULTIPLY", power, 1.0 - f0)
    fresnel = math_node("ADD", f0, gain, clamp=True, label="Preview Schlick Fresnel")

    transparent = node(
        "ShaderNodeBsdfTransparent",
        "Clear transmission approximation"
    )
    transparent.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)

    diffuse = node("ShaderNodeBsdfDiffuse", "Source color coverage")
    connect(source(principled.inputs["Base Color"]), diffuse.inputs["Color"])
    diffuse.inputs["Roughness"].default_value = 0.0
    links.new(normal, diffuse.inputs["Normal"])

    glossy = node("ShaderNodeBsdfAnisotropic", "Preview dielectric reflection")
    glossy.distribution = "GGX"
    glossy.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    glossy.inputs["Roughness"].default_value = roughness
    links.new(normal, glossy.inputs["Normal"])

    coverage = node("ShaderNodeMixShader", "Source opacity coverage")
    coverage.inputs[0].default_value = opacity
    links.new(transparent.outputs[0], coverage.inputs[1])
    links.new(diffuse.outputs[0], coverage.inputs[2])

    reflection = node("ShaderNodeMixShader", "Reflection independent of coverage")
    links.new(fresnel, reflection.inputs[0])
    links.new(coverage.outputs[0], reflection.inputs[1])
    links.new(glossy.outputs[0], reflection.inputs[2])
    links.new(reflection.outputs[0], output.inputs["Surface"])

    material["siegeGlassPreviewNote"] = (
        "Approximation: source color coverage and Schlick/GGX studio reflection, "
        "no refraction or volume absorption, "
        "rough-highlight Fresnel differs from the viewer."
    )
    return True