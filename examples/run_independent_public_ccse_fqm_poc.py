import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from urllib.request import urlopen

from hedis_engine.api import execute_request


POC_LABEL = "Independent technical PoC - not an official HEDIS measure and not equivalent to NCQA CCS-E."
MEASURE_ID = "CMS124FHIRCervicalCancerScreen"
MEASURE_VERSION = "public-qicore-2025"
MEASURE_BUNDLE_FILE = "CMS124FHIRCervicalCancerScreen-bundle.json"
TEST_CASE_API_BASE = (
    "https://api.github.com/repos/cqframework/dqm-content-qicore-2025/contents/"
    "input/tests/measure/CMS124FHIRCervicalCancerScreen"
)

_CACHE: Dict[str, Any] = {}


def _download_json(url: str) -> Dict[str, Any] | List[Any]:
    if url in _CACHE:
        return _CACHE[url]

    with urlopen(url, timeout=60) as response:
        payload = response.read().decode("utf-8")

    parsed = json.loads(payload)
    _CACHE[url] = parsed
    return parsed


def _without_resource_types(bundle: Dict[str, Any], resource_types: set[str]) -> Dict[str, Any]:
    cloned = json.loads(json.dumps(bundle))
    filtered_entries: List[Dict[str, Any]] = []
    for entry in cloned.get("entry", []):
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if isinstance(resource, dict) and resource.get("resourceType") in resource_types:
            continue
        filtered_entries.append(entry)
    cloned["entry"] = filtered_entries
    return cloned


def _without_root_library(bundle: Dict[str, Any]) -> Dict[str, Any]:
    cloned = json.loads(json.dumps(bundle))
    entries = cloned.get("entry", [])
    root_library_id = ""

    for entry in entries:
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if not isinstance(resource, dict) or resource.get("resourceType") != "Measure":
            continue
        references = resource.get("library")
        if isinstance(references, list) and references and isinstance(references[0], str):
            first_ref = references[0].strip()
            root_library_id = first_ref.split("/")[-1].split("|")[0]
        break

    if not root_library_id:
        return cloned

    filtered_entries: List[Dict[str, Any]] = []
    removed = False
    for entry in entries:
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if (
            not removed
            and isinstance(resource, dict)
            and resource.get("resourceType") == "Library"
            and str(resource.get("id", "")).strip() == root_library_id
        ):
            removed = True
            continue
        filtered_entries.append(entry)

    cloned["entry"] = filtered_entries
    return cloned


def _extract_population_counts(case_id: str) -> Dict[str, int] | None:
    listing_url = f"{TEST_CASE_API_BASE}/{case_id}?ref=master"
    listing = _download_json(listing_url)
    if not isinstance(listing, list):
        return None

    report_item = None
    for item in listing:
        if isinstance(item, dict) and str(item.get("name", "")).startswith("MeasureReport-"):
            report_item = item
            break

    if not report_item:
        return None

    report = _download_json(str(report_item.get("download_url", "")))
    if not isinstance(report, dict):
        return None

    groups = report.get("group", [])
    if not groups or not isinstance(groups[0], dict):
        return None

    counts = {
        "numerator": 0,
        "denominator": 0,
        "denominator_exclusion": 0,
    }

    for pop in groups[0].get("population", []):
        if not isinstance(pop, dict):
            continue
        coding = pop.get("code", {}).get("coding", [])
        code_value = ""
        if isinstance(coding, list) and coding and isinstance(coding[0], dict):
            code_value = str(coding[0].get("code", "")).strip().lower()

        try:
            count_value = int(pop.get("count", 0))
        except (TypeError, ValueError):
            count_value = 0

        if code_value == "numerator":
            counts["numerator"] = count_value
        elif code_value == "denominator":
            counts["denominator"] = count_value
        elif code_value == "denominator-exclusion":
            counts["denominator_exclusion"] = count_value

    return counts


def _discover_case_ids() -> tuple[str, str]:
    listing = _download_json(f"{TEST_CASE_API_BASE}?ref=master")
    if not isinstance(listing, list):
        raise ValueError("Public case directory listing payload was not a JSON array")

    positive_case = ""
    negative_case = ""

    for item in listing:
        if not isinstance(item, dict) or item.get("type") != "dir":
            continue

        case_id = str(item.get("name", "")).strip()
        if not case_id or case_id.startswith("."):
            continue

        counts = _extract_population_counts(case_id)
        if counts is None:
            continue

        if counts["numerator"] > 0 and not positive_case:
            positive_case = case_id
        if (
            counts["numerator"] == 0
            and counts["denominator"] > 0
            and counts["denominator_exclusion"] == 0
            and not negative_case
        ):
            negative_case = case_id

        if positive_case and negative_case:
            break

    if not positive_case or not negative_case:
        raise ValueError("Could not discover both positive and negative public CMS124 case IDs")

    return positive_case, negative_case


def _patient_bundle_for_case(case_id: str) -> Dict[str, Any]:
    listing_url = f"{TEST_CASE_API_BASE}/{case_id}?ref=master"
    listing = _download_json(listing_url)

    entries: List[Dict[str, Any]] = []
    for item in listing:
        if not isinstance(item, dict) or item.get("type") != "file":
            continue

        name = str(item.get("name", ""))
        if name.startswith("MeasureReport-") or name.lower().endswith(".txt"):
            continue

        download_url = item.get("download_url")
        if not isinstance(download_url, str) or not download_url:
            continue

        resource = _download_json(download_url)
        if isinstance(resource, dict) and resource.get("resourceType"):
            entries.append({"resource": resource})

    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": entries,
    }


def _run(member_id: str, measure_bundle: Dict[str, Any], patient_bundle: Dict[str, Any], include_measure_report: bool = True) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="independent-ccse-poc-") as tmp_dir:
        bundle_path = Path(tmp_dir) / "public-cms124-bundle.json"
        bundle_path.write_text(json.dumps(measure_bundle), encoding="utf-8")

        request = {
            "measurement_year": "2026",
            "measure": {
                "id": MEASURE_ID,
                "version": MEASURE_VERSION,
            },
            "execution": {
                "adapter": "fqm-execution",
                "fqm": {
                    "measure_bundle_path": str(bundle_path),
                    "runtime_enabled": False,
                    "trust_meta_profile": False,
                    "include_measure_report": include_measure_report,
                },
            },
            "member": {
                "member_id": member_id,
                "fhir_bundle": patient_bundle,
            },
            "input": {},
        }
        return execute_request(request)


def main() -> None:
    base_dir = Path(__file__).resolve().parent / "independent_public_ccse"
    output_dir = Path(__file__).resolve().parent / "_tmp_independent_public_ccse_fqm_poc"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(POC_LABEL)
    print("Executing through local fqm-execution CLI adapter with public-source CMS124 artifacts.")

    measure_bundle = json.loads((base_dir / MEASURE_BUNDLE_FILE).read_text(encoding="utf-8-sig"))
    positive_case_id, negative_case_id = _discover_case_ids()

    positive_bundle = _patient_bundle_for_case(positive_case_id)
    negative_bundle = _patient_bundle_for_case(negative_case_id)
    unresolved_bundle = _without_resource_types(positive_bundle, {"Encounter", "Observation", "Procedure"})
    missing_dependency_bundle = _without_root_library(measure_bundle)

    scenarios = [
        ("positive", positive_case_id, measure_bundle, positive_bundle, True),
        ("negative", negative_case_id, measure_bundle, negative_bundle, True),
        ("unresolved_evidence", f"{positive_case_id}-unresolved", measure_bundle, unresolved_bundle, True),
        ("missing_dependency", f"{positive_case_id}-missing-dependency", missing_dependency_bundle, positive_bundle, False),
    ]

    summary: List[Dict[str, Any]] = []
    for scenario_name, member_id, scenario_measure_bundle, patient_bundle, include_measure_report in scenarios:
        result = _run(member_id, scenario_measure_bundle, patient_bundle, include_measure_report=include_measure_report)

        scenario_path = output_dir / f"{scenario_name}_result.json"
        labeled = {
            "label": POC_LABEL,
            "scenario": scenario_name,
            "result": result,
        }
        scenario_path.write_text(json.dumps(labeled, indent=2) + "\n", encoding="utf-8")

        populations = result.get("populations", {}) if isinstance(result, dict) else {}
        summary.append(
            {
                "label": POC_LABEL,
                "scenario": scenario_name,
                "member_id": member_id,
                "status": result.get("status") if isinstance(result, dict) else None,
                "outcome": result.get("outcome") if isinstance(result, dict) else None,
                "error_codes": [err.get("code") for err in result.get("errors", [])]
                if isinstance(result, dict)
                else [],
                "initial_population": populations.get("initial_population") if isinstance(populations, dict) else None,
                "denominator": populations.get("denominator") if isinstance(populations, dict) else None,
                "numerator": populations.get("numerator") if isinstance(populations, dict) else None,
                "measure_report_resource_type": result.get("measure_report", {}).get("resourceType")
                if isinstance(result, dict)
                else None,
                "measure_report_measure": result.get("measure_report", {}).get("measure")
                if isinstance(result, dict)
                else None,
                "result_path": str(scenario_path),
            }
        )

    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    sources_path = output_dir / "public_sources_used.json"
    sources_payload = {
        "label": POC_LABEL,
        "base_artifacts": [
            str(base_dir / "CMS124FHIRCervicalCancerScreen-bundle.json"),
            str(base_dir / "CervicalCancerScreeningFHIR-0.0.001.cql"),
            str(base_dir / "CMS124FHIRCervicalCancerScreen.elm.json"),
            str(base_dir / "CMS124FHIR-v0.0.001-FHIR.json"),
        ],
        "public_source_urls": [
            "https://raw.githubusercontent.com/cqframework/dqm-content-qicore-2025/master/bundles/measure/CMS124FHIRCervicalCancerScreen/CMS124FHIRCervicalCancerScreen-bundle.json",
            "https://api.github.com/repos/cqframework/dqm-content-qicore-2025/contents/input/tests/measure/CMS124FHIRCervicalCancerScreen?ref=master",
            "https://raw.githubusercontent.com/cqframework/ecqm-content-qicore-2024/main/bundles/mat/CMS124FHIR-v0.0.001-FHIR4/cql/CervicalCancerScreeningFHIR-0.0.001.cql",
            "https://raw.githubusercontent.com/cqframework/ecqm-content-qicore-2024/main/bundles/mat/CMS124FHIR-v0.0.001-FHIR4/CMS124FHIR-v0.0.001-FHIR.json",
        ],
        "selected_positive_case_id": positive_case_id,
        "selected_negative_case_id": negative_case_id,
    }
    sources_path.write_text(json.dumps(sources_payload, indent=2) + "\n", encoding="utf-8")

    print("Execution summary:")
    print(json.dumps(summary, indent=2))
    print(f"Wrote artifacts: {output_dir}")


if __name__ == "__main__":
    main()
