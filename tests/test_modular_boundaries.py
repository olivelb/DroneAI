import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _line_count(relative_path):
    return len(_source(relative_path).splitlines())


def test_map_api_keeps_a_small_composition_router():
    composition = "app4-dashboard/api/routers/maps.py"
    feature_modules = [
        "app4-dashboard/api/routers/map_analyses.py",
        "app4-dashboard/api/routers/map_exports.py",
        "app4-dashboard/api/routers/map_feature_mutations.py",
        "app4-dashboard/api/routers/map_features.py",
        "app4-dashboard/api/routers/map_rasters.py",
        "app4-dashboard/api/routers/map_styles.py",
    ]

    assert _line_count(composition) < 40
    assert all(_line_count(module) < 330 for module in feature_modules)
    assert "include_router" in _source(composition)
    assert all("confluent_kafka" not in _source(module) for module in [composition, *feature_modules])


def test_mission_catalog_is_separate_from_lifecycle_commands():
    catalog = "app4-dashboard/api/routers/mission_catalog.py"
    lifecycle = "app4-dashboard/api/routers/missions.py"

    assert _line_count(catalog) < 180
    assert "mission_catalog_router" in _source(lifecycle)
    assert '@router.get("/missions")' in _source(catalog)
    assert '@router.get("/missions/{vol_id}")' in _source(catalog)


def test_qgis_crs_logic_stays_framework_neutral():
    source = _source("shared/qgis_crs.py")

    assert "fastapi" not in source
    assert "HTTPException" not in source
    assert "def resolve_export_crs" in source
    assert "def reproject_features" in source


def test_processing_worker_delegates_long_running_workflows():
    main_source = _source("app3-processing/main.py")
    workflow_source = _source("app3-processing/analysis_workflow.py")
    tiler_source = _source("app3-processing/orthomosaic_tiler.py")
    dispatcher_source = _source("app3-processing/processing_dispatcher.py")
    legacy_source = _source("app3-processing/legacy_aggregation.py")

    assert _line_count("app3-processing/main.py") < 200
    assert "AnalysisWorkflow(" in main_source
    assert "OrthomosaicTiler(" in main_source
    assert "ProcessingDispatcher(" in main_source
    assert "LegacyAggregationWorkflow(" in main_source
    assert "def recover_analysis_runs" not in main_source
    assert "def slice_orthomosaic" not in main_source
    assert "geoalchemy2" not in main_source
    assert "shared.database" not in main_source
    assert "import cv2" not in main_source
    assert "import main" not in workflow_source
    assert "import main" not in tiler_source
    assert "import main" not in dispatcher_source
    assert "import main" not in legacy_source
    assert "confluent_kafka" not in dispatcher_source
    assert "confluent_kafka" not in legacy_source

    workflow_tree = ast.parse(workflow_source)
    write_calls = [
        node
        for node in ast.walk(workflow_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_write_verified_json"
    ]
    assert write_calls
    assert all(len(call.args) == 3 for call in write_calls)


def test_ai_worker_keeps_model_and_tile_workflow_out_of_composition_root():
    composition = "app2-ia/main.py"
    sam_backend = "app2-ia/sam3_backend.py"
    tile_workflow = "app2-ia/tile_detection_workflow.py"
    source = _source(composition)

    assert _line_count(composition) < 180
    assert "TileDetectionWorkflow(" in source
    assert "Sam3Backend(" in source
    assert "def run_sam3_detection" not in source
    assert "def transform_detection_coordinates" not in source
    assert "import cv2" not in source
    assert "import torch" not in source
    assert "import main" not in _source(sam_backend)
    assert "import main" not in _source(tile_workflow)
    assert "confluent_kafka" not in _source(sam_backend)
    assert "confluent_kafka" not in _source(tile_workflow)


def test_long_running_workers_use_the_shared_durable_inbox_boundary():
    worker_entrypoints = [
        "app1-colmap/colmap_worker/worker.py",
        "app2-ia/main.py",
        "app3-processing/main.py",
    ]

    for entrypoint in worker_entrypoints:
        source = _source(entrypoint)
        assert "make_inbox_work_handler" in source
        assert "shared.database" not in source


def test_every_worker_uses_shared_durable_cancellation():
    cancellation_roots = [
        "app1-colmap/colmap_worker/runtime.py",
        "app2-ia/main.py",
        "app3-processing/main.py",
    ]

    assert all(
        "DurableCancellationRegistry" in _source(module)
        for module in cancellation_roots
    )


def test_colmap_worker_keeps_a_small_side_effect_free_composition_root():
    composition = "app1-colmap/main.py"
    worker = "app1-colmap/colmap_worker/worker.py"
    runner = "app1-colmap/colmap_worker/mission_runner.py"
    stage_modules = [
        "app1-colmap/colmap_worker/stages/alignment.py",
        "app1-colmap/colmap_worker/stages/gaussian.py",
        "app1-colmap/colmap_worker/stages/preparation.py",
        "app1-colmap/colmap_worker/stages/publication.py",
        "app1-colmap/colmap_worker/stages/reconstruction.py",
        "app1-colmap/colmap_worker/stages/rtk.py",
    ]
    helper_modules = [
        "app1-colmap/colmap_worker/dronegs_config.py",
        "app1-colmap/colmap_worker/sparse_mapping.py",
    ]

    composition_source = _source(composition)
    worker_source = _source(worker)
    runner_source = _source(runner)

    assert _line_count(composition) < 120
    assert _line_count(runner) < 120
    assert all(_line_count(module) < 700 for module in stage_modules)
    assert all(_line_count(module) < 350 for module in helper_modules)
    assert "create_producer(" not in composition_source
    assert "basicConfig(" not in composition_source
    assert "run_colmap_pipeline(" in runner_source
    assert "configure_worker_runtime(" in worker_source
    assert "create_producer(" in worker_source
    assert all("confluent_kafka" not in _source(module) for module in stage_modules)
    assert all("import main" not in _source(module) for module in stage_modules)
    assert all("confluent_kafka" not in _source(module) for module in helper_modules)
    assert all("import main" not in _source(module) for module in helper_modules)


def test_results_workspace_is_split_into_focused_components():
    viewer = "app4-dashboard/frontend/app/components/ResultsViewer.tsx"
    components = [
        "app4-dashboard/frontend/app/components/geospatial/AnalysisPanel.tsx",
        "app4-dashboard/frontend/app/components/geospatial/ExportCrsSelector.tsx",
        "app4-dashboard/frontend/app/components/geospatial/ExportPanel.tsx",
        "app4-dashboard/frontend/app/components/geospatial/FeatureEditors.tsx",
        "app4-dashboard/frontend/app/components/geospatial/LayersPanel.tsx",
        "app4-dashboard/frontend/app/components/geospatial/SearchPanel.tsx",
        "app4-dashboard/frontend/app/components/geospatial/VectorExportCards.tsx",
        "app4-dashboard/frontend/app/components/geospatial/ViewerHeader.tsx",
        "app4-dashboard/frontend/app/components/geospatial/ViewerSidePanel.tsx",
        "app4-dashboard/frontend/app/components/geospatial/ViewerToolbar.tsx",
    ]
    source = _source(viewer)
    workspace_source = (
        source
        + _source("app4-dashboard/frontend/app/components/geospatial/ViewerSidePanel.tsx")
        + _source("app4-dashboard/frontend/app/components/geospatial/ExportPanel.tsx")
    )

    assert _line_count(viewer) < 550
    assert all(_line_count(component) < 300 for component in components)
    assert all(Path(component).stem in workspace_source for component in components)


def test_frontend_authentication_has_an_independent_provider_boundary():
    auth = "app4-dashboard/frontend/app/lib/auth.tsx"
    store = "app4-dashboard/frontend/app/lib/store.tsx"
    page = "app4-dashboard/frontend/app/page.tsx"
    providers = "app4-dashboard/frontend/app/components/AppProviders.tsx"
    auth_source = _source(auth)
    store_source = _source(store)
    page_source = _source(page)
    providers_source = _source(providers)

    assert _line_count(auth) < 130
    assert _line_count(store) < 190
    assert "createSession" in auth_source
    assert "deleteSession" in auth_source
    assert "fetchSession" in auth_source
    assert "createSession" not in store_source
    assert "deleteSession" not in store_source
    assert "fetchSession" not in store_source
    assert "const { authStatus } = useAuth()" in store_source
    assert "<AppProviders>" in page_source
    assert "<AuthProvider>" in providers_source


def test_frontend_mission_runtime_owns_server_state_and_realtime_io():
    runtime = "app4-dashboard/frontend/app/lib/mission-runtime.tsx"
    store = "app4-dashboard/frontend/app/lib/store.tsx"
    page = "app4-dashboard/frontend/app/page.tsx"
    providers = "app4-dashboard/frontend/app/components/AppProviders.tsx"
    runtime_source = _source(runtime)
    store_source = _source(store)
    page_source = _source(page)
    providers_source = _source(providers)

    assert _line_count(runtime) < 240
    assert "fetchSummary" in runtime_source
    assert "new WebSocket" in runtime_source
    assert "autoSelectMission" in runtime_source
    assert "fetchSummary" not in store_source
    assert "new WebSocket" not in store_source
    assert "MissionSummary" not in store_source
    assert "<AppProviders>" in page_source
    assert "<MissionRuntimeProvider>" in providers_source


def test_frontend_workspace_cache_is_separate_from_local_editing_state():
    workspace = "app4-dashboard/frontend/app/lib/workspace-data.tsx"
    store = "app4-dashboard/frontend/app/lib/store.tsx"
    page = "app4-dashboard/frontend/app/page.tsx"
    providers = "app4-dashboard/frontend/app/components/AppProviders.tsx"
    workspace_source = _source(workspace)
    store_source = _source(store)
    page_source = _source(page)
    providers_source = _source(providers)

    assert _line_count(workspace) < 110
    assert "fetchBrowse" in workspace_source
    assert "fetchPods" in workspace_source
    assert "fetchBrowse" not in store_source
    assert "fetchPods" not in store_source
    assert "DatasetItem" not in store_source
    assert "PodState" not in store_source
    assert "selectedPath" in store_source
    assert "<AppProviders>" in page_source
    assert "<WorkspaceDataProvider>" in providers_source
