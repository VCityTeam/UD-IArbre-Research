from pysolar.solar import get_altitude, get_azimuth
import datetime, time
import csv
import argparse
from calendar import monthrange

def get_sun_position_csv(latitude : float, longitude : float, year : int, month : int, day : int):
    """
    This function uses pysolar to calculate and store into a csv the position of the sun for a given day
    :param latitude: a latitude in degrees
    :type latitude: float

    :param longitude: a longitude in degrees datetime(year, month, day, hour, minute)
    :type longitude: float

    :param year: The year for which to calculate the sunpath (ex: 2025, 1979, ...)
    :type year: int

    :param month: The month for which to calculate the sunpath (ex: 01, 06, ...)
    :type month: int

    :param day: The day for which to calculate the sunpath (ex: 01, 25, ...)
    :type day: int
    :param longitude: a longitude in degrees datetime(year, month, day, hour, minute)
    """

    data = [['hour', 'altitude', 'azimut']]

    for i in range(0,24):
        date = datetime.datetime(year, month, day, i, 0, tzinfo=datetime.timezone.utc)
        altitude = get_altitude(latitude, longitude, date)
        if altitude > 0:
            data.append([i, altitude, get_azimuth(latitude, longitude, date)])
        else:
            data.append([i, '--', '--'])
    
    with open(f"{year}-{month}-{day}-{latitude}-{longitude}-sunpath", 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerows(data)

def calculate_annual_sun_path(latitude : float, longitude : float, year: int):
    """
    This function uses pysolar to calculate the sunpath for every hour of a given year
    :param latitude: a latitude in degrees
    :type latitude: float

    :param longitude: a longitude in degrees datetime(year, month, day, hour, minute)
    :type longitude: float

    :param year: The year for which to calculate the sunpath (ex: 2025, 1979, ...)
    :type year: int

    :returns: [[str]] A list of list of strings, each list representing a day
    """

    data = []

    for month in range(1, 13): # Month is always 1..12
        for day in range(1, monthrange(year, month)[1] + 1):
            daydata = [time.strftime("%Y-%m-%d", datetime.datetime(year,month,day).timetuple()), '--', '--']
            for i in range(0,24):
                date = datetime.datetime(year, month, day, i, 0, tzinfo=datetime.timezone.utc)
                altitude = get_altitude(latitude, longitude, date)
                if altitude > -1:
                    daydata.append(str(round(altitude, 2)))
                    daydata.append(str(round(get_azimuth(latitude, longitude, date), 2)))
                else:
                    daydata.append('--')
                    daydata.append('--')
            data.append(daydata)
    
    return data

def calculate_range_sunpath(latitude : float, longitude : float, startyear : int, endyear : int, filename : str):
    """
    This function uses pysolar to calculate the sunpath for every hour of a given interval startyear and endyear included
    The result is exported in csv format to the file specified by filename
    :param latitude: a latitude in degrees
    :type latitude: float

    :param longitude: a longitude in degrees datetime(year, month, day, hour, minute)
    :type longitude: float

    :param startyear: The first year for which to calculate the sunpath (ex: 2025, 1979, ...)
    :type startyear: int

    :param endyear: The last year for which to calculate the sunpath (ex: 2025, 1979, ...)
    :type endyear: int

    :param filename: Name of the file in wich to export the results. The file will be created if it doesn't exist or written over if it does
    :type filename: str
    """

    data = [[str(latitude), str(longitude)]]
    for i in range(0, 24):
        hourstring = time.strftime("%H:%M:%S", datetime.datetime(2000,month=1,day=1,hour=i).timetuple()) #Just to create the string
        data[0].append('E ' + hourstring)
        data[0].append('A ' + hourstring)

    for y in range(startyear, endyear+1):
        data.extend(calculate_annual_sun_path(latitude, longitude, y))
        print(f"{(y - startyear) + 1}/{endyear-startyear} Finished with year {y}", end="\r")
    print("\033[92m Done !\033[00m")


    with open(filename, "w", newline='') as file:
        writer = csv.writer(file, delimiter=';', lineterminator=";\r\n")
        writer.writerows(data)


def parse_command_line():
    """
    The function `parse_command_line` is a Python function that uses the `argparse` module to parse
    command line arguments and returns the parsed arguments.
    :return: The function `parse_command_line` returns the parsed command line arguments.
    """
    parser = argparse.ArgumentParser(description="Sunpath calculation based on the pysolar library, returns a csv compatible with pySunlight. The csv contains the altitude and azimuth of the sun, for a given coordinate, at each hour of the given interval. If the sun is bellow the horizon, the elevation and azimuth are not given.")
    parser.add_argument('--latitude', '-la', dest='latitude', type=float, help="The latitude with two decimal places", required=True)
    parser.add_argument('--longitude', '-lo', dest='longitude', type=float, help="The longitude with two decimal places", required=True)
    parser.add_argument('--start-year', '-s', dest='start_year', type=int, help='Start year of sunlight computation. Ex : --start-year 1975', required=True)
    parser.add_argument('--end-year', '-e', dest='end_year', type=int, help='End year of sunlight computation. Ex : --end-year 2075', required=True)
    parser.add_argument('--filename', '-f', nargs='?', type=str, help='Output file for sunpath results', default='sunpath.csv')

    return parser.parse_known_args()[0]

def main():
    args = parse_command_line()

    calculate_range_sunpath(args.latitude, args.longitude, args.start_year, args.end_year, args.filename)

if __name__ == '__main__':
    main()
