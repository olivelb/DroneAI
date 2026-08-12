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


def test_gcp_api_separates_imports_queries_and_mutations():
    composition = "app4-dashboard/api/routers/map_gcps.py"
    route_modules = [
        "app4-dashboard/api/routers/map_gcp_imports.py",
        "app4-dashboard/api/routers/map_gcp_mutations.py",
        "app4-dashboard/api/routers/map_gcp_queries.py",
    ]
    support_modules = [
        "app4-dashboard/api/gcp_candidate_support.py",
        "app4-dashboard/api/gcp_route_support.py",
    ]

    composition_source = _source(composition)
    import_source = _source(route_modules[0])
    assert _line_count(composition) < 40
    assert all(_line_count(module) < 400 for module in route_modules)
    assert all(_line_count(module) < 200 for module in support_modules)
    assert composition_source.count("include_router") == 3
    assert all("map_gcps" not in _source(module) for module in [*route_modules, *support_modules])
    assert import_source.index("added_count += 1") < import_source.index(
        'action="candidates_refreshed"'
    )
    assert '"candidate_count": added_count' in import_source


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


def test_storage_delegates_immutable_publication_algorithms():
    facade = "shared/storage.py"
    immutable = "shared/storage_immutable.py"
    facade_source = _source(facade)
    immutable_source = _source(immutable)

    assert _line_count(facade) < 850
    assert _line_count(immutable) < 600
    assert "immutable.publish_content_addressed_file(" in facade_source
    assert "immutable.copy_verified_object(" in facade_source
    assert "from shared import storage" not in immutable_source
    assert "boto3.client" not in immutable_source


def test_processing_worker_delegates_long_running_workflows():
    main_source = _source("app3-processing/main.py")
    workflow_source = _source("app3-processing/analysis_workflow.py")
    publication_source = _source("app3-processing/analysis_publication.py")
    recovery_source = _source("app3-processing/analysis_recovery.py")
    tiler_source = _source("app3-processing/orthomosaic_tiler.py")
    dispatcher_source = _source("app3-processing/processing_dispatcher.py")
    legacy_source = _source("app3-processing/legacy_aggregation.py")

    assert _line_count("app3-processing/main.py") < 200
    assert _line_count("app3-processing/analysis_workflow.py") < 750
    assert _line_count("app3-processing/analysis_publication.py") < 350
    assert _line_count("app3-processing/analysis_recovery.py") < 250
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
    assert "publication.load_tile_payloads(" in workflow_source
    assert "recovery.plan_recovery(" in workflow_source
    assert "analysis_workflow" not in publication_source
    assert "analysis_workflow" not in recovery_source
    assert "confluent_kafka" not in publication_source
    assert "confluent_kafka" not in recovery_source
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


def test_dataset_uploads_separate_contract_storage_and_recovery_boundaries():
    commands = "app4-dashboard/api/dataset_uploads.py"
    contracts = "app4-dashboard/api/dataset_upload_contracts.py"
    storage_boundary = "app4-dashboard/api/dataset_upload_storage.py"
    recovery = "app4-dashboard/api/dataset_upload_recovery.py"
    command_source = _source(commands)
    contract_source = _source(contracts)
    storage_source = _source(storage_boundary)
    recovery_source = _source(recovery)

    assert _line_count(commands) < 550
    assert _line_count(contracts) < 400
    assert _line_count(storage_boundary) < 450
    assert _line_count(recovery) < 200
    assert "storage_transitions.initialize_pending_files(" in command_source
    assert "storage_transitions.complete_file_from_intent(" in command_source
    assert "recovery.reconcile_pending_uploads(" in command_source
    assert "recovery.cleanup_expired_uploads(" in command_source
    assert "from shared import storage" not in contract_source
    assert "get_session" not in storage_source
    assert "from . import dataset_uploads" not in storage_source
    assert "from . import dataset_uploads" not in recovery_source


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


def test_identity_control_plane_keeps_sessions_members_and_credentials_separate():
    composition = "app4-dashboard/api/routers/identity.py"
    modules = [
        "app4-dashboard/api/routers/auth.py",
        "app4-dashboard/api/routers/identity_capabilities.py",
        "app4-dashboard/api/routers/identity_members.py",
        "app4-dashboard/api/routers/identity_credentials.py",
        "app4-dashboard/api/identity_api.py",
        "shared/identity.py",
        "shared/identity_capabilities.py",
    ]

    assert _line_count(composition) < 30
    assert _line_count(
        "app4-dashboard/api/routers/identity_capabilities.py"
    ) < 425
    assert all(
        _line_count(module) < 350
        for module in modules
        if not module.endswith("identity_capabilities.py")
    )
    assert "include_router" in _source(composition)
    assert "fastapi" not in _source("shared/identity.py")
    assert "fastapi" not in _source("shared/identity_capabilities.py")


def test_platform_identity_stays_out_of_the_tenant_data_plane():
    route = "app4-dashboard/api/routers/platform.py"
    domain = "shared/platform_identity.py"
    operator = "tools/manage_platform_support.py"
    sources = [_source(path) for path in (route, domain, operator)]

    assert _line_count(route) < 400
    assert _line_count(domain) < 250
    assert _line_count(operator) < 250
    assert "fastapi" not in _source(domain)
    assert "Mission" not in _source(route)
    assert "Dataset" not in _source(route)
    assert all("app1-colmap" not in source for source in sources)
    assert all("app2-ia" not in source for source in sources)
    assert all("app3-processing" not in source for source in sources)


def test_frontend_mission_runtime_owns_server_state_and_realtime_io():
    runtime = "app4-dashboard/frontend/app/lib/mission-runtime.tsx"
    runtime_state = "app4-dashboard/frontend/app/lib/mission-runtime-state.ts"
    store = "app4-dashboard/frontend/app/lib/store.tsx"
    page = "app4-dashboard/frontend/app/page.tsx"
    providers = "app4-dashboard/frontend/app/components/AppProviders.tsx"
    runtime_source = _source(runtime)
    runtime_state_source = _source(runtime_state)
    store_source = _source(store)
    page_source = _source(page)
    providers_source = _source(providers)

    assert _line_count(runtime) < 240
    assert _line_count(runtime_state) < 110
    assert "fetchMissionCatalog" in runtime_source
    assert "fetchMissionDetail" in runtime_source
    assert "new WebSocket" in runtime_source
    assert "autoSelectMission" in runtime_source
    assert "missionSummaryFromDetail" in runtime_state_source
    assert "mergeMissionSnapshots" in runtime_state_source
    assert "fetchMissionCatalog" not in store_source
    assert "fetchMissionDetail" not in store_source
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
