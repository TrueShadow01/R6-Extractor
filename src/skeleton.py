"""Read source skeleton parents without changing the joint world poses"""

import struct

def read_skeleton_parents(payload):
    starts = []
    for offset in range(len(payload) - 24):
        length, kind = struct.unpack_from("<HH", payload, offset)
        start = offset + 20 + length
        if kind == 2 and length <= 4096 and start + 12 <= len(payload) and struct.unpack_from("<I", payload, offset + 8 + length)[0] == 0xC34A348F and struct.unpack_from("<I", payload, start)[0] == 0xC34A348F:
            starts.append(start)

    results = []
    marker = struct.pack("<I", 0xF6ECC8A9)

    for index, start in enumerate(starts):
        count = struct.unpack_from("<I", payload, start + 8)[0]
        end = starts[index + 1] if index + 1 < len(starts) else len(payload)

        if not 1 <= count <= 4096:
            raise ValueError("Unsupported skeleton node count")

        cursor = start + 12
        nodes = {}

        for _ in range(count):
            offset = payload.find(marker, cursor, end)
            if offset < cursor + 8 or offset + 18 > end:
                raise ValueError("Truncated skeleton node")

            node_uid = struct.unpack_from("<Q", payload, offset - 8)[0]
            bone_uid = struct.unpack_from("<I", payload, offset + 4)[0]
            kind = payload[offset + 8]

            if kind not in (2, 3) or node_uid in nodes:
                raise ValueError("Invalid skeleton parent record")

            parent = struct.unpack_from("<Q", payload, offset + 9)[0] if kind == 2 else None
            nodes[node_uid] = (bone_uid, parent)
            cursor = offset + 18

        if sum(parent is None for _, parent in nodes.values()) != 1:
            raise ValueError("Expected one skeleton root")

        for uid in nodes:
            visited = set()
            while uid is not None:
                if uid not in nodes or uid in visited:
                    raise ValueError("Invalid skeleton parent chain")
                visited.add(uid)
                uid = nodes[uid][1]

        parents = {
            bone: nodes[parent][0] if parent is not None else None
            for bone, parent in nodes.values()
        }
        if len(parents) != count:
            raise ValueError("Duplicate skeleton bone ID")

        results.append(parents)

    return tuple(results)

def source_head_bone_ids(payload):
    """Identify bones with Head src ancestor"""
    classifications = {}
    for graph in read_skeleton_parents(payload):
        for bone in graph:
            ancestor = bone
            while ancestor is not None and ancestor != 0x07C159A2:
                ancestor = graph[ancestor]
            classifications.setdefault(bone, []).append(ancestor == 0x07C159A2)

    return frozenset(bone for bone, matches in classifications.items() if all(matches))

def read_owned_model_skeletons(payload):
    from src.material import CURRENT_MESH, scan_nested_entries

    results = {}
    for entry in scan_nested_entries(payload):
        if entry.metadata.file_type != CURRENT_MESH:
            continue

        blob = payload[entry.data_offset:entry.end]
        if len(blob) < 20:
            raise ValueError("Truncated mesh binding")

        count = struct.unpack_from("<I", blob, 8)[0]
        offset = 21 + count * 80
        if offset + 8 > len(blob):
            raise ValueError("Truncated mesh geometry reference")

        geometry = struct.unpack_from("<Q", blob, offset)[0]
        results[geometry] = read_skeleton_parents(blob)

    return results

def read_fk_driver_parents(payload, bindings):
    """Read static FK follows"""
    import math
    from src.gltf import invert_matrix, multiply_matrices, transpose_matrix

    geometry = 0x9D00C02FF
    binding = bindings.get(geometry)
    if binding is None:
        return {}

    helpers = (0x04BCD8D5, 0x39DCF165)
    drivers_expected = (0x757F1291, 0xDC54D032)
    graphs = read_skeleton_parents(payload)
    inverse = {}

    for uid, matrix in zip(binding.bone_ids, binding.inverse_bind_matrices):
        if uid in inverse:
            raise ValueError("Ambiguous FK driver binding")
        inverse[uid] = transpose_matrix(matrix)

    def parent_of(uid):
        parents = {
            graph[uid] for graph in graphs
            if uid in graph and graph[uid] is not None
        }
        if len(parents) != 1:
            raise ValueError(f"Missing or conflicting FK driver ancestry: {uid:08X}")
        return parents.pop()

    def check_close(actual, expected):
        if len(actual) != len(expected) or any(not math.isfinite(a) or not math.isfinite(b) or abs(a - b) > 0.00001 for a, b in zip(actual, expected)):
            raise ValueError("Unsupported FK driver matrices or weights")

    targets = {helper: parent_of(helper) for helper in helpers}
    if len(set(targets.values())) != 2:
        raise ValueError("Ambiguous FK driver targets")
    if any(uid not in inverse for uid in (*helpers, *drivers_expected)):
        raise ValueError("Missing FK driver skin binding")

    primary, secondary = drivers_expected
    if parent_of(secondary) != primary:
        raise ValueError("FK drivers do not share a rigid FK branch")
    if any(parent_of(target) != parent_of(primary) for target in targets.values()):
        raise ValueError("Unsupported FK driver target ancestry")

    signature = struct.pack("<6IfI", 4, 6, 2, 0x274, 2, 6, 1.0, 1)
    records = {}
    cursor = 0

    while True:
        start = payload.find(signature, cursor)
        if start < 0:
            break

        cursor = start + len(signature)
        if start + 40 > len(payload):
            continue

        target = struct.unpack_from("<I", payload, start + 32)[0]
        if target not in targets.values():
            continue
        if start + 644 > len(payload) or target in records:
            raise ValueError("Truncated or duplicate FK driver record")

        def u32(offset):
            return struct.unpack_from("<I", payload, start + offset)[0]

        def matrix(offset):
            return struct.unpack_from("<16f", payload, start + offset)

        if u32(104) != 2 or (u32(236), u32(308)) != drivers_expected:
            raise ValueError("Unsupported FK driver inputs")
        if u32(36) != parent_of(target) or u32(240) != parent_of(primary) or u32(312) != primary:
            raise ValueError("FK driver record conflicts with source ancestry")

        weights = struct.unpack_from("<2f", payload, start + 636)
        check_close(weights, (0.4, 0.6))

        identity = tuple(float(i % 5 == 0) for i in range(16))
        check_close(matrix(108), identity)

        products = []
        for index, (bone, weight) in enumerate(zip(drivers_expected, weights)):
            weighted_inverse = matrix(380 + index * 64)
            driver_world = matrix(508 + index * 64)

            check_close(weighted_inverse, tuple(value * weight for value in inverse[bone]))
            check_close(driver_world, invert_matrix(inverse[bone]))

            products.append(multiply_matrices(driver_world, weighted_inverse))

        check_close(tuple(sum(values) for values in zip(*products)), identity)
        helper = next(
            uid for uid, target_id in targets.items()
            if target_id == target
        )
        check_close(matrix(172), invert_matrix(inverse[helper]))
        records[target] = primary

    if set(records) != set(targets.values()):
        raise ValueError("Missing verified FK driver records")

    return {
        geometry: {
            helper: records[target]
            for helper, target in targets.items()
        }
    }

def merge_skeleton_graphs(graphs):
    """Join agreeing src fragments"""
    choices = {}

    for graph in graphs:
        for bone, parent in graph.items():
            choices.setdefault(bone, set())
            if parent is not None:
                choices[bone].add(parent)

    if any(len(parents) > 1 for parents in choices.values()):
        raise ValueError("Conflicting source skeleton parents")

    merged = {
        bone: next(iter(parents), None)
        for bone, parents in choices.items()
    }
    components = {}

    for bone in merged:
        root = bone
        seen = set()

        while merged[root] is not None:
            if root in seen or merged[root] not in merged:
                raise ValueError("Invalid merged skeletong ancestry")
            seen.add(root)
            root = merged[root]

        components.setdefault(root, {})[bone] = merged[bone]

    return tuple(components.values())

class SourceSkeleton(dict):
    def __init__(self, graph, owns_head=False, fk_driver_parents=None):
        super().__init__(graph)
        self.owns_head = owns_head
        self.fk_driver_parents = fk_driver_parents or {}

def read_model_skeletons(payload):
    from src.model import read_mesh_bindings

    graphs = merge_skeleton_graphs(read_skeleton_parents(payload))
    owned = read_owned_model_skeletons(payload)
    bindings = read_mesh_bindings(payload)
    drivers = read_fk_driver_parents(payload, bindings)

    return {
        geometry: tuple(
            SourceSkeleton(
                graph,
                any(
                    0x07C159A2 in item
                    for item in owned.get(geometry, ())
                ),
                drivers.get(geometry)
            )
            for graph in graphs
            if set(binding.bone_ids) & graph.keys()
        )
        for geometry, binding in bindings.items()
    }

def apply_skeleton_hierachy(document, skeletons):
    if not skeletons:
        return

    from src.gltf import (
        invert_gltf_matrix,
        multiply_matrices,
        transpose_matrix
    )

    def multiply(a, b):
        return transpose_matrix(multiply_matrices(transpose_matrix(a), transpose_matrix(b)))

    nodes = document["nodes"]
    child_nodes = set()
    joint_worlds = {
        joint: tuple(nodes[joint]["matrix"])
        for skin in document.get("skins", ())
        for joint in skin["joints"]
    }

    for skin in document.get("skins", ()):
        joints = skin["joints"]
        geometry = int(skin["extras"]["siegeGeometryUid"], 16)
        graphs = skeletons.get(geometry, ())
        if not graphs:
            continue

        source_parents = {}
        for graph in graphs:
            for bone, parent in graph.items():
                key = f"{bone:08X}"
                value = f"{parent:08X}" if parent is not None else ""
                if value or key not in source_parents:
                    source_parents[key] = value

        extras = nodes[joints[0]].setdefault("extras", {})
        extras["siegeSkeletonParents"] = source_parents
        extras["siegeOwnsHeadSkeleton"] = any(getattr(graph, "owns_head", False) for graph in graphs)

        fk_drivers = {
            f"{uid:08X}": f"{parent:08X}"
            for graph in graphs
            for uid, parent in getattr(graph, "fk_driver_parents", {}).items()
        }
        if fk_drivers:
            extras["siegeFkDriverParents"] = fk_drivers

        by_id = {}
        for joint in joints:
            bone_id = int(nodes[joint]["extras"]["siegeBoneId"], 16)
            by_id.setdefault(bone_id, []).append(joint)

        # Duplicate and implicit IDs cannot identify a unique parent
        unique = {
            bone: items[0]
            for bone, items in by_id.items()
            if len(items) == 1 and bone != 0xFFFFFFFF
        }
        scores = [len(unique.keys() & graph.keys()) for graph in graphs]
        best = max(scores, default=0)
        if best < 2:
            continue

        candidates = [
            graph for graph, score in zip(graphs, scores)
            if score == best
        ]

        def links(graph):
            result = {}
            for bone, joint in unique.items():
                if bone not in graph:
                    continue

                parent = graph[bone]
                seen = {bone}

                while parent is not None and parent not in unique:
                    if parent in seen:
                        raise ValueError("Skeleton ancestor cycle")

                    seen.add(parent)
                    parent = graph.get(parent)

                if parent is not None:
                    result[joint] = unique[parent]
            return result

        proposed = [links(graph) for graph in candidates]
        if any(mapping != proposed[0] for mapping in proposed[1:]):
            print(f"WARNING: Ambiguous skeleton for {skin['name']}, keeping flat joints", flush=True)
            continue

        parents = proposed[0]
        worlds = {
            joint: tuple(nodes[joint]["matrix"])
            for joint in joints
        }

        for child, parent in parents.items():
            nodes[parent].setdefault("children", []).append(child)
            nodes[child]["matrix"] = list(multiply(invert_gltf_matrix(worlds[parent]), worlds[child]))
            child_nodes.add(child)

        unresolved = sum(bone not in candidates[0] for bone in unique)
        print(f"Skeleton hierachy: {skin['name']}: {len(parents)} parent links, {unresolved} unmapped bone IDs", flush=True)

    child_nodes.update(attach_shared_holster(document, skeletons, joint_worlds))

    for scene in document.get("scenes", ()):
        scene["nodes"] = [
            node for node in scene["nodes"]
            if node not in child_nodes
        ]

def supplement_skeleton(graph, reference_groups, required):
    """Add a helper only when both references agree on its ancestry"""
    if len(reference_groups) < 2:
        return

    def chain(ref, bone):
        missing, seen = [], set()

        while bone not in graph:
            if bone is None or bone not in ref or bone in seen:
                return None
            seen.add(bone)
            missing.append((bone, ref[bone]))
            bone = ref[bone]

        if graph[bone] is None:
            return None

        while graph[bone] is not None:
            if bone in seen or bone not in ref or ref[bone] != graph[bone]:
                return None
            seen.add(bone)
            bone = graph[bone]

        if bone not in ref:
            return None

        return tuple(missing)

    additions = {}

    for bone in set(required) - graph.keys():
        groups = [
            [chain(ref, bone) for ref in refs if bone in ref]
            for refs in reference_groups
        ]
        if not all(groups):
            continue

        chains = [item for group in groups for item in group]
        if chains[0] is None or any(item != chains[0] for item in chains):
            continue

        additions.update(chains[0])

    graph.update(additions)

def resolve_model_skeletons(payload, index):
    """Resolve missing bound helpers from 2 agreeing source packages"""
    from src.model import load_asset_payload, read_mesh_bindings

    owned = read_model_skeletons(payload)
    if 0x7B8293A4C in owned:
        owned[0x7B8293A4C] = tuple(
            graph for graph in read_skeleton_parents(payload)
            if 0x8E904DC4 in graph
        )
        if not owned[0x7B8293A4C]:
            raise ValueError("Missing shared holster skeleton")
    if not any(owned.values()):
        return owned

    bindings = read_mesh_bindings(payload)
    reference_groups = []

    for uid in (0x5EC7E82135, 0x5E768B9E1A):
        record = index.primary(uid)
        if record is None:
            print(f"WARNING: Skeleton reference {uid:016X} unavailable, using owned skeletons", flush=True)
            return owned

        reference_groups.append(read_skeleton_parents(load_asset_payload(record)))

    for geometry, graphs in owned.items():
        binding = bindings.get(geometry)
        if binding is None:
            continue

        required = set(binding.bone_ids) - {0xFFFFFFFF}
        for graph in graphs:
            supplement_skeleton(graph, reference_groups, required)

    return owned

def attach_shared_holster(document, skeletons, worlds):
    """Connect the shared holster to its nearest exported source ancestor"""
    nodes = document["nodes"]
    skins = document.get("skins", ())
    matches = [
        skin for skin in skins
        if skin.get("extras", {}).get("siegeGeometryUid") == "00000007B8293A4C"
    ]
    if not matches:
        return set()
    if len(matches) != 1:
        raise ValueError("Ambiguous shared holster skin")

    skin = matches[0]
    if len(skin["joints"]) != 1:
        raise ValueError("Unexpected shared holster joints")

    child = skin["joints"][0]
    if nodes[child].get("extras", {}).get("siegeBoneId") != "8E904DC4":
        raise ValueError("Unexpected shared holster bone")

    graphs = skeletons.get(0x7B8293A4C, ())
    if not graphs:
        raise ValueError("Missing shared holster skeleton")

    available = {}
    for other in skins:
        if other is skin:
            continue
        for joint in other["joints"]:
            uid = int(nodes[joint]["extras"]["siegeBoneId"], 16)
            available.setdefault(uid, set()).add(joint)

    targets = set()
    for graph in graphs:
        parent = graph.get(0x8E904DC4)
        seen = {0x8E904DC4}

        while parent is not None and parent not in available:
            if parent in seen or parent not in graph:
                raise ValueError("Invalid shared holster ancestry")
            seen.add(parent)
            parent = graph[parent]

        if parent is None or len(available[parent]) != 1:
            raise ValueError("Missing or ambiguous shared holster ancestor")
        targets.add(next(iter(available[parent])))

    if len(targets) != 1:
        raise ValueError("Conflicting shared holster ancestors")
    parent = targets.pop()

    if any(child in node.get("children", ()) for node in nodes):
        raise ValueError("Shared holster already has a parent")

    from src.gltf import invert_gltf_matrix, multiply_matrices, transpose_matrix

    local = transpose_matrix(multiply_matrices(transpose_matrix(invert_gltf_matrix(worlds[parent])), transpose_matrix(worlds[child]),))
    nodes[parent].setdefault("children", []).append(child)
    nodes[child]["matrix"] = list(local)
    return {child}