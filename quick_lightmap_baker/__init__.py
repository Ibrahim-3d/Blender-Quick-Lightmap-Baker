bl_info = {
    "name": "Quick Lightmap Baker",
    "author": "bpy",
    "version": (1, 6, 0),
    "blender": (4, 0, 0),
    "category": "Object",
}

import bpy
import math
import mathutils

from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)


LIGHT_UV = "Light Map"
BAKE_NODE = "__LIGHTMAP_BAKE_TARGET__"

GENERATED_PROP = "__quick_lightmap_generated__"
WORKING_PROP = "__quick_lightmap_working_source__"
SOURCE_COLLECTION_PROP = "__quick_lightmap_source_collection__"
ATLAS_GROUP_PROP = "__quick_lightmap_atlas_group__"

EXCLUDE_PROP = "exclude_from_lightmap_bake"


# -------------------------------------------------------------------------
# Collection helpers
# -------------------------------------------------------------------------

def get_or_create_scene_collection(scene, name):
    col = bpy.data.collections.get(name)

    if col is None:
        col = bpy.data.collections.new(name)

    if col.name not in scene.collection.children.keys():
        scene.collection.children.link(col)

    return col


def find_layer_collection(layer_col, collection):
    if layer_col.collection == collection:
        return layer_col

    for child in layer_col.children:
        found = find_layer_collection(child, collection)
        if found:
            return found

    return None


def set_collection_checked(context, collection, checked):
    collection.hide_viewport = not checked
    collection.hide_render = not checked

    context.view_layer.update()

    layer_col = find_layer_collection(
        context.view_layer.layer_collection,
        collection,
    )

    if layer_col:
        layer_col.exclude = not checked
        layer_col.hide_viewport = not checked

    context.view_layer.update()


def collection_objects_recursive(collection):
    if collection is None:
        return []

    result = list(collection.objects)

    for child in collection.children:
        result.extend(collection_objects_recursive(child))

    return result


def clear_collection_objects(collection, generated_only=True):
    for obj in list(collection.objects):
        if generated_only and not obj.get(GENERATED_PROP, False):
            continue

        mesh = obj.data if obj.type == "MESH" else None
        bpy.data.objects.remove(obj, do_unlink=True)

        if mesh and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def clear_temp_working_collection(collection):
    for obj in list(collection.objects):
        if not obj.get(WORKING_PROP, False):
            continue

        mesh = obj.data if obj.type == "MESH" else None
        bpy.data.objects.remove(obj, do_unlink=True)

        if mesh and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def move_objects_to_collection(objects, target_col):
    for obj in objects:
        if obj.name not in target_col.objects.keys():
            target_col.objects.link(obj)

        for col in list(obj.users_collection):
            if col != target_col:
                col.objects.unlink(obj)


# -------------------------------------------------------------------------
# Name helpers
# -------------------------------------------------------------------------

def safe_name(name):
    result = []

    for char in str(name):
        if char.isalnum() or char in "_-":
            result.append(char)
        else:
            result.append("_")

    clean = "".join(result).strip("_")
    return clean or "Atlas"


def make_atlas_image_name(settings, atlas_index, atlas_key):
    if settings.atlas_mode == "SINGLE":
        return settings.image_name

    return f"{settings.atlas_name_prefix}_{atlas_index:03d}_{safe_name(atlas_key)}"


def make_atlas_material_name(settings, atlas_index, atlas_key):
    if settings.atlas_mode == "SINGLE":
        return settings.output_material_name

    return f"{settings.output_material_name}_{atlas_index:03d}_{safe_name(atlas_key)}"


# -------------------------------------------------------------------------
# UV helpers
# -------------------------------------------------------------------------

def ensure_lightmap_uv(obj):
    mesh = obj.data

    if len(mesh.uv_layers) == 0:
        mesh.uv_layers.new(name="UVMap")

    if mesh.uv_layers.find(LIGHT_UV) == -1:
        mesh.uv_layers.new(name=LIGHT_UV)

    index = mesh.uv_layers.find(LIGHT_UV)
    mesh.uv_layers.active_index = index

    if hasattr(mesh.uv_layers, "active_render_index"):
        mesh.uv_layers.active_render_index = index

    mesh.update()


def force_lightmap_as_only_uv(mesh):
    light_index = mesh.uv_layers.find(LIGHT_UV)

    if light_index == -1:
        return False

    coords = [tuple(loop.uv) for loop in mesh.uv_layers[light_index].data]

    while len(mesh.uv_layers):
        mesh.uv_layers.remove(mesh.uv_layers[0])

    uv = mesh.uv_layers.new(name="UVMap")

    for loop, co in zip(uv.data, coords):
        loop.uv = co

    mesh.uv_layers.active_index = 0

    if hasattr(mesh.uv_layers, "active_render_index"):
        mesh.uv_layers.active_render_index = 0

    mesh.update()
    return True


def smart_unwrap_lightmap(context, objects, margin):
    if context.object and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.object.select_all(action="DESELECT")

    view_layer_names = {obj.name for obj in context.view_layer.objects}

    safe_objects = [
        obj for obj in objects
        if obj.name in view_layer_names
    ]

    if not safe_objects:
        raise RuntimeError("No selectable objects in the active View Layer.")

    for obj in safe_objects:
        ensure_lightmap_uv(obj)

        obj.hide_set(False)
        obj.hide_viewport = False
        obj.hide_select = False
        obj.select_set(True)

    context.view_layer.objects.active = safe_objects[0]

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_mode(type="FACE")
    bpy.ops.mesh.select_all(action="SELECT")

    bpy.ops.uv.smart_project(
        angle_limit=math.radians(66),
        island_margin=margin,
        area_weight=0.0,
        correct_aspect=True,
        scale_to_bounds=False,
    )

    try:
        bpy.ops.uv.pack_islands(
            rotate=True,
            scale=True,
            margin=margin,
        )
    except TypeError:
        bpy.ops.uv.pack_islands(margin=margin)

    bpy.ops.object.mode_set(mode="OBJECT")

    for obj in safe_objects:
        ensure_lightmap_uv(obj)

    return safe_objects


# -------------------------------------------------------------------------
# Image / material helpers
# -------------------------------------------------------------------------

def get_or_create_bake_image(name, resolution):
    image = bpy.data.images.get(name)

    if image and (image.size[0] != resolution or image.size[1] != resolution):
        bpy.data.images.remove(image)
        image = None

    if image is None:
        image = bpy.data.images.new(
            name=name,
            width=resolution,
            height=resolution,
            alpha=False,
            float_buffer=False,
        )

    image.generated_color = (0.0, 0.0, 0.0, 1.0)

    try:
        image.colorspace_settings.name = "sRGB"
    except Exception:
        pass

    return image


def ensure_source_materials(obj):
    mesh = obj.data

    if len(mesh.materials) == 0:
        mat = bpy.data.materials.new(obj.name + "_Material")
        mat.use_nodes = True
        mesh.materials.append(mat)

    for i, mat in enumerate(mesh.materials):
        if mat is None:
            mat = bpy.data.materials.new(obj.name + "_Material")
            mat.use_nodes = True
            mesh.materials[i] = mat

    for poly in mesh.polygons:
        if poly.material_index >= len(mesh.materials):
            poly.material_index = 0


def add_active_bake_node(mat, image, state):
    mat.use_nodes = True
    nodes = mat.node_tree.nodes

    if mat not in state:
        state[mat] = {
            "active": nodes.active.name if nodes.active else "",
            "selected": [node.name for node in nodes if node.select],
        }

    node = nodes.get(BAKE_NODE)

    if node is None:
        node = nodes.new(type="ShaderNodeTexImage")
        node.name = BAKE_NODE
        node.label = "Temporary Lightmap Bake Target"
        node.location = (-700, -300)

    node.image = image

    for n in nodes:
        n.select = False

    node.select = True
    nodes.active = node


def restore_source_materials(state, remove_bake_nodes):
    for mat, item in state.items():
        if not mat or not mat.use_nodes:
            continue

        nodes = mat.node_tree.nodes

        if remove_bake_nodes:
            node = nodes.get(BAKE_NODE)
            if node:
                nodes.remove(node)

        for node in nodes:
            node.select = node.name in item["selected"]

        if item["active"] and item["active"] in nodes:
            nodes.active = nodes[item["active"]]


def create_newlightmap_material(name, image, strength):
    mat = bpy.data.materials.get(name)

    if mat is None:
        mat = bpy.data.materials.new(name)

    mat.use_nodes = True
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    mat["lightmap_image"] = image.name

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    nodes.clear()

    uv = nodes.new(type="ShaderNodeUVMap")
    uv.name = "Baked UVMap"
    uv.label = "Baked UVMap"
    uv.uv_map = "UVMap"
    uv.location = (-650, 0)

    tex = nodes.new(type="ShaderNodeTexImage")
    tex.name = "Baked Lightmap Texture"
    tex.label = image.name
    tex.image = image
    tex.location = (-430, 0)

    emission = nodes.new(type="ShaderNodeEmission")
    emission.name = "Lightmap Emission"
    emission.label = "Lightmap Emission"
    emission.inputs["Strength"].default_value = strength
    emission.location = (-180, 0)

    output = nodes.new(type="ShaderNodeOutputMaterial")
    output.location = (80, 0)

    links.new(uv.outputs["UV"], tex.inputs["Vector"])
    links.new(tex.outputs["Color"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], output.inputs["Surface"])

    return mat


# -------------------------------------------------------------------------
# Source object helpers
# -------------------------------------------------------------------------

def valid_source_object(obj):
    return (
        obj
        and obj.type == "MESH"
        and len(obj.data.polygons) > 0
        and not obj.get(GENERATED_PROP, False)
        and not obj.get(WORKING_PROP, False)
        and not obj.get(EXCLUDE_PROP, False)
    )


def get_source_objects(context, settings):
    if settings.scope == "SELECTED":
        objects = list(context.selected_objects)

    elif settings.scope == "VISIBLE":
        objects = [
            obj for obj in context.view_layer.objects
            if obj.visible_get()
        ]

    elif settings.scope == "SOURCE_COLLECTION":
        col = bpy.data.collections.get(settings.source_collection_name)
        view_layer_names = {obj.name for obj in context.view_layer.objects}

        objects = [
            obj for obj in collection_objects_recursive(col)
            if obj.name in view_layer_names
        ]

    else:
        objects = list(context.view_layer.objects)

    result = []

    for obj in objects:
        if valid_source_object(obj) and obj not in result:
            result.append(obj)

    return result


def get_main_material_name(obj):
    if obj.data.materials and obj.data.materials[0]:
        return obj.data.materials[0].name

    return "No_Material"


def get_primary_collection_name(obj, settings):
    stored = obj.get(SOURCE_COLLECTION_PROP)

    if stored:
        return stored

    ignored = {
        settings.source_collection_name,
        settings.output_collection_name,
        settings.temp_collection_name,
    }

    for col in obj.users_collection:
        if col.name not in ignored:
            obj[SOURCE_COLLECTION_PROP] = col.name
            return col.name

    if obj.users_collection:
        obj[SOURCE_COLLECTION_PROP] = obj.users_collection[0].name
        return obj.users_collection[0].name

    return "Scene"


def get_source_atlas_key(obj, settings):
    if settings.atlas_mode == "SINGLE":
        return "Atlas_001"

    if settings.atlas_mode == "CUSTOM_PROPERTY":
        return obj.get(settings.custom_atlas_property, "Atlas_001")

    if settings.atlas_mode == "COLLECTION":
        return get_primary_collection_name(obj, settings)

    if settings.atlas_mode == "MATERIAL":
        return get_main_material_name(obj)

    return "Auto_Area"


def object_world_area(obj):
    mesh = obj.data
    mw = obj.matrix_world
    total = 0.0

    for poly in mesh.polygons:
        verts = [mw @ mesh.vertices[i].co for i in poly.vertices]

        if len(verts) < 3:
            continue

        base = verts[0]

        for i in range(1, len(verts) - 1):
            total += mathutils.geometry.area_tri(
                base,
                verts[i],
                verts[i + 1],
            )

    return total


def group_objects_for_atlases(objects, settings):
    groups = {}

    if settings.atlas_mode == "AUTO_AREA":
        sorted_objects = sorted(
            objects,
            key=object_world_area,
            reverse=True,
        )

        atlas_index = 1
        current = []
        current_area = 0.0

        for obj in sorted_objects:
            area = object_world_area(obj)

            too_much_area = (
                current
                and current_area + area > settings.max_area_per_atlas
            )

            too_many_objects = (
                current
                and len(current) >= settings.max_objects_per_atlas
            )

            if too_much_area or too_many_objects:
                groups[f"Atlas_{atlas_index:03d}"] = current
                atlas_index += 1
                current = []
                current_area = 0.0

            current.append(obj)
            current_area += area

        if current:
            groups[f"Atlas_{atlas_index:03d}"] = current

        return groups

    for obj in objects:
        key = obj.get(ATLAS_GROUP_PROP)

        if not key:
            key = get_source_atlas_key(obj, settings)

        groups.setdefault(key, []).append(obj)

    return groups


# -------------------------------------------------------------------------
# Modifier-applied temporary bake objects
# -------------------------------------------------------------------------

def create_modifier_applied_working_objects(context, source_objects, settings):
    scene = context.scene
    depsgraph = context.evaluated_depsgraph_get()

    temp_col = get_or_create_scene_collection(
        scene,
        settings.temp_collection_name,
    )

    set_collection_checked(context, temp_col, True)
    clear_temp_working_collection(temp_col)

    working_objects = []

    for src in source_objects:
        atlas_key = get_source_atlas_key(src, settings)
        eval_obj = src.evaluated_get(depsgraph)

        try:
            mesh = bpy.data.meshes.new_from_object(
                eval_obj,
                depsgraph=depsgraph,
                preserve_all_data_layers=True,
            )
        except TypeError:
            mesh = bpy.data.meshes.new_from_object(
                eval_obj,
                depsgraph=depsgraph,
            )

        mesh.name = src.name + "_modifier_applied_mesh"

        mesh.materials.clear()

        for mat in src.data.materials:
            mesh.materials.append(mat)

        if len(mesh.materials) == 0:
            mat = bpy.data.materials.new(src.name + "_Material")
            mat.use_nodes = True
            mesh.materials.append(mat)

        for poly in mesh.polygons:
            if poly.material_index >= len(mesh.materials):
                poly.material_index = 0

        obj = bpy.data.objects.new(src.name + "_BAKE_WORK", mesh)
        obj.matrix_world = src.matrix_world.copy()

        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_select = False

        obj[WORKING_PROP] = True
        obj["source_object"] = src.name
        obj[ATLAS_GROUP_PROP] = atlas_key

        temp_col.objects.link(obj)
        working_objects.append(obj)

    context.view_layer.update()
    return working_objects


def save_object_visibility_state(objects):
    state = {}

    for obj in objects:
        state[obj] = {
            "hide_get": obj.hide_get(),
            "hide_viewport": obj.hide_viewport,
            "hide_render": obj.hide_render,
            "hide_select": obj.hide_select,
        }

    return state


def restore_object_visibility_state(state):
    for obj, values in state.items():
        if obj.name not in bpy.data.objects:
            continue

        obj.hide_select = values["hide_select"]
        obj.hide_viewport = values["hide_viewport"]
        obj.hide_render = values["hide_render"]
        obj.hide_set(values["hide_get"])


def hide_original_sources_for_modifier_bake(source_objects):
    for obj in source_objects:
        obj.hide_render = True


def warn_unapplied_scale(operator, objects):
    bad = [
        obj.name for obj in objects
        if any(abs(v - 1.0) > 0.001 for v in obj.scale)
    ]

    if bad:
        operator.report(
            {"WARNING"},
            "Unapplied object scale: " + ", ".join(bad[:8]),
        )


# -------------------------------------------------------------------------
# Output duplication
# -------------------------------------------------------------------------

def duplicate_baked_group_to_collection(
    context,
    bake_objects,
    image,
    material_name,
    settings,
):
    output_col = get_or_create_scene_collection(
        context.scene,
        settings.output_collection_name,
    )

    set_collection_checked(context, output_col, True)

    mat = create_newlightmap_material(
        material_name,
        image,
        settings.emission_strength,
    )

    new_objects = []

    for src in bake_objects:
        mesh = src.data.copy()
        source_name = src.get("source_object", src.name)

        mesh.name = source_name + "_baked_mesh"

        ok = force_lightmap_as_only_uv(mesh)

        if not ok:
            print("Missing Light Map UV on:", src.name)

        mesh.materials.clear()
        mesh.materials.append(mat)

        for poly in mesh.polygons:
            poly.material_index = 0

        obj = bpy.data.objects.new(source_name + "_BAKED", mesh)
        obj.matrix_world = src.matrix_world.copy()

        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_select = False

        obj[GENERATED_PROP] = True
        obj["source_object"] = source_name
        obj["lightmap_image"] = image.name
        obj["lightmap_material"] = material_name

        output_col.objects.link(obj)
        new_objects.append(obj)

    context.view_layer.update()
    return new_objects


# -------------------------------------------------------------------------
# Bake helpers
# -------------------------------------------------------------------------

def prepare_bake_objects(context, source_objects, settings):
    for obj in source_objects:
        ensure_source_materials(obj)

    if settings.make_meshes_single_user and not settings.apply_modifiers_for_bake:
        for obj in source_objects:
            if obj.data.users > 1:
                obj.data = obj.data.copy()

    if settings.apply_modifiers_for_bake:
        bake_objects = create_modifier_applied_working_objects(
            context,
            source_objects,
            settings,
        )
    else:
        bake_objects = source_objects

        for obj in bake_objects:
            obj[ATLAS_GROUP_PROP] = get_source_atlas_key(obj, settings)

    for obj in bake_objects:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_select = False

        ensure_lightmap_uv(obj)
        ensure_source_materials(obj)

    return bake_objects


def bake_one_atlas(
    context,
    atlas_objects,
    image,
    settings,
    material_state,
):
    scene = context.scene

    atlas_objects = smart_unwrap_lightmap(
        context,
        atlas_objects,
        settings.uv_margin,
    )

    for obj in atlas_objects:
        ensure_lightmap_uv(obj)

        for mat in obj.data.materials:
            add_active_bake_node(mat, image, material_state)

    scene.render.engine = "CYCLES"
    scene.cycles.samples = settings.samples

    scene.render.bake.use_clear = True
    scene.render.bake.margin = settings.bake_margin
    scene.render.bake.use_selected_to_active = False

    bpy.ops.object.select_all(action="DESELECT")

    for obj in atlas_objects:
        obj.select_set(True)

    context.view_layer.objects.active = atlas_objects[0]

    if settings.bake_mode == "LIGHT_ONLY":
        bpy.ops.object.bake(
            type="DIFFUSE",
            pass_filter={"DIRECT", "INDIRECT"},
            margin=settings.bake_margin,
            use_clear=True,
            uv_layer=LIGHT_UV,
        )
    else:
        bpy.ops.object.bake(
            type="COMBINED",
            margin=settings.bake_margin,
            use_clear=True,
            uv_layer=LIGHT_UV,
        )

    return atlas_objects


def save_bake_image(image):
    image.filepath_raw = bpy.path.abspath("//" + image.name + ".png")
    image.file_format = "PNG"
    image.save()


# -------------------------------------------------------------------------
# Settings
# -------------------------------------------------------------------------

class QuickLightmapSettings(bpy.types.PropertyGroup):
    scope: EnumProperty(
        name="Bake Scope",
        items=[
            ("SELECTED", "Selected", "Use selected source mesh objects"),
            ("VISIBLE", "Visible", "Use visible source mesh objects"),
            ("SCENE", "Whole Scene", "Use all enabled View Layer mesh objects"),
            ("SOURCE_COLLECTION", "Unbaked Collection", "Use objects inside the unbaked source collection"),
        ],
        default="SELECTED",
    )

    atlas_mode: EnumProperty(
        name="Atlas Mode",
        items=[
            ("SINGLE", "Single Atlas", "Bake everything into one image"),
            ("COLLECTION", "By Collection", "One atlas per source collection"),
            ("CUSTOM_PROPERTY", "By Custom Property", "Use object custom property"),
            ("AUTO_AREA", "Auto By Area", "Split atlases by world surface area"),
            ("MATERIAL", "By Main Material", "One atlas per object's first material"),
        ],
        default="COLLECTION",
    )

    bake_mode: EnumProperty(
        name="Bake Mode",
        items=[
            ("LIGHT_ONLY", "Light Only", "Bake diffuse direct + indirect lighting"),
            ("COMBINED", "Combined", "Bake final material appearance"),
        ],
        default="LIGHT_ONLY",
    )

    resolution: IntProperty(
        name="Resolution",
        default=2048,
        min=128,
        max=16384,
    )

    samples: IntProperty(
        name="Samples",
        default=64,
        min=1,
        max=4096,
    )

    uv_margin: FloatProperty(
        name="UV Margin",
        default=0.02,
        min=0.001,
        max=0.2,
    )

    bake_margin: IntProperty(
        name="Bake Margin",
        default=16,
        min=0,
        max=256,
    )

    image_name: StringProperty(
        name="Single Atlas Image",
        default="LightMap_Bake",
    )

    atlas_name_prefix: StringProperty(
        name="Multi Atlas Prefix",
        default="LightMap_Atlas",
    )

    custom_atlas_property: StringProperty(
        name="Custom Atlas Property",
        default="lightmap_atlas",
    )

    max_area_per_atlas: FloatProperty(
        name="Max Area Per Atlas",
        default=80.0,
        min=0.01,
    )

    max_objects_per_atlas: IntProperty(
        name="Max Objects Per Atlas",
        default=40,
        min=1,
        max=10000,
    )

    source_collection_name: StringProperty(
        name="Unbaked Collection",
        default="Unbaked_Source",
    )

    output_collection_name: StringProperty(
        name="Baked Collection",
        default="Baked_Lightmap_Output",
    )

    temp_collection_name: StringProperty(
        name="Temporary Bake Collection",
        default="_Lightmap_Temp_Modifier_Applied",
    )

    output_material_name: StringProperty(
        name="Output Material",
        default="newlightmap",
    )

    emission_strength: FloatProperty(
        name="Emission Strength",
        default=1.0,
        min=0.0,
        max=100.0,
    )

    apply_modifiers_for_bake: BoolProperty(
        name="Apply Modifiers For Bake",
        default=True,
        description="Uses temporary evaluated copies. Original objects are not changed.",
    )

    make_meshes_single_user: BoolProperty(
        name="Make Source Meshes Single User",
        default=False,
    )

    move_sources_to_unbaked: BoolProperty(
        name="Move Sources To Unbaked Collection",
        default=True,
    )

    uncheck_unbaked_after_bake: BoolProperty(
        name="Uncheck Unbaked Collection After Bake",
        default=True,
    )

    clear_previous_output: BoolProperty(
        name="Replace Previous Output",
        default=True,
    )

    remove_temp_bake_nodes: BoolProperty(
        name="Remove Temporary Bake Nodes",
        default=True,
    )

    remove_temp_working_objects: BoolProperty(
        name="Remove Temporary Modifier Copies",
        default=True,
    )


# -------------------------------------------------------------------------
# Operators
# -------------------------------------------------------------------------

class OBJECT_OT_quick_lightmap_prepare(bpy.types.Operator):
    bl_idname = "object.quick_lightmap_prepare"
    bl_label = "Prepare Light Map UVs"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.quick_lightmap_settings

        src_col = get_or_create_scene_collection(
            context.scene,
            settings.source_collection_name,
        )

        temp_col = get_or_create_scene_collection(
            context.scene,
            settings.temp_collection_name,
        )

        set_collection_checked(context, src_col, True)
        set_collection_checked(context, temp_col, True)

        source_objects = get_source_objects(context, settings)

        if not source_objects:
            self.report({"ERROR"}, "No source mesh objects found.")
            return {"CANCELLED"}

        warn_unapplied_scale(self, source_objects)

        bake_objects = prepare_bake_objects(
            context,
            source_objects,
            settings,
        )

        groups = group_objects_for_atlases(bake_objects, settings)

        prepared_count = 0

        try:
            for atlas_key, atlas_objects in groups.items():
                safe_objects = smart_unwrap_lightmap(
                    context,
                    atlas_objects,
                    settings.uv_margin,
                )

                prepared_count += len(safe_objects)

        except RuntimeError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        if settings.remove_temp_working_objects:
            clear_temp_working_collection(temp_col)
            set_collection_checked(context, temp_col, False)

        self.report(
            {"INFO"},
            f"Prepared {prepared_count} object(s) across {len(groups)} atlas group(s).",
        )

        return {"FINISHED"}


class OBJECT_OT_quick_lightmap_bake_and_output(bpy.types.Operator):
    bl_idname = "object.quick_lightmap_bake_and_output"
    bl_label = "Bake And Build Baked Collection"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.quick_lightmap_settings
        scene = context.scene

        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        src_col = get_or_create_scene_collection(
            scene,
            settings.source_collection_name,
        )

        out_col = get_or_create_scene_collection(
            scene,
            settings.output_collection_name,
        )

        temp_col = get_or_create_scene_collection(
            scene,
            settings.temp_collection_name,
        )

        set_collection_checked(context, src_col, True)
        set_collection_checked(context, out_col, True)
        set_collection_checked(context, temp_col, True)

        source_objects = get_source_objects(context, settings)

        if not source_objects:
            self.report({"ERROR"}, "No source mesh objects found. Selected objects may be excluded.")
            return {"CANCELLED"}

        source_visibility_state = save_object_visibility_state(source_objects)

        warn_unapplied_scale(self, source_objects)

        material_state = {}
        all_new_objects = []

        try:
            bake_objects = prepare_bake_objects(
                context,
                source_objects,
                settings,
            )

            if settings.apply_modifiers_for_bake:
                hide_original_sources_for_modifier_bake(source_objects)

            if settings.clear_previous_output:
                clear_collection_objects(out_col, generated_only=True)

            atlas_groups = group_objects_for_atlases(
                bake_objects,
                settings,
            )

            if not atlas_groups:
                self.report({"ERROR"}, "No atlas groups created.")
                return {"CANCELLED"}

            for atlas_index, (atlas_key, atlas_objects) in enumerate(
                atlas_groups.items(),
                start=1,
            ):
                image_name = make_atlas_image_name(
                    settings,
                    atlas_index,
                    atlas_key,
                )

                material_name = make_atlas_material_name(
                    settings,
                    atlas_index,
                    atlas_key,
                )

                image = get_or_create_bake_image(
                    image_name,
                    settings.resolution,
                )

                baked_atlas_objects = bake_one_atlas(
                    context,
                    atlas_objects,
                    image,
                    settings,
                    material_state,
                )

                save_bake_image(image)

                new_objects = duplicate_baked_group_to_collection(
                    context,
                    baked_atlas_objects,
                    image,
                    material_name,
                    settings,
                )

                all_new_objects.extend(new_objects)

            if settings.move_sources_to_unbaked:
                move_objects_to_collection(source_objects, src_col)

            if settings.uncheck_unbaked_after_bake:
                set_collection_checked(context, src_col, False)

            set_collection_checked(context, out_col, True)

            bpy.ops.object.select_all(action="DESELECT")

            for obj in all_new_objects:
                obj.select_set(True)

            if all_new_objects:
                context.view_layer.objects.active = all_new_objects[0]

            self.report(
                {"INFO"},
                f"Baked {len(atlas_groups)} atlas image(s), created {len(all_new_objects)} baked object(s).",
            )

        except RuntimeError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        finally:
            restore_source_materials(
                material_state,
                settings.remove_temp_bake_nodes,
            )

            restore_object_visibility_state(source_visibility_state)

            if settings.remove_temp_working_objects:
                clear_temp_working_collection(temp_col)
                set_collection_checked(context, temp_col, False)

        return {"FINISHED"}


class OBJECT_OT_quick_lightmap_build_output(bpy.types.Operator):
    bl_idname = "object.quick_lightmap_build_output"
    bl_label = "Build Output From Existing Atlas Images"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.quick_lightmap_settings
        scene = context.scene

        src_col = get_or_create_scene_collection(
            scene,
            settings.source_collection_name,
        )

        out_col = get_or_create_scene_collection(
            scene,
            settings.output_collection_name,
        )

        temp_col = get_or_create_scene_collection(
            scene,
            settings.temp_collection_name,
        )

        set_collection_checked(context, src_col, True)
        set_collection_checked(context, out_col, True)
        set_collection_checked(context, temp_col, True)

        source_objects = get_source_objects(context, settings)

        if not source_objects:
            self.report({"ERROR"}, "No source mesh objects found. Selected objects may be excluded.")
            return {"CANCELLED"}

        bake_objects = prepare_bake_objects(
            context,
            source_objects,
            settings,
        )

        atlas_groups = group_objects_for_atlases(
            bake_objects,
            settings,
        )

        if settings.clear_previous_output:
            clear_collection_objects(out_col, generated_only=True)

        all_new_objects = []

        try:
            for atlas_index, (atlas_key, atlas_objects) in enumerate(
                atlas_groups.items(),
                start=1,
            ):
                image_name = make_atlas_image_name(
                    settings,
                    atlas_index,
                    atlas_key,
                )

                image = bpy.data.images.get(image_name)

                if image is None:
                    self.report(
                        {"ERROR"},
                        f"Missing image for atlas: {image_name}",
                    )
                    return {"CANCELLED"}

                atlas_objects = smart_unwrap_lightmap(
                    context,
                    atlas_objects,
                    settings.uv_margin,
                )

                material_name = make_atlas_material_name(
                    settings,
                    atlas_index,
                    atlas_key,
                )

                new_objects = duplicate_baked_group_to_collection(
                    context,
                    atlas_objects,
                    image,
                    material_name,
                    settings,
                )

                all_new_objects.extend(new_objects)

            if settings.move_sources_to_unbaked:
                move_objects_to_collection(source_objects, src_col)

            if settings.uncheck_unbaked_after_bake:
                set_collection_checked(context, src_col, False)

            set_collection_checked(context, out_col, True)

            bpy.ops.object.select_all(action="DESELECT")

            for obj in all_new_objects:
                obj.select_set(True)

            if all_new_objects:
                context.view_layer.objects.active = all_new_objects[0]

        finally:
            if settings.remove_temp_working_objects:
                clear_temp_working_collection(temp_col)
                set_collection_checked(context, temp_col, False)

        self.report(
            {"INFO"},
            f"Created {len(all_new_objects)} baked output object(s).",
        )

        return {"FINISHED"}


class OBJECT_OT_quick_lightmap_set_custom_atlas(bpy.types.Operator):
    bl_idname = "object.quick_lightmap_set_custom_atlas"
    bl_label = "Set Custom Atlas On Selected"
    bl_options = {"REGISTER", "UNDO"}

    atlas_name: StringProperty(
        name="Atlas Name",
        default="Atlas_001",
    )

    def execute(self, context):
        settings = context.scene.quick_lightmap_settings
        count = 0

        for obj in context.selected_objects:
            if obj.type == "MESH":
                obj[settings.custom_atlas_property] = self.atlas_name
                count += 1

        self.report(
            {"INFO"},
            f"Set custom atlas '{self.atlas_name}' on {count} object(s).",
        )

        return {"FINISHED"}


class OBJECT_OT_quick_lightmap_exclude_selected(bpy.types.Operator):
    bl_idname = "object.quick_lightmap_exclude_selected"
    bl_label = "Exclude Selected From Bake"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        count = 0

        for obj in context.selected_objects:
            if obj.type == "MESH":
                obj[EXCLUDE_PROP] = True
                count += 1

        self.report(
            {"INFO"},
            f"Excluded {count} object(s) from lightmap bake.",
        )

        return {"FINISHED"}


class OBJECT_OT_quick_lightmap_include_selected(bpy.types.Operator):
    bl_idname = "object.quick_lightmap_include_selected"
    bl_label = "Include Selected In Bake"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        count = 0

        for obj in context.selected_objects:
            if obj.type == "MESH":
                if EXCLUDE_PROP in obj:
                    del obj[EXCLUDE_PROP]

                count += 1

        self.report(
            {"INFO"},
            f"Included {count} object(s) in lightmap bake.",
        )

        return {"FINISHED"}


class OBJECT_OT_quick_lightmap_edit_sources(bpy.types.Operator):
    bl_idname = "object.quick_lightmap_edit_sources"
    bl_label = "Show Unbaked Sources For Editing"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.quick_lightmap_settings

        src_col = get_or_create_scene_collection(
            context.scene,
            settings.source_collection_name,
        )

        out_col = get_or_create_scene_collection(
            context.scene,
            settings.output_collection_name,
        )

        temp_col = get_or_create_scene_collection(
            context.scene,
            settings.temp_collection_name,
        )

        set_collection_checked(context, src_col, True)
        set_collection_checked(context, out_col, False)
        set_collection_checked(context, temp_col, False)

        self.report({"INFO"}, "Unbaked source collection enabled.")
        return {"FINISHED"}


class OBJECT_OT_quick_lightmap_show_output(bpy.types.Operator):
    bl_idname = "object.quick_lightmap_show_output"
    bl_label = "Show Baked Output"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.quick_lightmap_settings

        src_col = get_or_create_scene_collection(
            context.scene,
            settings.source_collection_name,
        )

        out_col = get_or_create_scene_collection(
            context.scene,
            settings.output_collection_name,
        )

        temp_col = get_or_create_scene_collection(
            context.scene,
            settings.temp_collection_name,
        )

        set_collection_checked(context, src_col, False)
        set_collection_checked(context, out_col, True)
        set_collection_checked(context, temp_col, False)

        self.report({"INFO"}, "Baked output collection enabled.")
        return {"FINISHED"}


class OBJECT_OT_quick_lightmap_clean_temp(bpy.types.Operator):
    bl_idname = "object.quick_lightmap_clean_temp"
    bl_label = "Clean Temporary Bake Objects"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.quick_lightmap_settings

        temp_col = get_or_create_scene_collection(
            context.scene,
            settings.temp_collection_name,
        )

        clear_temp_working_collection(temp_col)
        set_collection_checked(context, temp_col, False)

        self.report({"INFO"}, "Temporary modifier-applied bake objects removed.")
        return {"FINISHED"}


# -------------------------------------------------------------------------
# Panel
# -------------------------------------------------------------------------

class VIEW3D_PT_quick_lightmap_baker(bpy.types.Panel):
    bl_label = "Quick Lightmap Baker"
    bl_idname = "VIEW3D_PT_quick_lightmap_baker"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Lightmap"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.quick_lightmap_settings

        col = layout.column(align=True)
        col.operator("object.quick_lightmap_prepare", icon="UV")
        col.operator("object.quick_lightmap_bake_and_output", icon="RENDER_STILL")
        col.operator("object.quick_lightmap_build_output", icon="DUPLICATE")

        layout.separator()

        col = layout.column(align=True)
        op = col.operator("object.quick_lightmap_set_custom_atlas", icon="BOOKMARKS")
        op.atlas_name = "Atlas_001"

        col.operator("object.quick_lightmap_exclude_selected")
        col.operator("object.quick_lightmap_include_selected")

        layout.separator()

        col = layout.column(align=True)
        col.operator("object.quick_lightmap_edit_sources", icon="HIDE_OFF")
        col.operator("object.quick_lightmap_show_output", icon="HIDE_ON")
        col.operator("object.quick_lightmap_clean_temp", icon="TRASH")

        box = layout.box()
        box.label(text="Bake Settings")
        box.prop(settings, "scope")
        box.prop(settings, "bake_mode")
        box.prop(settings, "resolution")
        box.prop(settings, "samples")
        box.prop(settings, "uv_margin")
        box.prop(settings, "bake_margin")

        box = layout.box()
        box.label(text="Atlas Settings")
        box.prop(settings, "atlas_mode")
        box.prop(settings, "image_name")
        box.prop(settings, "atlas_name_prefix")
        box.prop(settings, "custom_atlas_property")

        if settings.atlas_mode == "AUTO_AREA":
            box.prop(settings, "max_area_per_atlas")
            box.prop(settings, "max_objects_per_atlas")

        box = layout.box()
        box.label(text="Output")
        box.prop(settings, "source_collection_name")
        box.prop(settings, "output_collection_name")
        box.prop(settings, "temp_collection_name")
        box.prop(settings, "output_material_name")
        box.prop(settings, "emission_strength")

        box = layout.box()
        box.label(text="Workflow")
        box.prop(settings, "apply_modifiers_for_bake")
        box.prop(settings, "make_meshes_single_user")
        box.prop(settings, "move_sources_to_unbaked")
        box.prop(settings, "uncheck_unbaked_after_bake")
        box.prop(settings, "clear_previous_output")
        box.prop(settings, "remove_temp_bake_nodes")
        box.prop(settings, "remove_temp_working_objects")


# -------------------------------------------------------------------------
# Registration
# -------------------------------------------------------------------------

classes = (
    QuickLightmapSettings,
    OBJECT_OT_quick_lightmap_prepare,
    OBJECT_OT_quick_lightmap_bake_and_output,
    OBJECT_OT_quick_lightmap_build_output,
    OBJECT_OT_quick_lightmap_set_custom_atlas,
    OBJECT_OT_quick_lightmap_exclude_selected,
    OBJECT_OT_quick_lightmap_include_selected,
    OBJECT_OT_quick_lightmap_edit_sources,
    OBJECT_OT_quick_lightmap_show_output,
    OBJECT_OT_quick_lightmap_clean_temp,
    VIEW3D_PT_quick_lightmap_baker,
)


def unregister_old_classes():
    old_names = [
        "VIEW3D_PT_quick_lightmap_baker",
        "VIEW3D_PT_quick_lightmap_baker_fixed",
        "OBJECT_OT_quick_lightmap_prepare",
        "OBJECT_OT_quick_lightmap_prepare_fixed",
        "OBJECT_OT_quick_lightmap_build_output",
        "OBJECT_OT_quick_lightmap_build_output_fixed",
        "OBJECT_OT_quick_lightmap_bake_output",
        "OBJECT_OT_quick_lightmap_bake_and_output",
        "OBJECT_OT_quick_lightmap_bake_and_output_fixed",
        "OBJECT_OT_quick_lightmap_set_custom_atlas",
        "OBJECT_OT_quick_lightmap_exclude_selected",
        "OBJECT_OT_quick_lightmap_include_selected",
        "OBJECT_OT_quick_lightmap_edit_sources",
        "OBJECT_OT_quick_lightmap_edit_sources_fixed",
        "OBJECT_OT_quick_lightmap_show_output",
        "OBJECT_OT_quick_lightmap_show_output_fixed",
        "OBJECT_OT_quick_lightmap_clean_temp",
        "QuickLightmapSettings",
    ]

    if hasattr(bpy.types.Scene, "quick_lightmap_settings"):
        try:
            del bpy.types.Scene.quick_lightmap_settings
        except Exception:
            pass

    for name in old_names:
        cls = getattr(bpy.types, name, None)

        if cls:
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass


def register():
    unregister_old_classes()

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.quick_lightmap_settings = PointerProperty(
        type=QuickLightmapSettings,
    )


def unregister():
    if hasattr(bpy.types.Scene, "quick_lightmap_settings"):
        del bpy.types.Scene.quick_lightmap_settings

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()