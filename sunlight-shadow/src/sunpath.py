from pysolar.solar import get_altitude, get_azimuth
import datetime, time
import csv
from calendar import monthrange

def get_sun_position_csv(latitude : float, longitude : float, year : int, month : int, day : int):
    """
    This function uses pysolar to calculate and store into a csv the position of the sun for a given day
    :param latitude: a latitude in degrees
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
    :type longiture: float

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

def calculate_range_sunpath(latitude : float, longitude : float, startyear : int, endyear : int):

    data = [[str(latitude), str(longitude)]]
    for i in range(0, 24):
        hourstring = time.strftime("%H:%M:%S", datetime.datetime(2000,month=1,day=1,hour=i).timetuple()) #Just to create the string
        data[0].append('E ' + hourstring)
        data[0].append('A ' + hourstring)

    for y in range(startyear, endyear+1):
        data.extend(calculate_annual_sun_path(latitude, longitude, y))
        print(f"{(y - start) + 1}/{end-start} Finished with year {y}", end="\r")
    print("\033[92m Done !\033[00m")


    with open(f"sunpath.csv", "w", newline='') as file:
        writer = csv.writer(file, delimiter=';', lineterminator=";\r\n")
        writer.writerows(data)

start = 1975
end = 2075
calculate_range_sunpath(45.75, 4.85, start, end)

#for y in range(start,end):
#    calculate_annual_sun_path(45.75, 4.85, y)
    
#get_sun_position_csv(45.7578137, 4.8320114, 2025, 6, 10)