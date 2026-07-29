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
        "app4-dashboard/api/routers/map_features.py",
        "app4-dashboard/api/routers/map_rasters.py",
    ]

    assert _line_count(composition) < 40
    assert all(_line_count(module) < 330 for module in feature_modules)
    assert "include_router" in _source(composition)
    assert all("confluent_kafka" not in _source(module) for module in [composition, *feature_modules])


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

    assert _line_count("app3-processing/main.py") < 650
    assert "AnalysisWorkflow(" in main_source
    assert "OrthomosaicTiler(" in main_source
    assert "def recover_analysis_runs" not in main_source
    assert "def slice_orthomosaic" not in main_source
    assert "import main" not in workflow_source
    assert "import main" not in tiler_source

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
