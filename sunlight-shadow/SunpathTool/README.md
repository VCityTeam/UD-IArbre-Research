# Sunpath Calculation Tool

Calculate sun position (altitude and azimuth) for any geographic coordinate and year range. Outputs CSV files compatible with [pySunlight](https://github.com/VCityTeam/pySunlight) and [Sunlight](https://github.com/VCityTeam/Sunlight) libraries.

## Requirements

- Python 3.12 or higher (for native execution)
- Docker and Docker Compose (for containerized execution)
- [pysolar](https://docs.pysolar.org/en/latest/) library (automatically installed)

## Installation

### Option 1: Native Python Installation

```bash
cd SunpathTool
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
python3 -m pip install -r requirements.txt
```

### Option 2: Docker (Recommended for Reproducibility)

```bash
cd SunpathTool
docker build -t sunpath:latest .
```

## Usage

### Native Python

```bash
python3 sunpath.py --latitude 45.75 --longitude 4.85 -s 1975 -e 2075 -f output.csv
```

With logging:
```bash
python3 sunpath.py --latitude 45.75 --longitude 4.85 -s 1975 -e 2075 --log-level DEBUG
```

### Docker

```bash
docker run --rm -v $(pwd)/output:/workspace/output sunpath:latest \
  --latitude 45.75 --longitude 4.85 -s 1975 -e 2075 -f /workspace/output/sunpath.csv
```

### Docker Compose

```bash
# Run calculation for Lyon
docker-compose run --rm sunpath

# Run tests
docker-compose --profile test run --rm sunpath-test

# Custom coordinates
docker-compose run --rm sunpath \
  --latitude 48.85 --longitude 2.35 --start-year 2000 --end-year 2050 \
  --filename /workspace/output/sunpath_paris.csv
```

## Command-Line Arguments

| Argument | Short | Type | Required | Description | Example |
|----------|-------|------|----------|-------------|---------|
| `--latitude` | `-la` | float | Yes | Latitude in degrees (-90 to 90) | `45.75` |
| `--longitude` | `-lo` | float | Yes | Longitude in degrees (-180 to 180) | `4.85` |
| `--start-year` | `-s` | int | Yes | Start year (inclusive) | `1975` |
| `--end-year` | `-e` | int | Yes | End year (inclusive) | `2075` |
| `--filename` | `-f` | str | No | Output CSV file path (default: `sunpath.csv`) | `output/sunpath.csv` |
| `--log-level` | - | str | No | Logging verbosity: DEBUG, INFO, WARNING, ERROR (default: INFO) | `DEBUG` |

## Features

- **Input Validation**: Validates geographic coordinates and date ranges
- **Error Handling**: Comprehensive error messages for debugging
- **Progress Tracking**: Real-time progress updates during calculation
- **Logging**: Configurable logging levels for monitoring
- **File Management**: Automatically creates output directories
- **Docker Support**: Reproducible execution in containers
- **Documentation**: Detailed docstrings and type hints

## Output Format

The CSV file contains:
- **Header Row 1**: Latitude and longitude, followed by column headers for each hour (E = Elevation, A = Azimuth)
- **Data Rows**: One row per day with the date and 48 values (altitude and azimuth for each of 24 hours)
- **Missing Values**: Represented as `--` when sun is below horizon

Example output structure:
```
45.75;4.85;E 00:00:00;A 00:00:00;E 01:00:00;A 01:00:00;...
2000-01-01;--;--;15.5;250.3;...;
```

## Importing as a Library

```python
from sunpath import calculate_range_sunpath

calculate_range_sunpath(
    latitude=45.75,
    longitude=4.85,
    start_year=1975,
    end_year=2075,
    filename="output.csv"
)
```

## Integration with pySunlight

Use the generated CSV file to replace the default sunpath data in pySunlight:

```bash
python3 sunpath.py --latitude 48.85 --longitude 2.35 -s 1975 -e 2075 -f sunpath_paris.csv
cp sunpath_paris.csv /path/to/pySunlight/datas/AnnualSunPath_Paris.csv
```

## Development

### Running Tests

```bash
pytest tests/ -v --cov=sunpath
```

### Code Quality

```bash
black sunpath.py
isort sunpath.py
flake8 sunpath.py
mypy sunpath.py
```

## Troubleshooting

### `ValidationError: Latitude must be between -90 and 90`
Check that latitude is in valid range (-90 to 90). Note: Latitude is the vertical coordinate (North/South).

### `ValidationError: Start year must be <= end year`
Ensure `--start-year` is not greater than `--end-year`.

### `IOError: Cannot create output directory`
Check write permissions for the output directory and parent directories.

### Slow Execution
For large year ranges, calculation may take hours. Consider:
- Running on a faster machine
- Reducing the year range
- Using Docker with volume mounts for I/O optimization

## Performance Notes

- Single year calculation: ~30-60 seconds
- Multi-year range: Linear scaling (approximately 45-60 seconds per year)
- Memory usage: ~100-500 MB depending on year range
