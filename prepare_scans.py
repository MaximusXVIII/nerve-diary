#!/usr/bin/env python3
"""
prepare_scans.py

Run this LOCALLY against your raw, unedited DICOM export (the folder your
hospital/clinic gave you). It strips identifying metadata (name, DOB, ID
numbers, address, referring physician, institution, device serial, etc.)
from every file, and packs everything into a single zip file, organized
into subfolders by MRI series (e.g. sag-t1/, ax-t2/).

Requirements:
    pip install pydicom

Usage:
    python3 prepare_scans.py /path/to/raw_dicom_folder /path/to/repo/scans.zip

Example:
    python3 prepare_scans.py ~/Downloads/MRI_EXPORT ~/dev/nerve-diary/scans.zip

After running, commit the resulting scans.zip to your repo (replacing any
previous version).

IMPORTANT LIMITATION: this strips metadata tags only. If any of your images
have patient details "burned in" as visible text on the image itself
(common on exported screenshots, less common on raw scanner DICOM), this
script cannot remove that — check a few slices visually before committing.
"""

import sys
import re
import zipfile
import tempfile
from pathlib import Path

try:
    import pydicom
    from pydicom.errors import InvalidDicomError
except ImportError:
    print("Missing dependency. Install it with:\n    pip install pydicom")
    sys.exit(1)

# Tags that can identify the patient, referring clinicians, or the
# scanning site. Left untouched: SeriesDescription, ProtocolName, and
# clinical/technical fields (needed to label the scan correctly).
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
    for elem in ds.iterall():
        if elem.VR == "PN":  # Person Name value representation
            elem.value = ""


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    src_dir = Path(sys.argv[1]).expanduser()
    out_zip = Path(sys.argv[2]).expanduser()

    if not src_dir.is_dir():
        print(f"Input folder not found: {src_dir}")
        sys.exit(1)

    if out_zip.suffix.lower() != ".zip":
        print("Output path should end in .zip, e.g. .../repo/scans.zip")
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

    out_zip.parent.mkdir(parents=True, exist_ok=True)

    print("\nWriting zip...")
    used_slugs = set()

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)

        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for series_uid, data in sorted(series_map.items(), key=lambda kv: kv[1]["label"]):
                label = data["label"]
                slug = slugify(label)
                base_slug = slug
                n = 2
                while slug in used_slugs:
                    slug = f"{base_slug}-{n}"
                    n += 1
                used_slugs.add(slug)

                instances = sorted(data["instances"], key=lambda t: t[0])
                for i, (_, ds) in enumerate(instances, start=1):
                    filename = f"{i:04d}.dcm"
                    tmp_path = tmp_dir / filename
                    ds.save_as(tmp_path, write_like_original=False)
                    zf.write(tmp_path, f"{slug}/{filename}")
                    tmp_path.unlink()

                print(f"  {label}  ->  {slug}/  ({len(instances)} slices)")

    size_mb = out_zip.stat().st_size / (1024 * 1024)
    print(f"\nDone. Wrote {out_zip} ({size_mb:.1f} MB, {len(series_map)} series).")
    if skipped:
        print(f"Skipped {skipped} file(s) that weren't readable as DICOM.")
    print("Review a few slices visually for any burned-in text before committing —")
    print("this script only strips metadata tags, not pixel content.")


if __name__ == "__main__":
    main()
