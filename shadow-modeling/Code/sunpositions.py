import math

# Def sunpos : The function calculates the sun's positions every three hours throughout the day for a given date and stores them in the 'time_list' array.
# Source of this function : https://www.mdpi.com/2220-9964/10/9/583
# Parameters : latitude, tau angle (time intervals), date
# Outputs : elevations, azimuths, hour_angles (angular displacement of the sun east or west of the local meridian), time_list
def sunpos(latitude_deg, tau_deg, date):
    phi = math.radians(latitude_deg)
    n_day_in_year = date.timetuple().tm_yday

    # Delta : day angle
    Delta = (2.0 * math.pi * n_day_in_year) / 365.25

    # delta : sun declination
    # The constants are part of the formula proposed by the article that is the source of this function.
    delta = math.asin(
        0.3978 * math.sin(Delta - 1.4 + 0.0355 * math.sin(Delta - 0.0489))
    )
    omega_ss = math.acos(-math.tan(phi) * math.tan(delta))
    omega_sr = -omega_ss
    tau_omega = math.radians(tau_deg)
    elevations = []
    azimuths = []
    hour_angles = []
    time_list = []
    omega = omega_sr
    while omega <= omega_ss:
        alpha = math.asin(
            math.sin(delta) * math.sin(phi)
            + math.cos(delta) * math.cos(omega) * math.cos(phi)
        )
        cos_alpha = math.cos(alpha)
        value = (
            math.sin(delta) * math.cos(phi)
            - math.cos(delta) * math.cos(omega) * math.sin(phi)
        ) / cos_alpha
        value = max(-1.0, min(1.0, value))
        psi = math.acos(value)
        if omega >= 0:
            psi = 2 * math.pi - psi

        elevations.append(math.degrees(alpha))
        azimuths.append(math.degrees(psi))
        hour_angles.append(math.degrees(omega))

        solar_hour = 12 + math.degrees(omega) / 15
        hours = int(solar_hour)
        minutes = int((solar_hour - hours) * 60)
        time_list.append(f"{hours:02d}:{minutes:02d}")

        omega += tau_omega

    return elevations, azimuths, hour_angles, time_list