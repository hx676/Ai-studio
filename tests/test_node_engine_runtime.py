import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.models.runtime_nodes import RuntimeGraphRunRequest
from app.services import node_engine_service as service


def input_port(port_id, raw_type, *, required=True, widget=False, default=None, multiple=False):
    return {
        "id": port_id,
        "name": port_id,
        "types": [service._port_type(raw_type)],
        "raw_type": raw_type,
        "required": required,
        "multiple": multiple,
        "widget": {"enabled": widget, "type": raw_type.lower(), "default": default},
    }


def output_port(index, raw_type):
    return {
        "id": f"out-{index}",
        "name": raw_type,
        "types": [service._port_type(raw_type)],
        "raw_type": raw_type,
        "required": False,
        "multiple": False,
        "index": index,
    }


def definition(class_type, *, inputs=None, outputs=None, output_node=False, compatibility="supported", fingerprint="fp"):
    return {
        "class_type": class_type,
        "display_name": class_type,
        "description": "",
        "category": "test",
        "python_module": "nodes",
        "package": "",
        "compatibility": compatibility,
        "compatibility_reasons": [],
        "inputs": inputs or [],
        "outputs": outputs or [],
        "output_node": output_node,
        "deprecated": False,
        "experimental": False,
        "fingerprint": fingerprint,
    }


class NodeCatalogTests(unittest.TestCase):
    def test_builtin_chinese_catalog_localizes_names_ports_and_categories(self):
        translations = service._load_chinese_catalog(Path("missing-runtime"))
        raw = {
            "input": {"required": {"text": ["STRING", {"default": ""}]}},
            "output": ["STRING"],
            "output_name": ["text"],
            "python_module": "nodes",
            "category": "dataset/text",
            "display_name": "Add Text Prefix",
        }
        item = service._normalize_node("AddTextPrefix", raw, set(), translations)
        self.assertEqual(item["display_name_zh"], "添加文本前缀")
        self.assertEqual(item["category_zh"], "数据集/文本")
        self.assertEqual(item["inputs"][0]["name_zh"], "文本")

    def test_normalizes_widgets_ports_and_opaque_types(self):
        raw = {
            "input": {
                "required": {
                    "model": ["MODEL"],
                    "steps": ["INT", {"default": 20, "min": 1, "max": 100, "step": 1}],
                    "sampler": [["euler", "ddim"]],
                },
                "optional": {"note": ["STRING", {"default": "", "multiline": True}]},
            },
            "output": ["LATENT", "IMAGE"],
            "output_name": ["latent", "preview"],
            "output_is_list": [False, True],
            "python_module": "nodes",
            "category": "sampling",
        }
        item = service._normalize_node("Sampler", raw, set())
        self.assertEqual(item["compatibility"], "supported")
        self.assertEqual(item["inputs"][0]["types"], ["comfy:MODEL"])
        self.assertFalse(item["inputs"][0]["widget"]["enabled"])
        self.assertEqual(item["inputs"][1]["widget"]["default"], 20)
        self.assertEqual(item["inputs"][2]["widget"]["type"], "enum")
        self.assertEqual(item["outputs"][0]["id"], "out-0")
        self.assertTrue(item["outputs"][1]["multiple"])
        self.assertFalse(item["canvas_ready"])

    def test_image_and_scalar_node_is_canvas_utility(self):
        raw = {
            "input": {"required": {"image": ["IMAGE"], "strength": ["FLOAT", {"default": 0.5}]}},
            "output": ["IMAGE"],
            "python_module": "nodes",
            "category": "image/adjust",
        }
        item = service._normalize_node("ImageAdjust", raw, set())
        self.assertTrue(item["canvas_ready"])

    def test_custom_package_with_javascript_is_limited(self):
        raw = {
            "input": {"required": {}},
            "output": ["STRING"],
            "python_module": "custom_nodes.FancyPack.nodes",
            "category": "custom",
        }
        item = service._normalize_node("Fancy", raw, {"fancypack"})
        self.assertEqual(item["compatibility"], "limited")
        self.assertIn("专用前端", item["compatibility_reasons"][0])

    def test_catalog_search_is_server_paginated(self):
        old_catalog, old_meta = service._CATALOG, service._CATALOG_META
        try:
            service._CATALOG = {
                f"Node{index}": definition(f"Node{index}") | {"display_name": f"Math {index}"}
                for index in range(125)
            }
            service._CATALOG_META = {"revision": "test"}
            with patch.dict(os.environ, {"SYNCANVAS_NODE_ENGINE_URL": "http://127.0.0.1:65535"}):
                result = service.search_catalog("Math", page=2, page_size=50)
            self.assertEqual(result["total"], 125)
            self.assertEqual(len(result["items"]), 50)
            self.assertNotIn("inputs", result["items"][0])
        finally:
            service._CATALOG, service._CATALOG_META = old_catalog, old_meta

    def test_default_search_hides_model_graph_nodes(self):
        old_catalog, old_meta = service._CATALOG, service._CATALOG_META
        try:
            service._CATALOG = {
                "ImageUtility": definition("ImageUtility", inputs=[input_port("image", "IMAGE")], outputs=[output_port(0, "IMAGE")]),
                "ModelLoader": definition("ModelLoader", outputs=[output_port(0, "MODEL")]),
            }
            service._CATALOG_META = {"revision": "scope"}
            with patch.dict(os.environ, {"SYNCANVAS_NODE_ENGINE_URL": "http://127.0.0.1:65535"}):
                utility = service.search_catalog()
                all_nodes = service.search_catalog(scope="all")
            self.assertEqual([item["class_type"] for item in utility["items"]], ["ImageUtility"])
            self.assertEqual(all_nodes["total"], 2)
        finally:
            service._CATALOG, service._CATALOG_META = old_catalog, old_meta

    def test_unmanaged_service_on_engine_port_is_not_accepted(self):
        with patch.object(service, "_tracked_process", return_value={"port": service.ENGINE_PORT, "managed": False}), \
             patch.object(service, "_engine_request", return_value={"queue_running": []}):
            status = service.process_status(probe=True)
        self.assertFalse(status["ready"])
        self.assertIn("未受 SynCanvas 管理", status["error"])

    def test_stale_catalog_from_another_runtime_is_ignored(self):
        old_catalog, old_meta = service._CATALOG, service._CATALOG_META
        try:
            service._CATALOG = {"OldNode": definition("OldNode")}
            service._CATALOG_META = {"runtime_root": "E:/old-runtime"}
            with tempfile.TemporaryDirectory() as directory:
                runtime = Path(directory) / "runtime"
                (runtime / "python").mkdir(parents=True)
                (runtime / "main.py").write_text("", encoding="utf-8")
                (runtime / "python" / "python.exe").write_bytes(b"")
                with patch.object(service.node_engine_component_service, "runtime_root", return_value=runtime), \
                     patch.object(service, "CATALOG_FILE", Path(directory) / "missing.json"):
                    loaded = service.load_catalog()
            self.assertEqual(loaded["nodes"], {})
        finally:
            service._CATALOG, service._CATALOG_META = old_catalog, old_meta


class RuntimeProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_managed_record_cannot_bypass_unmanaged_port_guard(self):
        status = {
            "ready": False,
            "managed": True,
            "error": f"端口 {service.ENGINE_PORT} 被未受 SynCanvas 管理的服务占用",
        }
        with patch.object(service, "process_status", return_value=status), \
             patch.object(service.node_engine_component_service, "get_status") as component_status:
            with self.assertRaises(HTTPException) as caught:
                await service.start_engine()
        self.assertEqual(caught.exception.status_code, 409)
        component_status.assert_not_called()


class RuntimeGraphCompilerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"SYNCANVAS_NODE_ENGINE_URL": "http://127.0.0.1:65535"})
        self.environment.start()
        self.old_catalog = service._CATALOG
        self.old_meta = service._CATALOG_META
        service._CATALOG = {
            "LoadImage": definition(
                "LoadImage",
                inputs=[input_port("image", "COMBO", widget=True, default="")],
                outputs=[output_port(0, "IMAGE"), output_port(1, "MASK")],
                fingerprint="load",
            ),
            "PreviewImage": definition(
                "PreviewImage",
                inputs=[input_port("images", "IMAGE")],
                outputs=[],
                output_node=True,
                fingerprint="preview",
            ),
            "ModelSink": definition(
                "ModelSink",
                inputs=[input_port("model", "MODEL")],
                outputs=[],
                output_node=True,
                fingerprint="model",
            ),
            "ModelSource": definition(
                "ModelSource",
                outputs=[output_port(0, "MODEL")],
                fingerprint="source",
            ),
            "SaveImage": definition("SaveImage", output_node=True, fingerprint="save"),
            "MaskToImage": definition("MaskToImage", outputs=[output_port(0, "IMAGE")], fingerprint="mask"),
            "ImageBatch": definition(
                "ImageBatch",
                inputs=[input_port("images", "IMAGE", multiple=True)],
                output_node=True,
                fingerprint="batch",
            ),
            "WidgetSink": definition(
                "WidgetSink",
                inputs=[input_port("value", "INT", required=False, widget=True, default=5)],
                output_node=True,
                fingerprint="widget",
            ),
            "AudioSink": definition(
                "AudioSink",
                inputs=[input_port("audio", "AUDIO")],
                output_node=True,
                fingerprint="audio-sink",
            ),
            "VideoSink": definition(
                "VideoSink",
                inputs=[input_port("video", "VIDEO")],
                output_node=True,
                fingerprint="video-sink",
            ),
            "AudioSource": definition(
                "AudioSource",
                outputs=[output_port(0, "AUDIO")],
                fingerprint="audio-source",
            ),
            "VideoSource": definition(
                "VideoSource",
                outputs=[output_port(0, "VIDEO")],
                fingerprint="video-source",
            ),
            "LoadAudio": definition("LoadAudio", outputs=[output_port(0, "AUDIO")], fingerprint="load-audio"),
            "LoadVideo": definition("LoadVideo", outputs=[output_port(0, "VIDEO")], fingerprint="load-video"),
            "SaveAudio": definition("SaveAudio", output_node=True, fingerprint="save-audio"),
            "SaveVideo": definition("SaveVideo", output_node=True, fingerprint="save-video"),
        }
        service._CATALOG_META = {"revision": "test"}

    def tearDown(self):
        service._CATALOG = self.old_catalog
        service._CATALOG_META = self.old_meta
        self.environment.stop()

    async def test_external_canvas_image_becomes_load_image_node(self):
        payload = RuntimeGraphRunRequest(
            nodes=[{"id": "preview", "class_type": "PreviewImage", "widgets": {}, "definition_fingerprint": "preview"}],
            external_inputs=[{"to_node": "preview", "to_port": "images", "kind": "image", "value": "/assets/input/test.png"}],
            target_ids=["preview"],
        )
        with patch.object(service, "_upload_boundary_image", new=AsyncMock(return_value="boundary.png")):
            prompt, collectors = await service.compile_graph(payload, "run-1")
        self.assertEqual(prompt["1"]["class_type"], "PreviewImage")
        loader = next(value for key, value in prompt.items() if key != "1")
        self.assertEqual(loader, {"class_type": "LoadImage", "inputs": {"image": "boundary.png"}})
        self.assertEqual(prompt["1"]["inputs"]["images"][1], 0)
        self.assertEqual(collectors[0]["kind"], "native")

    async def test_opaque_type_cannot_cross_canvas_boundary(self):
        payload = RuntimeGraphRunRequest(
            nodes=[{"id": "sink", "class_type": "ModelSink", "widgets": {}, "definition_fingerprint": "model"}],
            external_inputs=[{"to_node": "sink", "to_port": "model", "kind": "json", "value": {"model": "not-transferable"}}],
            target_ids=["sink"],
        )
        with self.assertRaises(HTTPException) as caught:
            await service.compile_graph(payload, "run-2")
        self.assertIn("不能从普通 SynCanvas 节点输入", str(caught.exception.detail))

    async def test_multiple_external_images_are_not_overwritten(self):
        payload = RuntimeGraphRunRequest(
            nodes=[{"id": "batch", "class_type": "ImageBatch", "widgets": {}, "definition_fingerprint": "batch"}],
            external_inputs=[
                {"to_node": "batch", "to_port": "images", "kind": "image", "value": "/assets/input/a.png"},
                {"to_node": "batch", "to_port": "images", "kind": "image", "value": "/assets/input/b.png"},
            ],
            target_ids=["batch"],
        )
        with patch.object(service, "_upload_boundary_image", new=AsyncMock(side_effect=["a.png", "b.png"])):
            prompt, _ = await service.compile_graph(payload, "run-many")
        self.assertEqual(prompt["1"]["inputs"]["images"], [["2", 0], ["3", 0]])

    async def test_port_mode_omits_stale_widget_value(self):
        payload = RuntimeGraphRunRequest(
            nodes=[{
                "id": "sink",
                "class_type": "WidgetSink",
                "widgets": {"value": 99},
                "input_modes": {"value": "port"},
                "definition_fingerprint": "widget",
            }],
            target_ids=["sink"],
        )
        prompt, _ = await service.compile_graph(payload, "run-widget-port")
        self.assertNotIn("value", prompt["1"]["inputs"])

    async def test_audio_and_video_boundaries_use_runtime_loaders(self):
        payload = RuntimeGraphRunRequest(
            nodes=[
                {"id": "audio", "class_type": "AudioSink", "widgets": {}, "definition_fingerprint": "audio-sink"},
                {"id": "video", "class_type": "VideoSink", "widgets": {}, "definition_fingerprint": "video-sink"},
            ],
            external_inputs=[
                {"to_node": "audio", "to_port": "audio", "kind": "audio", "value": "/assets/input/sample.wav"},
                {"to_node": "video", "to_port": "video", "kind": "video", "value": "/assets/input/sample.mp4"},
            ],
            target_ids=["audio", "video"],
        )
        with patch.object(service, "_upload_boundary_file", new=AsyncMock(side_effect=["sample.wav", "sample.mp4"])):
            prompt, _ = await service.compile_graph(payload, "run-media-input")
        self.assertEqual(prompt["3"], {"class_type": "LoadAudio", "inputs": {"audio": "sample.wav"}})
        self.assertEqual(prompt["4"], {"class_type": "LoadVideo", "inputs": {"file": "sample.mp4"}})
        self.assertEqual(prompt["1"]["inputs"]["audio"], ["3", 0])
        self.assertEqual(prompt["2"]["inputs"]["video"], ["4", 0])

    async def test_audio_and_video_outputs_get_result_collectors(self):
        payload = RuntimeGraphRunRequest(
            nodes=[
                {"id": "audio", "class_type": "AudioSource", "widgets": {}, "definition_fingerprint": "audio-source"},
                {"id": "video", "class_type": "VideoSource", "widgets": {}, "definition_fingerprint": "video-source"},
            ],
            target_ids=["audio", "video"],
        )
        prompt, collectors = await service.compile_graph(payload, "run-media-output")
        self.assertEqual(prompt["3"]["class_type"], "SaveAudio")
        self.assertEqual(prompt["3"]["inputs"]["audio"], ["1", 0])
        self.assertEqual(prompt["4"]["class_type"], "SaveVideo")
        self.assertEqual(prompt["4"]["inputs"]["video"], ["2", 0])
        self.assertEqual([item["kind"] for item in collectors], ["audio", "video"])

    async def test_non_multiple_input_rejects_internal_and_external_sources(self):
        payload = RuntimeGraphRunRequest(
            nodes=[
                {"id": "source", "class_type": "LoadImage", "widgets": {}, "definition_fingerprint": "load"},
                {"id": "preview", "class_type": "PreviewImage", "widgets": {}, "definition_fingerprint": "preview"},
            ],
            connections=[{"from_node": "source", "from_port": "out-0", "to_node": "preview", "to_port": "images"}],
            external_inputs=[{"to_node": "preview", "to_port": "images", "kind": "image", "value": "/assets/input/b.png"}],
            target_ids=["preview"],
        )
        with self.assertRaises(HTTPException) as caught:
            await service.compile_graph(payload, "run-conflict")
        self.assertIn("只允许一个来源", str(caught.exception.detail))

    async def test_opaque_type_can_connect_inside_runtime_island(self):
        payload = RuntimeGraphRunRequest(
            nodes=[
                {"id": "source", "class_type": "ModelSource", "widgets": {}, "definition_fingerprint": "source"},
                {"id": "sink", "class_type": "ModelSink", "widgets": {}, "definition_fingerprint": "model"},
            ],
            connections=[{"from_node": "source", "from_port": "out-0", "to_node": "sink", "to_port": "model"}],
            target_ids=["sink"],
        )
        prompt, _ = await service.compile_graph(payload, "run-3")
        self.assertEqual(prompt["2"]["inputs"]["model"], ["1", 0])

    async def test_stale_definition_fingerprint_is_rejected(self):
        payload = RuntimeGraphRunRequest(
            nodes=[{"id": "preview", "class_type": "PreviewImage", "widgets": {}, "definition_fingerprint": "old"}],
            target_ids=["preview"],
        )
        with self.assertRaises(HTTPException) as caught:
            await service.compile_graph(payload, "run-4")
        self.assertEqual(caught.exception.status_code, 409)


class RuntimeGraphProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_result_collection_preserves_audio_and_video_kinds(self):
        manager = service.RuntimeGraphRunManager()
        history = {
            "outputs": {
                "3": {"audio": [{"filename": "sample.wav", "type": "output"}]},
                "4": {"videos": [{"filename": "sample.mp4", "type": "output"}]},
            }
        }
        collectors = [
            {"kind": "audio", "node_id": "3", "target_id": "audio", "port_id": "out-0"},
            {"kind": "video", "node_id": "4", "target_id": "video", "port_id": "out-0"},
        ]
        with patch.object(
            manager,
            "_download_result_image",
            new=AsyncMock(side_effect=["/assets/output/sample.wav", "/assets/output/sample.mp4"]),
        ):
            result = await manager._collect_result("run-media-result", history, collectors)
        self.assertEqual(result["audio"], ["/assets/output/sample.wav"])
        self.assertEqual(result["videos"], ["/assets/output/sample.mp4"])
        self.assertEqual(result["outputs"]["audio:out-0"][0]["kind"], "audio")
        self.assertEqual(result["outputs"]["video:out-0"][0]["kind"], "video")

    async def test_success_marks_last_active_node_complete(self):
        payload = RuntimeGraphRunRequest(
            nodes=[{"id": "preview", "class_type": "PreviewImage", "widgets": {}}],
            target_ids=["preview"],
        )
        manager = service.RuntimeGraphRunManager()
        run_id = "terminal-progress"
        manager.records[run_id] = {
            "run_id": run_id,
            "status": "running",
            "active_node_id": "preview",
            "active_class_type": "PreviewImage",
            "node_progress": {"preview": {"status": "running", "progress": 0.0}},
        }
        manager.cancel_events[run_id] = asyncio.Event()
        history = {"status": {"status_str": "success"}, "outputs": {"1": {"images": []}}}

        async def wait_until_cancelled(_client_id, _event_queue, ws_ready):
            ws_ready.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                raise

        with patch.object(service, "compile_graph", new=AsyncMock(return_value=({"1": {"class_type": "PreviewImage", "inputs": {}}}, [{"kind": "native", "node_id": "1", "target_id": "preview", "port_id": ""}]))), \
             patch.object(service, "process_status", return_value={"ready": True}), \
             patch.object(service, "_consume_engine_events", new=wait_until_cancelled), \
             patch.object(service, "_engine_request", side_effect=[{"prompt_id": "prompt-1"}, {"prompt-1": history}]), \
             patch.object(manager, "_persist"), \
             patch.object(manager, "_broadcast", new=AsyncMock()), \
             patch.object(manager, "_collect_result", new=AsyncMock(return_value={"images": []})), \
             patch("app.services.digital_human_service.acquire_digital_human_resource", new=AsyncMock(return_value="owner")), \
             patch("app.services.digital_human_service.release_digital_human_resource"):
            await manager._execute(run_id, payload)

        record = manager.records[run_id]
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["node_progress"]["preview"]["status"], "succeeded")
        self.assertEqual(record["node_progress"]["preview"]["progress"], 1.0)
        self.assertEqual(record["active_node_id"], "")

    async def test_cancelled_run_clears_active_node(self):
        payload = RuntimeGraphRunRequest(
            nodes=[{"id": "sampler", "class_type": "KSampler", "widgets": {}}],
            target_ids=["sampler"],
        )
        manager = service.RuntimeGraphRunManager()
        run_id = "cancel-progress"
        manager.records[run_id] = {
            "run_id": run_id,
            "status": "running",
            "active_node_id": "sampler",
            "active_class_type": "KSampler",
            "node_progress": {"sampler": {"status": "running", "progress": 0.4}},
        }
        cancel_event = asyncio.Event()
        cancel_event.set()
        manager.cancel_events[run_id] = cancel_event

        with patch.object(manager, "_persist"), \
             patch.object(manager, "_broadcast", new=AsyncMock()):
            await manager._execute(run_id, payload)

        record = manager.records[run_id]
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["node_progress"]["sampler"]["status"], "cancelled")
        self.assertEqual(record["active_node_id"], "")


class RuntimeFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.frontend = (root / "custom_nodes/syncanvas_runtime_node/web/index.js").read_text(encoding="utf-8")
        cls.classic = (root / "static/js/canvas/state.js").read_text(encoding="utf-8")
        cls.smart = (root / "static/js/smart-canvas/state.js").read_text(encoding="utf-8")

    def test_frontend_uses_paginated_catalog_and_graph_api(self):
        self.assertIn("/api/runtime-nodes?", self.frontend)
        self.assertIn("page_size:'30'", self.frontend)
        self.assertIn("scope:nodeScope", self.frontend)
        self.assertIn('data-runtime-scope="utility"', self.frontend)
        self.assertIn("/api/runtime-graphs/runs", self.frontend)

    def test_both_canvases_build_runtime_graph_islands(self):
        self.assertIn("buildRuntimeGraph:() => buildRuntimeGraph(node)", self.classic)
        self.assertIn("buildRuntimeGraph:() => buildSmartRuntimeGraph(node)", self.smart)
        self.assertIn("definition_fingerprint", self.classic)
        self.assertIn("definition_fingerprint", self.smart)

    def test_runtime_progress_is_mapped_to_each_canvas_node(self):
        self.assertIn("context.applyRuntimeProgress?.(record, graph)", self.frontend)
        self.assertIn("applyRuntimeGraphProgress(record, graph)", self.classic)
        self.assertIn("applySmartRuntimeGraphProgress(record, graph)", self.smart)

    def test_special_frontend_nodes_are_labeled_limited(self):
        self.assertIn("有限兼容", self.frontend)
        self.assertIn("compatibility_reasons", self.frontend)


if __name__ == "__main__":
    unittest.main()
