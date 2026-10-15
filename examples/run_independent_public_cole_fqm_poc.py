import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from hedis_engine.api import execute_request


POC_LABEL = "Independent technical PoC - not an official HEDIS measure and not equivalent to NCQA COL-E."
MEASURE_ID = "PublicColorectalCancerScreeningTechnicalDemo"
MEASURE_VERSION = "0.1.0"


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


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


def _run(member_id: str, measure_bundle: Dict[str, Any], patient_bundle: Dict[str, Any], include_measure_report: bool = True) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="independent-cole-poc-") as tmp_dir:
        bundle_path = Path(tmp_dir) / "public-cole-bundle.json"
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
    base_dir = Path(__file__).resolve().parent / "independent_public_cole"
    output_dir = Path(__file__).resolve().parent / "_tmp_independent_public_cole_fqm_poc"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(POC_LABEL)
    print("Executing through local fqm-execution CLI adapter with independently authored artifacts and public-source rules.")

    measure_bundle = _load_json(base_dir / f"{MEASURE_ID}-bundle.json")
    positive_bundle = _load_json(base_dir / "patients" / "patient_positive_synthea_bundle.json")
    negative_bundle = _load_json(base_dir / "patients" / "patient_negative_synthea_bundle.json")
    unresolved_bundle = _load_json(base_dir / "patients" / "patient_unresolved_synthea_bundle.json")
    missing_dependency_bundle = _without_root_library(measure_bundle)

    scenarios = [
        ("positive", "p-cole-positive", measure_bundle, positive_bundle, True),
        ("negative", "p-cole-negative", measure_bundle, negative_bundle, True),
        ("unresolved_evidence", "p-cole-unresolved", measure_bundle, unresolved_bundle, True),
        ("missing_dependency", "p-cole-missing-dependency", missing_dependency_bundle, positive_bundle, False),
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
            str(base_dir / f"{MEASURE_ID}.cql"),
            str(base_dir / f"{MEASURE_ID}.elm.json"),
            str(base_dir / f"{MEASURE_ID}-bundle.json"),
        ],
        "public_source_urls": [
            "https://github.com/cqframework/ecqm-content-qicore-2024",
            "https://github.com/cqframework/ecqm-content-qicore-2024/blob/master/input/cql/ColonCancerScreeningFHIR.cql",
            "https://raw.githubusercontent.com/cqframework/ecqm-content-qicore-2024/master/LICENSE",
            "https://snomed.org",
            "https://www.ama-assn.org/practice-management/cpt",
        ],
        "synthea_source_files": {
            "positive": "C:/synthea/synthea-master/output/fhir/Guadalupe206_Russel238_e9d42389-a8e8-c60d-1f49-da5a95118ffb.json",
            "negative": "C:/synthea/synthea-master/output/fhir/Claudio955_Pizarro791_022b9d89-4321-91a2-393c-ca9ced3c2cd6.json",
            "unresolved": "C:/synthea/synthea-master/output/fhir/Avery919_Koss676_6f7c251a-e153-c9a7-a641-3d92f0f4b87f.json"
        },
    }
    sources_path.write_text(json.dumps(sources_payload, indent=2) + "\n", encoding="utf-8")

    print("Execution summary:")
    print(json.dumps(summary, indent=2))
    print(f"Wrote artifacts: {output_dir}")


if __name__ == "__main__":
    main()
