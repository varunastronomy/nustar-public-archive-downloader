import os
import re
import html
import shutil
import urllib.parse
import urllib.request
import subprocess
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed


BUCKET = "nasa-heasarc"
TABLEHEAD = (
    "name=heasarc_numaster&description=NuSTAR Master Catalog&"
    "url=https://heasarc.gsfc.nasa.gov/W3Browse/nustar/numaster.html&"
    "archive=Y&radius=12&mission=NuSTAR&priority=2&tabletype=Observation"
)


def ask(text, default):
    answer = input(text + " [" + default + "]: ").strip()
    if answer == "":
        return default
    return answer


def get_obsids(source, radius):
    params = {
        "tablehead": TABLEHEAD,
        "Action": "Start Search",
        "Coordinates": "Equatorial",
        "ResultMax": "10000",
        "displaymode": "Pure Text",
        "Entry": source,
        "Radius": radius,
        "varon": ["name", "obsid", "exposure_a", "time", "public_date"],
    }

    url = "https://heasarc.gsfc.nasa.gov/db-perl/W3Browse/w3query.pl?"
    url = url + urllib.parse.urlencode(params, doseq=True)

    page = urllib.request.urlopen(url, timeout=120).read().decode(errors="ignore")
    text = html.unescape(re.sub("<[^>]+>", " ", page)).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)

    rows = []
    pattern = (
        r"D\s+(?:B\s+)?(\S+)\s+(\d{11})\s+(-?[0-9.]+)\s+"
        r"(\d{4}-\d{2}-\d{2})(?:\s+\d{2}:\d{2}:\d{2})?\s+"
        r"(\d{4}-\d{2}-\d{2})"
    )
    today = date.today().isoformat()

    for name, obsid, exposure, start_date, public_date in re.findall(pattern, text):
        exposure = float(exposure)
        if exposure > 0 and public_date <= today:
            rows.append([name, obsid, exposure, start_date])

    return rows


def download_one(row, output, mode):
    name, obsid, exposure, start_date = row
    program = obsid[1:3]
    base = "s3://" + BUCKET + "/nustar/data/obs/" + program + "/" + obsid[0] + "/" + obsid + "/"

    if mode == "event_cl":
        s3_path = base + "event_cl/"
        local_path = os.path.join(output, obsid, "event_cl")
    else:
        s3_path = base
        local_path = os.path.join(output, obsid)

    command = [
        "aws", "s3", "sync", "--no-sign-request",
        s3_path, local_path, "--only-show-errors"
    ]

    result = subprocess.run(command)

    if result.returncode == 0 and os.path.isdir(local_path):
        for folder, subfolders, files in os.walk(local_path):
            if len(files) > 0:
                return obsid + " done"

    return obsid + " failed (no files downloaded)"


def print_rows(rows):
    old_year = ""

    for i, row in enumerate(rows):
        name, obsid, exposure, start_date = row
        year = start_date[:4]

        if year != old_year:
            print("")
            print("Year:", year)
            old_year = year

        print(str(i + 1).rjust(4), obsid, start_date, str(round(exposure / 1000, 2)).rjust(8), "ks")


def select_rows(rows, selection):
    if selection.lower() == "all":
        return rows

    chosen = []
    parts = selection.replace(",", " ").split()

    for part in parts:
        if "-" in part:
            start, stop = part.split("-")
            for number in range(int(start), int(stop) + 1):
                chosen.append(rows[number - 1])
        else:
            number = int(part)
            chosen.append(rows[number - 1])

    return chosen


def select_years(rows, year_text):
    if year_text == "" or year_text.lower() == "all":
        return rows

    years = year_text.replace(",", " ").split()
    return [row for row in rows if row[3][:4] in years]


source = ask("Source name", "2S 0918-549")
output = ask("Output folder", "NuSTAR_" + source.replace(" ", "_").replace("-", "_"))

print("Querying NUMASTER")
rows = get_obsids(source, "20 arcmin")
print("Public observations with usable FPMA exposure:", len(rows))
print("--------------------")

if len(rows) == 0:
    print("No public observations with positive exposure found")
    raise SystemExit

rows = sorted(rows, key=lambda x: x[3])
dates = sorted(set(row[3] for row in rows))
years = sorted(set(row[3][:4] for row in rows))

print("Available date range:", dates[0], "to", dates[-1])
print("Available years and FPMA exposure:")

for year in years:
    year_rows = [row for row in rows if row[3].startswith(year)]
    year_exposure = sum(row[2] for row in year_rows)
    print(year, ":", len(year_rows), "obs,", round(year_exposure / 1000, 2), "ks")

print("--------------------")

year_text = ask("Select year(s), example 2020 or 2020,2021 or all", "all")
rows = select_years(rows, year_text)
print("After year cut:", len(rows))

if len(rows) == 0:
    print("No observations to download")
    raise SystemExit

print("--------------------")
print("No.  ObsID        Date        FPMA Exposure")
print_rows(rows)
print("--------------------")

selection = ask("Select ObsIDs by number/range/comma, or all", "all")
rows = select_rows(rows, selection)
print("Selected observations:", len(rows))

mode = ask("Download mode: all or event_cl", "all").lower()
if mode not in ["all", "event_cl"]:
    print("Unknown mode. Use all or event_cl.")
    raise SystemExit

workers = int(ask("Parallel downloads", str(os.cpu_count())))

if shutil.which("aws") is None:
    print("AWS CLI not found. Install awscli first.")
    print("Example: pip install awscli")
    raise SystemExit

os.makedirs(output, exist_ok=True)

print("Downloading from AWS S3")
print("Bucket:", BUCKET)
print("--------------------")

done = 0

with ThreadPoolExecutor(max_workers=workers) as executor:
    jobs = [executor.submit(download_one, row, output, mode) for row in rows]

    for job in as_completed(jobs):
        done = done + 1
        print(job.result(), "|", done, "/", len(rows))

print("--------------------")
print("Download completed")
print("Output folder:", output)
print("Whole process is completed, thank you")
