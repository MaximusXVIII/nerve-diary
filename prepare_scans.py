#!/usr/bin/env python3
"""
prepare_scans.py

Run this LOCALLY against your raw, unedited DICOM export (the folder your
hospital/clinic gave you). It does two things:

  1. Strips identifying metadata (name, DOB, ID numbers, address, referring
     physician, institution, device serial, etc.) from every file.
  2. Groups the files by MRI series (e.g. "Sag T1", "Ax T2"), renames them
     sequentially, and writes them into scans/<series-folder>/, plus a
     scans/manifest.json that the website reads to build the series picker
     and auto-load each series.
  3. Zips each series (and the whole set) into scans/downloads/ so visitors
     can download the anonymized DICOM files directly from the site.

Requirements:
    pip install pydicom

Usage:
    python3 prepare_scans.py /path/to/raw_dicom_folder /path/to/repo/scans

Example:
    python3 prepare_scans.py ~/Downloads/MRI_EXPORT ~/dev/nerve-diary/scans

After running, commit the resulting scans/ folder to your repo.

IMPORTANT LIMITATION: this strips metadata tags only. If any of your images
have patient details "burned in" as visible text on the image itself
(common on exported screenshots, less common on raw scanner DICOM), this
script cannot remove that — check a few slices visually before committing.
"""

import sys
import re
import json
import shutil
import zipfile
from pathlib import Path

try:
    import pydicom
    from pydicom.errors import InvalidDicomError
except ImportError:
    print("Missing dependency. Install it with:\n    pip install pydicom")
    sys.exit(1)

# Tags that can identify the patient, referring clinicians, or the
# scanning site. Left untouched: SeriesDescription, ProtocolName, and
# clinical/technical fields (needed to label and render the scan correctly).
PHI_TAGS = [
    "PatientName", "PatientID", "PatientBirthDate", "PatientBirthTime",
    "PatientSex", "PatientAge", "PatientSize", "PatientWeight",
    "PatientAddress", "PatientTelephoneNumbers", "PatientMotherBirthName",
    "OtherPatientIDs", "OtherPatientNames", "PatientComments",
    "PatientReligiousPreference", "PatientInsurancePlanCodeSequence",
    "ReferringPhysicianName", "ReferringPhysicianAddress",
    "ReferringPhysicianTelephoneNumbers", "PerformingPhysicianName",
    "OperatorsName", "RequestingPhysician", "PhysiciansOfRecord",
    "NameOfPhysiciansReadingStudy", "AdmittingDiagnosesDescription",
    "InstitutionName", "InstitutionAddress", "InstitutionalDepartmentName",
    "StationName", "DeviceSerialNumber", "StudyID", "AccessionNumber",
    "IssuerOfPatientID", "RequestingService",
]


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "series"


def anonymize(ds: pydicom.Dataset) -> None:
    for tag in PHI_TAGS:
        if tag in ds:
            ds.data_element(tag).value = ""
    ds.remove_private_tags()
    # Wipe any patient-identifying elements that live in nested sequences too.
    for elem in ds.iterall():
        if elem.VR == "PN":  # Person Name value representation
            elem.value = ""


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    src_dir = Path(sys.argv[1]).expanduser()
    out_dir = Path(sys.argv[2]).expanduser()

    if not src_dir.is_dir():
        print(f"Input folder not found: {src_dir}")
        sys.exit(1)

    files = [p for p in src_dir.rglob("*") if p.is_file()]
    if not files:
        print(f"No files found in {src_dir}")
        sys.exit(1)

    print(f"Found {len(files)} file(s). Reading and anonymizing...")

    series_map = {}  # series_uid -> {"label": str, "instances": [(instance_number, dataset)]}
    skipped = 0

    for idx, path in enumerate(files, start=1):
        try:
            ds = pydicom.dcmread(path)
        except (InvalidDicomError, Exception):
            skipped += 1
            continue

        series_uid = str(getattr(ds, "SeriesInstanceUID", path.parent.name))
        label = str(getattr(ds, "SeriesDescription", "")).strip() or f"Series {series_uid[-6:]}"
        instance_number = int(getattr(ds, "InstanceNumber", 0) or 0)

        anonymize(ds)

        series_map.setdefault(series_uid, {"label": label, "instances": []})
        series_map[series_uid]["instances"].append((instance_number, ds))

        if idx % 25 == 0 or idx == len(files):
            print(f"  processed {idx}/{len(files)} files...")

    if not series_map:
        print("No readable DICOM files found — nothing to do.")
        sys.exit(1)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    manifest = {"series": []}
    used_slugs = set()

    downloads_dir = out_dir / "downloads"
    downloads_dir.mkdir(parents=True)
    all_zip_path = downloads_dir / "all-series.zip"
    all_zip = zipfile.ZipFile(all_zip_path, "w", zipfile.ZIP_DEFLATED)

    print("\nWriting series and building downloadable zips...")

    # Sort series by their first instance's label for a stable, readable order.
    for series_uid, data in sorted(series_map.items(), key=lambda kv: kv[1]["label"]):
        label = data["label"]
        slug = slugify(label)
        base_slug = slug
        n = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{n}"
            n += 1
        used_slugs.add(slug)

        series_dir = out_dir / slug
        series_dir.mkdir(parents=True)

        instances = sorted(data["instances"], key=lambda t: t[0])
        file_paths = []
        series_zip_path = downloads_dir / f"{slug}.zip"
        with zipfile.ZipFile(series_zip_path, "w", zipfile.ZIP_DEFLATED) as series_zip:
            for i, (_, ds) in enumerate(instances, start=1):
                filename = f"{i:04d}.dcm"
                file_disk_path = series_dir / filename
                ds.save_as(file_disk_path, write_like_original=False)
                file_paths.append(f"scans/{slug}/{filename}")

                arcname = f"{slug}/{filename}"
                series_zip.write(file_disk_path, arcname)
                all_zip.write(file_disk_path, arcname)

        manifest["series"].append({
            "id": slug,
            "label": label,
            "count": len(file_paths),
            "files": file_paths,
            "zip": f"scans/downloads/{slug}.zip",
        })
        print(f"  {label}  ->  scans/{slug}/  ({len(file_paths)} slices)  +  scans/downloads/{slug}.zip")

    all_zip.close()
    manifest["allZip"] = "scans/downloads/all-series.zip"

    with open(out_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nDone. {len(manifest['series'])} series written to {out_dir}")
    print(f"All-series download: scans/downloads/all-series.zip")
    if skipped:
        print(f"Skipped {skipped} file(s) that weren't readable as DICOM.")
    print("Review a few slices visually for any burned-in text before committing —")
    print("this script only strips metadata tags, not pixel content.")


if __name__ == "__main__":
    main()
