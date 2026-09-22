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

def read_model_skeletons(payload):
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

    for skin in document.get("skins", ()):
        joints = skin["joints"]
        geometry = int(skin["extras"]["siegeGeometryUid"], 16)
        graphs = skeletons.get(geometry, ())
        if not graphs:
            continue

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

    for scene in document.get("scenes", ()):
        scene["nodes"] = [
            node for node in scene["nodes"]
            if node not in child_nodes
        ]

def supplement_skeleton(graph, reference_groups, required):
    """Add only unanimous helper chains anchored below the owned root"""
    roots = {
        bone for bone, parent in graph.items()
        if parent is None
    }
    groups = [
        [
            ref for ref in references
            if {
                bone for bone, parent in ref.items()
                if parent is None
            } == roots and all(ref[bone] == graph[bone] for bone in graph.keys() & ref.keys())
        ]
        for references in reference_groups
    ]
    if not all(groups):
        return

    def chain(ref, bone):
        missing = []
        seen = set()

        while bone not in graph:
            if bone is None or bone not in ref or bone in seen:
                return None
            seen.add(bone)
            missing.append((bone, ref[bone]))
            bone = ref[bone]

        # Sharing only the root is insufficient evidence
        if graph[bone] is None:
            return None

        # The existing ancestry must agree all the way to the root
        while bone is not None:
            if bone in seen or bone not in ref or bone not in graph:
                return None
            if ref[bone] != graph[bone]:
                return None
            seen.add(bone)
            bone = graph[bone]

        return tuple(missing)

    candidates = [
        ref for group in groups
        for ref in group
    ]
    common = set.intersection(*(set(ref) for ref in candidates))
    additions = {}

    for bone in (common & set(required)) - graph.keys():
        chains = [chain(ref, bone) for ref in candidates]
        if chains[0] is None:
            continue
        if any(item != chains[0] for item in chains[1:]):
            continue
        additions.update(chains[0])

    graph.update(additions)

def resolve_model_skeletons(payload, index):
    """Resolve missing bound helpers from 2 agreeing source packages"""
    from src.model import load_asset_payload, read_mesh_bindings

    owned = read_model_skeletons(payload)
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