from datetime import date, timedelta
from pathlib import Path
import re

import numpy as np
import pandas as pd
import rasterio
from rasterio.crs import CRS
from rasterio.warp import transform

from config.paths import(
    CHIRPS_DIR,
    PROCESSED_DIR,
)

#configuration
CHIRPS_FILE_PATTERN = ("chirps_v3_sat_nairobi_*.tif")
CHIRPS_OUTPUT_DIR = (PROCESSED_DIR /"weather" /"chirps")
EXPECTED_START_DATE = date(2004, 1, 1)
EXPECTED_END_DATE = date(2026, 7, 31)

WGS84 = CRS.from_epsg(4326)

#file discovery
def discover_chirps_files() -> dict[int, Path]:

    #discover annual Nairobi CHIRPS GeoTIFFS and map each file to its corresponding year

    files = sorted(CHIRPS_DIR.glob(CHIRPS_FILE_PATTERN))
    if not files: 
        raise FileNotFoundError("No CHIRPS GeoTIFF files were found in "f"{CHIRPS_DIR}")

    year_pattern = re.compile(r"(20\d{2})")
    year_files: dict[int, Path] ={}

    for file_path  in files:
        match = year_pattern.search(file_path.stem)

        if match is None:
            raise ValueError("Could not determine year from " f"CHIRPS filename: {file_path.name}")

        year = int(match.group(1))

        if year in year_files:
            raise ValueError("Multiple CHIRPS files found for " f"year {year}.")
        year_files[year] = file_path

        return dict(sorted(year_files.items()))

def _parse_band_date( description: str | None, ) -> date | None:

    #parse a date from an Earth Engine CHIRPS band description 

    if not description:
        return None

    match = re.search(r"(\d{4})[_-](\d{2})[_-](\d{2})", description)

    if match is None:
        return None
    return date(int(match.group(1)), int(match.group(2)), int(match.group(3)), )

def get_band_dates(dataset: rasterio.io.DatasetReader, year: int, ) -> list[date]:

    #determine the calendar date represented by every band in one annual CHIRPS GeoTIFF. Band descriptions are used where available. If descriptions are unavailable, dates are derived sequential from 1 January because acquisition was exported a one continuous daily band per day.

    descriptions = (dataset.descriptions or ())
    parsed_dates = [_parse_band_date(description) for description in descriptions]

    if (len(parsed_dates) == dataset.count and all (parsed_date is not None for parsed_date in parsed_dates)):
        dates = parsed_dates
    else:
        dates = [ date(year, 1, 1)+ timedelta(days=index) for index in range(dataset.count)]

    if len(set(dates)) != len(dates):
        raise ValueError(f"Duplicate CHIRPS band dates detected " f"for year {year}.")
    if any(band_date.year != year for band_date in dates):
        raise ValueError(f"CHIRPS band dates do not match " f"filename year {year}.")
    return dates

#archive inventory/ validation

def build_chirps_inventory() -> pd.DataFrame:

    #inspect every annual CHIRPS raster and return archive metadata

    year_files = (discover_chirps_files())
    records = []

    for year, file_path in year_files.items():
        with rasterio.open(file_path) as dataset:

            band_dates = get_band_dates(dataset, year,)
            records.append(
                {
                    "year": year,
                    "file_name": file_path.name,
                    "band_count": dataset.count,
                    "first_date": min(band_dates),
                    "last_date": max(band_dates),
                    "crs": str(dataset.crs),
                    "width": dataset.width,
                    "height": dataset.height,
                    "resolution_x": abs(dataset.transform.a),
                    "resolution_y": abs(dataset.transform.e),
                    "nodata": dataset.nodata,
                    "dtype": dataset.dtypes[0]
                }
            )
    return pd.DataFrame(
        records
    ).sort_values("year").reset_index(drop=True)

def validate_chirps_archive() -> pd.DataFrame:

    #validate that the finalized CHIRPS acquisition contains daily coverage from 2004-01-01 to 2026-07-31

    year_files = (discover_chirps_files())
    expected_years = set (range(EXPECTED_START_DATE.year, EXPECTED_END_DATE.year + 1))
    actual_years = set(year_files.keys())
    missing_years = (expected_years - actual_years)
    unexpected_years = (actual_years - expected_years)

    if missing_years:
        raise ValueError("CHIRPS archive is missing years: " f"{sorted(missing_years)}")

    if unexpected_years:
        raise ValueError("CHIRPS archive contains unexpected years: " f"{sorted(unexpected_years)}")
    all_dates = []

    for year, file_path in year_files.items():
        with rasterio.open(file_path) as dataset:
            band_dates = get_band_dates(dataset, year)
            all_dates.extend(band_dates)

    if len(set(all_dates)) != len(all_dates):
        raise ValueError("Duplicate CHIRPS band dates detected in the annual rasters archive.")

    expected_dates = set(pd.date_range(EXPECTED_START_DATE, EXPECTED_END_DATE, freq="D").date)
    actual_dates = set(all_dates)
    missing_dates = expected_dates - actual_dates
    unexpected_dates = actual_dates - expected_dates

    if missing_dates:
        raise ValueError("CHIRPS archive is missing " f"{len(missing_dates):,} daily bands")

    if unexpected_dates:
        raise ValueError("CHIRPS archive contains " f"{len(unexpected_dates):,}" " unexpected dates")
    return build_chirps_inventory()

#request validation
def validate_location_date_requests( dataframe: pd.DataFrame, date_column: str, latitude_column: str, longitude_column: str,) -> None:

    #validate the coordinate/date requests supplied to the CHIRPS extraction module

    required_columns = { date_column, latitude_column, longitude_column, }
    missing_columns = required_columns - set(dataframe.columns)

    if missing_columns:
        raise ValueError("CHIRPS extraction request is missing " f"columns: {sorted(missing_columns)}")
    if dataframe[latitude_column].isna().any():
        raise ValueError("CHIRPS extraction request contains missing latitude values.")
    if dataframe[longitude_column].isna().any():
        raise ValueError("CHIRPS extraction request contains missing longitude values.")
    if not dataframe[latitude_column].between(-90, 90).all():
        raise ValueError("Invalid latitude values detected.")
    if not dataframe[longitude_column].between(-180, 180).all():
        raise ValueError("Invalid longitude values detected.")

#rainfall extraction
def extract_daily_rainfall(request: pd.DataFrame, date_column: str, latitude_column: str = "latitude", longitude_column: str = "longitude") -> pd.DataFrame:

    #extract CHIRPS daily rainfall for arbitrary location and date requests. 
    # Rainfall value is expressed in mm/day. 
    # Input cordinates are expected in WGS84 lattitude/longitude.

    dataframe = request.copy()

    validate_location_date_requests(dataframe, date_column, latitude_column, longitude_column, )
    dataframe[date_column] = (pd.to_datetime(dataframe[date_column], errors="raise")
                              .dt.date)

    year_files = (discover_chirps_files())
    dataframe["_year"] = (
        dataframe[date_column].map(lambda value: value.year)
    )
    unavailable_years = (set(
        dataframe["_year"]
    )- set(year_files.keys()))

    if unavailable_years:
        raise ValueError("CHIRPS rainfall requested for unavailable years: " f"{sorted(unavailable_years)}")

    dataframe["rainfall_mm"] = np.nan
    dataframe["chirps_year"] = (dataframe["_year"])
    dataframe["chirps_band"] = (pd.Series(pd.NA, index=dataframe.index, dtype="Int64",))

    for year, year_rows in dataframe.groupby("_year"):
        file_path = year_files[int(year)]
        with rasterio.open(file_path) as dataset:
            if dataset.crs is None:
                raise ValueError("CHIRPS raster does not define " f"a CRS: {file_path.name}")

            band_dates = get_band_dates(dataset, int(year),)
            band_lookup = {band_date: band_number for band_number, band_date in enumerate(band_dates, start=1)}
            requested_dates = set( year_rows[date_column])
            missing_dates = requested_dates - set(band_lookup.keys())

            if missing_dates:
                raise ValueError("Requested CHIRPS dates are " f"missing for {year}: " f"{sorted(missing_dates)[:10]}")

            for (requested_date, date_rows,) in year_rows.groupby(date_column):
                band_number = (band_lookup[requested_date])
                longitudes = (date_rows[longitude_column].astype(float).tolist())
                latitudes = (date_rows[latitude_column].astype(float).tolist())

                if dataset.crs == WGS84:
                    raster_x = longitudes
                    raster_y = latitudes

                else: (
                    raster_x, raster_y,
                ) = transform(WGS84, dataset.crs, longitudes, latitudes)
                    
                coordinates = list(zip(raster_x, raster_y))
                sampled_values = []

                for sample in dataset.sample(coordinates, indexes=band_number, masked=True):
                    value = sample[0]

                    if np.ma.is_masked(value):
                        sampled_values.append(np.nan)

                    else:
                        sampled_values.append(float(value))
                
                dataframe.loc[date_rows.index, "rainfall_mm"] = sampled_values
                dataframe.loc[date_rows.index, "chirps_band"] = band_number

            negative_rainfall = dataframe["rainfall_mm"].dropna() < 0
            if negative_rainfall.any():
                raise ValueError("Negative CHIRPS rainfall values were detected after extraction")

            return (dataframe.drop(columns=["_year"]).reset_index(drop=True))


#groundsource event date requests

def load_groundsource_event_requests() -> pd.DataFrame:

    #location/date extraction request from grounsource positive-candidate output
    # this data is used as real-data validation of the CHIRPS extraction engine and does not construct the final modelling dataset

    flood_label_file =(PROCESSED_DIR/"flood_labels"/"groundsource_positive_candidates.csv")  

    if not flood_label_file.exists():
        raise FileNotFoundError("Processed Groundsource flood labels were not found. Run:  "f"{flood_label_file}")  

    dataframe = pd.read_csv(flood_label_file)
    required_columns = {
        "label_record_id",
        "event_group_id",
        "start_date",
        "latitude",
        "longitude",
        "verification_status"
    }

    missing = required_columns - set(dataframe.columns)
    if missing:
        raise ValueError("Processed Groundsource file is missing " f"columns: {sorted(missing)}")

    requests = dataframe[[
        "label_record_id",
        "event_group_id",
        "start_date",
        "latitude",
        "longitude",
        "verification_status"
    ]].copy()

    requests = requests.rename(columns={"start_date": "date"})

    return requests

#output writer

def save_chirps_outputs(inventory: pd.DataFrame, rainfall:pd.DataFrame, ) -> None:

    #save CHIRPS archive metadata and extracted event-date rainfall observations

    CHIRPS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    inventory_file = CHIRPS_OUTPUT_DIR / "chirps_archive_inventory.csv"
    rainfall_file = CHIRPS_OUTPUT_DIR / "groundsource_event_date_rainfall.csv"

    inventory.to_csv(inventory_file, index=False)
    rainfall.to_csv(rainfall_file, index=False)

    print(f"Saved CHIRPS inventory: " f"{inventory_file}")
    print(f"Saved event-date rainfall data: " f"{rainfall_file}")


       
                 