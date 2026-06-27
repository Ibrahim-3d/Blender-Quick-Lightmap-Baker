bl_info = {
    "name": "Quick Lightmap Baker Fixed Output",
    "author": "bpy",
    "version": (1, 3, 0),
    "blender": (4, 0, 0),
    "category": "Object",
}

import bpy
import math

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


def clear_collection_objects(collection):
    for obj in list(collection.objects):
        data = obj.data if obj.type == "MESH" else None
        bpy.data.objects.remove(obj, do_unlink=True)

        if data and data.users == 0:
            bpy.data.meshes.remove(data)


def move_objects_to_collection(objects, target_col):
    for obj in objects:
        if obj.name not in target_col.objects.keys():
            target_col.objects.link(obj)

        for col in list(obj.users_collection):
            if col != target_col:
                col.objects.unlink(obj)


def collection_objects_recursive(collection):
    result = list(collection.objects)

    for child in collection.children:
        result.extend(collection_objects_recursive(child))

    return result


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
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")

    for obj in objects:
        ensure_lightmap_uv(obj)
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.hide_select = False
        obj.select_set(True)

    context.view_layer.objects.active = objects[0]

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

    bpy.ops.uv.pack_islands(
        rotate=True,
        scale=True,
        margin=margin,
    )

    bpy.ops.object.mode_set(mode="OBJECT")

    for obj in objects:
        ensure_lightmap_uv(obj)


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

    if mat.name not in state:
        state[mat.name] = {
            "material": mat,
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
    for item in state.values():
        mat = item["material"]

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
    uv.uv_map = "UVMap"
    uv.location = (-650, 0)

    tex = nodes.new(type="ShaderNodeTexImage")
    tex.name = "Baked Lightmap Texture"
    tex.label = image.name
    tex.image = image
    tex.location = (-430, 0)

    emission = nodes.new(type="ShaderNodeEmission")
    emission.name = "Lightmap Emission"
    emission.inputs["Strength"].default_value = strength
    emission.location = (-180, 0)

    output = nodes.new(type="ShaderNodeOutputMaterial")
    output.location = (80, 0)

    links.new(uv.outputs["UV"], tex.inputs["Vector"])
    links.new(tex.outputs["Color"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], output.inputs["Surface"])

    return mat


# -------------------------------------------------------------------------
# Object helpers
# -------------------------------------------------------------------------

def valid_source_object(obj):
    return (
        obj
        and obj.type == "MESH"
        and len(obj.data.polygons) > 0
        and not obj.get(GENERATED_PROP, False)
    )


def get_source_objects(context, settings):
    if settings.scope == "SELECTED":
        objects = list(context.selected_objects)

    elif settings.scope == "VISIBLE":
        objects = [obj for obj in context.view_layer.objects if obj.visible_get()]

    elif settings.scope == "SOURCE_COLLECTION":
        col = bpy.data.collections.get(settings.source_collection_name)
        objects = collection_objects_recursive(col) if col else []

    else:
        objects = list(context.scene.objects)

    result = []

    for obj in objects:
        if valid_source_object(obj) and obj not in result:
            result.append(obj)

    return result


def duplicate_baked_scene_to_collection(context, source_objects, image, settings):
    scene = context.scene

    output_col = get_or_create_scene_collection(
        scene,
        settings.output_collection_name,
    )

    set_collection_checked(context, output_col, True)

    if settings.clear_previous_output:
        clear_collection_objects(output_col)

    mat = create_newlightmap_material(
        settings.output_material_name,
        image,
        settings.emission_strength,
    )

    new_objects = []

    for src in source_objects:
        mesh = src.data.copy()
        mesh.name = src.name + "_baked_mesh"

        force_lightmap_as_only_uv(mesh)

        mesh.materials.clear()
        mesh.materials.append(mat)

        for poly in mesh.polygons:
            poly.material_index = 0

        obj = bpy.data.objects.new(src.name + "_BAKED", mesh)
        obj.matrix_world = src.matrix_world.copy()

        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_select = False
        obj[GENERATED_PROP] = True
        obj["source_object"] = src.name
        obj["lightmap_image"] = image.name

        output_col.objects.link(obj)
        new_objects.append(obj)

    context.view_layer.update()

    return new_objects


def warn_unapplied_scale(operator, objects):
    bad = [
        obj.name for obj in objects
        if any(abs(v - 1.0) > 0.001 for v in obj.scale)
    ]

    if bad:
        operator.report(
            {"WARNING"},
            "Unapplied scale: " + ", ".join(bad[:8]),
        )


# -------------------------------------------------------------------------
# Settings
# -------------------------------------------------------------------------

class QuickLightmapSettings(bpy.types.PropertyGroup):
    scope: EnumProperty(
        name="Bake Scope",
        items=[
            ("SELECTED", "Selected", "Use selected source mesh objects"),
            ("VISIBLE", "Visible", "Use visible source mesh objects"),
            ("SCENE", "Whole Scene", "Use all scene mesh objects"),
            ("SOURCE_COLLECTION", "Unbaked Collection", "Use objects inside the unbaked source collection"),
        ],
        default="SELECTED",
    )

    bake_mode: EnumProperty(
        name="Bake Mode",
        items=[
            ("LIGHT_ONLY", "Light Only", "Bake direct and indirect lighting only"),
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
        name="Bake Image",
        default="LightMap_Bake",
    )

    source_collection_name: StringProperty(
        name="Unbaked Collection",
        default="Unbaked_Source",
    )

    output_collection_name: StringProperty(
        name="Baked Collection",
        default="Baked_Lightmap_Output",
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

    make_single_user_meshes: BoolProperty(
        name="Make Meshes Single User",
        default=True,
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


# -------------------------------------------------------------------------
# Operators
# -------------------------------------------------------------------------

class OBJECT_OT_quick_lightmap_prepare(bpy.types.Operator):
    bl_idname = "object.quick_lightmap_prepare_fixed"
    bl_label = "Prepare Light Map UVs"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.quick_lightmap_settings

        src_col = get_or_create_scene_collection(
            context.scene,
            settings.source_collection_name,
        )

        set_collection_checked(context, src_col, True)

        objects = get_source_objects(context, settings)

        if not objects:
            self.report({"ERROR"}, "No source mesh objects found.")
            return {"CANCELLED"}

        if settings.make_single_user_meshes:
            for obj in objects:
                if obj.data.users > 1:
                    obj.data = obj.data.copy()

        warn_unapplied_scale(self, objects)

        for obj in objects:
            ensure_lightmap_uv(obj)
            ensure_source_materials(obj)

        smart_unwrap_lightmap(context, objects, settings.uv_margin)

        self.report({"INFO"}, f"Prepared Light Map UVs on {len(objects)} object(s).")
        return {"FINISHED"}


class OBJECT_OT_quick_lightmap_build_output(bpy.types.Operator):
    bl_idname = "object.quick_lightmap_build_output_fixed"
    bl_label = "Build Output From Current Light Map"
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

        set_collection_checked(context, src_col, True)
        set_collection_checked(context, out_col, True)

        objects = get_source_objects(context, settings)

        if not objects:
            self.report({"ERROR"}, "No source mesh objects found.")
            return {"CANCELLED"}

        image = bpy.data.images.get(settings.image_name)

        if image is None:
            self.report({"ERROR"}, f"Image not found: {settings.image_name}")
            return {"CANCELLED"}

        new_objects = duplicate_baked_scene_to_collection(
            context,
            objects,
            image,
            settings,
        )

        if settings.move_sources_to_unbaked:
            move_objects_to_collection(objects, src_col)

        if settings.uncheck_unbaked_after_bake:
            set_collection_checked(context, src_col, False)

        set_collection_checked(context, out_col, True)

        bpy.ops.object.select_all(action="DESELECT")

        for obj in new_objects:
            obj.select_set(True)

        if new_objects:
            context.view_layer.objects.active = new_objects[0]

        self.report(
            {"INFO"},
            f"Created {len(new_objects)} baked duplicate object(s) in {out_col.name}.",
        )

        return {"FINISHED"}


class OBJECT_OT_quick_lightmap_bake_and_output(bpy.types.Operator):
    bl_idname = "object.quick_lightmap_bake_and_output_fixed"
    bl_label = "Bake And Build New Baked Collection"
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

        set_collection_checked(context, src_col, True)
        set_collection_checked(context, out_col, True)

        objects = get_source_objects(context, settings)

        if not objects:
            self.report({"ERROR"}, "No source mesh objects found.")
            return {"CANCELLED"}

        if settings.make_single_user_meshes:
            for obj in objects:
                if obj.data.users > 1:
                    obj.data = obj.data.copy()

        warn_unapplied_scale(self, objects)

        image = get_or_create_bake_image(
            settings.image_name,
            settings.resolution,
        )

        material_state = {}

        try:
            for obj in objects:
                obj.hide_set(False)
                obj.hide_viewport = False
                obj.hide_render = False
                obj.hide_select = False

                ensure_lightmap_uv(obj)
                ensure_source_materials(obj)

            smart_unwrap_lightmap(context, objects, settings.uv_margin)

            for obj in objects:
                ensure_lightmap_uv(obj)

                for mat in obj.data.materials:
                    add_active_bake_node(mat, image, material_state)

            scene.render.engine = "CYCLES"
            scene.cycles.samples = settings.samples
            scene.render.bake.use_clear = True
            scene.render.bake.margin = settings.bake_margin
            scene.render.bake.use_selected_to_active = False

            bpy.ops.object.select_all(action="DESELECT")

            for obj in objects:
                obj.select_set(True)

            context.view_layer.objects.active = objects[0]

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

            image.filepath_raw = bpy.path.abspath("//" + settings.image_name + ".png")
            image.file_format = "PNG"
            image.save()

            new_objects = duplicate_baked_scene_to_collection(
                context,
                objects,
                image,
                settings,
            )

            if settings.move_sources_to_unbaked:
                move_objects_to_collection(objects, src_col)

            if settings.uncheck_unbaked_after_bake:
                set_collection_checked(context, src_col, False)

            set_collection_checked(context, out_col, True)

            bpy.ops.object.select_all(action="DESELECT")

            for obj in new_objects:
                obj.select_set(True)

            if new_objects:
                context.view_layer.objects.active = new_objects[0]

            self.report(
                {"INFO"},
                f"Baked {len(objects)} source object(s), created {len(new_objects)} duplicate baked object(s).",
            )

        finally:
            restore_source_materials(
                material_state,
                settings.remove_temp_bake_nodes,
            )

        return {"FINISHED"}


class OBJECT_OT_quick_lightmap_edit_sources(bpy.types.Operator):
    bl_idname = "object.quick_lightmap_edit_sources_fixed"
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

        set_collection_checked(context, src_col, True)
        set_collection_checked(context, out_col, False)

        self.report({"INFO"}, "Unbaked source collection enabled.")
        return {"FINISHED"}


class OBJECT_OT_quick_lightmap_show_output(bpy.types.Operator):
    bl_idname = "object.quick_lightmap_show_output_fixed"
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

        set_collection_checked(context, src_col, False)
        set_collection_checked(context, out_col, True)

        self.report({"INFO"}, "Baked output collection enabled.")
        return {"FINISHED"}


# -------------------------------------------------------------------------
# Panel
# -------------------------------------------------------------------------

class VIEW3D_PT_quick_lightmap_baker_fixed(bpy.types.Panel):
    bl_label = "Quick Lightmap Baker"
    bl_idname = "VIEW3D_PT_quick_lightmap_baker_fixed"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Lightmap"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.quick_lightmap_settings

        col = layout.column(align=True)
        col.operator("object.quick_lightmap_prepare_fixed", icon="UV")
        col.operator("object.quick_lightmap_bake_and_output_fixed", icon="RENDER_STILL")
        col.operator("object.quick_lightmap_build_output_fixed", icon="DUPLICATE")

        layout.separator()

        col = layout.column(align=True)
        col.operator("object.quick_lightmap_edit_sources_fixed", icon="HIDE_OFF")
        col.operator("object.quick_lightmap_show_output_fixed", icon="HIDE_ON")

        box = layout.box()
        box.label(text="Bake Settings")
        box.prop(settings, "scope")
        box.prop(settings, "bake_mode")
        box.prop(settings, "resolution")
        box.prop(settings, "samples")
        box.prop(settings, "uv_margin")
        box.prop(settings, "bake_margin")
        box.prop(settings, "image_name")

        box = layout.box()
        box.label(text="Output")
        box.prop(settings, "source_collection_name")
        box.prop(settings, "output_collection_name")
        box.prop(settings, "output_material_name")
        box.prop(settings, "emission_strength")

        box = layout.box()
        box.label(text="Workflow")
        box.prop(settings, "make_single_user_meshes")
        box.prop(settings, "move_sources_to_unbaked")
        box.prop(settings, "uncheck_unbaked_after_bake")
        box.prop(settings, "clear_previous_output")
        box.prop(settings, "remove_temp_bake_nodes")


# -------------------------------------------------------------------------
# Registration
# -------------------------------------------------------------------------

classes = (
    QuickLightmapSettings,
    OBJECT_OT_quick_lightmap_prepare,
    OBJECT_OT_quick_lightmap_build_output,
    OBJECT_OT_quick_lightmap_bake_and_output,
    OBJECT_OT_quick_lightmap_edit_sources,
    OBJECT_OT_quick_lightmap_show_output,
    VIEW3D_PT_quick_lightmap_baker_fixed,
)


def unregister_old_classes():
    old_names = [
        "VIEW3D_PT_quick_lightmap_baker",
        "VIEW3D_PT_quick_lightmap_baker_fixed",
        "OBJECT_OT_quick_lightmap_prepare",
        "OBJECT_OT_quick_lightmap_prepare_fixed",
        "OBJECT_OT_quick_lightmap_bake_output",
        "OBJECT_OT_quick_lightmap_bake_and_output_fixed",
        "OBJECT_OT_quick_lightmap_build_output_fixed",
        "OBJECT_OT_quick_lightmap_edit_sources_fixed",
        "OBJECT_OT_quick_lightmap_show_output_fixed",
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
