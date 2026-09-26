"""Save the hackathon's synthetic BigQuery table locally without a query job."""

import csv
import gzip
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


BASE = (
    "https://bigquery.googleapis.com/bigquery/v2/projects/batalha-time-05-xew3"
    "/datasets/hackathon_dados/tables/extrato_sintetico"
)
OUTPUT = Path(__file__).parent / "data" / "extrato_sintetico.csv.gz"


def main():
    token = subprocess.check_output(
        ["gcloud", "auth", "application-default", "print-access-token"], text=True
    ).strip()

    def get(url):
        for attempt in range(5):
            try:
                request = urllib.request.Request(
                    url, headers={"Authorization": f"Bearer {token}"}
                )
                with urllib.request.urlopen(request, timeout=60) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 4:
                    raise
                time.sleep(2**attempt)

    metadata = get(BASE)
    fields = metadata["schema"]["fields"]
    expected = int(metadata["numRows"])
    OUTPUT.parent.mkdir(exist_ok=True)
    temporary = OUTPUT.with_suffix(OUTPUT.suffix + ".part")
    count = 0
    page_token = None
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(field["name"] for field in fields)
            while True:
                params = {"maxResults": 10000}
                if page_token:
                    params["pageToken"] = page_token
                page = get(BASE + "/data?" + urllib.parse.urlencode(params))
                for row in page.get("rows", []):
                    values = [cell.get("v") for cell in row["f"]]
                    for index, field in enumerate(fields):
                        if field["type"] == "TIMESTAMP" and values[index] is not None:
                            values[index] = datetime.fromtimestamp(
                                float(values[index]), timezone.utc
                            ).isoformat()
                    writer.writerow(values)
                    count += 1
                page_token = page.get("pageToken")
                if not page_token:
                    break
        if count != expected:
            raise ValueError(f"Downloaded {count} rows; expected {expected}")
        os.replace(temporary, OUTPUT)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    print(f"Saved {count} rows to {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
