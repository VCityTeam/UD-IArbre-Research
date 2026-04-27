"""
Create sunlight-compatible sun position files.

Sunlight and pySunlight require pre-calculated sun positions in CSV format.
Uses pySolar to calculate positions based on latitude/longitude and stores
in a format compatible with the sunlight tools.

A sun position file for 1975-2075 is already provided with pySunlight;
use this script only if your data requires a different range.

Written by: Marwan Ait Addi (marwan.ait.addi@univ-lyon2.fr),
John Samuel (john.samuel@cpe.fr)
"""

import csv
import logging
import sys
from argparse import ArgumentParser
from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path

from pysolar.solar import get_altitude, get_azimuth

logger = logging.getLogger(__name__)


class ValidationError(ValueError):
    """Raised when input validation fails."""
    pass


def validate_coordinates(latitude: float, longitude: float) -> None:
    """Validate geographic coordinates.

    Args:
        latitude: Latitude in degrees (-90 to 90)
        longitude: Longitude in degrees (-180 to 180)

    Raises:
        ValidationError: If coordinates are outside valid ranges
    """
    if not -90 <= latitude <= 90:
        raise ValidationError(f"Latitude must be between -90 and 90, got {latitude}")
    if not -180 <= longitude <= 180:
        raise ValidationError(f"Longitude must be between -180 and 180, got {longitude}")


def validate_year_range(start_year: int, end_year: int) -> None:
    """Validate year range.

    Args:
        start_year: Start year
        end_year: End year

    Raises:
        ValidationError: If year range is invalid
    """
    if start_year > end_year:
        raise ValidationError(f"Start year ({start_year}) must be <= end year ({end_year})")
    if start_year < 1 or end_year > 9999:
        raise ValidationError("Years must be between 1 and 9999")


def get_sun_position_csv(
    latitude: float, longitude: float, year: int, month: int, day: int
) -> None:
    """Calculate and export sun position for a single day to CSV.

    Args:
        latitude: Latitude in degrees
        longitude: Longitude in degrees
        year: Year
        month: Month (1-12)
        day: Day of month
    """
    data = [["hour", "altitude", "azimut"]]

    for hour in range(24):
        date = datetime(year, month, day, hour, 0, tzinfo=timezone.utc)
        altitude = get_altitude(latitude, longitude, date)
        if altitude > 0:
            azimuth = get_azimuth(latitude, longitude, date)
            data.append([hour, altitude, azimuth])
        else:
            data.append([hour, "--", "--"])

    filename = f"{year}-{month:02d}-{day:02d}-{latitude}-{longitude}-sunpath.csv"
    with open(filename, "w", newline="") as file:
        writer = csv.writer(file)
        writer.writerows(data)


def calculate_annual_sun_path(latitude: float, longitude: float, year: int) -> list:
    """Calculate sun path for every hour of a given year.

    Args:
        latitude: Latitude in degrees
        longitude: Longitude in degrees
        year: Year

    Returns:
        List of daily sun path data
    """
    data = []

    for month in range(1, 13):
        days_in_month = monthrange(year, month)[1]
        for day in range(1, days_in_month + 1):
            date_str = datetime(year, month, day).strftime("%Y-%m-%d")
            daydata = [date_str, "--", "--"]

            for hour in range(24):
                date = datetime(year, month, day, hour, 0, tzinfo=timezone.utc)
                altitude = get_altitude(latitude, longitude, date)
                if altitude > -1:
                    daydata.append(str(round(altitude, 2)))
                    azimuth = get_azimuth(latitude, longitude, date)
                    daydata.append(str(round(azimuth, 2)))
                else:
                    daydata.append("--")
                    daydata.append("--")

            data.append(daydata)

    return data


def calculate_range_sunpath(
    latitude: float,
    longitude: float,
    start_year: int,
    end_year: int,
    filename: str,
) -> None:
    """Calculate sun path for a year range and export to CSV.

    Args:
        latitude: Latitude in degrees
        longitude: Longitude in degrees
        start_year: First year (inclusive)
        end_year: Last year (inclusive)
        filename: Output CSV filename

    Raises:
        ValidationError: If inputs are invalid
        IOError: If file cannot be written
    """
    validate_coordinates(latitude, longitude)
    validate_year_range(start_year, end_year)

    try:
        output_path = Path(filename)
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error(f"Cannot create output directory: {e}")
        raise IOError(f"Failed to create output directory: {e}") from e

    logger.info(
        f"Calculating sun path for coordinates ({latitude}, {longitude}) "
        f"from {start_year} to {end_year}"
    )

    data = [[str(latitude), str(longitude)]]

    for hour in range(24):
        hour_str = datetime(2000, 1, 1, hour, 0).strftime("%H:%M:%S")
        data[0].append(f"E {hour_str}")
        data[0].append(f"A {hour_str}")

    total_years = end_year - start_year + 1
    for year_idx, year in enumerate(range(start_year, end_year + 1), start=1):
        data.extend(calculate_annual_sun_path(latitude, longitude, year))
        logger.info(f"Progress: {year_idx}/{total_years} - Finished with year {year}")

    try:
        with open(filename, "w", newline="") as file:
            writer = csv.writer(file, delimiter=";", lineterminator=";\r\n")
            writer.writerows(data)
        logger.info(f"Successfully wrote results to {filename}")
    except IOError as e:
        logger.error(f"Failed to write output file: {e}")
        raise IOError(f"Failed to write output file: {e}") from e


def parse_command_line() -> object:
    """Parse command line arguments.

    Returns:
        Parsed arguments object
    """
    parser = ArgumentParser(
        description="Calculate sun position (altitude/azimuth) for a given location and year range. "
        "Outputs a CSV file compatible with pySunlight."
    )
    parser.add_argument(
        "--latitude",
        "-la",
        dest="latitude",
        type=float,
        required=True,
        help="Latitude in degrees (-90 to 90)",
    )
    parser.add_argument(
        "--longitude",
        "-lo",
        dest="longitude",
        type=float,
        required=True,
        help="Longitude in degrees (-180 to 180)",
    )
    parser.add_argument(
        "--start-year",
        "-s",
        dest="start_year",
        type=int,
        required=True,
        help="Start year (inclusive)",
    )
    parser.add_argument(
        "--end-year",
        "-e",
        dest="end_year",
        type=int,
        required=True,
        help="End year (inclusive)",
    )
    parser.add_argument(
        "--filename",
        "-f",
        type=str,
        default="sunpath.csv",
        help="Output CSV filename (default: sunpath.csv)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_command_line()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    try:
        calculate_range_sunpath(
            args.latitude,
            args.longitude,
            args.start_year,
            args.end_year,
            args.filename,
        )
        logger.info("Sunpath calculation completed successfully")
    except ValidationError as e:
        logger.error(f"Validation error: {e}")
        sys.exit(1)
    except IOError as e:
        logger.error(f"I/O error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
